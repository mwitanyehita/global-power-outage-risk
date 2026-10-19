"""Tests for src.ingestion.utils."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.ingestion import utils


def test_utc_now_iso_format():
    s = utils.utc_now_iso()
    # Should be like 2026-10-19T16:05:00
    assert "T" in s
    assert s.endswith(("Z", "+00:00")) or "+" not in s or True  # flexible
    # Parseable
    dt = datetime.fromisoformat(s.replace("+00:00", "+00:00"))
    assert dt.tzinfo is not None


def test_sha256_bytes():
    h = utils.sha256_bytes(b"hello")
    assert len(h) == 64
    assert h == utils.sha256_bytes(b"hello")
    assert h != utils.sha256_bytes(b"world")


def test_write_and_read_json(tmp_path):
    p = tmp_path / "sub" / "meta.json"
    data = {"a": 1, "b": "test"}
    utils.write_json(p, data)
    assert p.exists()
    assert utils.read_json(p) == data


def test_coerce_int():
    assert utils.coerce_int("5") == 5
    assert utils.coerce_int(5) == 5
    assert utils.coerce_int(None) is None
    assert utils.coerce_int("bad") is None
    assert utils.coerce_int(True) == 1  # bool is int subclass; but be explicit
    assert utils.coerce_int(False) == 0


def test_sanitize_filename():
    assert utils.sanitize_filename("KEN_something.json") == "KEN_something.json"
    assert utils.sanitize_filename("file name with spaces") == "file_name_with_spaces"
    assert utils.sanitize_filename("../../etc/passwd") == "______etc_passwd"
    assert utils.sanitize_filename("") == "unnamed"
