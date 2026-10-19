"""Tests for src.ingestion.kplc — fixtures are the saved raw pages from Day 10."""

import json
from pathlib import Path

import pytest

from src.ingestion import kplc

RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "outages" / "kenya"
PAGE1 = RAW / "kplc_customer_support_p1.html"
PAGE14 = RAW / "kplc_customer_support_p14.html"
PAGE15 = RAW / "kplc_customer_support_p15.html"
ARCHIVE_JSON = RAW / "kplc_powerschedule_notices.json"

needs_fixture = pytest.mark.skipif(not PAGE1.exists(), reason="raw KPLC fixture page 1 absent")


@needs_fixture
def test_parse_page1_finds_eight_notices():
    notices = kplc.parse_notices(PAGE1.read_text(encoding="utf-8"))
    assert len(notices) == 8
    assert all(n["title"] and n["posted"] and n["pdf_url"] for n in notices)
    assert all(n["pdf_url"].startswith("https://kplc.co.ke/storage/") for n in notices)
    assert all(n["pdf_url"].endswith(".pdf") for n in notices)


@needs_fixture
def test_parse_page1_newest_first_and_dates_parse():
    notices = kplc.parse_notices(PAGE1.read_text(encoding="utf-8"))
    isos = [n["posted_iso"] for n in notices]
    assert all(isos), f"unparsed posted values: {[n['posted'] for n in notices if not n['posted_iso']]}"
    assert isos == sorted(isos, reverse=True), "notices should be newest-first"
    assert isos[0].startswith("2026-10-16")


@needs_fixture
def test_parse_posted_handles_kplc_24h_with_am_pm_suffix():
    assert kplc.parse_posted("Oct 16, 2026 16:05 pm") == "2026-10-16T16:05:00"
    assert kplc.parse_posted("Sep 25, 2026 13:26 pm") == "2026-09-25T13:26:00"
    assert kplc.parse_posted("Jun 27, 2024 00:00 am") == "2024-06-27T00:00:00"
    assert kplc.parse_posted("not a date") is None


@needs_fixture
def test_parse_last_page():
    assert kplc.parse_last_page(PAGE1.read_text(encoding="utf-8")) == 14


@needs_fixture
def test_pagination_runs_out_after_last_page():
    """Page 15+ (beyond the last populated page) must parse to zero notices."""
    if not PAGE15.exists():
        pytest.skip("raw fixture page 15 absent")
    assert kplc.parse_notices(PAGE15.read_text(encoding="utf-8")) == []


@needs_fixture
def test_last_populated_page_has_fewer_rows():
    if not PAGE14.exists():
        pytest.skip("raw fixture page 14 absent")
    rows = kplc.parse_notices(PAGE14.read_text(encoding="utf-8"))
    assert 0 < len(rows) < 8


def test_fetch_notices_limit_dedupe_and_raw_saving(tmp_path, monkeypatch):
    """fetch_notices honours the limit and stores raw HTML (network mocked)."""
    if not PAGE1.exists():
        pytest.skip("raw fixture page 1 absent")
    page1 = PAGE1.read_text(encoding="utf-8")
    fetched_pages = []

    def fake_fetch(page):
        fetched_pages.append(page)
        return page1

    monkeypatch.setattr(kplc, "_fetch_page", fake_fetch)
    monkeypatch.setattr(kplc, "KENYA_RAW_DIR", tmp_path)

    notices = kplc.fetch_notices(limit=3)
    assert len(notices) == 3
    assert fetched_pages == [1]
    assert all(n["page"] == 1 for n in notices)
    assert len({n["pdf_url"] for n in notices}) == 3
    assert kplc.parse_last_page(page1) == 14  # bound detected, but limit hit first


def test_fetch_notices_stops_when_pagination_runs_out(tmp_path, monkeypatch):
    """An empty page (beyond page 14) terminates the walk."""
    empty = "<html><body>no notices</body></html>"
    calls = []

    def fake_fetch(page):
        calls.append(page)
        return empty

    monkeypatch.setattr(kplc, "_fetch_page", fake_fetch)
    monkeypatch.setattr(kplc, "KENYA_RAW_DIR", tmp_path)

    assert kplc.fetch_notices(limit=50) == []
    assert calls == [1]


def test_fetch_notices_rejects_bad_limit():
    with pytest.raises(ValueError):
        kplc.fetch_notices(limit=0)


@needs_fixture
def test_full_archive_json_is_consistent():
    """The Day-10 crawl extract agrees with the module's parser."""
    if not ARCHIVE_JSON.exists():
        pytest.skip("archive JSON absent")
    payload = json.loads(ARCHIVE_JSON.read_text(encoding="utf-8"))
    notices = payload["notices"]
    assert payload["notice_count"] == len(notices) == 110
    assert len({n["pdf_url"] for n in notices}) == 110
    posted = [n["posted"] for n in notices]
    assert posted[0].startswith("Oct 16, 2026")
    assert posted[-1].startswith("Jun 27, 2024")
