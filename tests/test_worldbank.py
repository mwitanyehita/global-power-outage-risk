"""Tests for src.ingestion.worldbank."""

import json

import pandas as pd
import pytest

from src.ingestion import worldbank as wb


def _write_payload(wb_dir, indicator, rows):
    path = wb_dir / f"{indicator}.json"
    payload = [{"page": 1, "pages": 1, "per_page": "50", "total": len(rows)}, rows]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(iso3, name, year, value):
    return {
        "indicator": {"id": wb.OUTAGE_INDICATORS and "IC.FRM.INFRA.IN16", "value": "x"},
        "country": {"id": iso3, "value": name},
        "countryiso3code": iso3,
        "date": str(year),
        "value": value,
        "unit": "",
        "obs_status": "observed",
        "decimal": 0,
    }


@pytest.fixture
def wb_dir(tmp_path, monkeypatch):
    d = tmp_path / "worldbank"
    d.mkdir()
    monkeypatch.setattr(wb, "WB_DIR", d)
    return d


def test_load_indicator_normalises_api_rows(wb_dir):
    _write_payload(
        wb_dir, "IC.FRM.INFRA.IN16",
        [_row("KEN", "Kenya", 2013, 42.5), _row("KEN", "Kenya", 2018, 38.1),
         _row("ZAF", "South Africa", 2007, 15.0)],
    )
    df = wb.load_indicator("IC.FRM.INFRA.IN16")
    assert list(df.columns) == ["country", "country_name", "year", "value", "indicator"]
    assert df["country"].tolist() == ["KEN", "KEN", "ZAF"]
    assert df["value"].tolist() == [42.5, 38.1, 15.0]
    assert df["indicator"].nunique() == 1


def test_load_indicator_handles_error_payload(wb_dir):
    """API returns [{'message': [...]}] when an indicator does not exist."""
    (wb_dir / "IC.ELC.OUTG.json").write_text(
        json.dumps([{"message": [{"id": "175", "key": "Invalid format",
                                  "value": "The indicator was not found."}]}]),
        encoding="utf-8",
    )
    df = wb.load_indicator("IC.ELC.OUTG")
    assert df.empty
    assert list(df.columns) == ["country", "country_name", "year", "value", "indicator"]


def test_coverage_table_marks_missing_files(wb_dir, tmp_path):
    cov = wb.coverage_table({"MISSING.IND": "does not exist"})
    assert set(cov["indicator"]) == {"MISSING.IND"}
    assert set(cov["country"]) == set(wb.COUNTRIES)
    assert (cov["n_obs"] == 0).all()
    assert cov["last"].isna().all()


def test_coverage_counts_only_non_null_values(wb_dir):
    rows = [_row("NGA", "Nigeria", 2014, 30.0), _row("NGA", "Nigeria", 2015, None)]
    _write_payload(wb_dir, "IC.FRM.INFRA.IN16", rows)
    cov = wb.coverage_table({"IC.FRM.INFRA.IN16": "x"})
    nga = cov[cov["country"] == "NGA"].iloc[0]
    assert nga["n_obs"] == 1
    assert nga["years"] == "2014"


def test_write_audit_produces_report(wb_dir, tmp_path):
    _write_payload(
        wb_dir, "IC.FRM.INFRA.IN16",
        [_row("KEN", "Kenya", 2018, 40.0)],
    )
    out = tmp_path / "coverage.md"
    result = wb.write_audit(out_path=out)
    text = result.read_text(encoding="utf-8")
    assert "# Day 5 audit" in text
    assert "country-year" in text
    assert "IC.FRM.INFRA.IN16" in text


def test_indicator_ids_look_like_world_bank_ids():
    for ind in (*wb.OUTAGE_INDICATORS, *wb.CONTEXT_INDICATORS):
        assert ind.count(".") >= 1
        assert ind == ind.upper()
