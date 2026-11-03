#!/usr/bin/env python3
"""
Convert the waste collection PDF into a waste_collection_schedule.yaml
for Home Assistant (static source). Pure parsing, no network, no AI.

Usage:
    python3 waste_pdf_to_yaml.py schedule.pdf
    python3 waste_pdf_to_yaml.py schedule.pdf -o waste_collection_schedule.yaml
    python3 waste_pdf_to_yaml.py schedule.pdf --until 2027-12-31

Also accepts a .txt file containing already-extracted text (for debugging).

Requires ONE of: `pypdf` (pip install pypdf) or `pdftotext` (poppler-utils).
"""
import argparse
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta

# --- Swedish -> English mapping (the only place Swedish lives) --------------
# (substring to look for in lowercase PDF name, English type, sensor name,
#  force_dates: always emit a plain date list, e.g. seasonal services)
SERVICES = [
    ("matavfall", "Food Waste", "food_waste", False),
    ("restavfall", "Residual Waste", "residual_waste", False),
    ("trädgård", "Garden Waste", "garden_waste", True),
    ("glas", "Glass", "glass", False),
    ("metall", "Metal", "metal", False),
    ("papper", "Paper & Plastic", "paper_plastic", False),
]

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}
WEEKDAYS = {"mån": 0, "tis": 1, "ons": 2, "tors": 3, "fre": 4, "lör": 5, "sön": 6}

DATE_RE = re.compile(
    r"(mån|tis|ons|tors|fre|lör|sön)\s+(\d{1,2})\s+"
    r"(jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec)\s+(\d{4})",
    re.IGNORECASE,
)
SERVICE_ID_RE = re.compile(r"Tjänst:\s*(\d+)")

MIN_DATES_FOR_RECURRENCE = 3
# Dummy date in the past (Jan 1 of this year), used for services with no
# dates in the PDF. On Jan 1 itself it would count as today, so use last year.
_today = date.today()
PLACEHOLDER_DATE = f"{_today.year - (1 if (_today.month, _today.day) == (1, 1) else 0)}-01-01"


def extract_text(path):
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8") as f:
            return f.read()
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None
    if PdfReader:
        try:
            return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
        except Exception as err:  # pypdf raises many error types on bad files
            sys.exit(f"Could not read the PDF: {err}")
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", path, "-"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"Could not read the PDF: {r.stderr.strip() or 'pdftotext failed'}")
        return r.stdout
    sys.exit("Need `pypdf` (pip install pypdf) or `pdftotext` (poppler-utils).")


def split_blocks(text):
    """One block per service. Each block starts at the line before 'Tjänst:'."""
    ids = list(SERVICE_ID_RE.finditer(text))
    if not ids:
        sys.exit("No 'Tjänst:' markers found - PDF layout changed?")
    blocks = []
    for i, m in enumerate(ids):
        before = text[:m.start()].rstrip().splitlines()
        name = before[-1].strip() if before else ""
        end = len(text)
        if i + 1 < len(ids):
            # Dates for this block end where the next block's header begins.
            nxt = text[:ids[i + 1].start()]
            h = nxt.rfind("Fastighet")
            end = h if h > m.end() else ids[i + 1].start()
        blocks.append((name, m.group(1), text[m.end():end]))
    return blocks


def parse_dates(chunk, label):
    out = []
    for wd, d, mon, y in DATE_RE.findall(chunk):
        dt = date(int(y), MONTHS[mon.lower()], int(d))
        if dt.weekday() != WEEKDAYS[wd.lower()]:
            print(f"WARNING [{label}]: {dt} is not a '{wd}' - check the PDF parse",
                  file=sys.stderr)
        out.append(dt)
    return sorted(set(out))


def detect_interval_weeks(dates):
    """Return N if all gaps are the same multiple of 7 days, else None."""
    gaps = {(b - a).days for a, b in zip(dates, dates[1:])}
    if len(gaps) == 1:
        g = gaps.pop()
        if g % 7 == 0 and g > 0:
            return g // 7
    return None


def lookup(name):
    low = name.lower()
    for key, eng, sensor, force in SERVICES:
        if key in low:
            return eng, sensor, force
    return None


def render(entries, until_override):
    lines = ["sources:"]
    for e in entries:
        dates = e["dates"]
        if not dates:
            lines += [f"  # {e['type']}: no dates in this PDF. Inert placeholder in the",
                      "  # past, so the source and its sensor still exist but show nothing",
                      "  # upcoming.",
                      "  - name: static",
                      f"    calendar_title: {e['type']}",
                      "    args:",
                      f"      type: {e['type']}",
                      "      dates:",
                      f'        - "{PLACEHOLDER_DATE}"  # dummy date',
                      ""]
            continue
        lines += [f"  # Service ID {e['id']}",
                  "  - name: static",
                  f"    calendar_title: {e['type']}",
                  "    args:",
                  f"      type: {e['type']}"]
        weeks = None if e["force"] else (
            detect_interval_weeks(dates)
            if len(dates) >= MIN_DATES_FOR_RECURRENCE else None)
        if weeks:
            until = until_override or dates[-1]
            lines += ["      frequency: WEEKLY",
                      f"      interval: {weeks}",
                      f'      start: "{dates[0]}"',
                      f'      until: "{until}"']
        else:
            lines.append("      dates:")
            lines += [f'        - "{d}"' for d in dates]
            if not e["force"] and len(dates) >= MIN_DATES_FOR_RECURRENCE:
                print(f"NOTE [{e['type']}]: irregular gaps, emitted as date list",
                      file=sys.stderr)
        lines.append("")
    # Last collection date of the non-seasonal services = end of PDF coverage.
    # Exposed as its own source so HA can warn before the dates run out.
    ends = [e["dates"][-1] for e in entries if e["dates"] and not e["force"]]
    ends = ends or [e["dates"][-1] for e in entries if e["dates"]]
    end_idx = None
    if ends:
        end_idx = len(entries)
        lines += ["  # Last collection date in this PDF. Used by the 'schedule ends' warning.",
                  "  - name: static",
                  "    calendar_title: Schedule End",
                  "    args:",
                  "      type: Schedule End",
                  "      dates:",
                  f'        - "{max(ends)}"',
                  ""]
    lines.append("sensors:")
    for i, e in enumerate(entries):
        lines += [f"  - name: {e['sensor']}",
                  f"    source_index: {i}",
                  "    details_format: upcoming", ""]
    # One sensor over all sources: same-day collections merge into one state.
    lines += ["  - name: waste_next",
              f"    source_index: [{', '.join(str(i) for i in range(len(entries)))}]",
              "    details_format: upcoming",
              "    value_template: '{{value.types|join(\", \")}}'",
              "    add_days_to: true", ""]
    if end_idx is not None:
        lines += ["  - name: waste_schedule_end",
                  f"    source_index: {end_idx}",
                  "    details_format: upcoming",
                  "    add_days_to: true", ""]
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default="waste_collection_schedule.yaml")
    ap.add_argument("--until", help="end date (YYYY-MM-DD) for recurring "
                    "schedules; default = last date in the PDF")
    ap.add_argument("--omit-empty", action="store_true",
                    help="leave empty/missing services out entirely (their "
                    "sensors disappear from HA and source_index shifts); "
                    "default keeps them as inert placeholders")
    a = ap.parse_args()
    until = datetime.strptime(a.until, "%Y-%m-%d").date() if a.until else None

    found = {}  # english type -> entry, for services present in the PDF
    for name, sid, chunk in split_blocks(extract_text(a.input)):
        info = lookup(name)
        if not info:
            sys.exit(f"Unknown service '{name}' (ID {sid}) - add it to SERVICES.")
        eng, sensor, force = info
        dates = parse_dates(chunk, name)
        if eng in found:  # same service listed twice: merge
            found[eng]["dates"] = sorted(set(found[eng]["dates"]) | set(dates))
        else:
            found[eng] = dict(id=sid, type=eng, sensor=sensor, force=force,
                              dates=dates)

    # Always walk the full SERVICES table in fixed order, so source_index and
    # entity names stay the same whatever the PDF contains.
    entries = []
    for _key, eng, sensor, force in SERVICES:
        e = found.get(eng)
        if e is None:
            e = dict(id=None, type=eng, sensor=sensor, force=force, dates=[])
            print(f"NOTE [{eng}]: not in this PDF", file=sys.stderr)
        elif not e["dates"]:
            print(f"NOTE [{eng}]: in the PDF but has no dates", file=sys.stderr)
        if not e["dates"] and a.omit_empty:
            continue
        entries.append(e)

    with open(a.output, "w", encoding="utf-8") as f:
        f.write(render(entries, until))
    empty = sum(1 for e in entries if not e["dates"])
    print(f"Wrote {a.output} ({len(entries)} services, {empty} empty)")


if __name__ == "__main__":
    main()
