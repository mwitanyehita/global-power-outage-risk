"""Tests for src.eda.coverage_maps (Day 13).

Real-data tests skip when boundaries/processed inputs are absent.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.eda import coverage_maps as cm


@pytest.fixture(scope="module")
def units():
    from src.geo.units import load_units
    u = load_units()
    if u.empty:
        pytest.skip("boundaries not downloaded (python -m src.ingestion.boundaries)")
    return u


def test_coverage_table_has_expected_shape(units):
    table = cm.coverage_table(units)
    assert set(table.columns) == {"country", "layer", "status",
                                  "coverage_detail", "source"}
    assert set(table["country"]) == {"KEN", "ZAF", "NGA"}
    # boundaries + weather + wb context for every country
    for iso3 in ("KEN", "ZAF", "NGA"):
        layers = set(table[table["country"] == iso3]["layer"])
        assert {"boundaries_adm0", "boundaries_adm1", "boundaries_adm2",
                "weather_hourly", "wb_context"} <= layers
    # statuses are from the documented vocabulary
    assert set(table["status"]) <= {"available", "partial", "pending",
                                    "absent"}
    # KEN unplanned labels must be declared absent-by-design (Day 7)
    ken = table[(table["country"] == "KEN")
                & (table["layer"] == "label_unplanned")].iloc[0]
    assert ken["status"] == "absent"


def test_coverage_table_reflects_reality_not_hardcoding(units):
    table = cm.coverage_table(units)
    weather = table[table["layer"] == "weather_hourly"]
    assert (weather["status"] == "available").all()
    # unit counts in the detail string must match the registry
    adm1 = table[table["layer"] == "boundaries_adm1"].set_index("country")
    for iso3 in ("KEN", "ZAF", "NGA"):
        n_units = int(((units["iso3"] == iso3)
                       & (units["adm_level"] == "ADM1")).sum())
        assert f"{n_units} units" in adm1.loc[iso3, "coverage_detail"]


def test_figures_and_report_written(tmp_path, units):
    paths = cm.write_all(out_root=tmp_path, units=units)
    for key in ("ken", "zaf", "nga", "labels"):
        p = paths[key]
        assert p.exists() and p.stat().st_size > 1000, key
    report = paths["report"]
    text = report.read_text(encoding="utf-8")
    assert "# Day 13" in text
    # every figure the report references exists on disk (relative paths)
    for line in text.splitlines():
        if line.startswith("!["):
            name = line.split("](")[1].rstrip(")")
            assert (report.parent / name).exists(), name


def test_label_panels_tolerate_missing_csvs(tmp_path):
    """Coverage figure must still render when processed CSVs are absent."""
    out = tmp_path / "labels.png"
    p = cm.fig_label_coverage(out)
    assert p.exists() and p.stat().st_size > 1000
