#!/usr/bin/env python3
"""
End-to-end smoke test for the add-on server. Stdlib only.

    python3 smoke_test.py path/to/schedule.pdf

Starts server.py with a temporary data dir and free ports,
walks through upload / preview / persist / file server / rollback, and exits
non-zero if anything is wrong. Needs pypdf or pdftotext (same as the parser).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
failures = []


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(url, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jcall(url, data=None, method=None):
    code, body = call(url, data, method)
    try:
        return code, json.loads(body)
    except ValueError:
        return code, {}


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        failures.append(name)


def main():
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        sys.exit(__doc__)
    pdf = open(sys.argv[1], "rb").read()
    ui_port, file_port = free_port(), free_port()
    with tempfile.TemporaryDirectory() as data:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "server.py"),
             "--data-dir", data, "--ui-port", str(ui_port),
             "--file-port", str(file_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        U = f"http://127.0.0.1:{ui_port}"
        F = f"http://127.0.0.1:{file_port}/sources.yaml"
        try:
            for _ in range(50):
                try:
                    call(U + "/status")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                sys.exit("server did not start:\n" + (proc.stderr.read() or ""))

            code, st = jcall(U + "/status")
            check("starts empty", code == 200 and not st["has_current"])
            check("file server 404 before persist", call(F)[0] == 404)
            check("page loads", call(U + "/")[0] == 200)
            check("stylesheet and script load",
                  call(U + "/style.css")[0] == 200 and call(U + "/app.js")[0] == 200)
            check("only named static files are served",
                  call(U + "/static/index.html")[0] == 404
                  and call(U + "/../server.py")[0] == 404
                  and call(U + "/server.py")[0] == 404)

            code, j = jcall(U + "/upload", b"not a pdf")
            check("rejects non-PDF", code == 400 and not j.get("ok"))

            code, j = jcall(U + "/upload", b"%PDF-1.4 this is not a real pdf")
            check("corrupt PDF gives a readable error, not a traceback",
                  code == 422 and "Traceback" not in json.dumps(j), str(j)[:200])

            code, j = jcall(U + "/upload?until=nope", pdf)
            check("rejects bad until date", code == 400)

            code, j = jcall(U + "/upload?filename=test.pdf", pdf)
            check("upload and parse", code == 200 and j.get("ok"), str(j)[:300])
            if not j.get("ok"):
                return
            check("yaml has sources and sensors",
                  "sources:" in j["yaml"] and "sensors:" in j["yaml"])
            check("yaml has the combined and end sensors",
                  "waste_next" in j["yaml"] and "waste_schedule_end" in j["yaml"])
            token = j["token"]

            code, _ = jcall(U + "/persist?token=wrong", b"")
            check("rejects wrong token", code == 409)
            code, _ = jcall(U + "/persist?token=" + token, b"")
            check("persist", code == 200)
            code, _ = jcall(U + "/persist?token=" + token, b"")
            check("token cannot be reused", code == 409)

            code, body = call(F)
            check("file server serves the persisted file",
                  code == 200 and body.decode() == j["yaml"])
            check("file server only serves /sources.yaml",
                  call(F.replace("sources.yaml", "meta.json"))[0] == 404)
            check("file server is read-only",
                  call(F, b"x")[0] in (405, 501))

            code, j2 = jcall(U + "/upload?until=2099-12-31", pdf)
            check("second upload diffs against current",
                  j2.get("ok") and "2099-12-31" in j2["diff"])
            check("rollback with no previous version fails",
                  jcall(U + "/rollback", b"")[0] == 409)
            jcall(U + "/persist?token=" + j2["token"], b"")
            check("second persist changed the file", call(F)[1].decode() == j2["yaml"])
            check("rollback", jcall(U + "/rollback", b"")[0] == 200)
            check("file is back to the first version", call(F)[1].decode() == j["yaml"])
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
