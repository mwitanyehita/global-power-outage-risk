"""Tests for src.ingestion.boundaries.

Geometry tests require the Day 2 downloads in data/external/boundaries/
(git-ignored); they skip automatically when the files are absent.

The synthetic-geometry tests below always run: they exercise bbox/unit
invariants, metadata-free inspection, and the network-free skip path of
``download`` without touching the internet.
"""

import datetime as dt
import json

import pytest

from src.ingestion import boundaries as bnd


def test_as_int_coerces_api_strings():
    assert bnd._as_int("47") == 47
    assert bnd._as_int(37) == 37
    assert bnd._as_int(None) is None
    assert bnd._as_int("not-a-number") is None


def test_pilot_and_level_constants():
    assert bnd.PILOT_COUNTRIES == ("KEN", "ZAF", "NGA")
    assert bnd.DEFAULT_ADM_LEVELS == ("ADM0", "ADM1", "ADM2")
    for iso3 in bnd.PILOT_COUNTRIES:
        for adm in ("ADM0", "ADM1", "ADM2"):
            assert (iso3, adm) in bnd.EXPECTED_UNITS


def test_expected_bbox_covers_pilot_capitals():
    """Each pilot's sanity bbox must contain a well-known city coordinate.

    Guards against a typo'd envelope that would flag every real download
    as out-of-bbox (or, worse, accept grossly wrong geometry).
    """
    capitals = {
        "KEN": (36.8, -1.3),   # Nairobi
        "ZAF": (18.4, -33.9),  # Cape Town
        "NGA": (3.4, 6.5),     # Lagos
    }
    assert set(capitals) == set(bnd.PILOT_COUNTRIES)
    for iso3, (lon, lat) in capitals.items():
        west, south, east, north = bnd.EXPECTED_BBOX[iso3]
        assert west <= lon <= east, f"{iso3}: lon {lon} outside {bnd.EXPECTED_BBOX[iso3]}"
        assert south <= lat <= north, f"{iso3}: lat {lat} outside {bnd.EXPECTED_BBOX[iso3]}"


def test_utc_now_is_aware_utc_iso_string():
    parsed = dt.datetime.fromisoformat(bnd._utc_now())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == dt.timedelta(0)


def test_looks_like_geojson_rejects_junk_and_accepts_featurecollection():
    assert bnd._looks_like_geojson(
        b'{"type": "FeatureCollection", "features": [{"type": "Feature"}]}'
    )
    assert not bnd._looks_like_geojson(b"not json at all")
    assert not bnd._looks_like_geojson(b'{"type": "FeatureCollection", "features": []}')
    assert not bnd._looks_like_geojson(b'[1, 2, 3]')


def _downloaded_files():
    """Boundary files on disk; empty list when downloads are absent."""
    return sorted(bnd.BOUNDARY_DIR.glob("*.geojson"))


# Parametrized at import time so the suite still collects cleanly on a
# fresh clone where data/external/boundaries/ does not exist yet.
_BOUNDARY_FILES = _downloaded_files() or [None]


@pytest.mark.parametrize("path", _BOUNDARY_FILES, ids=lambda p: p.stem if p else "missing")
def test_inspect_downloaded_boundaries(path):
    if path is None:
        pytest.skip("boundary downloads not present (run: python -m src.ingestion.boundaries)")
    info = bnd.inspect(path)

    assert info["crs"] == "EPSG:4326"
    assert info["null_geom"] == 0
    assert info["empty_geom"] == 0
    assert info["invalid_geom"] == 0
    assert info["bbox_ok"] is True
    assert info["count_ok"] is True
    assert info["rows"] == info["meta_units"] == info["expected_units"]
    assert info["license"] != "?"


@pytest.mark.parametrize(
    "iso3,adm",
    [(iso3, adm) for iso3 in bnd.PILOT_COUNTRIES for adm in ("ADM0", "ADM1")],
)
def test_metadata_files_exist_and_are_verbatim_json(iso3, adm):
    meta_path = bnd.BOUNDARY_DIR / f"{iso3}_{adm}_meta.json"
    geojson_path = bnd.BOUNDARY_DIR / f"{iso3}_{adm}.geojson"
    if not meta_path.exists():
        pytest.skip("downloads not present")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["boundaryISO"] == iso3
    assert meta["boundaryType"] == adm
    assert "_retrieved_utc" in meta
    assert meta.get("gjDownloadURL")
    # metadata and geometry files are written together
    assert geojson_path.exists()


def test_write_audit_creates_report(tmp_path):
    files = [p for p in bnd.BOUNDARY_DIR.glob("*.geojson")]
    if not files:
        pytest.skip("downloads not present")
    out = tmp_path / "audit.md"
    result = bnd.write_audit(files, out_path=out)
    text = result.read_text(encoding="utf-8")
    assert "# Day 2 audit" in text
    assert "## Findings" in text
    assert "EPSG:4326" in text


def _synthetic_boundary(tmp_path, stem, names, meta_units, boxes=None):
    """Write a tiny GeoJSON + metadata pair that mimics a real download."""
    import geopandas as gpd
    from shapely.geometry import box

    if boxes is None:
        boxes = [box(34 + i * 0.1, -1, 34.05 + i * 0.1, -0.5) for i in range(len(names))]
    gdf = gpd.GeoDataFrame({"shapeName": names}, geometry=boxes, crs=4326)
    path = tmp_path / f"{stem}.geojson"
    gdf.to_file(path, driver="GeoJSON")
    meta = {
        "admUnitCount": str(meta_units),
        "boundaryLicense": "Test License",
        "boundarySource": "synthetic",
        "boundaryYearRepresented": 2020,
        "gjDownloadURL": "https://example.invalid/x.geojson",
        "_retrieved_utc": "2026-01-01T00:00:00+00:00",
    }
    path.with_name(f"{stem}_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_inspect_accepts_consistent_synthetic_boundary(tmp_path):
    path = _synthetic_boundary(tmp_path, "ZAF_ADM1", [f"Province {i}" for i in range(9)], 9)
    info = bnd.inspect(path)
    assert info["count_ok"] is True
    assert info["rows"] == 9
    assert info["dup_names"] == 0
    assert info["license"] == "Test License"


def test_inspect_flags_count_and_name_problems(tmp_path):
    path = _synthetic_boundary(tmp_path, "KEN_ADM1", ["Nairobi", "Nairobi", "Mombasa"], 47)
    info = bnd.inspect(path)
    assert info["count_ok"] is False
    assert info["dup_names"] == 1
    assert info["bbox_ok"] is True


def test_inspect_without_metadata_sidecar_reports_unknown_counts(tmp_path):
    """No sidecar -> count_ok is None (unknown), license falls back to '?'."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"shapeName": ["Somewhere"]}, geometry=[box(34.0, -1.0, 34.5, -0.5)], crs=4326
    )
    path = tmp_path / "KEN_ADM1.geojson"
    gdf.to_file(path, driver="GeoJSON")  # deliberately no *_meta.json sidecar

    info = bnd.inspect(path)
    assert info["count_ok"] is None
    assert info["meta_units"] is None
    assert info["license"] == "?"
    assert info["source"] == "?"
    assert info["bbox_ok"] is True


def test_inspect_flags_geometry_outside_expected_bbox(tmp_path):
    """A KEN polygon in the Gulf of Guinea must fail the bbox sanity check."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"shapeName": ["Wrong"]}, geometry=[box(4.0, 5.0, 5.0, 6.0)], crs=4326
    )
    path = tmp_path / "KEN_ADM1.geojson"
    gdf.to_file(path, driver="GeoJSON")

    info = bnd.inspect(path)
    assert info["bbox_ok"] is False


def test_download_skips_valid_existing_file_without_network(tmp_path, monkeypatch):
    """A complete existing download must short-circuit before any HTTP call."""
    path = _synthetic_boundary(tmp_path, "KEN_ADM0", ["Kenya"], 1)

    def _fail(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("fetch_metadata must not be called on the skip path")

    monkeypatch.setattr(bnd, "fetch_metadata", _fail)
    result = bnd.download("KEN", "ADM0", dest=tmp_path, overwrite=False)
    assert result == path
    assert path.read_bytes()  # untouched
