"""Tests for src.ingestion.weather.

Timestamp-validation logic is tested on synthetic frames; tests against the
real downloads skip when data/raw/weather/ is absent.
"""

import json

import pandas as pd
import pytest

from src.ingestion import weather as wx


def _frame(times, **overrides):
    """Build a minimal hourly frame around the requested index."""
    data = {"time": pd.DatetimeIndex(times)}
    for var in wx.HOURLY_VARIABLES:
        lo, hi = wx.VALUE_RANGES[var]
        overrides.setdefault(var, (lo + hi) / 2)
    data.update(overrides)
    return pd.DataFrame(data)


def test_slugify():
    entry = {"country": "ZAF", "city": "Cape Town", "lat": 0, "lon": 0}
    assert wx.slugify(entry) == "ZAF_cape-town"


def test_value_ranges_cover_all_hourly_variables():
    assert set(wx.VALUE_RANGES) == set(wx.HOURLY_VARIABLES)


def test_default_end_is_iso_yesterday():
    end = wx.default_end()
    parsed = pd.Timestamp(end, tz="UTC")
    now = pd.Timestamp.now(tz="UTC").normalize()
    assert (now - parsed).days == 1


def test_validate_frame_accepts_clean_grid():
    times = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
    res = wx.validate_frame(_frame(times), "2024-01-01", "2024-01-02")
    assert res["row_count_ok"] is True
    assert res["tz_utc"] is True
    assert res["monotonic"] is True
    assert res["duplicate_times"] == 0
    assert res["gap_count"] == 0
    assert res["grid_matches_expected"] is True
    assert res["total_nulls"] == 0
    assert res["out_of_range_total"] == 0


def test_validate_frame_detects_gap_and_duplicate():
    times = list(pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC"))
    broken = times[:6] + [times[5]] + times[7:]  # duplicate t5, then skip t6
    res = wx.validate_frame(_frame(broken), "2024-01-01", "2024-01-01")
    assert res["duplicate_times"] == 1
    assert res["gap_count"] >= 2
    assert res["grid_matches_expected"] is False
    assert res["row_count_ok"] is False


def test_validate_frame_detects_nulls_and_out_of_range():
    times = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
    frame = _frame(times)
    frame.loc[0, "temperature_2m"] = None          # null
    frame.loc[1, "temperature_2m"] = 999.0         # out of range
    res = wx.validate_frame(frame, "2024-01-01", "2024-01-01")
    assert res["nulls_by_var"]["temperature_2m"] == 1
    assert res["out_of_range_by_var"]["temperature_2m"] == 1


def test_validate_frame_detects_naive_timestamps():
    times = pd.date_range("2024-01-01", periods=6, freq="h")  # no tz
    res = wx.validate_frame(_frame(times), "2024-01-01", "2024-01-01")
    assert res["tz_utc"] is False


def test_real_downloads_pass_validation():
    paths = [p for p in wx.WEATHER_DIR.glob("*.json") if p.name != "_manifest.json"]
    if not paths:
        pytest.skip("weather downloads not present (run: python -m src.ingestion.weather)")
    report = wx.validate_all()
    assert report["grid_alignment_ok"] is True
    for stem, res in report["cities"].items():
        assert res["row_count_ok"], stem
        assert res["tz_utc"], stem
        assert res["duplicate_times"] == 0, stem
        assert res["gap_count"] == 0, stem


def test_load_city_returns_hourly_utc_index():
    paths = [p for p in wx.WEATHER_DIR.glob("*.json") if p.name != "_manifest.json"]
    if not paths:
        pytest.skip("weather downloads not present")
    df = wx.load_city(paths[0])
    assert str(df["time"].dt.tz) == "UTC"
    assert df["time"].is_monotonic_increasing
    assert (df["time"].diff().dropna() == pd.Timedelta(hours=1)).all()


def test_manifest_records_hashes_for_all_downloads():
    manifest_path = wx.WEATHER_DIR / "_manifest.json"
    if not manifest_path.exists():
        pytest.skip("manifest not present")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = [p for p in wx.WEATHER_DIR.glob("*.json") if p.name != "_manifest.json"]
    assert len(manifest) == len(files)
    for f in files:
        assert len(manifest[f.name]["sha256"]) == 64
        assert manifest[f.name]["retrieved_utc"]
