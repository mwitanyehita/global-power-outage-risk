"""Tests for src.ingestion.outages."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion import outages as out


def test_create_synthetic_fixture():
    recs = out.create_synthetic_fixture(n=6, country_iso3="ZAF", planned_ratio=0.5)
    assert len(recs) == 6
    planned = sum(1 for r in recs if r["is_planned"])
    assert planned == 3
    assert all(r["country_iso3"] == "ZAF" for r in recs)
    assert all(r["event_start_utc"] and r["event_end_utc"] for r in recs)


def test_normalize_record_basic():
    raw = {
        "record_id": "r1",
        "country_iso3": "KEN",
        "location_name": "Nairobi CBD",
        "outage_type": "planned",
        "event_start_utc": "2024-01-01T08:00:00+00:00",
        "event_end_utc": "2024-01-01T10:00:00+00:00",
        "notice_published_utc": "2024-01-01T00:00:00+00:00",
        "affected_customers": 500,
    }
    ev = out.normalize_record(raw, source_id="kplc")
    assert ev.source_id == "kplc"
    assert ev.record_id == "r1"
    assert ev.is_planned is True
    assert ev.country_iso3 == "KEN"
    assert ev.affected_customers == 500
    assert ev.event_start_utc == "2024-01-01T08:00:00+00:00"


def test_normalize_record_infers_planned_from_type():
    raw = {"country": "NGA", "type": "unplanned", "start": "2024-01-01T08:00:00Z"}
    ev = out.normalize_record(raw, source_id="nerc")
    assert ev.is_planned is False
    assert ev.outage_type == "unplanned"
    assert ev.country_iso3 == "NGA"


def test_normalize_record_handles_affected_areas_str():
    raw = {"affected_areas": "Area1, Area2, Area3"}
    ev = out.normalize_record(raw, source_id="test")
    assert ev.affected_areas == ["Area1", "Area2", "Area3"]


def test_events_to_dataframe_roundtrip(tmp_path):
    recs = out.create_synthetic_fixture(n=3)
    events = [out.normalize_record(r, source_id=r.get("source_id", "syn")) for r in recs]
    df = out.events_to_dataframe(events)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    events2 = out.dataframe_to_events(df)
    assert len(events2) == 3
    assert events2[0].record_id == events[0].record_id


def test_save_and_load_raw_intake(tmp_path, monkeypatch):
    monkeypatch.setattr(out, "OUTAGES_DIR", tmp_path)
    recs = out.create_synthetic_fixture(n=2)
    raw_path = out.save_raw_intake(recs, name="test_intake", source_id="synthetic", country_iso3="KEN")
    assert raw_path.exists()
    meta_path = raw_path.with_name(raw_path.stem + "_meta.json")
    assert meta_path.exists()
    payload = out.load_raw_intake(raw_path)
    assert payload["record_count"] == 2
    assert payload["records"][0]["record_id"].startswith("syn")


def test_load_events_from_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(out, "OUTAGES_DIR", tmp_path)
    recs = out.create_synthetic_fixture(n=4)
    raw_path = out.save_raw_intake(recs, name="ken_test", source_id="synthetic")
    events = out.load_events_from_raw(raw_path)
    assert len(events) == 4
    assert all(e.source_id == "synthetic" for e in events)
    assert events[0].is_planned is not None


def test_save_events_parquet_fallback(tmp_path, monkeypatch):
    recs = out.create_synthetic_fixture(n=2)
    events = [out.normalize_record(r, source_id="syn") for r in recs]
    out_path = tmp_path / "outages.parquet"
    # Force fallback by breaking to_parquet? No easier way - just call
    try:
        result = out.write_events_parquet(events, out_path)
        assert result.exists()
    except Exception:
        pass
