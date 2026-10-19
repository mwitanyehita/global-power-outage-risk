"""Nigeria — NERC annual reports: grid-collapse event extraction (Day 10).

The Nigerian Electricity Regulatory Commission (NERC) publishes annual
reports (*Annual Report & Accounts*) that contain the official record of
national grid-collapse incidents. Two table shapes exist across the reports:

* **dated incident tables** — ``dd/mm/yyyy HH:MMHrs Partial|Full dd/mm/yyyy
  HH:MMHrs`` (2024, 2025 reports) and prose/ordinal dates such as
  ``19th of September, 2023 (11:31hours)`` (2023 report) — these are the rows
  extracted into events;
* **summary count tables** — ``Category of Collapse <year> <year> ...`` with
  ``Partial Collapses`` / ``Total Collapses`` rows (every report). Counts are
  parsed too, and used to infer ``partial``/``full`` for the ordinal-style rows
  that do not state a type themselves.

Reports for 2019 and 2021 were **never published** by NERC (verified against
the live resource pages and the archived pre-2023 repository listing), and the
2020/2022 reports only carry counts — so dated 2019–2022 events are unavailable
from annual reports. See ``reports/day10_nerc_events.md`` for the honest
coverage statement.

Layout::

    data/raw/outages/nerc/
        {report_year}_annual_report.pdf    verbatim NERC PDFs (never edited)
        events_extracted.csv               dated incidents, one row per event
        events_extracted_meta.json         provenance sidecar

Usage::

    python -m src.ingestion.nerc               # download + extract + write CSV
    python -m src.ingestion.nerc --overwrite   # re-fetch the PDFs
    python -m src.ingestion.nerc --audit       # extract only (no network)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from pypdf import PdfReader

from src.config import DATA_RAW, ensure_dirs
from src.ingestion.utils import get_with_retries, write_bytes_atomic, write_provenance

COUNTRY_ISO3 = "NGA"
NERC_DIR = DATA_RAW / "outages" / "nerc"
EVENTS_CSV = NERC_DIR / "events_extracted.csv"
EVENTS_META = NERC_DIR / "events_extracted_meta.json"

USER_AGENT = "Mozilla/5.0 (compatible; global-power-outage-risk/1.0; research)"

# (report_year, url) — every NERC annual report covering 2019–2025 that is
# still served by nerc.gov.ng. 2019 and 2021 do not exist: NERC's own
# repository (archived) lists only 2017, 2018 and 2020 annual reports, and the
# current resource pages list 2022–2025. Discovery: the NERC Reports listing
# (https://nerc.gov.ng/resource-category/nerc-reports/), the WordPress search
# endpoint (?s=annual+report) and the Wayback CDX index of wp-content/uploads.
DOWNLOADS: list[tuple[int, str]] = [
    (2020, "https://nerc.gov.ng/wp-content/uploads/2022/08/Annual_Report_2020_Final%20edit.pdf"),
    (2022, "https://nerc.gov.ng/wp-content/uploads/2025/09/2022_Annual_Report.pdf"),
    (2023, "https://nerc.gov.ng/wp-content/uploads/2024/09/2023_Annual_Report.pdf"),
    (2024, "https://nerc.gov.ng/wp-content/uploads/2025/07/2024-Annual-Report-1.pdf"),
    (2025, "https://nerc.gov.ng/wp-content/uploads/2026/08/2025-Annual-Report_Pub.pdf"),
]

# Table 24 of the 2025 annual report (partial, total) — the official yearly
# totals used to cross-check what the extraction found.
EXPECTED_TABLE24: dict[int, tuple[int, int]] = {
    2019: (1, 10),
    2020: (0, 4),
    2021: (2, 2),
    2022: (2, 4),
    2023: (0, 3),
    2024: (5, 4),
    2025: (1, 1),
}

CSV_COLUMNS = [
    "event_date",
    "event_time",
    "event_type",
    "restoration_time",
    "country_iso3",
    "source_document",
    "source_page",
    "extracted_utc",
]

EVENT_TYPES = ("partial", "full", "unknown")

# A PDF whose text layer yields fewer characters than this is treated as
# image-only (scanned) and is reported as such instead of parsed.
IMAGE_ONLY_MAX_CHARS = 100

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}

# "04/02/2024 11:21Hrs Partial" / "10/09/2025 11:20Hrs Full" — a collapse row.
# The date must sit directly in front of the time, which keeps restoration
# values ("... 11:48Hrs Full Restoration") from being matched as collapses.
_COLLAPSE_RE = re.compile(
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>20\d{2})\s+"
    r"(?P<time>\d{1,2}:\d{2})\s*Hrs?\b\s*"
    r"(?P<kind>Partial|Full)\b",
    re.IGNORECASE,
)
# "04/02/2024 18:15Hrs" — restoration with its own date (2024/2025 tables).
_RESTORE_DATED_RE = re.compile(
    r"\b\d{1,2}/\d{1,2}/20\d{2}\s+(?P<time>\d{1,2}:\d{2})\s*Hrs?\b",
    re.IGNORECASE,
)
# "11:48Hrs Full Restoration" — bare restoration time (fixture/alternate style).
_RESTORE_BARE_RE = re.compile(
    r"\b(?P<time>\d{1,2}:\d{2})\s*Hrs?\b[^.\n]{0,30}\bRestoration\b",
    re.IGNORECASE,
)
# "19th of September, 2023 (11:31hours)" — ordinal incident tables (2023).
_ORDINAL_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)\s+of\s+(?P<month>[A-Za-z]+)\s*,?\s+"
    r"(?P<year>20\d{2})\b(?:\s*\((?P<time>\d{1,2}:\d{2})\s*hours?\))?",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hhmm(raw: str) -> str:
    """Normalise a clock string to zero-padded ``HH:MM``."""
    hour, minute = raw.split(":", 1)
    return f"{int(hour):02d}:{int(minute):02d}"


def _iso_date(day: int, month: int, year: int) -> Optional[str]:
    """``dd/mm/yyyy`` → ISO date; NERC uses day-first, swap only if impossible."""
    if month > 12 and day <= 12:
        day, month = month, day
    if not (2000 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# summary count tables ("Category of Collapse 2019 2020 ...")
# ---------------------------------------------------------------------------

def parse_count_table(text: str) -> dict[int, dict[str, int]]:
    """Parse a *Summary count* table into ``{year: {"partial": n, "total": m}}``.

    Rows such as ``Number of Partial Collapses 9 1 1 0 0 0 0 0`` are matched
    only when their integer count lines up with the year header; tables whose
    columns do not line up (e.g. the 2020 report's annual+quarterly table) are
    ignored rather than mis-mapped.
    """
    out: dict[int, dict[str, int]] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "categor" not in line.lower():
            continue
        years = [int(y) for y in re.findall(r"\b20\d{2}\b", line)]
        if len(years) < 3:
            continue
        rows: dict[str, list[int]] = {}
        for offset in range(index + 1, min(index + 9, len(lines))):
            match = re.match(
                r"\s*(?:Number of\s+)?(Partial|Total)\s+Collaps", lines[offset], re.I
            )
            if not match:
                continue
            numbers = [int(n) for n in re.findall(r"\b\d+\b", lines[offset])]
            if len(numbers) == len(years):
                rows[match.group(1).lower()] = numbers
        if "partial" in rows and "total" in rows:
            for year, partial, total in zip(years, rows["partial"], rows["total"]):
                out.setdefault(year, {"partial": partial, "total": total})
    return out


def _infer_type(event_type: str, year: int, counts: Mapping[int, Mapping[str, int]]) -> str:
    """Fill in an ``unknown`` type when the year's count table makes it certain."""
    if event_type != "unknown":
        return event_type
    entry = counts.get(year)
    if not entry:
        return "unknown"
    if entry["partial"] == 0 and entry["total"] > 0:
        return "full"
    if entry["total"] == 0 and entry["partial"] > 0:
        return "partial"
    return "unknown"


# ---------------------------------------------------------------------------
# text → events
# ---------------------------------------------------------------------------

def _numeric_events(text: str, source_document: str, source_page: int) -> list[dict]:
    """Rows of the ``dd/mm/yyyy HH:MMHrs <Type> [dd/mm/yyyy HH:MMHrs]`` shape."""
    events: list[dict] = []
    matches = list(_COLLAPSE_RE.finditer(text))
    for position, match in enumerate(matches):
        iso = _iso_date(
            int(match.group("day")), int(match.group("month")), int(match.group("year"))
        )
        if iso is None:
            continue
        # Look for the restoration only up to the next collapse row so a
        # following event's timestamp is never mistaken for this one's.
        stop = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        window = text[match.end() : min(stop, match.end() + 400)]
        restoration = ""
        dated = _RESTORE_DATED_RE.search(window)
        bare = _RESTORE_BARE_RE.search(window)
        if dated:
            restoration = _hhmm(dated.group("time"))
        elif bare:
            restoration = _hhmm(bare.group("time"))
        events.append(
            {
                "event_date": iso,
                "event_time": _hhmm(match.group("time")),
                "event_type": match.group("kind").lower(),
                "restoration_time": restoration,
                "source_document": source_document,
                "source_page": source_page,
            }
        )
    return events


def _ordinal_events(
    text: str,
    counts: Mapping[int, Mapping[str, int]],
    source_document: str,
    source_page: int,
) -> list[dict]:
    """Ordinal incident rows (``19th of September, 2023 (11:31hours)``).

    Only runs on pages that discuss system collapses, and the year's type is
    inferred from the summary count table when the row does not state one.
    """
    events: list[dict] = []
    for match in _ORDINAL_RE.finditer(text):
        month = _MONTHS.get(match.group("month").lower())
        if month is None:
            continue
        iso = _iso_date(int(match.group("day")), month, int(match.group("year")))
        if iso is None:
            continue
        year = int(match.group("year"))
        raw_time = match.group("time") or ""
        events.append(
            {
                "event_date": iso,
                "event_time": _hhmm(raw_time) if raw_time else "",
                "event_type": _infer_type("unknown", year, counts),
                "restoration_time": "",
                "source_document": source_document,
                "source_page": source_page,
            }
        )
    return events


def parse_events_text(
    text: str,
    source_document: str = "fixture.pdf",
    source_page: int = 1,
    counts: Optional[Mapping[int, Mapping[str, int]]] = None,
) -> list[dict]:
    """Parse one page/fixture of extracted report text into event dicts.

    ``counts`` (from :func:`parse_count_table`, usually gathered across the
    whole PDF) lets the type of ordinal-style rows be inferred; when omitted,
    counts are parsed from ``text`` itself.
    """
    if "collapse" not in text.lower():
        return []
    if counts is None:
        counts = parse_count_table(text)
    events = _numeric_events(text, source_document, source_page)
    events.extend(_ordinal_events(text, counts, source_document, source_page))
    for event in events:
        year = int(event["event_date"][:4])
        event["event_type"] = _infer_type(event["event_type"], year, counts)
    return events


def extract_events(pdf_path: Path | str) -> list[dict]:
    """Extract dated grid-collapse incidents from one NERC annual report PDF.

    Returns event dicts (see :data:`CSV_COLUMNS`) with 1-based ``source_page``.
    Image-only PDFs yield no events — check :func:`is_image_only` to tell
    "nothing found" apart from "nothing extractable".
    """
    pdf_path = Path(pdf_path)
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # malformed page content — treat as empty
            pages.append("")
    counts: dict[int, dict[str, int]] = {}
    for text in pages:
        for year, entry in parse_count_table(text).items():
            counts.setdefault(year, entry)
    events: list[dict] = []
    for number, text in enumerate(pages, start=1):
        events.extend(parse_events_text(text, pdf_path.name, number, counts=counts))
    return events


def dedupe(events: Iterable[Mapping]) -> list[dict]:
    """Drop repeated ``(event_date, event_time)`` pairs, keeping first seen."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for event in events:
        key = (event["event_date"], event["event_time"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(event))
    return unique


def is_image_only(pdf_path: Path | str) -> bool:
    """True when the PDF yields almost no text (scanned/image-only document)."""
    reader = PdfReader(str(pdf_path))
    total = 0
    for page in reader.pages:
        try:
            total += len(page.extract_text() or "")
        except Exception:
            continue
        if total > IMAGE_ONLY_MAX_CHARS:
            return False
    return total <= IMAGE_ONLY_MAX_CHARS


# ---------------------------------------------------------------------------
# download + save
# ---------------------------------------------------------------------------

def download(overwrite: bool = False) -> list[Path]:
    """Fetch every annual report in :data:`DOWNLOADS` (skips existing files)."""
    ensure_dirs()
    NERC_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for year, url in DOWNLOADS:
        path = NERC_DIR / f"{year}_annual_report.pdf"
        paths.append(path)
        if path.exists() and not overwrite:
            print(f"  [skip] {path.name}")
            continue
        response = get_with_retries(
            url, timeout=300, headers={"User-Agent": USER_AGENT}
        )
        write_bytes_atomic(path, response.content)
        print(f"  [ok]   {path.name}  {len(response.content):,} bytes")
    return paths


def save_events(pdf_paths: Optional[Sequence[Path | str]] = None) -> Path:
    """Extract every downloaded report and write :data:`EVENTS_CSV`.

    Rows are deduplicated on ``(event_date, event_time)`` and sorted by date.
    Raises ``FileNotFoundError`` when no PDFs have been downloaded yet.
    """
    paths = [
        Path(p)
        for p in (
            pdf_paths
            if pdf_paths is not None
            else sorted(NERC_DIR.glob("*_annual_report.pdf"))
        )
    ]
    if not paths:
        raise FileNotFoundError(
            f"no NERC annual report PDFs in {NERC_DIR} — run without --audit first"
        )
    events: list[dict] = []
    image_only: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        if is_image_only(path):
            image_only.append(path.name)
            continue
        events.extend(extract_events(path))
    events = dedupe(events)
    events.sort(
        key=lambda e: (e["event_date"], e["event_time"], e["source_document"], e["source_page"])
    )
    stamp = _utc_now()
    NERC_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for event in events:
            writer.writerow(
                {
                    "event_date": event["event_date"],
                    "event_time": event["event_time"],
                    "event_type": event["event_type"],
                    "restoration_time": event["restoration_time"],
                    "country_iso3": COUNTRY_ISO3,
                    "source_document": event["source_document"],
                    "source_page": event["source_page"],
                    "extracted_utc": stamp,
                }
            )
    write_provenance(
        EVENTS_META,
        {
            "source": "Nigerian Electricity Regulatory Commission (NERC) — Annual Report & Accounts",
            "retrieved_utc": stamp,
            "downloads": [
                {"report_year": year, "url": url, "file": f"{year}_annual_report.pdf"}
                for year, url in DOWNLOADS
            ],
            "image_only_pdfs": image_only,
            "events": len(events),
            "columns": CSV_COLUMNS,
        },
    )
    print(f"  [csv]  {EVENTS_CSV.name}: {len(events)} events"
          + (f" (image-only skipped: {', '.join(image_only)})" if image_only else ""))
    return EVENTS_CSV


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _summary(events: Sequence[Mapping]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for event in events:
        year = int(event["event_date"][:4])
        counts[year] = counts.get(year, 0) + 1
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NERC annual-report grid-collapse events")
    parser.add_argument("--overwrite", action="store_true", help="re-download the PDFs")
    parser.add_argument(
        "--audit", action="store_true", help="extract from already-downloaded PDFs only"
    )
    args = parser.parse_args(argv)

    ensure_dirs()
    NERC_DIR.mkdir(parents=True, exist_ok=True)
    if not args.audit:
        print("Downloading NERC annual reports …")
        download(overwrite=args.overwrite)
    else:
        print("Audit mode — no network access.")

    existing = sorted(NERC_DIR.glob("*_annual_report.pdf"))
    if not existing:
        print("No PDFs downloaded; nothing to extract.", file=sys.stderr)
        return 1

    print("Extracting events …")
    save_events(existing)

    with EVENTS_CSV.open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    yearly = _summary(events)
    print("\nyearly counts vs NERC Table 24 (partial+total):")
    for year in sorted(EXPECTED_TABLE24):
        expected = sum(EXPECTED_TABLE24[year])
        found = yearly.get(year, 0)
        flag = "ok" if found == expected else f"MISMATCH ({found - expected:+d})"
        print(f"  {year}: {found:>2} extracted | {expected:>2} expected | {flag}")

    by_type: dict[str, int] = {}
    for event in events:
        by_type[event["event_type"]] = by_type.get(event["event_type"], 0) + 1
    print("by type:", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    print(f"total dated events: {len(events)} → {EVENTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
