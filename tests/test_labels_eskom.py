"""Tests for src.labels.eskom (Day 15).

Hermetic tests build a tiny sqlite fixture with the real string formats;
integration tests against the Day 4 mirror skip when it is absent.
"""

import sqlite3

import pandas as pd
import pytest

from src.labels import eskom as labels
from src.labels.eskom import (
    MLR_COL, SQLITE_PATH, TIME_COL,
    annual_validation, daily_labels, hourly_quality, load_hourly,
)


def _write_fixture(db_path, rows):
    con = sqlite3.connect(db_path)
    con.execute(f'CREATE TABLE "{labels.TABLE}" '
                f'("{TIME_COL}" TEXT, "{MLR_COL}" TEXT)')
    con.executemany(
        f'INSERT INTO "{labels.TABLE}" VALUES (?, ?)', rows)
    con.commit()
    con.close()


@pytest.fixture
def small_db(tmp_path):
    """Three SAST hours across a day boundary + one missing MLR value."""
    db = tmp_path / "fixture.sqlite"
    rows = [
        ("2024-03-01 11:00:00 PM", "1500"),   # SAST 23:00 -> UTC 21:00
        ("2024-03-02 12:00:00 AM", "0"),      # SAST 00:00 -> UTC 22:00 prev day
        ("2024-03-02 01:00:00 AM", ""),       # missing MLR
        ("2024-03-02 02:00:00 AM", "4200"),   # active
    ]
    _write_fixture(db, rows)
    return db


def test_load_hourly_parses_sast_and_converts_to_utc(small_db):
    h = load_hourly(small_db)
    assert list(h.columns) == labels.HOURLY_COLUMNS
    assert str(h["time_utc"].dt.tz) == "UTC"
    assert str(h["time_sast"].dt.tz) == "Africa/Johannesburg"
    assert h.iloc[0]["time_utc"] == pd.Timestamp("2024-03-01 21:00", tz="UTC")
    assert h.iloc[1]["time_utc"] == pd.Timestamp("2024-03-01 22:00", tz="UTC")


def test_load_hourly_label_semantics(small_db):
    h = load_hourly(small_db)
    assert h["mlr_mw"].tolist()[:2] == [1500.0, 0.0]
    assert bool(h.iloc[0]["load_shedding"]) is True
    assert bool(h.iloc[1]["load_shedding"]) is False
    # missing MLR -> <NA>, never False (a hole must not read as no-shedding)
    assert pd.isna(h.iloc[2]["load_shedding"])
    assert pd.isna(h.iloc[2]["mlr_mw"])
    assert bool(h.iloc[3]["load_shedding"]) is True


def test_leakage_guard_column_whitelist(small_db):
    h = load_hourly(small_db)
    assert set(h.columns) == {"time_sast", "time_utc", "mlr_mw",
                              "load_shedding"}


def test_daily_labels_group_by_local_day(small_db):
    h = load_hourly(small_db)
    daily = daily_labels(h)
    assert len(daily) == 2  # 2024-03-01 (1 hour) and 2024-03-02 (3 hours)
    d2 = daily[daily["date"] == "2024-03-02"].iloc[0]
    assert d2["hours_reported"] == 3
    assert d2["mlr_missing"] == 1
    assert bool(d2["any_load_shedding"]) is True
    assert d2["mlr_mw_max"] == 4200.0
    d1 = daily[daily["date"] == "2024-03-01"].iloc[0]
    assert d1["hours_active"] == 1


def test_annual_validation_percent(small_db):
    h = load_hourly(small_db)
    v = annual_validation(h)
    assert v["year"].tolist() == [2024]
    row = v.iloc[0]
    assert row["hours"] == 4
    assert row["hours_active"] == 2
    assert row["pct_hours_active"] == 50.0


def test_hourly_quality_counts_gaps_and_duplicates(small_db):
    h = load_hourly(small_db)
    q = hourly_quality(h)
    assert q["rows"] == 4
    assert q["duplicate_hours"] == 0
    assert q["missing_hours"] == 0
    assert q["mlr_missing"] == 1
    assert q["mlr_negative"] == 0


# --- real mirror (skipped on fresh clones) -----------------------------------

@pytest.mark.skipif(not SQLITE_PATH.exists(),
                    reason="Eskom mirror not acquired (Day 4)")
def test_real_mirror_reproduces_known_calendar():
    h = load_hourly()
    assert len(h) == 83280
    q = hourly_quality(h)
    assert q["duplicate_hours"] == 0
    assert q["mlr_negative"] <= 1  # known artifact: -0.104 MW on 2021-11-18 21:00 SAST

    v = annual_validation(h).set_index("year")
    pct = lambda y: float(v.loc[y, "pct_hours_active"]) if y in v.index else 0.0
    assert pct(2020) < 15         # shedding returned Aug 2020 (~10% of hours)
    assert 60 <= pct(2023) <= 90  # peak year, ~78% of hours
    assert pct(2025) < 10         # recovery
    assert pct(2026) < 10         # YTD
