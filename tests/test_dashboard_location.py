"""Tests for the user-facing location dashboard layer (src.dashboard.location).

Hermetic tests cover the geospatial/notice logic and the serving model; the
real-data integration test skips when the processed snapshot is absent, so a
fresh clone stays green.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.dashboard import location as L


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def test_haversine_known_distance():
    # Nairobi -> Mombasa is roughly 440 km.
    km = L.haversine_km(-1.2921, 36.8219, -4.0435, 39.6682)
    assert 425 <= km <= 455


def test_normalize_name_handles_punctuation():
    assert L.normalize_name("Murang’a") == "MURANGA"
    assert L.normalize_name("Elgeyo-Marakwet") == "ELGEYO MARAKWET"
    assert L.normalize_name(None) == ""
    assert L.normalize_name(float("nan")) == ""


def test_county_matches_aliases_and_compounds():
    assert L.county_matches("KAKAMEG", "Kakamega")
    assert L.county_matches("MAKEUNI", "Makueni")
    assert L.county_matches("NYANDADRUA", "Nyandarua")
    assert L.county_matches("HOMABAY", "Homa Bay")
    assert L.county_matches("BOMET/NAROK", "Bomet")
    assert L.county_matches("MURANG’A/KIAMBU", "Murang'a")
    assert L.county_matches("PARS OF KIRINYAGA", "Kirinyaga")
    assert L.county_matches("LAIKIPIA NITHI", "Laikipia")
    assert not L.county_matches("MOMBASA", "Nairobi")
    assert not L.county_matches("", "Nairobi")


# ---------------------------------------------------------------------------
# Point -> admin resolution
# ---------------------------------------------------------------------------
def _boundary_file(path: Path, name: str) -> None:
    ring = [[36.8, -0.2], [37.2, -0.2], [37.2, 0.2], [36.8, 0.2], [36.8, -0.2]]
    payload = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"shapeName": name},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }],
    }
    path.write_text(json.dumps(payload))


def test_locate_uses_boundaries(tmp_path):
    _boundary_file(tmp_path / "KEN_ADM0.geojson", "Kenya")
    _boundary_file(tmp_path / "KEN_ADM1.geojson", "Nairobi")
    _boundary_file(tmp_path / "KEN_ADM2.geojson", "Starehe")

    result = L.locate(0.0, 37.0, boundaries_dir=tmp_path)
    assert result["country_iso3"] == "KEN"
    assert result["country"] == "Kenya"
    assert result["adm1"] == "Nairobi"
    assert result["adm2"] == "Starehe"
    assert result["method"] == "boundaries"
    assert result["in_pilot"] is True
    assert result["adm1_geometry"]["type"] == "Polygon"


def test_locate_outside_pilot_countries(tmp_path):
    result = L.locate(48.8566, 2.3522, boundaries_dir=tmp_path)
    assert result["country_iso3"] is None
    assert result["in_pilot"] is False
    assert result["method"] is None


# ---------------------------------------------------------------------------
# KPLC planned notices
# ---------------------------------------------------------------------------
def _events_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "county": ["NAIROBI", "NAIROBI", "MOMBASA", None, "KAKAMEG"],
        "region": ["NAIROBI", "NAIROBI", "COAST", "NAIROBI", "WESTERN"],
        "event_date": pd.to_datetime([
            "2026-10-20", "2026-10-22", "2026-10-21", "2026-10-23", "2026-10-19",
        ]),
        "event_date_time_start_utc": pd.to_datetime([
            "2026-10-20 06:00", "2026-10-22 06:00", "2026-10-21 06:00",
            "2026-10-23 06:00", "2026-10-19 06:00",
        ], utc=True),
        "event_date_time_end_utc": pd.to_datetime([
            "2026-10-20 14:00", "2026-10-22 14:00", "2026-10-21 14:00",
            "2026-10-23 14:00", "2026-10-19 14:00",
        ], utc=True),
        "notice_class": ["planned", "planned", "planned", "planned", "special"],
        "time_start_local": ["09:00"] * 5,
        "time_end_local": ["17:00"] * 5,
        "area": ["A", "B", "C", "D", "E"],
        "premises_text": [""] * 5,
        "source_document": ["doc"] * 5,
    })


def test_upcoming_outages_filters_horizon_and_sorts():
    frame = _events_frame()
    out = L.upcoming_outages(
        frame, horizon_days=3, today=date(2026, 10, 19),
    )
    assert list(out["area"]) == ["A", "C"]
    assert out["event_date"].is_monotonic_increasing


def test_outages_for_admin1_matches_county_or_region():
    frame = _events_frame()
    out = L.outages_for_admin1(frame, "Nairobi")
    # Two county matches plus the blank-county row whose region says NAIROBI.
    assert set(out["area"]) == {"A", "B", "D"}


# ---------------------------------------------------------------------------
# Serving model
# ---------------------------------------------------------------------------
def test_fit_predict_latest_withholds_target_label():
    rng = np.random.default_rng(7)
    n = 90
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = (x1 + 0.3 * x2 + rng.normal(scale=0.5, size=n) > 0).astype(int)
    frame = pd.DataFrame({
        "date_sast": pd.date_range("2024-01-01", periods=n, freq="D"),
        "any_load_shedding": y,
        "f1": x1,
        "f2": x2,
    })

    result = L.fit_predict_latest(frame)
    assert result["target_is_labelled"] is True
    assert result["train_rows"] == n - 1
    assert result["train_positives"] == int(y[:-1].sum())
    assert 0.0 <= result["probability"] <= 1.0
    assert result["target_date"] == frame["date_sast"].iloc[-1]


def test_fit_predict_latest_requires_two_classes():
    frame = pd.DataFrame({
        "date_sast": pd.date_range("2024-01-01", periods=60, freq="D"),
        "any_load_shedding": [0] * 60,
        "f1": np.arange(60.0),
    })
    with pytest.raises(ValueError, match="single class"):
        L.fit_predict_latest(frame)


# ---------------------------------------------------------------------------
# Satellite map
# ---------------------------------------------------------------------------
def test_satellite_map_embeds_esri_imagery(tmp_path):
    fmap = L.build_satellite_map(
        0.0, 37.0, {"in_pilot": False},
        boundaries_dir=tmp_path, planned_events=pd.DataFrame(),
    )
    html = fmap.get_root().render()
    assert "World_Imagery" in html
    assert "World_Boundaries_and_Places" in html
    assert "Esri" in html


def test_satellite_map_survives_missing_boundaries(tmp_path):
    fmap = L.build_satellite_map(
        0.0, 37.0, {"in_pilot": True, "country_iso3": "KEN"},
        boundaries_dir=tmp_path, planned_events=pd.DataFrame(),
    )
    assert fmap is not None


# ---------------------------------------------------------------------------
# Real snapshot (local only)
# ---------------------------------------------------------------------------
def _processed_ready() -> bool:
    from src.config import DATA_PROCESSED
    return (
        (DATA_PROCESSED / "zaf_daily_dataset.csv.gz").exists()
        and (DATA_PROCESSED / "zaf_oof_predictions.csv.gz").exists()
    )


@pytest.mark.skipif(not _processed_ready(), reason="processed ZAF snapshot absent")
def test_latest_zaf_forecast_is_after_label_cutoff():
    result = L.latest_zaf_forecast()
    assert result["ok"] is True
    assert 0.0 <= result["probability"] <= 1.0
    assert pd.Timestamp(result["target_date"]) > pd.Timestamp(result["label_cutoff"])
    assert result["metrics"]["n"] > 100
    assert result["metrics"]["brier_skill"] > 0.5
