"""Tests for src.geo.join (Day 11).

Synthetic geometry tests run everywhere; real-boundary tests skip when
data/external/boundaries/ is absent.
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.geo import join as spatialjoin
from src.geo.units import load_units


@pytest.fixture(scope="module")
def units():
    u = load_units()
    if u.empty:
        pytest.skip("boundaries not downloaded (run: python -m src.ingestion.boundaries)")
    return u


# --- synthetic point-in-polygon ---------------------------------------------

def _synthetic_unit_frame():
    df = gpd.GeoDataFrame(
        {
            "iso3": ["TST", "TST"],
            "adm_level": ["ADM1", "ADM1"],
            "unit_id": ["a", "b"],
            "name": ["Alpha", "Beta"],
            "parent": ["", ""],
            "name_norm": ["alpha", "beta"],
            "match_key": ["alpha", "beta"],
            "rep_lon": [0.5, 2.5],
            "rep_lat": [0.5, 0.5],
            "dup_key_count": [0, 0],
            "is_duplicate_key": [False, False],
        },
        geometry=[box(0, 0, 1, 1), box(2, 0, 3, 1)],
        crs="EPSG:4326",
    )
    return df


def test_points_to_units_matches_by_coordinate():
    units = _synthetic_unit_frame()
    pts = spatialjoin.points_gdf(pd.DataFrame(
        {"city": ["in-a", "in-b", "outside"], "lon": [0.5, 2.5, 9.0],
         "lat": [0.5, 0.5, 0.5]}))
    out = spatialjoin.points_to_units(pts, "TST", "ADM1", units)
    assert out["unit_id"].tolist()[:2] == ["a", "b"]
    assert out["unit_name"].tolist()[:2] == ["Alpha", "Beta"]
    # misses are KEPT as rows with an explicit status
    assert out.iloc[2]["match_method"] == "unmatched"
    assert pd.isna(out.iloc[2]["unit_id"]) or out.iloc[2]["unit_id"] is None
    assert set(out["match_method"]) == {"point_in_polygon", "unmatched"}


def test_points_to_units_empty_units_frame():
    pts = spatialjoin.points_gdf(pd.DataFrame({"lon": [0.5], "lat": [0.5]}))
    out = spatialjoin.points_to_units(pts, "XXX", "ADM1", _synthetic_unit_frame()[:0])
    assert out["match_method"].iloc[0] == "unmatched"


# --- synthetic name matching -------------------------------------------------

def test_match_names_statuses():
    units = _synthetic_unit_frame()
    out = spatialjoin.match_names_to_units(
        ["Alpha", "Beta", "Gamma", "", "  "], "TST", "ADM1", units)
    assert out["status"].tolist() == ["unique", "unique", "unmatched",
                                      "no_key", "no_key"]
    assert "Alpha [a]" in out.iloc[0]["candidates"]


def test_match_names_flags_ambiguity():
    units = pd.concat([
        _synthetic_unit_frame(),
        gpd.GeoDataFrame(
            {
                "iso3": ["TST"], "adm_level": ["ADM1"], "unit_id": ["a2"],
                "name": ["Alpha"], "parent": ["Elsewhere"], "name_norm": ["alpha"],
                "match_key": ["alpha"], "rep_lon": [5.0], "rep_lat": [5.0],
                "dup_key_count": [2], "is_duplicate_key": [True],
            },
            geometry=[box(4, 4, 5, 5)], crs="EPSG:4326"),
    ], ignore_index=True)
    out = spatialjoin.match_names_to_units(["Alpha"], "TST", "ADM1", units)
    row = out.iloc[0]
    assert row["status"] == "ambiguous"
    assert row["n_candidates"] == 2
    assert "Elsewhere" in row["candidates"]  # parents shown, none preferred


# --- KPLC summary tolerance ---------------------------------------------------

def test_kplc_summary_none_when_csv_absent(tmp_path):
    assert spatialjoin.kplc_join_summary(
        csv_path=tmp_path / "missing.csv") is None


def test_kplc_summary_joins_when_csv_present(tmp_path, units):
    csv = tmp_path / "kplc.csv"
    pd.DataFrame({
        "county": ["KISUMU COUNTY", "Nairobi"],
        "area": ["PART OF PARKLANDS", "NONEXISTENT ESTATE XYZ"],
        "event_date": ["2026-10-16", "2026-10-04"],
    }).to_csv(csv, index=False)
    out = spatialjoin.kplc_join_summary(csv_path=csv, units=units)
    assert out["n_events"] == 2
    c = out["counties"].set_index("name")
    assert c.loc["KISUMU COUNTY", "status"] == "unique"
    assert c.loc["Nairobi", "status"] == "unique"
    a = out["areas"].set_index("name")
    assert a.loc["NONEXISTENT ESTATE XYZ", "status"] == "unmatched"


# --- real boundaries ----------------------------------------------------------

def test_all_nine_cities_join_adm1_by_coordinates(units):
    cities = spatialjoin.weather_cities_to_adm1(units)
    assert len(cities) == 9
    assert (cities["match_method"] == "point_in_polygon").all()
    assert cities["unit_name"].notna().all()


def test_city_units_are_the_expected_ones(units):
    cities = spatialjoin.weather_cities_to_adm1(units).set_index("city")
    key = lambda name: cities.loc[name, "unit_name"].casefold()
    assert "nairobi" in key("Nairobi")
    assert key("Cape Town") == "western cape"
    assert "lagos" in key("Lagos")
    assert "kano" in key("Kano")


def test_real_name_matching_ambiguity_and_uniqueness(units):
    out = spatialjoin.match_names_to_units(
        ["Nairobi", "Kisumu"], "KEN", "ADM1", units)
    assert (out["status"] == "unique").all()

    dup = spatialjoin.match_names_to_units(["Obi"], "NGA", "ADM2", units)
    assert dup.iloc[0]["status"] == "ambiguous"
    assert dup.iloc[0]["n_candidates"] == 2

    none = spatialjoin.match_names_to_units(["Atlantis"], "KEN", "ADM2", units)
    assert none.iloc[0]["status"] == "unmatched"


def test_day11_report_writes(units, tmp_path):
    out = spatialjoin.write_report(units, out_path=tmp_path / "day11.md")
    text = out.read_text(encoding="utf-8")
    assert "# Day 11" in text
    assert "point-in-polygon" in text
    assert "Nairobi" in text  # cities table present
