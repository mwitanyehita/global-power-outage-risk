"""Unit tests for src.ingestion.data_manifest (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from src.ingestion import data_manifest as dm
from src.ingestion import utils


def _seed(data_root: Path) -> None:
    raw = data_root / "raw" / "weather"
    raw.mkdir(parents=True)
    (raw / "KEN_nairobi.json").write_bytes(b'{"hourly": {}}')
    (raw / "_manifest.json").write_text(
        json.dumps({"KEN_nairobi.json": {"source": "Open-Meteo"}}),
        encoding="utf-8",
    )
    (raw / "ZAF_cape-town.json").write_bytes(b"{}")
    (raw / "ZAF_cape-town_meta.json").write_text(
        json.dumps({"source": "Open-Meteo", "license": "non-commercial"}),
        encoding="utf-8",
    )
    (raw / "partial.json.part").write_bytes(b"junk")

    ext = data_root / "external" / "boundaries"
    ext.mkdir(parents=True)
    (ext / "KEN_ADM0.geojson").write_bytes(b'{"type": "FeatureCollection"}')
    (ext / "KEN_ADM0_meta.json").write_text(
        json.dumps({"boundaryLicense": "CC BY 4.0", "sha256": "recorded"}),
        encoding="utf-8",
    )
    (ext / ".gitkeep").write_text("", encoding="utf-8")

    processed = data_root / "processed"
    processed.mkdir(parents=True)
    (processed / "outages.csv").write_text("record_id\na\n", encoding="utf-8")


def test_build_manifest_indexes_datasets_only(tmp_path: Path):
    _seed(tmp_path)
    manifest = dm.build_manifest(tmp_path, tmp_path / "manifest.json")

    paths = [d["path"] for d in manifest["files"]]
    assert sorted(paths) == sorted([
        "processed/outages.csv",
        "external/boundaries/KEN_ADM0.geojson",
        "raw/weather/KEN_nairobi.json",
        "raw/weather/ZAF_cape-town.json",
    ])
    # sidecars, manifests, .gitkeep and partial downloads are not datasets
    assert not any("_meta.json" in p or "_manifest" in p or ".gitkeep" in p
                   or p.endswith(".part") for p in paths)
    assert manifest["file_count"] == 4
    assert manifest["total_bytes"] == sum(d["bytes"] for d in manifest["files"])


def test_build_manifest_attaches_provenance(tmp_path: Path):
    _seed(tmp_path)
    manifest = dm.build_manifest(tmp_path, tmp_path / "manifest.json")
    by_path = {d["path"]: d for d in manifest["files"]}

    # _manifest.json entry
    nairobi = by_path["raw/weather/KEN_nairobi.json"]
    assert nairobi["provenance"]["source"] == "Open-Meteo"

    # per-file sidecar
    cape_town = by_path["raw/weather/ZAF_cape-town.json"]
    assert cape_town["provenance"]["license"] == "non-commercial"

    # sidecar sha256 wins; otherwise computed from bytes
    boundaries = by_path["external/boundaries/KEN_ADM0.geojson"]
    assert boundaries["sha256"] == "recorded"
    assert len(nairobi["sha256"]) == 64
    assert nairobi["sha256"] == utils.sha256_bytes(b'{"hourly": {}}')

    # file without any provenance records null
    assert by_path["processed/outages.csv"]["provenance"] is None


def test_build_manifest_writes_json_safe_output(tmp_path: Path):
    _seed(tmp_path)
    out = tmp_path / "manifest.json"
    dm.build_manifest(tmp_path, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["file_count"] == 4
    assert loaded["generated_utc"].endswith("+00:00")
    assert loaded["generator"] == "python -m src.ingestion.data_manifest"


def test_build_manifest_handles_empty_root(tmp_path: Path):
    manifest = dm.build_manifest(tmp_path / "nothing", tmp_path / "m.json")
    assert manifest["file_count"] == 0
    assert manifest["files"] == []


def test_cli_writes_manifest_and_prints_summary(tmp_path: Path, capsys):
    _seed(tmp_path)
    code = dm.main(["--data-root", str(tmp_path),
                    "--out", str(tmp_path / "manifest.json")])
    assert code == 0
    out = capsys.readouterr().out
    assert "Indexed 4 dataset file(s)" in out
    assert (tmp_path / "manifest.json").exists()


def test_cli_quiet_mode(tmp_path: Path, capsys):
    _seed(tmp_path)
    code = dm.main(["--data-root", str(tmp_path),
                    "--out", str(tmp_path / "manifest.json"), "--quiet"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_cli_missing_data_root_returns_1(tmp_path: Path, capsys):
    assert dm.main(["--data-root", str(tmp_path / "nope")]) == 1
    assert "does not exist" in capsys.readouterr().err
