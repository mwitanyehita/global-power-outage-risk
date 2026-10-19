"""Unit tests for src.ingestion.utils (no network)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import requests

from src.ingestion import utils


# --------------------------------------------------------------------------
# HTTP retries
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self.content = json.dumps(payload or {}).encode()
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            resp = requests.Response()
            resp.status_code = self.status_code
            raise requests.HTTPError(f"{self.status_code} error", response=resp)


class _FakeSession:
    """Scripted session: pops one outcome per call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, params=None, timeout=None, headers=None):  # noqa: ARG002
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_get_with_retries_returns_immediately_on_success():
    session = _FakeSession([_FakeResponse(200, {"ok": True})])
    resp = utils.get_with_retries("http://example.test/x", session=session)
    assert resp.json() == {"ok": True}
    assert session.calls == 1


def test_get_with_retries_retries_5xx_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(utils.time, "sleep", sleeps.append)
    session = _FakeSession([
        _FakeResponse(503),
        _FakeResponse(502),
        _FakeResponse(200, {"ok": 1}),
    ])
    resp = utils.get_with_retries("http://example.test/x", session=session,
                                  tries=4, base_delay=0.5)
    assert resp.status_code == 200
    assert session.calls == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential backoff: 0.5, 1.0


def test_get_with_retries_retries_connection_error(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)
    session = _FakeSession([
        requests.ConnectionError("boom"),
        _FakeResponse(200),
    ])
    resp = utils.get_with_retries("http://example.test/x", session=session)
    assert resp.status_code == 200
    assert session.calls == 2


def test_get_with_retries_fails_fast_on_404(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)
    session = _FakeSession([_FakeResponse(404)])
    with pytest.raises(requests.HTTPError):
        utils.get_with_retries("http://example.test/x", session=session)
    assert session.calls == 1  # no retry on a client error


def test_get_with_retries_propagates_last_error_after_tries(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)
    session = _FakeSession([_FakeResponse(500)] * 5)
    with pytest.raises(requests.HTTPError):
        utils.get_with_retries("http://example.test/x", session=session,
                               tries=3)
    assert session.calls == 3


def test_get_with_retries_propagates_connection_error_after_tries(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)
    session = _FakeSession([requests.ConnectionError("down")] * 5)
    with pytest.raises(requests.RequestException):
        utils.get_with_retries("http://example.test/x", session=session,
                               tries=2)
    assert session.calls == 2


def test_get_with_retries_rejects_bad_tries():
    with pytest.raises(ValueError):
        utils.get_with_retries("http://example.test/x", tries=0)


# --------------------------------------------------------------------------
# Atomic writes / JSON / provenance
# --------------------------------------------------------------------------

def test_write_bytes_atomic_writes_content_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "file.bin"
    utils.write_bytes_atomic(target, b"hello")
    assert target.read_bytes() == b"hello"
    assert list(tmp_path.iterdir()) == [target]  # no .part leftover


def test_write_bytes_atomic_replaces_existing(tmp_path: Path):
    target = tmp_path / "file.bin"
    target.write_bytes(b"old")
    utils.write_bytes_atomic(target, b"new")
    assert target.read_bytes() == b"new"


def test_write_json_and_read_json_roundtrip(tmp_path: Path):
    path = tmp_path / "nested" / "x.json"
    utils.write_json(path, {"a": 1})
    assert utils.read_json(path) == {"a": 1}
    assert utils.read_json(tmp_path / "missing.json") is None
    assert utils.read_json(tmp_path / "missing.json", default={}) == {}


def test_write_provenance_sidecar_when_no_key(tmp_path: Path):
    out = tmp_path / "zone_a.parquet"
    out.write_bytes(b"data")
    sidecar = tmp_path / "zone_a_meta.json"
    utils.write_provenance(sidecar, {"source": "Test Source", "bytes": 4})
    assert utils.read_json(sidecar) == {"source": "Test Source", "bytes": 4}


def test_write_provenance_merges_manifest_entries(tmp_path: Path):
    manifest_path = tmp_path / "_manifest.json"
    utils.write_provenance(manifest_path, {"source": "A"}, key="zone_a.parquet")
    utils.write_provenance(manifest_path, {"source": "B"}, key="zone_b.parquet")
    manifest = utils.read_json(manifest_path)
    assert manifest["zone_a.parquet"]["source"] == "A"
    assert manifest["zone_b.parquet"]["source"] == "B"


def test_write_provenance_manifest_rejects_non_object(tmp_path: Path):
    manifest_path = tmp_path / "_manifest.json"
    manifest_path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(utils.SchemaError):
        utils.write_provenance(manifest_path, {"source": "A"}, key="k")


# --------------------------------------------------------------------------
# parse_time
# --------------------------------------------------------------------------

def test_parse_time_aware_iso_is_converted_to_utc_and_not_assumed():
    ts, assumed, invalid = utils.parse_time("2026-03-01T10:30:00+03:00")
    assert not invalid and not assumed
    assert ts == pd.Timestamp("2026-03-01T10:30:00+03:00")
    assert ts.utctimetuple() == datetime(2026, 3, 1, 7, 30,
                                         tzinfo=timezone.utc).utctimetuple()


def test_parse_time_z_suffix():
    ts, assumed, invalid = utils.parse_time("2026-03-01T10:30:00Z")
    assert not invalid and not assumed
    assert ts == pd.Timestamp("2026-03-01 10:30:00", tz="UTC")


def test_parse_time_naive_string_is_assumed_utc():
    ts, assumed, invalid = utils.parse_time("2026-03-01 10:30:00")
    assert invalid is False and assumed is True
    assert ts == pd.Timestamp("2026-03-01 10:30:00", tz="UTC")


def test_parse_time_invalid_returns_none_with_invalid_flag():
    ts, assumed, invalid = utils.parse_time("not-a-date")
    assert invalid is True and assumed is False
    assert ts is None


def test_parse_time_empty_is_missing_not_invalid():
    ts, _assumed, invalid = utils.parse_time("")
    assert ts is None and invalid is False


def test_parse_time_null_returns_missing_without_invalid_flag():
    ts, assumed, invalid = utils.parse_time(None)
    assert ts is None and assumed is False and invalid is False


def test_parse_time_nan_counts_as_missing():
    ts, _assumed, invalid = utils.parse_time(float("nan"))
    assert ts is None and invalid is False


def test_parse_time_timestamp_converts_to_utc():
    stamp = pd.Timestamp("2026-03-01 10:30:00", tz="Europe/Berlin")
    ts, assumed, invalid = utils.parse_time(stamp)
    assert not assumed and not invalid
    assert ts == pd.Timestamp("2026-03-01 09:30:00", tz="UTC")


# --------------------------------------------------------------------------
# Schema helpers
# --------------------------------------------------------------------------

def test_missing_and_require_columns():
    frame = pd.DataFrame({"a": [1], "b": [2]})
    assert utils.missing_columns(frame, ["a", "b", "c"]) == ["c"]
    assert utils.missing_columns(frame, ["a"]) == []
    utils.require_columns(frame, ["a", "b"])  # no raise
    with pytest.raises(utils.SchemaError, match="missing required columns: c"):
        utils.require_columns(frame, ["a", "c"])


def test_require_columns_lists_every_missing_column():
    frame = pd.DataFrame({"a": [1]})
    with pytest.raises(utils.SchemaError) as exc:
        utils.require_columns(frame, ["a", "x", "y"])
    assert "x" in str(exc.value) and "y" in str(exc.value)


def test_is_tz_aware():
    naive = pd.Series(pd.to_datetime(["2026-01-01"]))
    aware = pd.Series(pd.to_datetime(["2026-01-01"])).dt.tz_localize("UTC")
    assert utils.is_tz_aware(naive) is False
    assert utils.is_tz_aware(aware) is True
    assert utils.is_tz_aware(pd.Series(["2026-01-01"])) is False


def test_require_tz_aware_accepts_aware_rejects_naive():
    aware = pd.Series(pd.to_datetime(["2026-01-01"])).dt.tz_localize("UTC")
    utils.require_tz_aware(aware, "start_time")  # no raise

    naive = pd.Series(pd.to_datetime(["2026-01-01"]))
    with pytest.raises(utils.SchemaError, match="timezone-naive"):
        utils.require_tz_aware(naive, "start_time")


def test_require_tz_aware_non_datetime_column_raises():
    frame_series = pd.Series(["2026-01-01"])
    with pytest.raises(utils.SchemaError, match="expected datetime"):
        utils.require_tz_aware(frame_series, "start_time")


# --------------------------------------------------------------------------
# Misc helpers
# --------------------------------------------------------------------------

def test_sha256_helpers(tmp_path: Path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    assert utils.sha256_bytes(b"abc") == utils.sha256_file(path)
    assert len(utils.sha256_bytes(b"")) == 64


def test_sanitize_filename_strips_separators():
    assert "/" not in utils.sanitize_filename("../../etc/passwd")
    assert "\\" not in utils.sanitize_filename("a/b\\c")
    assert not utils.sanitize_filename("../../etc/passwd").startswith(".")
    assert utils.sanitize_filename("  plain name.txt  ") == "__plain_name.txt__"
    assert utils.sanitize_filename("") == "unnamed"  # never empty


def test_sanitize_filename_neutralises_relative_path_components():
    # A sanitised name must never be ".." or a hidden file.
    assert utils.sanitize_filename("..") == "unnamed"
    assert utils.sanitize_filename(".") == "unnamed"
    assert not utils.sanitize_filename("..secret.json").startswith(".")


def test_utc_now_iso_is_utc():
    value = utils.utc_now_iso()
    assert value.endswith("+00:00")
    datetime.fromisoformat(value)  # parses


def test_frame_summary_shape():
    frame = pd.DataFrame({"a": [1, None], "b": ["x", "y"], "c": [None, None]})
    summary = utils.frame_summary(frame)
    assert summary["rows"] == 2
    assert summary["columns"] == ["a", "b", "c"]
    assert summary["empty_columns"] == ["c"]


def test_ensure_parent_creates_directories(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c.json"
    assert utils.ensure_parent(target) == target
    assert target.parent.is_dir()


def test_coerce_int():
    assert utils.coerce_int("42") == 42
    assert utils.coerce_int(3.0) == 3
    assert utils.coerce_int(4.7) == 4
    assert utils.coerce_int(True) == 1
    assert utils.coerce_int(None) is None
    assert utils.coerce_int("not a number") is None
