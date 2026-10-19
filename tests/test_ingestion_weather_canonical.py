"""Unit tests for src.ingestion.weather_canonical (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion import utils
from src.ingestion import weather
from src.ingestion import weather_canonical as wc

HOURS = ["2026-03-01T00:00", "2026-03-01T01:00", "2026-03-01T02:00"]

# Values chosen inside weather.VALUE_RANGES for every variable.
DEFAULT_VALUES = {
    "temperature_2m": 20.0,
    "relative_humidity_2m": 55.0,
    "precipitation": 0.2,
    "rain": 0.2,
    "weather_code": 3.0,
    "cloud_cover": 45.0,
    "wind_speed_10m": 12.0,
    "wind_gusts_10m": 20.0,
    "surface_pressure": 1012.0,
}


def _write_city(
    raw_dir: Path,
    slug: str,
    hours: list[str] | None = None,
    overrides: dict | None = None,
) -> Path:
    """Write a synthetic Open-Meteo style raw response for one city."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    hourly = {"time": list(hours if hours is not None else HOURS)}
    hourly.update(DEFAULT_VALUES)
    for var, value in (overrides or {}).items():
        hourly[var] = value
    path = raw_dir / f"{slug}.json"
    path.write_text(json.dumps({"hourly": hourly}), encoding="utf-8")
    return path


def _manifest(raw_dir: Path, entries: dict) -> None:
    (raw_dir / "_manifest.json").write_text(json.dumps(entries),
                                            encoding="utf-8")


# --------------------------------------------------------------------------
# Raw discovery and per-city loading
# --------------------------------------------------------------------------

def test_discover_raw_files_excludes_manifest_and_handles_missing_dir(tmp_path: Path):
    assert wc.discover_raw_files(tmp_path / "nope") == []
    _write_city(tmp_path, "KEN_nairobi")
    (tmp_path / "_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert [p.name for p in wc.discover_raw_files(tmp_path)] == ["KEN_nairobi.json"]


def test_load_city_canonical_maps_registry_and_schema(tmp_path: Path):
    path = _write_city(tmp_path, "KEN_nairobi")
    df, warnings = wc.load_city_canonical(path)

    assert list(df.columns) == list(wc.CANONICAL_COLUMNS)
    assert warnings == []
    assert utils.is_tz_aware(df["time_utc"])
    assert df["time_utc"].tolist() == [
        pd.Timestamp(h, tz="UTC") for h in HOURS
    ]
    row = df.iloc[0]
    assert row["city"] == "Nairobi"
    assert row["country_iso3"] == "KEN"
    assert row["latitude"] == pytest.approx(-1.292)
    assert row["longitude"] == pytest.approx(36.822)
    assert row["raw_file"] == "KEN_nairobi.json"
    assert row["temperature_2m"] == 20.0


def test_load_city_canonical_missing_variables_warn_but_keep_rows(tmp_path: Path):
    raw_dir = tmp_path
    raw_dir.mkdir(exist_ok=True)
    path = raw_dir / "ZAF_cape-town.json"
    path.write_text(
        json.dumps({"hourly": {"time": HOURS, "temperature_2m": [18.0] * 3}}),
        encoding="utf-8",
    )
    df, warnings = wc.load_city_canonical(path)
    assert len(df) == 3
    assert any("missing variable(s)" in w for w in warnings)
    assert df["precipitation"].isna().all()
    assert df["temperature_2m"].notna().all()


def test_load_city_canonical_rejects_response_without_time(tmp_path: Path):
    path = tmp_path / "KEN_nairobi.json"
    path.write_text(json.dumps({"hourly": {"temperature_2m": [1.0]}}),
                    encoding="utf-8")
    with pytest.raises(utils.SchemaError):
        wc.load_city_canonical(path)


def test_entry_for_unknown_slug_derives_fields(tmp_path: Path):
    entry, warnings = wc._entry_for(tmp_path / "XXX_some-city.json")
    assert entry["city"] == "Some City"
    assert entry["country"] == "XXX"
    assert any("not in registry" in w for w in warnings)


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def _tmp_two_city_frame(tmp: Path) -> pd.DataFrame:
    frames = [
        wc.load_city_canonical(_write_city(tmp, "KEN_nairobi"))[0],
        wc.load_city_canonical(_write_city(tmp, "ZAF_johannesburg"))[0],
    ]
    return pd.concat(frames, ignore_index=True)


def test_validate_accepts_clean_aligned_frames(tmp_path: Path):
    df = _tmp_two_city_frame(tmp_path)
    report = wc.validate(df)
    assert report["ok"] is True, report["errors"]
    assert report["errors"] == []
    assert report["counts"]["cities"] == 2
    assert report["counts"]["grid_alignment"] is True
    assert report["counts"]["by_country"] == {"KEN": 3, "ZAF": 3}
    assert report["counts"]["hours_per_city"] == {"Johannesburg": 3, "Nairobi": 3}
    assert report["counts"]["null_cells"] == 0


def test_validate_missing_columns_fails():
    report = wc.validate(pd.DataFrame({"city": ["Nairobi"]}))
    assert report["ok"] is False
    assert "missing columns" in report["errors"][0]


def test_validate_duplicate_city_time_fails(tmp_path: Path):
    one, _ = wc.load_city_canonical(_write_city(tmp_path, "KEN_nairobi"))
    df = pd.concat([one, one], ignore_index=True)
    report = wc.validate(df)
    assert report["ok"] is False
    assert any("duplicate (city, time_utc)" in e for e in report["errors"])


def test_validate_gap_in_city_grid_fails(tmp_path: Path):
    gap_hours = ["2026-03-01T00:00", "2026-03-01T02:00"]  # 01:00 missing
    one, _ = wc.load_city_canonical(
        _write_city(tmp_path, "KEN_nairobi", gap_hours))
    report = wc.validate(one)
    assert report["ok"] is False
    assert any("gap or duplicate hours" in e for e in report["errors"])


def test_validate_naive_time_column_fails(tmp_path: Path):
    df, _ = wc.load_city_canonical(_write_city(tmp_path, "KEN_nairobi"))
    df["time_utc"] = df["time_utc"].dt.tz_localize(None)  # strip timezone
    report = wc.validate(df)
    assert report["ok"] is False
    assert any("not a tz-aware" in e for e in report["errors"])


def test_validate_null_time_fails(tmp_path: Path):
    df, _ = wc.load_city_canonical(_write_city(tmp_path, "KEN_nairobi"))
    df.loc[1, "time_utc"] = pd.NaT
    report = wc.validate(df)
    assert report["ok"] is False
    assert any("null time_utc" in e for e in report["errors"])


def test_validate_out_of_range_is_a_warning_not_an_error(tmp_path: Path):
    df, _ = wc.load_city_canonical(
        _write_city(tmp_path, "KEN_nairobi",
                    overrides={"temperature_2m": [999.0, 999.0, 999.0]}))
    report = wc.validate(df)
    assert report["ok"] is True
    assert any("outside physical range" in w for w in report["warnings"])
    assert report["counts"]["out_of_range_cells"] == 3


def test_validate_null_cells_warn_without_failing(tmp_path: Path):
    df, _ = wc.load_city_canonical(
        _write_city(tmp_path, "KEN_nairobi",
                    overrides={"precipitation": [0.0, None, 0.2]}))
    report = wc.validate(df)
    assert report["ok"] is True
    assert report["counts"]["null_cells"] == 1
    assert any("null value cell" in w for w in report["warnings"])


def test_validate_is_json_safe(tmp_path: Path):
    df, _ = wc.load_city_canonical(_write_city(tmp_path, "KEN_nairobi"))
    json.dumps(wc.validate(df))  # must not raise


# --------------------------------------------------------------------------
# build — raw dir -> processed CSV
# --------------------------------------------------------------------------

def test_build_end_to_end(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_city(raw_dir, "KEN_nairobi")
    _write_city(raw_dir, "ZAF_johannesburg")
    _manifest(raw_dir, {"KEN_nairobi.json": {"sha256": "recorded-hash"}})
    out_path = tmp_path / "processed" / "weather_hourly.csv"

    summary = wc.build(raw_dir, out_path)

    assert summary["files"] == ["KEN_nairobi.json", "ZAF_johannesburg.json"]
    assert summary["rows"] == 6
    assert summary["validation"]["ok"] is True
    assert summary["written"] == str(out_path)
    assert summary["warnings"] == []

    # sidecar follows the overridden output path
    meta = utils.read_json(tmp_path / "processed" / "weather_hourly_meta.json")
    assert meta["source"] == wc.SOURCE_NAME
    assert meta["terms"] == wc.TERMS_URL
    assert meta["rows"] == 6
    by_file = {i["raw_file"]: i for i in meta["inputs"]}
    assert by_file["KEN_nairobi.json"]["sha256"] == "recorded-hash"  # manifest wins
    assert len(by_file["ZAF_johannesburg.json"]["sha256"]) == 64  # computed
    assert by_file["ZAF_johannesburg.json"]["city"] == "Johannesburg"

    # round-trip
    df = wc.load_processed(out_path)
    assert list(df.columns) == list(wc.CANONICAL_COLUMNS)
    assert utils.is_tz_aware(df["time_utc"])
    assert set(df["city"]) == {"Nairobi", "Johannesburg"}
    assert len(df) == 6


def test_build_with_no_raw_files_reports_error(tmp_path: Path):
    summary = wc.build(tmp_path / "empty", tmp_path / "out.csv")
    assert summary["files"] == []
    assert summary["written"] is None
    assert summary["validation"]["ok"] is False
    assert "no raw weather files found" in summary["validation"]["errors"][0]
    assert not (tmp_path / "out.csv").exists()


def test_build_audit_writes_nothing(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_city(raw_dir, "KEN_nairobi")
    out_path = tmp_path / "out.csv"
    summary = wc.build(raw_dir, out_path, write=False)
    assert summary["rows"] == 3
    assert summary["validation"]["ok"] is True
    assert summary["written"] is None
    assert not out_path.exists()


def test_build_skips_unreadable_files_and_keeps_rest(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_city(raw_dir, "KEN_nairobi")
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / "broken.json").write_text("not json at all", encoding="utf-8")

    summary = wc.build(raw_dir, tmp_path / "out.csv")
    assert summary["files"] == ["KEN_nairobi.json", "broken.json"]
    assert summary["rows"] == 3
    assert any("broken.json: unreadable" in w for w in summary["warnings"])
    assert summary["validation"]["ok"] is True
    assert summary["written"] is not None


def test_build_flags_unknown_city_slug(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_city(raw_dir, "XXX_somewhere-else")
    summary = wc.build(raw_dir, tmp_path / "out.csv")
    assert any("not in registry" in w for w in summary["warnings"])
    assert summary["rows"] == 3


def test_build_refuses_to_write_when_validation_fails(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_city(raw_dir, "KEN_nairobi",
                hours=["2026-03-01T00:00", "2026-03-01T02:00"])
    out_path = tmp_path / "out.csv"
    summary = wc.build(raw_dir, out_path)
    assert summary["validation"]["ok"] is False
    assert summary["written"] is None
    assert not out_path.exists()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_build_and_audit(tmp_path: Path, capsys):
    raw_dir = tmp_path / "raw"
    _write_city(raw_dir, "KEN_nairobi")
    out_path = tmp_path / "out.csv"

    assert wc.main(["--raw-dir", str(raw_dir), "--out", str(out_path)]) == 0
    assert out_path.exists()
    assert "wrote:" in capsys.readouterr().out

    out_path.unlink()
    assert wc.main(["--audit", "--raw-dir", str(raw_dir),
                    "--out", str(out_path)]) == 0
    assert not out_path.exists()
    assert "(skipped — --audit)" in capsys.readouterr().out


def test_cli_returns_1_when_no_raw_files(tmp_path: Path, capsys):
    code = wc.main(["--raw-dir", str(tmp_path / "empty"),
                    "--out", str(tmp_path / "out.csv")])
    assert code == 1
