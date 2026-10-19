"""Tests for src.ingestion.kplc_parse — hermetic inline text fixtures.

The two fixture documents mirror the verified structures of the real samples
(``reports/day10_kenya_probes.md``): a multi-AREA Rule-27 maintenance notice and a
SPECIAL interruption notice with a COUNTY header + cause prose. Saved sample PDFs
under ``data/raw/outages/kenya/`` are exercised by integration tests that skip when
absent. No test touches the network.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion import kplc_parse as kp

RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "outages" / "kenya"
SAMPLE_MAINTENANCE = RAW / "kplc_notice_2026-10-01.pdf"
SAMPLE_SPECIAL = RAW / "kplc_notice_2026-10-16.pdf"
SAMPLE_IMAGE_ONLY = RAW / "kplc_notice_day4_sample_01K097.pdf"

# --- inline fixtures (structures copied from the real PDFs) ------------------------
MAINTENANCE_TEXT = """\
Power Maintenance Notice
Notice is hereby given under Rule 27 of the Electric Power Rules
That the electricity supply will be interrupted as here under:
(It is necessary to interrupt supply periodically in order to facilitate maintenance and
upgrade of power lines to the network; to connect new customers or to replace power
lines during road construction, etc.)

NAIROBI REGION
AREA: PART OF PARKLANDS
DATE: Sunday 04.10.2026                                        TIME: 9.00 A.M. – 5.00 P.M.
4th Parklands, Masari Rd, Kusi Lane, Ngao Rd & adjacent customers.

AREA: KOSOVO
DATE: Monday 05.10.2026                                        TIME: 9:00 a.m. - 5:00 p.m.
Shofco, Utalii & adjacent customers.

CENTRAL RIFT REGION
PARTS OF NAKURU COUNTY
AREA: PANDA, NORTHLAKE
DATE: Thursday 08.10.2026
TIME: 9.00 A.M. – 5.00 P.M.
Whole of Panda Business Park, Ken Chic & adjacent customers.

AREA: GARISSA ROAD, MAKONGENI, GATUANYAGA,
DATE: Tuesday 06.10.2026                                          TIME 9.00 A.M. - 5.00 P.M.
Makongeni Mkt & adjacent customers.
"""

SPECIAL_TEXT = """\
SPECIAL POWER SUPPLY INTERRUPTION NOTICE

We wish to inform our customers that power has been interrupted in parts of
Kisumu County today Friday 16th October and will be reinstated at 6 p.m.

The interruption was caused by an occurence involving a battery charger at
Muhoroni Substation.

We regret any inconvenience that may be caused during the operation.

WESTERN REGION
KISUMU COUNTY
AREA: MUHORONI SUGAR FACTORY, MUHORONI TOWN, AGROCHEMICALS
DATE: Friday 16.10.2026   TIME: 11.00 A.M. – 6.00 P.M.
Homaline, Muhoroni Town, Muhoroni Sugar Factory, Agrochemicals, Stone Field,
Golden Bricks & adjacent customers.
"""

OVERNIGHT_TEXT = """\
NAIROBI REGION
AREA: INDUSTRIAL AREA
DATE: Saturday 10.10.2026   TIME: 10.00 P.M. – 6.00 A.M.
Industrial Area premises & adjacent customers.
"""


# --- maintenance (planned, Rule 27) ------------------------------------------------
def test_maintenance_notice_parses_one_event_per_area_block():
    events = kp.parse_notice_text(MAINTENANCE_TEXT, "fixture_maintenance.pdf")
    assert len(events) == 4
    assert all(e["country_iso3"] == "KEN" for e in events)
    assert all(e["notice_class"] == "planned" for e in events)
    assert all(e["source_document"] == "fixture_maintenance.pdf" for e in events)


def test_maintenance_first_block_fields():
    first = kp.parse_notice_text(MAINTENANCE_TEXT, "doc.pdf")[0]
    assert first["region"] == "NAIROBI"
    assert first["county"] == ""  # no COUNTY header under NAIROBI REGION
    assert first["area"] == "PART OF PARKLANDS"
    assert first["event_date"] == "2026-10-04"
    assert first["time_start_local"] == "09:00"
    assert first["time_end_local"] == "17:00"
    assert first["premises_text"].startswith("4th Parklands, Masari Rd")


def test_maintenance_county_header_scopes_later_blocks_only():
    events = kp.parse_notice_text(MAINTENANCE_TEXT, "doc.pdf")
    panda = events[2]
    assert panda["region"] == "CENTRAL RIFT"
    assert panda["county"] == "NAKURU"
    # TIME was on its own line and must not leak into the premises.
    assert panda["premises_text"].startswith("Whole of Panda Business Park")
    assert "TIME" not in panda["premises_text"]
    # The next block (still under the same county) keeps region/county.
    assert events[3]["region"] == "CENTRAL RIFT"
    assert events[3]["county"] == "NAKURU"


def test_area_is_verbatim_but_trailing_comma_dropped():
    events = kp.parse_notice_text(MAINTENANCE_TEXT, "doc.pdf")
    assert events[3]["area"] == "GARISSA ROAD, MAKONGENI, GATUANYAGA"


def test_date_line_without_time_token_yields_empty_times():
    """TIME on the following line is consumed; a date line with no TIME at all
    must leave both times empty rather than guess."""
    text = "NAIROBI REGION\nAREA: KOSOVO\nDATE: Monday 05.10.2026\nShofco premises.\n"
    event = kp.parse_notice_text(text, "doc.pdf")[0]
    assert event["event_date"] == "2026-10-05"
    assert event["time_start_local"] == ""
    assert event["time_end_local"] == ""


# --- special (fault-caused) --------------------------------------------------------
def test_special_notice_detects_class_county_and_cause_prose():
    events = kp.parse_notice_text(SPECIAL_TEXT, "fixture_special.pdf")
    assert len(events) == 1
    event = events[0]
    assert event["notice_class"] == "special"
    assert event["region"] == "WESTERN"
    assert event["county"] == "KISUMU"
    assert event["area"] == "MUHORONI SUGAR FACTORY, MUHORONI TOWN, AGROCHEMICALS"
    assert event["event_date"] == "2026-10-16"
    assert event["time_start_local"] == "11:00"
    assert event["time_end_local"] == "18:00"


def test_special_preamble_prose_stays_out_of_premises():
    event = kp.parse_notice_text(SPECIAL_TEXT, "doc.pdf")[0]
    assert event["premises_text"].startswith("Homaline, Muhoroni Town")
    assert "was caused by" not in event["premises_text"]
    assert "We wish to inform" not in event["premises_text"]


def test_notice_class_falls_back_to_cause_prose_without_header():
    text = ("NAIROBI REGION\nAREA: KOSOVO\nDATE: Monday 05.10.2026   "
            "TIME: 9.00 A.M. – 5.00 P.M.\nShofco & adjacent customers.\n"
            "The outage was caused by a fault on the feeder.\n")
    assert kp.detect_notice_class(text) == "special"
    assert kp.detect_notice_class(MAINTENANCE_TEXT) == "planned"


# --- time formats ------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("9.00 A.M. – 5.00 P.M.", ("09:00", "17:00")),      # en dash, dotted meridiem
    ("9.00 A.M. - 5.00 P.M.", ("09:00", "17:00")),       # hyphen
    ("9:00 a.m. - 5:00 p.m.", ("09:00", "17:00")),       # colon clock, lowercase
    ("9.00A.M.– 5.00 P.M.", ("09:00", "17:00")),         # no space before A.M.
    ("0900Hrs – 1700Hrs", ("09:00", "17:00")),           # military style
    ("10.00 P.M. – 6.00 A.M.", ("22:00", "06:00")),      # overnight, raw
    ("11.00 A.M. – 6.00 P.M.", ("11:00", "18:00")),
    ("12:00 P.M. – 3:00 P.M.", ("12:00", "15:00")),      # noon must not become 00
    ("8.30 A.M. - 5.00 P.M.", ("08:30", "17:00")),
    ("9.00 A.M. to 5.00 P.M.", ("09:00", "17:00")),      # word separator
    ("", ("", "")),
    ("not a time", ("", "")),
])
def test_parse_time_range_variants(raw, expected):
    assert kp.parse_time_range(raw) == expected


def test_unknown_time_is_never_guessed():
    assert kp.parse_time_token("25.00 P.M.") == ""   # out-of-range hour
    assert kp.parse_time_token("9.99 A.M.") == ""    # out-of-range minute


# --- KPLC source typos (structure repaired from content, values never invented) -----
def test_area_prefix_with_semicolon_still_starts_a_block():
    """'AREA; JOMVU' (47 occurrences in the corpus) must not lose the block."""
    text = ("COAST REGION\nPARTS OF MOMBASA COUNTY\n"
            "AREA; JOMVU\nDATE: Wednesday 28.05.2025   TIME: 9.00 A.M. – 5.00 P.M.\n"
            "Icolo, Black Devil & adjacent customers.\n")
    events = kp.parse_notice_text(text, "doc.pdf")
    assert len(events) == 1
    assert events[0]["area"] == "JOMVU"
    assert events[0]["event_date"] == "2025-05-28"
    assert events[0]["county"] == "MOMBASA"


def test_date_prefix_with_semicolon_is_still_a_date_line():
    text = ("NAIROBI REGION\nAREA: NKUBU\n"
            "DATE; Wednesday 19.03.2025   TIME: 9.00 A.M. – 5.00 P.M.\n"
            "Nkubu premises.\n")
    event = kp.parse_notice_text(text, "doc.pdf")[0]
    assert event["event_date"] == "2025-03-19"


def test_area_line_carrying_a_date_is_treated_as_the_previous_blocks_date_line():
    """2025-05-23 / 2025-08-08 notices write the date line as ``AREA: <date> TIME``."""
    text = ("COAST REGION\nAREA: SAMBURU TOWN, KINAGONI\n"
            "AREA: Thursday 29.05.2025   TIME: 9.00 A.M. – 5.00 P.M.\n"
            "Samburu Town, Kinagoni & adjacent customers.\n")
    events = kp.parse_notice_text(text, "doc.pdf")
    assert len(events) == 1  # no garbage block split off
    event = events[0]
    assert event["area"] == "SAMBURU TOWN, KINAGONI"
    assert event["event_date"] == "2025-05-29"
    assert (event["time_start_local"], event["time_end_local"]) == ("09:00", "17:00")


def test_date_line_carrying_an_area_name_becomes_the_area_line():
    """2025-08-08 notice writes the area line as ``DATE: PART OF NYALI WEST``."""
    text = ("COAST REGION\nPARTS OF MOMBASA COUNTY\n"
            "DATE: PART OF NYALI WEST\n"
            "AREA: Thursday 14.08.2025   TIME: 9.00 A.M. – 5.00 P.M.\n"
            "Engen, Orchid & adjacent customers.\n")
    events = kp.parse_notice_text(text, "doc.pdf")
    assert len(events) == 1
    assert events[0]["area"] == "PART OF NYALI WEST"
    assert events[0]["event_date"] == "2025-08-14"


@pytest.mark.parametrize("raw,expected", [
    ("Saturday 18.10.025", "2025-10-18"),     # 3-digit-year typo
    ("Monday 16.02 2026", "2026-02-16"),      # missing second dot
    ("Wednesday 07.10. 2026", "2026-10-07"),  # space after second dot (in corpus)
    ("Wednesday 07.10.2026", "2026-10-07"),
    ("Wednesday 15.10.", ""),                 # no year at all -> never guessed
    ("Thurs", ""),
    ("Sunday 27.10.2023", "2023-10-27"),      # source year typo kept verbatim
])
def test_date_parsing_tolerates_separator_typos_but_never_invents(raw, expected):
    text = (f"NAIROBI REGION\nAREA: X\n"
            f"DATE: {raw}   TIME: 9.00 A.M. – 5.00 P.M.\nPremises.\n")
    events = kp.parse_notice_text(text, "doc.pdf")
    assert len(events) == 1  # the DATE line stays a date line, no block split
    assert events[0]["event_date"] == expected
    assert events[0]["time_start_local"] == "09:00"


def test_plural_regions_header_is_matched():
    text = ("CENTRAL RIFT AND SOUTH NYANZA REGIONS\n"
            "AREA: KERICHO, BOMET\nDATE: Monday 30.09.2024   TIME: 7.00 A.M. - 5.00 P.M.\n"
            "Chemosit Market & adjacent customers.\n")
    event = kp.parse_notice_text(text, "doc.pdf")[0]
    assert event["region"] == "CENTRAL RIFT AND SOUTH NYANZA"


# --- empty / image-only ------------------------------------------------------------
def test_image_only_or_empty_text_yields_no_events():
    assert kp.parse_notice_text("", "image_only.pdf") == []
    assert kp.parse_notice_text("   \n\n\t  ", "image_only.pdf") == []


# --- overnight ---------------------------------------------------------------------
def test_overnight_range_keeps_raw_local_times_and_skips_utc_end():
    event = kp.parse_notice_text(OVERNIGHT_TEXT, "doc.pdf")[0]
    assert event["time_start_local"] == "22:00"
    assert event["time_end_local"] == "06:00"
    kp.add_utc_times([event])
    # Start is date+time known -> converted; end keeps no invented date shift.
    assert event["event_date_time_start_utc"] == pd.Timestamp("2026-10-10 19:00:00", tz="UTC")
    assert event["event_date_time_end_utc"] is None


# --- timezone conversion -----------------------------------------------------------
def test_add_utc_times_eat_to_utc_0900_is_0600():
    events = kp.parse_notice_text(MAINTENANCE_TEXT, "doc.pdf")
    kp.add_utc_times(events, assume_tz="Africa/Nairobi")
    first = events[0]
    assert first["event_date_time_start_utc"] == pd.Timestamp("2026-10-04 06:00:00", tz="UTC")
    assert first["event_date_time_end_utc"] == pd.Timestamp("2026-10-04 14:00:00", tz="UTC")


def test_add_utc_times_requires_date_and_time():
    events = [
        {"event_date": "2026-10-04", "time_start_local": "", "time_end_local": "17:00"},
        {"event_date": "", "time_start_local": "09:00", "time_end_local": "17:00"},
    ]
    kp.add_utc_times(events)
    for ev in events:
        assert ev["event_date_time_start_utc"] is None
        assert ev["event_date_time_end_utc"] is None
    assert kp.add_utc_times([]) == []


# --- dedup -------------------------------------------------------------------------
def test_dedup_events_keeps_first_per_key():
    key = {"area": "KOSOVO", "event_date": "2026-10-05",
           "time_start_local": "09:00", "notice_class": "planned"}
    events = [
        {**key, "premises_text": "first premises"},
        {**key, "premises_text": "second premises"},
        {**key, "event_date": "2026-10-06", "premises_text": "different day"},
    ]
    kept = kp.dedup_events(events)
    assert len(kept) == 2
    assert kept[0]["premises_text"] == "first premises"
    assert kept[1]["event_date"] == "2026-10-06"


# --- extract_all (hermetic, PDF reader monkeypatched) ------------------------------
def _fake_pdf_dir(tmp_path: Path, texts: dict[str, str]) -> Path:
    for name in texts:
        (tmp_path / name).write_bytes(b"%PDF-1.4 fake")
    return tmp_path


def test_extract_all_summary_dedup_and_csv_roundtrip(tmp_path, monkeypatch):
    texts = {
        "kplc_notice_a.pdf": (
            "NAIROBI REGION\nAREA: KOSOVO\nDATE: Monday 05.10.2026   "
            "TIME: 9.00 A.M. – 5.00 P.M.\nShofco & adjacent customers.\n"),
        "kplc_notice_b.pdf": (
            "NAIROBI REGION\nAREA: KOSOVO\nDATE: Monday 05.10.2026   "
            "TIME: 9.00 A.M. – 5.00 P.M.\nDuplicate block, same key.\n"
            "AREA: JOSKA, RUAI\nDATE: Wednesday 11.10.2026\nJoska premises list.\n"),
        "kplc_notice_c.pdf": "",  # image-only PDF
    }
    pdf_dir = _fake_pdf_dir(tmp_path, texts)
    monkeypatch.setattr(kp, "_pdf_text", lambda path: texts[path.name])

    out_csv = tmp_path / "events.csv"
    summary = kp.extract_all(pdf_dir, out_csv=out_csv)

    assert summary["n_notices"] == 3
    assert summary["n_unparseable"] == 1
    assert summary["unparseable_detail"] == [
        {"source_document": "kplc_notice_c.pdf", "reason": "image_only"}]
    assert summary["n_events"] == 2  # duplicate block collapsed
    assert summary["date_min"] == "2026-10-05"
    assert summary["date_max"] == "2026-10-11"
    assert summary["by_class"] == {"planned": 2}
    assert summary["by_region"] == {"NAIROBI": 2}

    df = pd.read_csv(out_csv)  # commas in areas must be quoted, not split
    assert list(df.columns) == kp.EVENT_COLUMNS
    assert len(df) == 2
    assert df["area"].tolist() == ["KOSOVO", "JOSKA, RUAI"]
    assert df.loc[0, "event_date_time_start_utc"] == "2026-10-05 06:00:00+00:00"
    # No time -> empty UTC cell -> NaN on reload (never the literal string "NaT").
    assert pd.isna(df.loc[1, "event_date_time_start_utc"])
    assert pd.isna(df.loc[1, "event_date_time_end_utc"])


def test_extract_all_unreadable_pdf_is_recorded(tmp_path, monkeypatch):
    pdf_dir = _fake_pdf_dir(tmp_path, {"kplc_notice_bad.pdf": ""})
    monkeypatch.setattr(
        kp, "_pdf_text", lambda path: (_ for _ in ()).throw(ValueError("broken xref")))
    summary = kp.extract_all(pdf_dir, out_csv=tmp_path / "events.csv")
    assert summary["n_events"] == 0
    assert summary["n_unparseable"] == 1
    assert summary["unparseable_detail"][0]["reason"].startswith("read_error")


def test_manifest_overrides_glob_when_present(tmp_path, monkeypatch):
    """Files listed in pdfs_manifest.json are the extraction set (stray PDFs skipped)."""
    texts = {
        "kplc_notice_a.pdf": (
            "NAIROBI REGION\nAREA: KOSOVO\nDATE: Monday 05.10.2026   "
            "TIME: 9.00 A.M. – 5.00 P.M.\nShofco & adjacent customers.\n"),
        "kplc_notice_not_a_notice.pdf": "",  # not in the manifest -> ignored
    }
    pdf_dir = _fake_pdf_dir(tmp_path, texts)
    monkeypatch.setattr(kp, "_pdf_text", lambda path: texts[path.name])
    kp._save_manifest(pdf_dir / kp.MANIFEST_NAME,
                      {"kplc_notice_a.pdf": {"filename": "kplc_notice_a.pdf"}})
    summary = kp.extract_all(pdf_dir, out_csv=tmp_path / "events.csv")
    assert summary["n_notices"] == 1
    assert summary["n_unparseable"] == 0


# --- notice file naming + manifest -------------------------------------------------
def test_notice_stem_uses_posted_date_and_disambiguates_same_day():
    claimed: set[str] = set()
    first = kp.notice_stem({"posted": "Oct 16, 2026 16:05 pm"}, 0, claimed)
    second = kp.notice_stem({"posted": "Oct 16, 2026 12:20 pm"}, 1, claimed)
    assert first == "kplc_notice_2026-10-16"
    assert second == "kplc_notice_2026-10-16_1220"


def test_download_notices_skips_existing_and_writes_manifest(tmp_path):
    """Existing files are skipped (no network) and the manifest is keyed by the
    *file name* so extraction picks them up — regression test for the stem-key bug."""
    import json

    existing = tmp_path / "kplc_notice_2026-10-16.pdf"
    existing.write_bytes(b"%PDF-1.4 sample")
    notices = [{
        "title": "Special Power Supply Interruption Notice - 16.10.2026",
        "posted": "Oct 16, 2026 16:05 pm",
        "pdf_url": "https://kplc.co.ke/storage/01M52D3TEST.pdf",
    }]
    report = kp.download_notices(notices, pdf_dir=tmp_path, limit=1, sleep_s=0)
    assert report["downloaded"] == 0
    assert report["skipped_existing"] == 1
    assert report["failed"] == 0

    manifest = json.loads((tmp_path / kp.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert list(manifest["files"]) == ["kplc_notice_2026-10-16.pdf"]
    entry = manifest["files"]["kplc_notice_2026-10-16.pdf"]
    assert entry["bytes"] == len(b"%PDF-1.4 sample")
    assert len(entry["sha256"]) == 64
    assert entry["url"].endswith("01M52D3TEST.pdf")
    assert entry["fetched_at"] is None  # pre-existing file, fetch time unknown
    assert kp._list_pdfs(tmp_path) == [existing]


def test_notice_stem_falls_back_to_title_slug_without_posted():
    claimed: set[str] = set()
    first = kp.notice_stem({"title": "Power Maintenance Notice"}, 7, claimed)
    second = kp.notice_stem({"title": "Power Maintenance Notice"}, 8, claimed)
    assert first == "kplc_notice_power-maintenance-notice"
    assert second == "kplc_notice_power-maintenance-notice_8"


# --- integration: saved sample PDFs (skip when absent) -----------------------------
needs_samples = pytest.mark.skipif(
    not (SAMPLE_MAINTENANCE.exists() or SAMPLE_SPECIAL.exists()),
    reason="saved KPLC sample PDFs absent")


@needs_samples
def test_saved_special_sample_parses():
    if not SAMPLE_SPECIAL.exists():
        pytest.skip("special sample absent")
    text = kp._pdf_text(SAMPLE_SPECIAL)
    assert text.strip(), "expected extractable text in the 2026-10-16 sample"
    event = kp.parse_notice_text(text, SAMPLE_SPECIAL.name)[0]
    assert event["notice_class"] == "special"
    assert (event["region"], event["county"]) == ("WESTERN", "KISUMU")
    assert event["event_date"] == "2026-10-16"
    assert (event["time_start_local"], event["time_end_local"]) == ("11:00", "18:00")


@needs_samples
def test_saved_maintenance_sample_parses_many_planned_blocks():
    if not SAMPLE_MAINTENANCE.exists():
        pytest.skip("maintenance sample absent")
    text = kp._pdf_text(SAMPLE_MAINTENANCE)
    assert text.strip(), "expected extractable text in the 2026-10-01 sample"
    events = kp.parse_notice_text(text, SAMPLE_MAINTENANCE.name)
    assert len(events) >= 50
    assert {e["notice_class"] for e in events} == {"planned"}
    assert {e["region"] for e in events} >= {"NAIROBI", "COAST", "MT. KENYA"}
    assert {e["county"] for e in events} >= {"NAKURU", "MOMBASA"}
    assert all(e["event_date"] and "T" not in e["event_date"] for e in events)
    assert all(e["premises_text"] for e in events)
    assert not any("Rule 27" in e["premises_text"] for e in events)  # letterhead filtered


@needs_samples
def test_saved_image_only_sample_yields_no_events():
    if not SAMPLE_IMAGE_ONLY.exists():
        pytest.skip("image-only sample absent")
    assert kp._pdf_text(SAMPLE_IMAGE_ONLY).strip() == ""  # 0 chars, no OCR available
    assert kp.parse_notice_text("", SAMPLE_IMAGE_ONLY.name) == []


def test_extract_all_on_saved_samples(tmp_path):
    """End-to-end over whatever sample PDFs exist: CSV must reload with pandas."""
    samples = [p for p in (SAMPLE_MAINTENANCE, SAMPLE_SPECIAL, SAMPLE_IMAGE_ONLY) if p.exists()]
    if not samples:
        pytest.skip("saved KPLC sample PDFs absent")
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    for sample in samples:
        shutil.copy(sample, pdf_dir / sample.name)

    out_csv = tmp_path / "events.csv"
    summary = kp.extract_all(pdf_dir, out_csv=out_csv)
    assert summary["n_notices"] == len(samples)
    assert summary["n_events"] >= 50
    if SAMPLE_IMAGE_ONLY.exists():
        assert summary["n_unparseable"] == 1
        assert summary["unparseable_detail"][0]["reason"] == "image_only"
    else:
        assert summary["n_unparseable"] == 0
    assert summary["date_min"] <= summary["date_max"]

    df = pd.read_csv(out_csv)
    assert list(df.columns) == kp.EVENT_COLUMNS
    assert len(df) == summary["n_events"]
    assert set(df["country_iso3"]) == {"KEN"}
