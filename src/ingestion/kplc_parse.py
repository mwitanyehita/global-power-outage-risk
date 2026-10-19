"""KPLC interruption-notice PDFs -> structured outage events (Day 11, Kenya).

Day 10 (``src/ingestion/kplc.py``, ``reports/day10_kenya_probes.md``) acquired the
official notice list: 110 notices under ``https://kplc.co.ke/customer-support#powerschedule``
(saved as ``data/raw/outages/kenya/kplc_powerschedule_notices.json``).  This module
turns the notice **PDFs** into one event row per ``AREA:`` block, joined (later) to
admin units via ``county``/``region``.

Verified PDF text structure (pypdf, real samples)::

    NAIROBI REGION
    AREA: PART OF PARKLANDS
    DATE: Sunday 04.10.2026                    TIME: 9.00 A.M. – 5.00 P.M.
    4th Parklands, Masari Rd, ... & adjacent customers.

    SPECIAL POWER SUPPLY INTERRUPTION NOTICE
    ...
    The interruption was caused by an occurence involving a battery charger ...
    WESTERN REGION
    KISUMU COUNTY
    AREA: MUHORONI SUGAR FACTORY, ...
    DATE: Friday 16.10.2026   TIME: 11.00 A.M. – 6.00 P.M.

Design rules (see reports/day11_kplc_planned_events.md for the caveats):

* ``notice_class`` is ``"planned"`` (Rule-27 maintenance) unless the document carries a
  SPECIAL header or ``was caused by`` prose -> ``"special"``.
* Dates are ``DD.MM.YYYY`` in structured lines; times accept ``9.00 A.M.``, ``9:00 a.m.``,
  ``9.00A.M.`` and ``0900Hrs`` variants. Unknown time -> empty string, never guessed.
* Overnight ranges (``10.00 P.M. – 6.00 A.M.``) keep the raw local times: no date shift
  is invented, so ``event_date_time_end_utc`` stays empty for them.
* Image-only PDFs (0 extracted chars) cannot be OCR'd here (no tesseract) -> recorded as
  unparseable, never guessed.
* Local time is EAT (Africa/Nairobi, UTC+3 year-round) -> UTC via
  ``src.cleaning.standardize.normalize_timestamp(assume_tz="Africa/Nairobi")``.

No network calls happen at import time; downloads live in :func:`download_notices`.

Usage:
    python -m src.ingestion.kplc_parse --limit 110
    python -m src.ingestion.kplc_parse --extract-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from src.cleaning.standardize import normalize_timestamp
from src.config import DATA_RAW
from src.ingestion import kplc  # session headers + backoff GET are reused, not re-invented

KENYA_RAW_DIR: Path = DATA_RAW / "outages" / "kenya"
DEFAULT_PDF_DIR: Path = KENYA_RAW_DIR
DEFAULT_OUT_CSV = Path("data/processed/kplc_planned_outages.csv")
MANIFEST_NAME = "pdfs_manifest.json"
NOTICES_JSON: Path = KENYA_RAW_DIR / "kplc_powerschedule_notices.json"

COUNTRY_ISO3 = "KEN"
ASSUME_TZ = "Africa/Nairobi"
PREMISES_MAX_CHARS = 500

EVENT_COLUMNS: list[str] = [
    "country_iso3",
    "region",
    "county",
    "area",
    "event_date",
    "time_start_local",
    "time_end_local",
    "event_date_time_start_utc",
    "event_date_time_end_utc",
    "notice_class",
    "premises_text",
    "source_document",
]

# --- line patterns -----------------------------------------------------------------
_AREA_RE = re.compile(r"^\s*AREAS?\s*:\s*(?P<area>.*)$", re.IGNORECASE)
_DATE_TOKEN_RE = re.compile(r"\bDATE\s*:", re.IGNORECASE)
_TIME_TOKEN_RE = re.compile(r"\bTIME\s*:?\s*(?P<rest>.*)$", re.IGNORECASE)
_DMY_RE = re.compile(r"(?P<d>\d{1,2})\.\s*(?P<m>\d{1,2})\.\s*(?P<y>\d{4})")
# Uppercase-only headers, e.g. "NAIROBI REGION", "MT. KENYA REGION",
# "PARTS OF UASIN GISHU COUNTY", "KISUMU COUNTY".
_REGION_RE = re.compile(
    r"^(?P<name>[A-Z0-9][A-Z0-9 .'’&/()-]{1,40}?)\s+REGIONS?\s*:?\s*$")
_COUNTY_RE = re.compile(
    r"^(?:PARTS?\s+OF\s+)?(?P<name>[A-Z0-9][A-Z0-9 .'’&/()-]{1,40}?)\s+COUNTY\s*:?\s*$")
_RANGE_SPLIT_RE = re.compile(r"\s*(?:[–—]|-|\bto\b)\s*", re.IGNORECASE)
_CLOCK_RE = re.compile(
    r"^(?P<h>\d{1,2})(?:[.:](?P<m>\d{1,2}))?\s*(?P<mer>[AaPp]\.?[Mm]\.?)?$")
_HRS_RE = re.compile(r"^(?P<h>\d{1,2})(?P<m>\d{2})\s*[Hh]rs?\.?$")
# Page furniture: page numbers, phone-number lines, per-page letterhead/prose.
_NOISE_RE = re.compile(r"^(?:[\d\s+()\-–—.]+|Page\s+\d+(?:\s+of\s+\d+)?)$")
_BOILERPLATE_PREFIXES = (
    "power maintenance notice",
    "special power supply interruption notice",
    "notice is hereby given under rule 27",
    "that the electricity supply will be interrupted",
    "(it is necessary to interrupt supply",
    "we wish to inform our customers",
    "we regret any inconvenience",
    "the interruption was caused by",
    "and will be reinstated at",
    "www.kplc.co.ke",
)
_SPECIAL_HEADER_RE = re.compile(
    r"SPECIAL\s+POWER\s+SUPPLY\s+INTERRUPTION", re.IGNORECASE)
_CAUSE_RE = re.compile(r"\bwas\s+caused\s+by\b", re.IGNORECASE)


# --- time helpers ------------------------------------------------------------------
def _fmt_time(hour: int, minute: int, meridiem: str) -> str:
    """24h ``HH:MM`` (local) or ``""`` when the token cannot be trusted."""
    if not (0 <= minute <= 59):
        return ""
    if meridiem:
        if not 1 <= hour <= 12:  # "25.00 P.M." is not a clock time
            return ""
        up = meridiem.upper().replace(".", "")
        hour = hour % 12 + (12 if up == "PM" else 0)
    if 0 <= hour <= 23:
        return f"{hour:02d}:{minute:02d}"
    return ""


def parse_time_token(token: str) -> str:
    """``'9.00 A.M.'`` / ``'9:00 a.m.'`` / ``'0900Hrs'`` / ``'1830hrs'`` -> ``'09:00'``.

    Returns ``""`` for anything that does not look like a clock time — the caller
    keeps the field empty rather than guessing.
    """
    text = token.strip(" \t,;()[]{}\"'")
    if not text:
        return ""
    m = _HRS_RE.match(text)
    if m:
        return _fmt_time(int(m.group("h")), int(m.group("m")), "")
    m = _CLOCK_RE.match(text)
    if m:
        return _fmt_time(
            int(m.group("h")), int(m.group("m") or 0), m.group("mer") or "")
    return ""


def parse_time_range(text: str) -> tuple[str, str]:
    """Split a TIME value into ``(start, end)`` local ``HH:MM`` strings.

    Handles en-dash, em-dash, hyphen and ``to`` separators. Either side may be
    unknown -> ``""``. Overnight ranges come back raw (start > end); the caller
    must not shift dates.
    """
    parts = [p for p in _RANGE_SPLIT_RE.split(text) if p.strip()]
    times = [t for t in (parse_time_token(p) for p in parts) if t]
    start = times[0] if times else ""
    end = times[1] if len(times) > 1 else ""
    return start, end


def _is_overnight(start: str, end: str) -> bool:
    """``end < start`` means the range crosses midnight (kept raw, no date shift)."""
    return bool(start and end and end < start)


# --- document-level helpers --------------------------------------------------------
def detect_notice_class(text: str) -> str:
    """``"special"`` for fault-caused interruptions, else ``"planned"`` (Rule 27)."""
    if _SPECIAL_HEADER_RE.search(text) or _CAUSE_RE.search(text):
        return "special"
    return "planned"


def _clean_area(area: str) -> str:
    """Verbatim area text with whitespace collapsed and trailing punctuation dropped."""
    return re.sub(r"\s+", " ", area).strip().rstrip(" \t,;:.")


def _iso_date(d: str, m: str, y: str) -> str:
    try:
        return _date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return ""


def _is_furniture(line: str) -> bool:
    """Page numbers / phone-number lines: dropped from premises_text."""
    return bool(_NOISE_RE.match(line))


def _is_boilerplate(line: str) -> bool:
    """Per-page letterhead or stock prose (e.g. the Rule-27 block repeated on page 2).

    Once one of these starts, the following wrapped lines belong to it too — they are
    absorbed via a per-block flag so they never leak into the previous event's
    premises_text.
    """
    return line.lower().startswith(_BOILERPLATE_PREFIXES)


# --- the parser --------------------------------------------------------------------
def parse_notice_text(text: str, source_document: str) -> list[dict]:
    """Parse one notice's extracted text into one dict per ``AREA:`` block.

    Args:
        text: text extracted from the notice PDF (pypdf ``extract_text``).
        source_document: provenance label stored on every row (file name).

    Returns:
        dicts with keys ``country_iso3, region, county, area, event_date,
        time_start_local, time_end_local, notice_class, premises_text,
        source_document``.  Empty text (image-only PDF) -> ``[]``; unknown values
        are empty strings — nothing is ever inferred.
    """
    if not text or not text.strip():
        return []

    notice_class = detect_notice_class(text)
    # Collapse intra-line whitespace (pypdf emits padding/NBSP), keep line order;
    # drop blank lines so date-line lookahead skips over them.
    lines = [ln for ln in (" ".join(raw.split()) for raw in text.split("\n")) if ln]

    events: list[dict] = []
    region = ""
    county = ""
    pending: dict | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        premises = re.sub(r"\s+", " ", " ".join(pending["premises"]))
        events.append({
            "country_iso3": COUNTRY_ISO3,
            "region": pending["region"],
            "county": pending["county"],
            "area": _clean_area(pending["area"]),
            "event_date": pending["event_date"],
            "time_start_local": pending["time_start"],
            "time_end_local": pending["time_end"],
            "notice_class": notice_class,
            "premises_text": premises[:PREMISES_MAX_CHARS],
            "source_document": source_document,
        })
        pending = None

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        m = _REGION_RE.match(line)
        if m:
            flush()
            region = m.group("name").strip()
            county = ""  # a new region resets any previous county
            continue
        m = _COUNTY_RE.match(line)
        if m:
            flush()
            county = m.group("name").strip()
            continue
        m = _AREA_RE.match(line)
        if m:
            flush()
            pending = {
                "area": m.group("area").strip(),
                "region": region,
                "county": county,
                "event_date": "",
                "time_start": "",
                "time_end": "",
                "premises": [],
                "seen_date": False,
                "in_boilerplate": False,
            }
            continue
        if pending is None:
            continue

        is_date_line = bool(_DATE_TOKEN_RE.search(line)) or (
            not pending["seen_date"] and bool(_DMY_RE.search(line)))
        if is_date_line:
            pending["seen_date"] = True
            d = _DMY_RE.search(line)
            if d:
                pending["event_date"] = _iso_date(d.group("d"), d.group("m"), d.group("y"))
            # TIME may sit on the date line, on the next line, or be absent.
            consumed = _apply_times(pending, line, lines, i)
            i += consumed
            continue

        if _TIME_TOKEN_RE.search(line) and not pending["seen_date"]:
            _apply_times(pending, line, lines, i)  # TIME before DATE (defensive)
            continue

        if not pending["seen_date"]:
            # Lines between AREA: and DATE: belong to the area value (wrap-around).
            pending["area"] = f"{pending['area']} {line}".strip()
            continue

        if _is_boilerplate(line):
            pending["in_boilerplate"] = True
            continue
        if pending["in_boilerplate"] or _is_furniture(line):
            continue
        pending["premises"].append(line)

    flush()
    return events


def _apply_times(pending: dict, line: str, lines: list[str], next_idx: int) -> int:
    """Read ``TIME: ...`` from ``line`` (or the following line) into ``pending``.

    A line is only adopted as a TIME source when it yields at least one clock time,
    so stray premises lines mentioning "time" are never swallowed. Returns how many
    extra lines were consumed after ``line`` itself.
    """
    m = _TIME_TOKEN_RE.search(line)
    if m and m.group("rest").strip():
        start, end = parse_time_range(m.group("rest"))
        if start or end:
            if not (pending["time_start"] or pending["time_end"]):
                pending["time_start"], pending["time_end"] = start, end
            return 0
    # No usable TIME on this line: peek at the next line for a dedicated TIME line.
    peek = lines[next_idx] if next_idx < len(lines) else ""
    m2 = _TIME_TOKEN_RE.search(peek) if peek else None
    if m2 and not _DMY_RE.search(peek) and not _AREA_RE.match(peek):
        start, end = parse_time_range(m2.group("rest"))
        if start or end:
            if not (pending["time_start"] or pending["time_end"]):
                pending["time_start"], pending["time_end"] = start, end
            return 1
    return 0


# --- timezone conversion -----------------------------------------------------------
def add_utc_times(events: list[dict], assume_tz: str = "Africa/Nairobi") -> list[dict]:
    """Add ``event_date_time_start_utc`` / ``event_date_time_end_utc`` in place.

    Local date+time are combined and localized with ``assume_tz`` (KPLC documents
    are EAT, ``Africa/Nairobi`` = UTC+3 year-round) then converted to UTC. A UTC
    date-time is set **only when both date and time are known**; overnight ends
    (``end < start``) are left empty because inventing a date shift would be a
    guess. Returns the same list for chaining.
    """
    starts: list[str | None] = []
    ends: list[str | None] = []
    for ev in events:
        day = ev.get("event_date") or ""
        start = ev.get("time_start_local") or ""
        end = ev.get("time_end_local") or ""
        starts.append(f"{day} {start}" if day and start else None)
        if day and start and end and not _is_overnight(start, end):
            ends.append(f"{day} {end}")
        else:
            ends.append(None)

    start_utc = normalize_timestamp(pd.Series(starts, dtype="object"), assume_tz=assume_tz)
    end_utc = normalize_timestamp(pd.Series(ends, dtype="object"), assume_tz=assume_tz)
    for ev, s_ts, e_ts in zip(events, start_utc, end_utc):
        ev["event_date_time_start_utc"] = None if pd.isna(s_ts) else s_ts
        ev["event_date_time_end_utc"] = None if pd.isna(e_ts) else e_ts
    return events


# --- downloads ---------------------------------------------------------------------
def load_notices(limit: int | None = None) -> list[dict]:
    """The Day-10 official notice list (newest first); re-crawls only if absent."""
    if not NOTICES_JSON.exists():
        print(f"  [warn] {NOTICES_JSON} absent -> re-crawling the official list")
        notices: list[dict] = kplc.fetch_all()
    else:
        payload = json.loads(NOTICES_JSON.read_text(encoding="utf-8"))
        notices = list(payload["notices"])
    return notices if limit is None else notices[:limit]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def notice_stem(notice: dict, position: int, claimed: set[str]) -> str:
    """Deterministic ``kplc_notice_<posted date>`` stem; ``_HHMM`` on same-day clashes."""
    posted_iso = notice.get("posted_iso") or kplc.parse_posted(notice.get("posted", "") or "")
    if posted_iso:
        base = f"kplc_notice_{posted_iso[:10]}"
        suffix = posted_iso[11:16].replace(":", "")
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", (notice.get("title") or "notice").lower()).strip("-")
        base = f"kplc_notice_{slug or 'notice'}"
        suffix = str(position)
    stem = base
    n = 2
    while stem in claimed:
        stem = f"{base}_{suffix}" if n == 2 else f"{base}_{suffix}_{n}"
        n += 1
    claimed.add(stem)
    return stem


def _load_manifest(manifest_path: Path) -> dict:
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload.get("files"), dict):
                return payload
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": {}}


def _save_manifest(manifest_path: Path, files: dict[str, dict]) -> None:
    payload = {
        "source": kplc.BASE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": dict(sorted(files.items())),
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def download_notices(
    notices: list[dict],
    pdf_dir: Path = DEFAULT_PDF_DIR,
    limit: int = 110,
    sleep_s: float = 1.0,
) -> dict:
    """Download notice PDFs (3 retries with backoff, existing files skipped).

    Files are named ``kplc_notice_<posted date>.pdf`` (``_HHMM`` suffix when two
    notices share a posted date). A ``pdfs_manifest.json`` next to the PDFs records
    ``sha256``, ``url``, ``bytes`` and ``fetched_at`` per file — it is rewritten on
    every call so a partially failed run still keeps what it got. Per-notice failures
    are collected, never raised.
    """
    pdf_dir = Path(pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = pdf_dir / MANIFEST_NAME
    files: dict[str, dict] = dict(_load_manifest(manifest_path).get("files", {}))
    claimed: set[str] = set()
    downloaded = skipped = 0
    failures: list[dict] = []

    try:
        for position, notice in enumerate(notices[:limit]):
            url = notice.get("pdf_url") or ""
            stem = notice_stem(notice, position, claimed)
            path = pdf_dir / f"{stem}.pdf"
            if path.exists():
                skipped += 1
                if not (files.get(stem) or {}).get("sha256"):
                    data = path.read_bytes()
                    files[stem] = {
                        "filename": path.name,
                        "url": url,
                        "title": notice.get("title", ""),
                        "sha256": _sha256(data),
                        "bytes": len(data),
                        "fetched_at": None,  # pre-existing file, fetch time unknown
                    }
                continue
            try:
                resp = kplc._get(url, tries=3)  # requests + backoff, reused from kplc.py
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                failures.append({"title": notice.get("title", ""), "url": url,
                                 "error": str(exc)})
                time.sleep(sleep_s)
                continue
            data = resp.content
            path.write_bytes(data)
            files[stem] = {
                "filename": path.name,
                "url": url,
                "title": notice.get("title", ""),
                "sha256": _sha256(data),
                "bytes": len(data),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            downloaded += 1
            print(f"  [get ] {path.name}: {len(data):,} bytes")
            time.sleep(sleep_s)
    finally:
        files = {k: v for k, v in files.items() if (pdf_dir / k).exists()}
        _save_manifest(manifest_path, files)

    return {
        "downloaded": downloaded,
        "skipped_existing": skipped,
        "failed": len(failures),
        "failures": failures,
        "n_files": len(files),
        "manifest": str(manifest_path),
    }


# --- extraction --------------------------------------------------------------------
def _pdf_text(path: Path) -> str:
    """Extracted text of one PDF (``""`` when the PDF is image-only)."""
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _list_pdfs(pdf_dir: Path) -> list[Path]:
    """Manifest-listed PDFs when a manifest exists, else every PDF in the directory."""
    files = _load_manifest(pdf_dir / MANIFEST_NAME).get("files", {})
    listed = [pdf_dir / name for name in sorted(files) if (pdf_dir / name).exists()]
    if listed:
        return listed
    return sorted(pdf_dir.glob("*.pdf"))


def dedup_events(events: list[dict]) -> list[dict]:
    """Keep the first row per ``(area, event_date, time_start_local, notice_class)``."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for ev in events:
        key = (ev["area"], ev["event_date"], ev["time_start_local"], ev["notice_class"])
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def extract_all(
    pdf_dir: Path = DEFAULT_PDF_DIR,
    out_csv: Path = DEFAULT_OUT_CSV,
) -> dict:
    """Parse every notice PDF, dedup, sort by date and write the event CSV.

    Image-only PDFs (0 extracted chars) and documents yielding no ``AREA`` block are
    recorded as unparseable — never guessed. Returns a summary dict
    ``{n_notices, n_events, n_unparseable, date_min, date_max, by_class, by_region}``
    (plus ``unparseable_detail`` for reporting).
    """
    pdf_dir = Path(pdf_dir)
    paths = _list_pdfs(pdf_dir)

    events: list[dict] = []
    unparseable: list[dict] = []
    for path in paths:
        try:
            text = _pdf_text(path)
        except Exception as exc:  # noqa: BLE001 - unreadable PDF == unparseable
            unparseable.append({"source_document": path.name, "reason": f"read_error: {exc}"})
            continue
        if not text.strip():
            unparseable.append({"source_document": path.name, "reason": "image_only"})
            continue
        doc_events = parse_notice_text(text, path.name)
        if not doc_events:
            unparseable.append({"source_document": path.name, "reason": "no_area_blocks"})
            continue
        events.extend(doc_events)

    events = dedup_events(events)
    events.sort(key=lambda ev: ev["event_date"])  # stable: date, then document order
    events = add_utc_times(events, assume_tz=ASSUME_TZ)

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(events, columns=EVENT_COLUMNS).to_csv(out_csv, index=False)

    dates = [ev["event_date"] for ev in events if ev["event_date"]]
    summary = {
        "n_notices": len(paths),
        "n_events": len(events),
        "n_unparseable": len(unparseable),
        "date_min": min(dates, default=""),
        "date_max": max(dates, default=""),
        "by_class": dict(sorted(Counter(ev["notice_class"] for ev in events).items())),
        "by_region": dict(sorted(Counter(ev["region"] or "(none)" for ev in events).items())),
        "unparseable_detail": unparseable,
        "out_csv": str(out_csv),
    }
    return summary


# --- CLI ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=110,
                        help="number of notice PDFs to download (default 110)")
    parser.add_argument("--extract-only", action="store_true",
                        help="skip downloads, parse the PDFs already on disk")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR,
                        help=f"PDF directory (default {DEFAULT_PDF_DIR})")
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV,
                        help=f"output CSV (default {DEFAULT_OUT_CSV})")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="seconds to sleep between downloads (default 1.0)")
    args = parser.parse_args(argv)

    if not args.extract_only:
        notices = load_notices()
        report = download_notices(notices, args.pdf_dir, limit=args.limit,
                                  sleep_s=args.sleep)
        print(f"[download] {report['downloaded']} fetched, "
              f"{report['skipped_existing']} already present, "
              f"{report['failed']} failed -> {report['manifest']}")
        for failure in report["failures"]:
            print(f"  [warn] failed: {failure['title']} ({failure['error']})")

    summary = extract_all(args.pdf_dir, out_csv=args.out_csv)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
