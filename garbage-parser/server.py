#!/usr/bin/env python3
"""
Waste schedule add-on: upload a PDF, preview the generated YAML, persist it,
and serve the persisted file to Home Assistant.

Two HTTP servers, stdlib only:

  UI server   (--ui-port, default 8099)   Upload, preview, persist, roll back.
  File server (--file-port, default 8100) Read-only. GET /sources.yaml returns
              the persisted file. Nothing else is served and nothing can be
              written through it. Home Assistant pulls from here with curl.

Defaults suit running it on your own machine: listen on 127.0.0.1 only, keep
state in the per-user data directory, accept any client that can reach the
listen address. The add-on's Dockerfile sets the container-specific values
through environment variables (data dir, listen address, ingress client).

State files in the data dir:
  sources.yaml, sources.yaml.prev, meta.json, pending.yaml, pending.json
"""
import difflib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
PARSER = os.path.join(HERE, "waste_pdf_to_yaml.py")
MAX_PDF = 5 * 1024 * 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def user_data_dir(app="waste-schedule"):
    """Per-user data directory for the current platform."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, app)


def split_list(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


# Filled in by configure(); main() calls it with the final settings.
DATA = CURRENT = PREVIOUS = META = PENDING = PENDING_META = ""
ALLOWED = set()


def configure(data_dir, allowed):
    """allowed: client addresses the UI accepts; empty means any (rely on the
    listen address)."""
    global DATA, CURRENT, PREVIOUS, META, PENDING, PENDING_META, ALLOWED
    DATA = data_dir
    CURRENT = os.path.join(DATA, "sources.yaml")
    PREVIOUS = os.path.join(DATA, "sources.yaml.prev")
    META = os.path.join(DATA, "meta.json")
    PENDING = os.path.join(DATA, "pending.yaml")
    PENDING_META = os.path.join(DATA, "pending.json")
    ALLOWED = set(allowed)


configure(os.environ.get("WASTE_DATA_DIR") or user_data_dir(),
          split_list(os.environ.get("WASTE_UI_ALLOWED_IPS")))

FILE_PORT = 8100  # set by main()
GARDEN_WASTE = False  # set by main(): also handle Garden Waste
LOCK = threading.Lock()


# --- small file helpers -----------------------------------------------------
def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path):
    t = read_text(path)
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --- actions ----------------------------------------------------------------
def make_preview(pdf_bytes, filename, until, omit_empty):
    """Run the parser on the PDF. Returns (status_code, payload dict)."""
    if not pdf_bytes.startswith(b"%PDF"):
        return 400, {"ok": False, "error": "That does not look like a PDF file."}
    if until and not DATE_RE.match(until):
        return 400, {"ok": False, "error": "'until' must be YYYY-MM-DD."}

    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "in.pdf")
        out = os.path.join(tmp, "out.yaml")
        with open(pdf, "wb") as f:
            f.write(pdf_bytes)
        # No -I here: it would hide pypdf installed with `pip install --user`.
        # The script path is fixed and the cwd is an empty temp dir.
        cmd = [sys.executable, PARSER, pdf, "-o", out]
        if until:
            cmd += ["--until", until]
        if omit_empty:
            cmd.append("--omit-empty")
        if GARDEN_WASTE:
            cmd.append("--garden-waste")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=60, cwd=tmp)
        except subprocess.TimeoutExpired:
            return 500, {"ok": False, "error": "Parser timed out."}
        notes = re.sub(r"^Wrote \S+", "Parsed",
                       (r.stderr + r.stdout).strip(), flags=re.M)
        if r.returncode != 0:
            return 422, {"ok": False, "error": "Parser failed.", "notes": notes}
        new = read_text(out)
    if not new or "sources:" not in new or "sensors:" not in new:
        return 422, {"ok": False, "error": "Parser output looks wrong.", "notes": notes}

    cur = read_text(CURRENT)
    if cur is None:
        diff = "(nothing persisted yet - everything is new)"
    else:
        diff = "".join(difflib.unified_diff(
            cur.splitlines(True), new.splitlines(True),
            "current", "new", n=2)) or "(identical to the current file)"

    token = secrets.token_hex(8)
    write_atomic(PENDING, new)
    write_atomic(PENDING_META, json.dumps({
        "token": token, "filename": filename, "created": now_iso()}))
    return 200, {"ok": True, "token": token, "notes": notes,
                 "diff": diff, "yaml": new}


def persist_pending(token):
    meta = read_json(PENDING_META)
    new = read_text(PENDING)
    if not meta or new is None:
        return 409, {"ok": False, "error": "Nothing to persist. Upload a PDF first."}
    if not token or not secrets.compare_digest(token, meta.get("token", "")):
        return 409, {"ok": False, "error": "The preview is outdated. Upload again."}
    cur = read_text(CURRENT)
    if cur is not None:
        write_atomic(PREVIOUS, cur)
    write_atomic(CURRENT, new)
    write_atomic(META, json.dumps({
        "persisted": now_iso(), "filename": meta.get("filename", "")}))
    for p in (PENDING, PENDING_META):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    return 200, {"ok": True}


def rollback():
    prev = read_text(PREVIOUS)
    if prev is None:
        return 409, {"ok": False, "error": "There is no previous version."}
    cur = read_text(CURRENT)
    write_atomic(CURRENT, prev)
    if cur is not None:  # swap, so a second rollback undoes the first
        write_atomic(PREVIOUS, cur)
    write_atomic(META, json.dumps({
        "persisted": now_iso(), "filename": "(rolled back)"}))
    return 200, {"ok": True}


def status():
    cur = read_text(CURRENT)
    meta = read_json(META) or {}
    return {
        "has_current": cur is not None,
        "lines": len(cur.splitlines()) if cur else 0,
        "persisted": meta.get("persisted"),
        "file_port": FILE_PORT,
        "filename": meta.get("filename"),
        "has_previous": os.path.exists(PREVIOUS),
    }


# --- UI server --------------------------------------------------------------
# --- static files -----------------------------------------------------------
# Only these names are served, so nothing else on disk is reachable.
STATIC_DIR = os.path.join(HERE, "static")
ASSETS = {
    "/": ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/style.css": ("style.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
}




class UIHandler(BaseHTTPRequestHandler):
    server_version = "WasteUI/1"

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        if not ALLOWED or self.client_address[0] in ALLOWED:
            return True
        self._send(403, {"ok": False, "error": "Forbidden"})
        return False

    def _route(self):
        parts = urlsplit(self.path)
        return "/" + parts.path.lstrip("/"), parse_qs(parts.query)

    def do_GET(self):
        if not self._allowed():
            return
        path, _ = self._route()
        if path in ASSETS:
            name, ctype = ASSETS[path]
            text = read_text(os.path.join(STATIC_DIR, name))
            if text is None:
                self._send(404, {"ok": False, "error": "Not found"})
            else:
                self._send(200, text, ctype)
        elif path == "/status":
            self._send(200, status())
        elif path == "/current":
            cur = read_text(CURRENT)
            if cur is None:
                self._send(404, "Nothing persisted yet.", "text/plain")
            else:
                self._send(200, cur, "text/plain")
        else:
            self._send(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if not self._allowed():
            return
        path, q = self._route()
        if path == "/upload":
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"ok": False, "error": "Length required"})
            if n <= 0 or n > MAX_PDF:
                return self._send(413, {"ok": False, "error": "PDF must be under 5 MB."})
            body = self.rfile.read(n)
            fname = os.path.basename(q.get("filename", [""])[0])[:120]
            until = q.get("until", [""])[0]
            omit = q.get("omit_empty", [""])[0] == "1"
            with LOCK:
                code, payload = make_preview(body, fname, until, omit)
            self._send(code, payload)
        elif path == "/persist":
            with LOCK:
                code, payload = persist_pending(q.get("token", [""])[0])
            self._send(code, payload)
        elif path == "/rollback":
            with LOCK:
                code, payload = rollback()
            self._send(code, payload)
        else:
            self._send(404, {"ok": False, "error": "Not found"})


# --- file server (read-only) ------------------------------------------------
class FileHandler(BaseHTTPRequestHandler):
    server_version = "WasteFile/1"

    def _serve(self, with_body):
        path = "/" + urlsplit(self.path).path.lstrip("/")
        cur = read_text(CURRENT) if path == "/sources.yaml" else None
        if cur is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = cur.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/yaml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if with_body:
            self.wfile.write(data)

    def do_GET(self):
        self._serve(True)

    def do_HEAD(self):
        self._serve(False)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Waste schedule server. Every option can also be set with "
        "the environment variable shown; the command line wins.")
    ap.add_argument("--data-dir", default=os.environ.get("WASTE_DATA_DIR"),
                    help="where state is kept [WASTE_DATA_DIR] "
                    f"(default: {user_data_dir()})")
    ap.add_argument("--host", default=os.environ.get("WASTE_HOST", "127.0.0.1"),
                    help="address to listen on [WASTE_HOST] (default 127.0.0.1)")
    ap.add_argument("--ui-port", type=int,
                    default=int(os.environ.get("WASTE_UI_PORT", "8099")),
                    help="[WASTE_UI_PORT] (default 8099)")
    ap.add_argument("--file-port", type=int,
                    default=int(os.environ.get("WASTE_FILE_PORT", "8100")),
                    help="read-only file server [WASTE_FILE_PORT] (default 8100)")
    ap.add_argument("--options-file", default=os.environ.get("WASTE_OPTIONS_FILE"),
                    help="JSON file with the add-on options [WASTE_OPTIONS_FILE]; "
                    "key: garden_waste (true/false, default false)")
    ap.add_argument("--garden-waste", action="store_true",
                    default=os.environ.get("WASTE_GARDEN_WASTE", "") == "1",
                    help="also handle Garden Waste [WASTE_GARDEN_WASTE=1]; "
                    "can also be set in the options file")
    ap.add_argument("--allow", default=os.environ.get("WASTE_UI_ALLOWED_IPS", ""),
                    help="comma-separated client addresses the UI accepts "
                    "[WASTE_UI_ALLOWED_IPS] (default: any; the listen address "
                    "is then the only restriction)")
    a = ap.parse_args()

    global FILE_PORT, GARDEN_WASTE
    FILE_PORT = a.file_port
    options = (read_json(a.options_file) if a.options_file else None) or {}
    GARDEN_WASTE = a.garden_waste or options.get("garden_waste") is True
    configure(a.data_dir or user_data_dir(), split_list(a.allow))
    os.makedirs(DATA, exist_ok=True)

    ui = ThreadingHTTPServer((a.host, a.ui_port), UIHandler)
    files = ThreadingHTTPServer((a.host, a.file_port), FileHandler)
    threading.Thread(target=files.serve_forever, daemon=True).start()
    clients = ", ".join(sorted(ALLOWED)) or "any"
    print(f"Data dir:    {DATA}\n"
          f"UI:          http://{a.host}:{a.ui_port}/  (clients: {clients})\n"
          f"File server: http://{a.host}:{a.file_port}/sources.yaml\n"
          f"Garden Waste: {'handled' if GARDEN_WASTE else 'left out'}", flush=True)
    try:
        ui.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
