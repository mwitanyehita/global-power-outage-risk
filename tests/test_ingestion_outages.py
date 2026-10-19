"""Unit tests for src.ingestion.outages (raw-file intake, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion import outages as out
from src.ingestion import utils


# --------------------------------------------------------------------------
# normalize_record — alias mapping, vocabularies, timestamps, ids
# --------------------------------------------------------------------------

def _raw_record(**overrides) -> dict:
    record = {
        "id": "N-100",
        "country": "kenya",
        "location": "Kilimani",
        "state": "Nairobi",
        "type": "scheduled",
        "start": "2026-03-01T10:30:00+03:00",
        "end": "2026-03-01T14:00:00+03:00",
        "customers": 1500,
        "url": "https://example.test/N-100",
        "description": "feeder maintenance",
    }
    record.update(overrides)
    return record


def test_normalize_record_maps_aliases_to_canonical_fields():
    row, warnings = out.normalize_record(
        _raw_record(), source="kplc", license="CC-BY", raw_file="kplc.json"
    )
    assert row["record_id"] == "kplc:N-100"
    assert row["source"] == "kplc"
    assert row["license"] == "CC-BY"
    assert row["country_iso3"] == "KEN"
    assert row["area_name"] == "Kilimani"
    assert row["admin1"] == "Nairobi"
    assert row["outage_type"] == "planned"
    assert row["customers_affected"] == 1500
    assert row["source_url"] == "https://example.test/N-100"
    assert row["cause"] == "feeder maintenance"
    assert row["raw_file"] == "kplc.json"
    # aware times converted to UTC, no warnings
    assert row["event_start_utc"] == pd.Timestamp("2026-03-01T07:30:00Z")
    assert row["event_end_utc"] == pd.Timestamp("2026-03-01T11:00:00Z")
    assert warnings == []
    assert list(row) == list(out.CANONICAL_COLUMNS)


def test_normalize_record_naive_time_is_flagged_as_assumed_utc():
    row, warnings = out.normalize_record(
        {"id": "1", "start": "2026-03-01 10:30:00"}, source="manual"
    )
    assert row["event_start_utc"] == pd.Timestamp("2026-03-01 10:30:00", tz="UTC")
    assert any("naive event_start_utc" in w and "assumed UTC" in w for w in warnings)


def test_normalize_record_unparseable_time_becomes_nat_with_warning():
    row, warnings = out.normalize_record(
        {"id": "1", "start": "yesterday-ish"}, source="manual"
    )
    assert pd.isna(row["event_start_utc"])
    assert any("unparseable event_start_utc" in w for w in warnings)


def test_normalize_record_missing_times_are_nat_without_invalid_warning():
    row, warnings = out.normalize_record({"id": "1"}, source="manual")
    assert pd.isna(row["event_start_utc"])
    assert pd.isna(row["event_end_utc"])
    assert warnings == []


def test_normalize_record_content_hash_id_is_stable_without_native_id():
    raw = {"country": "ZAF", "location": "Soweto", "start": "2026-03-01T06:00:00Z"}
    row_a, _ = out.normalize_record(raw, source="eskom")
    row_b, _ = out.normalize_record(dict(raw), source="eskom")
    assert row_a["record_id"].startswith("eskom:h")
    assert row_a["record_id"] == row_b["record_id"]  # idempotent re-intake


def test_normalize_record_outage_type_vocabulary():
    cases = {
        "Load Shedding": "unplanned",
        "scheduled maintenance": "planned",
        "fault": "unplanned",
        "something odd": "unknown",
        None: "unknown",
        True: "planned",
        False: "unplanned",
    }
    for value, expected in cases.items():
        row, _ = out.normalize_record({"type": value}, source="s")
        assert row["outage_type"] == expected, value
        assert row["outage_type"] in out.OUTAGE_TYPES


def test_normalize_record_country_names_and_invalid_values():
    assert out.normalize_record({"country": "South Africa"}, source="s")[0][
        "country_iso3"] == "ZAF"
    assert out.normalize_record({"country": "NGA"}, source="s")[0][
        "country_iso3"] == "NGA"
    assert out.normalize_record({"country": "Atlantis"}, source="s")[0][
        "country_iso3"] is None
    assert out.normalize_record({}, source="s")[0]["country_iso3"] is None


def test_normalize_record_bad_customers_value_warns_and_nulls():
    row, warnings = out.normalize_record(
        {"customers": "many"}, source="s"
    )
    assert row["customers_affected"] is pd.NA
    assert any("non-numeric customers_affected" in w for w in warnings)

    row, warnings = out.normalize_record({}, source="s")
    assert row["customers_affected"] is pd.NA
    assert warnings == []


# --------------------------------------------------------------------------
# normalize_records — frame shape and dtypes
# --------------------------------------------------------------------------

def test_normalize_records_frame_shape_and_dtypes():
    df, warnings = out.normalize_records(
        [_raw_record(), {"id": "N-101", "start": "2026-03-02T08:00:00Z"}],
        source="kplc",
        raw_file="kplc.json",
    )
    assert list(df.columns) == list(out.CANONICAL_COLUMNS)
    assert len(df) == 2
    for col in out.TIME_COLUMNS:
        assert utils.is_tz_aware(df[col]), col
    assert str(df["customers_affected"].dtype) == "Int64"
    assert int(df["customers_affected"].iloc[0]) == 1500
    assert pd.isna(df["customers_affected"].iloc[1])
    assert warnings == []


def test_normalize_records_empty_frame_keeps_canonical_dtypes():
    df, warnings = out.normalize_records([], source="empty")
    assert list(df.columns) == list(out.CANONICAL_COLUMNS)
    assert len(df) == 0
    assert warnings == []
    for col in out.TIME_COLUMNS:
        assert utils.is_tz_aware(df[col]), col


def test_normalize_records_source_and_license_are_forced_from_arguments():
    df, _ = out.normalize_records(
        [{"id": "1", "source": "spoofed", "license": "bogus"}], source="real"
    )
    assert df.loc[0, "source"] == "real"
    assert df.loc[0, "license"] == "unknown"


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def _good_frame(n: int = 2) -> pd.DataFrame:
    df, _ = out.normalize_records(
        [
            {"id": str(i), "country": "KEN", "type": "unplanned",
             "start": "2026-03-01T10:00:00Z", "end": "2026-03-01T12:00:00Z"}
            for i in range(n)
        ],
        source="kplc",
    )
    return df


def test_validate_accepts_a_clean_frame():
    report = out.validate(_good_frame())
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["counts"]["rows"] == 2
    assert report["counts"]["by_outage_type"] == {"unplanned": 2}


def test_validate_missing_columns_fails():
    report = out.validate(pd.DataFrame({"record_id": ["a"]}))
    assert report["ok"] is False
    assert "missing columns" in report["errors"][0]


def test_validate_null_start_and_bad_type_and_backwards_time_fail():
    df = _good_frame()
    df.loc[0, "event_start_utc"] = pd.NaT
    df.loc[1, "outage_type"] = "meltdown"
    report = out.validate(df)
    assert report["ok"] is False
    assert any("null event_start_utc" in e for e in report["errors"])
    assert any("outage_type outside" in e for e in report["errors"])


def test_validate_end_before_start_fails():
    df, _ = out.normalize_records(
        [{"id": "1", "start": "2026-03-01T12:00:00Z",
          "end": "2026-03-01T10:00:00Z"}],
        source="kplc",
    )
    report = out.validate(df)
    assert report["ok"] is False
    assert any("event_end < event_start" in e for e in report["errors"])


def test_validate_warnings_do_not_fail():
    df, _ = out.normalize_records(
        [
            {"id": "1", "country": "KEN", "start": "2026-03-01T10:00:00Z"},
            {"id": "1", "country": "USA", "start": "2026-03-01T11:00:00Z"},
        ],
        source="kplc",
    )
    report = out.validate(df)
    assert report["ok"] is True
    assert any("duplicate record_id" in w for w in report["warnings"])
    assert any("outside pilot countries" in w for w in report["warnings"])
    assert any("without event_end_utc" in w for w in report["warnings"])


def test_validate_is_json_safe():
    report = out.validate(_good_frame())
    json.dumps(report)  # must not raise


# --------------------------------------------------------------------------
# Raw intake path — write, discover, load
# --------------------------------------------------------------------------

def test_write_raw_intake_writes_payload_and_sidecar(tmp_path: Path):
    records = [_raw_record()]
    raw_path = out.write_raw_intake(
        records, "kplc", source="kplc", license="CC-BY 4.0",
        country_iso3="KEN", source_url="https://example.test",
        raw_dir=tmp_path,
    )
    assert raw_path == tmp_path / "kplc.json"
    payload = utils.read_json(raw_path)
    assert payload["source"] == "kplc"
    assert payload["records"] == records  # verbatim

    sidecar = utils.read_json(tmp_path / "kplc_meta.json")
    assert sidecar["source"] == "kplc"
    assert sidecar["license"] == "CC-BY 4.0"
    assert sidecar["record_count"] == 1
    assert sidecar["sha256"] == utils.sha256_file(raw_path)
    assert sidecar["retrieved_utc"].endswith("+00:00")


def test_write_raw_intake_sanitizes_hostile_names(tmp_path: Path):
    raw_path = out.write_raw_intake([], "../../evil", source="s",
                                    raw_dir=tmp_path)
    assert raw_path.parent == tmp_path
    assert "/" not in raw_path.name


def test_discover_raw_files_excludes_sidecars_and_handles_missing_dir(tmp_path: Path):
    assert out.discover_raw_files(tmp_path / "nope") == []
    (tmp_path / "b.json").write_text("[]", encoding="utf-8")
    (tmp_path / "a.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "b_meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    found = out.discover_raw_files(tmp_path)
    assert [p.name for p in found] == ["a.csv", "b.json"]


def test_load_raw_records_variants(tmp_path: Path):
    as_list = tmp_path / "list.json"
    as_list.write_text(json.dumps([{"id": 1}]), encoding="utf-8")
    assert out._load_raw_records(as_list) == [{"id": 1}]

    as_object = tmp_path / "obj.json"
    as_object.write_text(json.dumps({"source": "x", "records": [{"id": 2}]}),
                         encoding="utf-8")
    assert out._load_raw_records(as_object) == [{"id": 2}]

    as_csv = tmp_path / "f.csv"
    as_csv.write_text("id,country\n1,KEN\n", encoding="utf-8")
    records = out._load_raw_records(as_csv)
    assert records[0]["country"] == "KEN"


def test_load_raw_records_rejects_bad_shapes(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    with pytest.raises(utils.SchemaError):
        out._load_raw_records(bad)

    not_objects = tmp_path / "flat.json"
    not_objects.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(utils.SchemaError):
        out._load_raw_records(not_objects)


# --------------------------------------------------------------------------
# intake pipeline — raw dir -> processed CSV
# --------------------------------------------------------------------------

def test_intake_end_to_end(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    out_path = tmp_path / "processed" / "outages.csv"

    out.write_raw_intake(
        [
            {"id": "1", "country": "kenya", "location": "Kilimani",
             "type": "scheduled", "start": "2026-03-01T10:30:00+03:00",
             "customers": 100},
            {"id": "2", "country": "kenya", "location": "Runda",
             "type": "fault", "start": "2026-03-02T08:00:00Z"},
        ],
        "kplc", source="kplc", license="CC-BY 4.0", raw_dir=raw_dir,
    )
    summary = out.intake(raw_dir, out_path)

    assert summary["files"] == ["kplc.json"]
    assert summary["rows"] == 2
    assert summary["validation"]["ok"] is True
    assert summary["written"] == str(out_path)
    assert Path(summary["written"]).exists()

    # sidecar next to the overridden output, not the default processed path
    meta = utils.read_json(tmp_path / "processed" / "outages_meta.json")
    assert meta["rows"] == 2
    assert meta["inputs"][0]["raw_file"] == "kplc.json"
    assert meta["inputs"][0]["sha256"]

    # round-trip: canonical dtypes survive CSV
    df = out.load_processed(out_path)
    assert list(df.columns) == list(out.CANONICAL_COLUMNS)
    for col in out.TIME_COLUMNS:
        assert utils.is_tz_aware(df[col]), col
    assert str(df["customers_affected"].dtype) == "Int64"
    assert df["record_id"].tolist() == ["kplc:1", "kplc:2"]
    assert df.loc[0, "event_start_utc"] == pd.Timestamp("2026-03-01T07:30:00Z")
    assert df.loc[0, "outage_type"] == "planned"  # "scheduled" not merged away
    assert df.loc[1, "outage_type"] == "unplanned"
    # stable ids on re-intake
    assert df["record_id"].is_unique


def test_intake_with_no_raw_files_reports_error(tmp_path: Path):
    summary = out.intake(tmp_path / "empty", tmp_path / "out.csv")
    assert summary["files"] == []
    assert summary["written"] is None
    assert summary["validation"]["ok"] is False
    assert "no raw intake files found" in summary["validation"]["errors"][0]
    assert not (tmp_path / "out.csv").exists()


def test_intake_audit_writes_nothing(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    out.write_raw_intake([{"id": "1", "start": "2026-03-01T10:00:00Z"}],
                         "manual", source="manual", raw_dir=raw_dir)
    out_path = tmp_path / "out.csv"
    summary = out.intake(raw_dir, out_path, write=False)
    assert summary["rows"] == 1
    assert summary["validation"]["ok"] is True
    assert summary["written"] is None
    assert not out_path.exists()


def test_intake_drops_duplicate_record_ids(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    record = {"id": "9", "start": "2026-03-01T10:00:00Z"}
    out.write_raw_intake([record], "kplc", source="kplc", raw_dir=raw_dir)
    out.write_raw_intake([record], "kplc_copy", source="kplc", raw_dir=raw_dir)
    summary = out.intake(raw_dir, tmp_path / "out.csv")
    assert summary["rows"] == 1
    assert any("duplicate record_id" in w for w in summary["warnings"])


def test_intake_warns_on_missing_sidecar_and_skips_bad_files(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    # raw file without sidecar
    (raw_dir / "bare.json").write_text(
        json.dumps([{"id": "1", "start": "2026-03-01T10:00:00Z"}]),
        encoding="utf-8",
    )
    # structurally invalid file — must not abort the run
    (raw_dir / "broken.json").write_text(json.dumps({"foo": 1}),
                                         encoding="utf-8")
    summary = out.intake(raw_dir, tmp_path / "out.csv")
    assert summary["rows"] == 1
    assert any("no provenance sidecar" in w for w in summary["warnings"])
    assert any("license not recorded" in w for w in summary["warnings"])
    assert any("expected a JSON list" in w for w in summary["warnings"])
    assert summary["validation"]["ok"] is True


def test_intake_refuses_to_write_when_validation_fails(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    # null event start -> validation error
    out.write_raw_intake([{"id": "1"}], "manual", source="manual",
                         raw_dir=raw_dir)
    out_path = tmp_path / "out.csv"
    summary = out.intake(raw_dir, out_path)
    assert summary["validation"]["ok"] is False
    assert summary["written"] is None
    assert not out_path.exists()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_schema_prints_canonical_schema(capsys):
    assert out.main(["--schema"]) == 0
    text = capsys.readouterr().out
    for col in out.CANONICAL_COLUMNS:
        assert col in text
    assert "planned | unplanned | unknown" in text


def test_cli_intake_and_audit(tmp_path: Path, capsys):
    raw_dir = tmp_path / "raw"
    out.write_raw_intake(
        [{"id": "1", "country": "KEN", "type": "fault",
          "start": "2026-03-01T10:00:00Z"}],
        "kplc", source="kplc", license="CC-BY", raw_dir=raw_dir,
    )
    out_path = tmp_path / "out.csv"

    assert out.main(["--raw-dir", str(raw_dir), "--out", str(out_path)]) == 0
    assert out_path.exists()
    text = capsys.readouterr().out
    assert "wrote:" in text

    # audit mode validates without touching the output again
    out_path.unlink()
    assert out.main(["--audit", "--raw-dir", str(raw_dir),
                     "--out", str(out_path)]) == 0
    assert not out_path.exists()


def test_cli_returns_1_when_no_raw_files(tmp_path: Path, capsys):
    code = out.main(["--raw-dir", str(tmp_path / "empty"),
                     "--out", str(tmp_path / "out.csv")])
    assert code == 1


def test_cli_returns_1_on_validation_failure(tmp_path: Path, capsys):
    raw_dir = tmp_path / "raw"
    out.write_raw_intake([{"id": "1"}], "manual", source="manual",
                         raw_dir=raw_dir)
    code = out.main(["--raw-dir", str(raw_dir),
                     "--out", str(tmp_path / "out.csv")])
    assert code == 1
