"""Tests for src.labels.collapse_days (Day 17)."""

import pandas as pd
import pytest

from src.labels import collapse_days as cd
from src.labels.collapse_days import (
    EVENTS_CSV, WINDOW_END, WINDOW_START,
    daily_collapse_labels, labelable_events, load_events,
)


@pytest.fixture
def fixture_events(tmp_path):
    csv = tmp_path / "events_extracted.csv"
    pd.DataFrame({
        "event_date": ["2022-03-01", "2023-01-15", "2023-06-10",
                       "2024-09-17", "2025-04-02", "2026-01-05"],
        "event_time": ["12:30", "08:00", "", "23:15", "14:00", "10:00"],
        "event_type": ["full", "partial", "unknown", "full", "partial", "full"],
        "restoration_time": ["13:45", "", "", "01:30", "15:00", "11:00"],
        "country_iso3": ["NGA"] * 6,
        "source_document": ["x.pdf"] * 6,
        "source_page": [1, 2, 3, 4, 5, 6],
        "extracted_utc": ["2026-10-19T00:00:00Z"] * 6,
    }).to_csv(csv, index=False)
    return csv


def test_load_events_converts_wat_to_utc(fixture_events):
    ev = load_events(fixture_events)
    row = ev[ev["event_date"] == "2023-01-15"].iloc[0]
    # 08:00 WAT == 07:00 UTC
    assert row["event_datetime_utc"] == pd.Timestamp("2023-01-15 07:00", tz="UTC")
    # empty time -> NaT, never invented
    no_time = ev[ev["event_date"] == "2023-06-10"].iloc[0]
    assert pd.isna(no_time["event_datetime_utc"])


def test_labelable_window_excludes_undated_years_and_after(fixture_events):
    ev = load_events(fixture_events)
    w = labelable_events(ev)
    years = set(w["event_date"].dt.year)
    assert years == {2023, 2024, 2025}
    assert len(w) == 4  # 2022 (counts-only era) and 2026 (outside) excluded


def test_daily_labels_window_only_no_zero_filling_outside(fixture_events):
    ev = load_events(fixture_events)
    daily = daily_collapse_labels(ev)
    assert daily["date"].min() == WINDOW_START
    assert daily["date"].max() == WINDOW_END
    assert len(daily) == (WINDOW_END - WINDOW_START).days + 1
    assert int(daily["collapse_any"].sum()) == 4  # 15 Jan, 10 Jun, 17 Sep, 2 Apr
    pos = daily[daily["collapse_any"]]
    assert set(pos["collapse_types"]) == {"partial", "full", "unknown"}


def test_writes_are_roundtripable(fixture_events, tmp_path):
    ev = load_events(fixture_events)
    daily = daily_collapse_labels(ev)
    paths = cd.write_outputs(ev, daily, out_dir=tmp_path)
    events_back = pd.read_csv(paths["events"])
    daily_back = pd.read_csv(paths["daily"])
    assert len(events_back) == 4
    assert len(daily_back) == (WINDOW_END - WINDOW_START).days + 1
    assert daily_back["collapse_any"].dtype == bool


def test_report_writes(fixture_events, tmp_path):
    ev = load_events(fixture_events)
    daily = daily_collapse_labels(ev)
    out = tmp_path / "day17.md"
    cd.write_report(ev, daily, out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "NOT labelable" in text
    assert "prevalence" in text


# --- real NERC extraction (skipped when absent) --------------------------------

@pytest.mark.skipif(not EVENTS_CSV.exists(),
                    reason="NERC extraction not run (Day 10)")
def test_real_window_matches_reconciled_counts():
    ev = load_events()
    w = labelable_events(ev)
    by_year = w.groupby(w["event_date"].dt.year).size()
    assert by_year.to_dict() == {2023: 3, 2024: 9, 2025: 2}
    daily = daily_collapse_labels(ev)
    assert int(daily["collapse_any"].sum()) == 14
    # prevalence ~1.3% -> rare-event framing is mandatory
    assert 0.5 < 100 * daily["collapse_any"].mean() < 3
