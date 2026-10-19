"""Unit tests for src.ingestion.nerc.

Synthetic text fixtures exercise the parsers without any network or PDF;
tests that need the downloaded annual reports skip gracefully when the PDFs
are absent (run ``python -m src.ingestion.nerc`` first for full coverage).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from src.ingestion import nerc


# --------------------------------------------------------------------------
# synthetic extracted-text fixtures
# --------------------------------------------------------------------------

# Table 25 shape (2025 report) using the documented sample line plus a row
# with a dated restoration, exactly as they come out of pypdf.
FIXTURE_TABLE25 = """
Table 25: System collapse incidents in 2025
SN Date and Time of Collapse Type of Collapse Date and Time of full system restoration Remarks
1 10/09/2025 11:20Hrs Full System Collapse 11:48Hrs Full Restoration
2 29/12/2025 14:01Hrs Partial 29/12/2025 14:30Hrs The immediate cause of the collapse
"""

# Table 7.3 shape (2024 report): rows where restoration crosses midnight.
FIXTURE_TABLE73 = """
Table 7.3: System collapse incidents in 2024
1 04/02/2024 11:21Hrs Partial 04/02/2024 18:15Hrs The cascaded tripping of Sapele steam
5 14/10/2024 18:48Hrs Partial 15/10/2024 16:14Hrs The cascading tripping of Jebba-Osogbo lines
6 19/10/2024 08:15Hrs Full 19/10/2024 17:57Hrs The explosion of the yellow phase CT
"""

# Table 20 shape (2023 report): ordinal prose dates, no type column, plus the
# report's summary count table on the facing page.
FIXTURE_ORDINAL = """
Table 19: System Collapses in 2019 - 2023
Category of Collapse 2019 2020 2021 2022 2023
Partial Collapses 1 0 2 2 0
Total Collapses 10 4 2 4 3
"""
FIXTURE_ORDINAL_ROWS = """
Table 20: System collapse incidents in 2023
1 19th of September, 2023 (11:31hours) The immediate cause was due to the tripping
2 14th of September, 2023 (00:41hours) The immediate cause of the system collapse
3 11th of December, 2023 Between 13:48hrs & 13:49hrs there was a simultaneous tripping
"""

# The 2020 report's annual+quarterly count table: columns cannot line up with
# the year header, so it must be ignored rather than mis-mapped.
FIXTURE_MISALIGNED_COUNTS = """
Table 4.1: Total and Partial System Collapses in 2020
Annually: 2020 Quarterly:
Category 2017 2018 2019 2020 /Q4 /Q3 /Q2 /Q1
Number of Partial Collapses 9 1 1 0 0 0 0 0
Number of Total Collapses 15 12 10 4 1 0 2 1
"""


def test_sample_line_parses_exactly_as_documented():
    """The canonical sample line must yield date/time/type/restoration."""
    text = "10/09/2025 11:20Hrs Full System Collapse 11:48Hrs Full Restoration"
    events = nerc.parse_events_text(text)
    assert len(events) == 1
    event = events[0]
    assert event["event_date"] == "2025-09-10"
    assert event["event_time"] == "11:20"
    assert event["event_type"] == "full"
    assert event["restoration_time"] == "11:48"


def test_table25_fixture_rows_parse_with_types_and_restoration():
    events = nerc.parse_events_text(FIXTURE_TABLE25, source_document="2025_annual_report.pdf")
    assert len(events) == 2
    by_date = {e["event_date"]: e for e in events}
    assert by_date["2025-09-10"]["event_time"] == "11:20"
    assert by_date["2025-09-10"]["event_type"] == "full"
    assert by_date["2025-09-10"]["restoration_time"] == "11:48"
    assert by_date["2025-12-29"]["event_type"] == "partial"
    assert by_date["2025-12-29"]["restoration_time"] == "14:30"
    assert {e["source_document"] for e in events} == {"2025_annual_report.pdf"}


def test_table73_fixture_rows_and_midnight_restoration():
    events = nerc.parse_events_text(FIXTURE_TABLE73, source_document="2024_annual_report.pdf",
                                    source_page=163)
    assert [(e["event_date"], e["event_time"], e["event_type"], e["restoration_time"])
            for e in events] == [
        ("2024-02-04", "11:21", "partial", "18:15"),
        ("2024-10-14", "18:48", "partial", "16:14"),
        ("2024-10-19", "08:15", "full", "17:57"),
    ]
    assert {e["source_page"] for e in events} == {163}


def test_ordinal_rows_are_dated_and_typed_from_the_count_table():
    counts = nerc.parse_count_table(FIXTURE_ORDINAL)
    assert counts == {
        2019: {"partial": 1, "total": 10},
        2020: {"partial": 0, "total": 4},
        2021: {"partial": 2, "total": 2},
        2022: {"partial": 2, "total": 4},
        2023: {"partial": 0, "total": 3},
    }
    events = nerc.parse_events_text(FIXTURE_ORDINAL_ROWS, counts=counts)
    assert len(events) == 3
    by_date = {e["event_date"]: e for e in events}
    assert by_date["2023-09-19"]["event_time"] == "11:31"
    assert by_date["2023-09-14"]["event_time"] == "00:41"
    # no parenthesised time in the date cell -> left empty, never invented
    assert by_date["2023-12-11"]["event_time"] == ""
    # 2023 had 0 partial / 3 total collapses -> every event must be "full"
    assert {e["event_type"] for e in events} == {"full"}
    assert all(e["restoration_time"] == "" for e in events)


def test_unknown_type_when_counts_do_not_disambiguate():
    text = "14th of September, 2023 (00:41hours) some system collapse narrative"
    events = nerc.parse_events_text(text, counts={2023: {"partial": 1, "total": 3}})
    assert len(events) == 1
    assert events[0]["event_type"] == "unknown"


def test_misaligned_count_table_is_ignored():
    assert nerc.parse_count_table(FIXTURE_MISALIGNED_COUNTS) == {}


def test_text_without_collapse_context_yields_nothing():
    text = "04/02/2024 11:21Hrs Partial some tariff table unrelated to grids"
    assert nerc.parse_events_text(text) == []


def test_impossible_dates_are_dropped():
    text = "99/99/2024 11:21Hrs Partial 99/99/2024 18:15Hrs system collapse"
    assert nerc.parse_events_text(text) == []


def test_dedupe_keeps_first_occurrence_per_date_and_time():
    first = {"event_date": "2024-02-04", "event_time": "11:21",
             "source_document": "2024_annual_report.pdf", "source_page": 163}
    duplicate = dict(first, source_page=999)
    same_date_other_time = dict(first, event_time="18:15")
    out = nerc.dedupe([first, duplicate, same_date_other_time])
    assert len(out) == 2
    assert out[0]["source_page"] == 163
    assert out[1]["event_time"] == "18:15"


# --------------------------------------------------------------------------
# acquisition manifest
# --------------------------------------------------------------------------

def test_downloads_are_unique_https_pdfs_of_existing_reports():
    years = [year for year, _ in nerc.DOWNLOADS]
    assert len(years) == len(set(years)), "duplicate report years in DOWNLOADS"
    # 2019 and 2021 annual reports were never published by NERC (see
    # reports/day10_nerc_events.md); the rest of 2019-2025 must be covered.
    assert set(years) == {2020, 2022, 2023, 2024, 2025}
    for year, url in nerc.DOWNLOADS:
        assert url.startswith("https://")
        assert url.lower().endswith(".pdf")


def test_expected_table24_totals_match_the_published_counts():
    assert {year: sum(pair) for year, pair in nerc.EXPECTED_TABLE24.items()} == {
        2019: 11, 2020: 4, 2021: 4, 2022: 6, 2023: 3, 2024: 9, 2025: 2,
    }


# --------------------------------------------------------------------------
# PDF-dependent tests (skip when nothing has been downloaded)
# --------------------------------------------------------------------------

def _downloaded_pdfs() -> list[Path]:
    return sorted(nerc.NERC_DIR.glob("*_annual_report.pdf"))


def test_extract_events_from_real_pdfs():
    pdfs = _downloaded_pdfs()
    if not pdfs:
        pytest.skip("no NERC annual report PDFs downloaded yet")
    expected_per_year = {2023: 3, 2024: 9, 2025: 2}  # years with dated tables
    seen: dict[int, int] = {}
    for pdf in pdfs:
        assert not nerc.is_image_only(pdf), f"{pdf.name} unexpectedly image-only"
        for event in nerc.extract_events(pdf):
            assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", event["event_date"])
            assert re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", event["event_time"]) or \
                event["event_time"] == ""
            assert event["event_type"] in nerc.EVENT_TYPES
            assert event["source_page"] >= 1
            assert event["source_document"] == pdf.name
            year = int(event["event_date"][:4])
            seen[year] = seen.get(year, 0) + 1
    for year, expected in expected_per_year.items():
        if (nerc.NERC_DIR / f"{year}_annual_report.pdf").exists():
            assert seen.get(year) == expected, f"{year}: {seen.get(year)} != {expected}"


def test_events_csv_schema_if_present():
    if not nerc.EVENTS_CSV.exists():
        pytest.skip("events_extracted.csv not written yet")
    with nerc.EVENTS_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if rows:
        assert list(rows[0]) == nerc.CSV_COLUMNS
    keys = set()
    for row in rows:
        assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", row["event_date"]), row["event_date"]
        assert row["country_iso3"] == "NGA"
        assert row["event_type"] in nerc.EVENT_TYPES
        assert row["event_time"] == "" or re.fullmatch(
            r"([01]\d|2[0-3]):[0-5]\d", row["event_time"]
        )
        assert int(row["source_page"]) >= 1
        assert row["source_document"].endswith(".pdf")
        assert row["extracted_utc"].endswith("+00:00")
        key = (row["event_date"], row["event_time"])
        assert key not in keys, f"duplicate event {key}"
        keys.add(key)
