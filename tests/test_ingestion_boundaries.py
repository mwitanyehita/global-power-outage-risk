"""Unit tests for the hardened src.ingestion.boundaries downloader (no network).

``tests/test_boundaries.py`` covers the original inspection/audit behaviour;
this file covers the robustness guarantees added to the downloader: payload
validation, atomic writes, idempotent re-runs, sidecar self-healing and
per-file failure collection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion import boundaries
from src.ingestion import utils

GEOJSON_BYTES = json.dumps({
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"shapeName": "Nairobi"},
        "geometry": {"type": "Point", "coordinates": [36.82, -1.29]},
    }],
}).encode()


class _Resp:
    def __init__(self, content: bytes = b"", payload: dict | None = None):
        self.content = content
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def _routes(**overrides) -> tuple[dict, list[str]]:
    """URL -> response routes plus a call log; returns (routes, calls)."""
    api = boundaries.API_URL.format(iso3="KEN", adm="ADM0")
    routes = {
        api: _Resp(payload={
            "gjDownloadURL": "http://example.test/ken_adm0.geojson",
            "boundaryLicense": "CC BY 4.0",
            "boundaryCanonical": "Kenya ADM0",
            "admUnitCount": "1",
        }),
        "http://example.test/ken_adm0.geojson": _Resp(content=GEOJSON_BYTES),
    }
    routes.update(overrides)
    return routes, []


def _patch_get(monkeypatch, routes: dict, calls: list[str]):
    def fake_get(url, **kwargs):  # noqa: ARG001
        calls.append(url)
        outcome = routes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(boundaries.utils, "get_with_retries", fake_get)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_download_writes_geometry_and_meta_sidecar(tmp_path, monkeypatch):
    routes, calls = _routes()
    _patch_get(monkeypatch, routes, calls)

    path = boundaries.download("KEN", "ADM0", dest=tmp_path)
    assert path == tmp_path / "KEN_ADM0.geojson"
    assert path.read_bytes() == GEOJSON_BYTES
    assert not list(tmp_path.glob("*.part"))  # atomic write left no temp

    meta = utils.read_json(tmp_path / "KEN_ADM0_meta.json")
    assert meta["boundaryLicense"] == "CC BY 4.0"
    assert meta["_sha256"] == utils.sha256_bytes(GEOJSON_BYTES)
    assert meta["_retrieved_utc"].endswith("+00:00")
    assert meta["_api_url"] == boundaries.API_URL.format(iso3="KEN", adm="ADM0")


def test_download_skips_valid_existing_file_without_network(tmp_path, monkeypatch):
    (tmp_path / "KEN_ADM0.geojson").write_bytes(GEOJSON_BYTES)
    (tmp_path / "KEN_ADM0_meta.json").write_text("{}", encoding="utf-8")

    def no_network(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("network must not be touched for a valid file")

    monkeypatch.setattr(boundaries.utils, "get_with_retries", no_network)
    before = (tmp_path / "KEN_ADM0.geojson").read_bytes()

    path = boundaries.download("KEN", "ADM0", dest=tmp_path)
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# Self-healing and corrupt leftovers
# --------------------------------------------------------------------------

def test_download_restores_missing_sidecar_only(tmp_path, monkeypatch):
    (tmp_path / "KEN_ADM0.geojson").write_bytes(GEOJSON_BYTES)
    routes, calls = _routes()
    _patch_get(monkeypatch, routes, calls)

    path = boundaries.download("KEN", "ADM0", dest=tmp_path)

    api = boundaries.API_URL.format(iso3="KEN", adm="ADM0")
    assert calls == [api]  # metadata refetched, geometry NOT re-downloaded
    meta = utils.read_json(tmp_path / "KEN_ADM0_meta.json")
    assert meta["_sha256"] == utils.sha256_bytes(GEOJSON_BYTES)
    assert path.read_bytes() == GEOJSON_BYTES


def test_download_redownloads_corrupt_existing_file(tmp_path, monkeypatch):
    (tmp_path / "KEN_ADM0.geojson").write_bytes(b'{"type": "FeatureCollection"')
    routes, calls = _routes()
    _patch_get(monkeypatch, routes, calls)

    boundaries.download("KEN", "ADM0", dest=tmp_path)

    assert (tmp_path / "KEN_ADM0.geojson").read_bytes() == GEOJSON_BYTES
    assert len(calls) == 2  # metadata + geometry


def test_download_redownloads_valid_json_without_features(tmp_path, monkeypatch):
    (tmp_path / "KEN_ADM0.geojson").write_bytes(b'{"type": "FeatureCollection", "features": []}')
    routes, calls = _routes()
    _patch_get(monkeypatch, routes, calls)

    boundaries.download("KEN", "ADM0", dest=tmp_path)
    assert (tmp_path / "KEN_ADM0.geojson").read_bytes() == GEOJSON_BYTES


# --------------------------------------------------------------------------
# Rejection of bad payloads — nothing lands on disk
# --------------------------------------------------------------------------

def test_download_rejects_non_geojson_payload(tmp_path, monkeypatch):
    api = boundaries.API_URL.format(iso3="KEN", adm="ADM0")
    routes, calls = _routes(**{
        "http://example.test/ken_adm0.geojson": _Resp(content=b"<html>captive portal</html>"),
    })
    _patch_get(monkeypatch, routes, calls)

    with pytest.raises(RuntimeError, match="not a non-empty GeoJSON"):
        boundaries.download("KEN", "ADM0", dest=tmp_path)

    assert not (tmp_path / "KEN_ADM0.geojson").exists()
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_empty_features_payload(tmp_path, monkeypatch):
    routes, _ = _routes(**{
        "http://example.test/ken_adm0.geojson": _Resp(
            content=b'{"type": "FeatureCollection", "features": []}'),
    })
    _patch_get(monkeypatch, routes, [])

    with pytest.raises(RuntimeError, match="not a non-empty GeoJSON"):
        boundaries.download("KEN", "ADM0", dest=tmp_path)
    assert not (tmp_path / "KEN_ADM0.geojson").exists()


def test_download_requires_geojson_url_in_metadata(tmp_path, monkeypatch):
    api = boundaries.API_URL.format(iso3="KEN", adm="ADM0")
    routes, _ = _routes(**{api: _Resp(payload={"boundaryLicense": "CC0"})})
    _patch_get(monkeypatch, routes, [])

    with pytest.raises(RuntimeError, match="No GeoJSON URL"):
        boundaries.download("KEN", "ADM0", dest=tmp_path)
    assert not (tmp_path / "KEN_ADM0.geojson").exists()


def test_download_requires_dict_metadata_payload(tmp_path, monkeypatch):
    api = boundaries.API_URL.format(iso3="KEN", adm="ADM0")
    routes, _ = _routes(**{api: _Resp(payload=None)})
    routes[api] = _Resp(content=b"[]")
    routes[api]._payload = []  # json() returns a list, not a dict
    _patch_get(monkeypatch, routes, [])

    with pytest.raises(RuntimeError, match="metadata payload"):
        boundaries.download("KEN", "ADM0", dest=tmp_path)


# --------------------------------------------------------------------------
# _looks_like_geojson unit checks
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload,expected", [
    ({"type": "FeatureCollection", "features": [{"type": "Feature"}]}, True),
    ({"type": "Feature", "features": [{"type": "Feature"}]}, True),
    ({"type": "FeatureCollection", "features": []}, False),
    ({"type": "FeatureCollection"}, False),
    ({"features": [{"type": "Feature"}]}, False),
    ([{"type": "Feature"}], False),
    ("FeatureCollection", False),
])
def test_looks_like_geojson(payload, expected):
    assert boundaries._looks_like_geojson(json.dumps(payload).encode()) is expected


def test_looks_like_geojson_rejects_non_json_and_binary():
    assert boundaries._looks_like_geojson(b"<html>not json</html>") is False
    assert boundaries._looks_like_geojson(b"\xff\xfe\x00\x01") is False
    assert boundaries._looks_like_geojson(b"") is False


# --------------------------------------------------------------------------
# Batch behaviour and CLI exit codes
# --------------------------------------------------------------------------

def test_download_pilots_collects_per_file_failures(tmp_path, monkeypatch):
    boundaries.download_failures.append("stale entry from a previous run")

    def fake_download(iso3, adm, dest=None, overwrite=False, **kwargs):
        if iso3 == "ZAF":
            raise RuntimeError("simulated failure")
        return tmp_path / f"{iso3}_{adm}.geojson"

    monkeypatch.setattr(boundaries, "download", fake_download)
    monkeypatch.setattr(boundaries, "write_audit", lambda paths, out=None: Path("ignored"))

    paths = boundaries.download_pilots(("ADM0",), overwrite=False)

    assert len(paths) == 2  # KEN + NGA succeeded despite ZAF failing
    assert boundaries.download_failures == ["ZAF_ADM0: simulated failure"]


def test_main_returns_1_when_some_downloads_fail(tmp_path, monkeypatch):
    def fake_download(iso3, adm, dest=None, overwrite=False, **kwargs):
        if iso3 == "NGA":
            raise RuntimeError("simulated failure")
        return tmp_path / f"{iso3}_{adm}.geojson"

    audited: list[list] = []
    monkeypatch.setattr(boundaries, "download", fake_download)
    monkeypatch.setattr(
        boundaries, "write_audit",
        lambda paths, out=None: audited.append(list(paths)) or Path("x"),
    )

    code = boundaries.main(["--adm", "ADM0"])
    assert code == 1
    assert audited and len(audited[0]) == 2  # audit still ran for successes


def test_main_returns_0_when_everything_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(
        boundaries, "download",
        lambda iso3, adm, **kwargs: tmp_path / f"{iso3}_{adm}.geojson",
    )
    monkeypatch.setattr(boundaries, "write_audit", lambda paths, out=None: Path("x"))
    assert boundaries.main(["--adm", "ADM1"]) == 0


def test_main_returns_1_when_no_files_downloaded(monkeypatch, capsys):
    monkeypatch.setattr(boundaries, "download_pilots", lambda *a, **k: [])
    assert boundaries.main(["--adm", "ADM0"]) == 1
    assert "No boundary files downloaded" in capsys.readouterr().err


def test_main_audit_returns_1_without_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(boundaries, "BOUNDARY_DIR", tmp_path)
    assert boundaries.main(["--audit"]) == 1
    assert "download first" in capsys.readouterr().err


def test_main_audit_uses_existing_files(tmp_path, monkeypatch):
    (tmp_path / "KEN_ADM0.geojson").write_bytes(GEOJSON_BYTES)
    monkeypatch.setattr(boundaries, "BOUNDARY_DIR", tmp_path)
    written: list[Path] = []
    monkeypatch.setattr(
        boundaries, "write_audit",
        lambda paths, out=None: written.extend(paths) or Path("x"),
    )
    assert boundaries.main(["--audit"]) == 0
    assert written == [tmp_path / "KEN_ADM0.geojson"]
