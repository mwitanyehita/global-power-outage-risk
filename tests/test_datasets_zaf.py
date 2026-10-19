"""Tests for src.datasets.zaf_daily (Day 16).

Hermetic tests build a synthetic 3-city weather panel + labels; the
real-data integration test skips when raw downloads or labels are absent.
"""

import numpy as np
import pandas as pd
import pytest

from src.datasets import zaf_daily as ds
from src.datasets.zaf_daily import (
    TIME_COL, build_dataset, feature_columns, leakage_audit, load_labels,
    zaf_weather_daily,
)

CITIES = ["johannesburg", "cape-town", "durban"]


PERTURB_DAY = "2024-02-20"  # 10 days before the frame end


def _hourly_weather(city_key, start="2024-01-01", days=60, temp_offset=0.0,
                    perturb_day=None):
    times = pd.date_range(start, periods=24 * days, freq="h", tz="UTC")
    hours = pd.Series(times.hour, dtype="int64")
    temp = 20 + temp_offset + 5 * ((hours - 14) % 24 - 12).abs() / 12
    gust = pd.Series(np.full(len(times), 30.0))
    precip = pd.Series(np.zeros(len(times)))
    if perturb_day is not None:
        mask = times.date == pd.Timestamp(perturb_day).date()
        temp[mask] += 100.0
        gust[mask] += 50.0
        precip[mask] += 40.0
    return pd.DataFrame({
        "time": times,
        "temperature_2m": temp,
        "temperature_2m_max": temp,
        "relative_humidity_2m": 60.0,
        "precipitation": precip,
        "rain": precip,
        "weather_code": 0,
        "cloud_cover": 50.0,
        "wind_speed_10m": 15.0,
        "wind_gusts_10m": gust,
        "surface_pressure": 1010.0,
        "_city": city_key,
    })


@pytest.fixture
def labels_csv(tmp_path):
    days = pd.date_range("2024-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "date": days.strftime("%Y-%m-%d"),
        "hours_reported": 24,
        "mlr_missing": 0,
        "hours_active": [0, 24, 12, 0] * 15,
        "mlr_mw_mean": [0.0, 3000.0, 1500.0, 0.0] * 15,
        "mlr_mw_max": [0.0, 5000.0, 2500.0, 0.0] * 15,
        "mlr_mwh": [0.0] * 60,
    })
    df["any_load_shedding"] = df["hours_active"] > 0
    path = tmp_path / "zaf_daily_loadshedding.csv"
    df.to_csv(path, index=False)
    return path


@pytest.fixture
def weather_dir(tmp_path):
    d = tmp_path / "weather"
    d.mkdir()
    import json
    for i, city in enumerate(CITIES):
        frame = _hourly_weather(city, temp_offset=float(i))
        hourly = {k: frame[k].tolist() for k in frame.columns
                  if k not in ("_city", "time")}
        hourly["time"] = [t.tz_localize(None).isoformat() for t in frame["time"]]
        payload = {
            "hourly": hourly,
            "hourly_units": {"temperature_2m": "°C"},
            "timezone": "UTC",
        }
        (d / f"ZAF_{city}.json").write_text(json.dumps(payload))
    return d


def _load_raw(weather_dir, city):
    import json
    from src.ingestion import weather as wx
    payload = json.loads((weather_dir / f"ZAF_{city}.json").read_text())
    return wx.load_city(weather_dir / f"ZAF_{city}.json")


# --- assembly -----------------------------------------------------------------

def test_build_dataset_shape_and_grid(weather_dir, labels_csv):
    frame = build_dataset(weather_dir=weather_dir, labels_csv=labels_csv)
    assert frame[TIME_COL].is_monotonic_increasing
    diffs = frame[TIME_COL].diff().dropna()
    assert (diffs == pd.Timedelta(days=1)).all()
    # labels span 60 SAST days and every one has weather -> full inner join
    assert len(frame) == 60
    assert "any_load_shedding" in frame.columns


def test_labels_join_correctly(weather_dir, labels_csv):
    frame = build_dataset(weather_dir=weather_dir, labels_csv=labels_csv)
    labels = load_labels(labels_csv)
    merged = frame[[TIME_COL, "hours_active"]].merge(
        labels[[TIME_COL, "hours_active"]], on=TIME_COL,
        suffixes=("", "_src"))
    assert (merged["hours_active"] == merged["hours_active_src"]).all()


def test_leakage_whitelist_clean(weather_dir, labels_csv):
    frame = build_dataset(weather_dir=weather_dir, labels_csv=labels_csv)
    assert leakage_audit(frame) == []
    for col in feature_columns(frame):
        assert any(tag in col for tag in ("_lag_", "_past_")) or col in {
            "dow", "month", "is_weekend", "doy_sin", "doy_cos"}, col
    # every city-derived weather feature is strictly past
    for col in feature_columns(frame):
        if col.split("_", 1)[0] in CITIES:
            assert "_lag_" in col or "_past_" in col


def test_future_weather_perturbation_changes_only_later_rows(weather_dir,
                                                             labels_csv, tmp_path):
    """THE invariant: day D's weather may influence features only after D."""
    frame_a = build_dataset(weather_dir=weather_dir, labels_csv=labels_csv)

    perturbed_dir = tmp_path / "weather_perturbed"
    perturbed_dir.mkdir()
    import json
    for city in CITIES:
        src = weather_dir / f"ZAF_{city}.json"
        payload = json.loads(src.read_text())
        if city == "johannesburg":
            frame = _hourly_weather(city, perturb_day=PERTURB_DAY)
            payload["hourly"]["temperature_2m"] = frame["temperature_2m"].tolist()
            payload["hourly"]["wind_gusts_10m"] = frame["wind_gusts_10m"].tolist()
            payload["hourly"]["precipitation"] = frame["precipitation"].tolist()
        (perturbed_dir / f"ZAF_{city}.json").write_text(json.dumps(payload))

    frame_b = build_dataset(weather_dir=perturbed_dir, labels_csv=labels_csv)
    feats = feature_columns(frame_a)
    cut = pd.Timestamp(PERTURB_DAY).tz_localize("Africa/Johannesburg")
    idx = int((frame_a[TIME_COL] == cut).idxmax())  # position of day D

    # rows up to and including day D: features use only earlier weather -> equal
    pd.testing.assert_frame_equal(
        frame_a[feats].iloc[:idx + 1], frame_b[feats].iloc[:idx + 1],
        check_exact=False, rtol=1e-12)
    # row D+1: its lag-1 sees the perturbed day -> must differ
    changed = ~(
        (frame_a[feats].iloc[idx + 1].fillna(-999)
         == frame_b[feats].iloc[idx + 1].fillna(-999))
    )
    assert changed.any()


# --- real data (skipped when absent) ------------------------------------------

def _real_ready():
    from src.ingestion.weather import WEATHER_DIR
    return (WEATHER_DIR / "ZAF_johannesburg.json").exists() and ds.LABELS_CSV.exists()


@pytest.mark.skipif(not _real_ready(),
                    reason="ZAF weather and/or labels not present")
def test_real_dataset_span_and_cleanliness():
    frame = build_dataset()
    assert leakage_audit(frame) == []
    assert frame[TIME_COL].dt.year.min() == 2021
    assert frame[TIME_COL].max() == pd.Timestamp("2026-09-30",
                                                 tz="Africa/Johannesburg")
    # 2023 must be fully covered — it is the peak-shedding training year
    year23 = frame[frame[TIME_COL].dt.year == 2023]
    assert len(year23) == 365
    assert year23["any_load_shedding"].mean() > 0.6
