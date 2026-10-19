"""Tests for src.geo.units (Day 12).

Synthetic-geometry tests run everywhere; registry tests against the real
boundaries skip when data/external/boundaries/ is absent.
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.geo import units as reg


def _synthetic_units():
    rows = [
        ("NGA", "ADM2", "u1", "Obu", ""),
        ("NGA", "ADM2", "u2", "Obu", ""),          # true duplicate name
        ("NGA", "ADM2", "u3", "Lagos State", ""),
        ("KEN", "ADM1", "u4", "Nairobi City County", ""),
        ("KEN", "ADM1", "u5", "Nairobi County", ""),  # suffix collapse
        ("KEN", "ADM1", "u6", "", ""),              # empty name: never grouped
        ("KEN", "ADM1", "u7", "", ""),
    ]
    geoms = [box(i, 0, i + 0.9, 0.9) for i in range(len(rows))]
    df = gpd.GeoDataFrame(
        {
            "iso3": [r[0] for r in rows],
            "adm_level": [r[1] for r in rows],
            "unit_id": [r[2] for r in rows],
            "name": [r[3] for r in rows],
            "parent": [r[4] for r in rows],
        },
        geometry=geoms,
        crs="EPSG:4326",
    )
    df["name_norm"] = df["name"].map(reg.normalize_unit_name)
    df["match_key"] = df["name"].map(reg.name_match_key)
    units = reg.flag_duplicates(df)
    rep = units.geometry.representative_point()
    units["rep_lon"] = rep.x
    units["rep_lat"] = rep.y
    return units


def test_flag_duplicates_groups_same_key_within_scope():
    units = _synthetic_units()
    dup = units.set_index("unit_id")["is_duplicate_key"]
    assert bool(dup["u1"]) and bool(dup["u2"])   # Obu/Obu
    assert not bool(dup["u3"])
    assert units.set_index("unit_id")["dup_key_count"]["u1"] == 2


def test_empty_names_never_group():
    units = _synthetic_units()
    u6 = units[units["unit_id"] == "u6"].iloc[0]
    assert u6["match_key"] == ""
    assert u6["dup_key_count"] == 0
    assert not bool(u6["is_duplicate_key"])


def test_suffix_collapses_are_detected():
    units = _synthetic_units()
    collapsed = reg.key_collapses(units)
    nai = collapsed[collapsed["match_key"] == "nairobi"]
    assert len(nai) == 1
    assert int(nai.iloc[0]["n_distinct_names"]) == 2


def test_duplicate_report_frame_lists_collisions():
    report = reg.duplicate_report_frame(_synthetic_units())
    assert list(report.columns) == ["iso3", "adm_level", "match_key",
                                    "n_units", "names", "parents"]
    obu = report[report["match_key"] == "obu"].iloc[0]
    assert obu["n_units"] == 2
    assert "Obu" in obu["names"]


def test_write_registry_csv_roundtrip(tmp_path):
    units = _synthetic_units()
    out = reg.write_registry(units, out_path=tmp_path / "registry.csv")
    df = pd.read_csv(out)
    assert len(df) == 7
    assert set(["iso3", "adm_level", "unit_id", "name", "match_key",
                "rep_lon", "rep_lat", "dup_key_count"]).issubset(df.columns)
    assert df["rep_lon"].between(-180, 180).all()


def test_report_writes_for_synthetic(tmp_path):
    out = tmp_path / "day12.md"
    reg.write_report(_synthetic_units(), out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "# Day 12" in text
    assert "Duplicate match keys" in text
    assert "Resolution rule" in text


def test_report_empty_boundaries(tmp_path):
    out = tmp_path / "empty.md"
    reg.write_report(gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), out_path=out)
    assert "No boundaries found" in out.read_text(encoding="utf-8")


# --- real boundaries (skipped on fresh clones) ------------------------------

def _real_units():
    if not (reg.bnd.BOUNDARY_DIR / "KEN_ADM1.geojson").exists():
        pytest.skip("boundaries not downloaded (run: python -m src.ingestion.boundaries)")
    return reg.load_units()


def test_real_registry_counts_match_day2_expectations():
    units = _real_units()
    counts = units.groupby(["iso3", "adm_level"]).size()
    for (iso3, level), n in counts.items():
        expected = reg.bnd.EXPECTED_UNITS[(iso3, level)]
        assert n == expected, f"{iso3} {level}: {n} != {expected}"


def test_real_registry_true_name_duplicates_only_in_nga_adm2():
    units = _real_units()
    # True duplicates = same name after normalization (verbatim-level check
    # from Day 2), independent of suffix-stripping collapses.
    true_dup = units.groupby(["iso3", "adm_level", "name_norm"])["unit_id"].transform("size")
    is_true = true_dup.gt(1) & units["name_norm"].ne("")
    nga = is_true & units["iso3"].eq("NGA") & units["adm_level"].eq("ADM2")
    others = is_true & ~nga
    assert nga.sum() >= 2, "expected the known NGA_LGA duplicate names"
    assert others.sum() == 0, "no verbatim duplicates expected outside NGA_ADM2"
    # and the registry's broader match-key flag fires for NGA_ADM2 at least
    assert units[units["iso3"].eq("NGA") &
                 units["adm_level"].eq("ADM2")]["is_duplicate_key"].any()


def test_real_registry_keys_unique_per_unit_id():
    units = _real_units()
    assert not units["unit_id"].duplicated().any()
    assert (units["rep_lon"].between(-180, 180)).all()
    assert (units["rep_lat"].between(-90, 90)).all()
