"""Provenance index for ``data/`` — one machine-readable inventory of everything on disk.

Every dataset file in ``data/raw``, ``data/processed`` and ``data/external`` is
listed with its byte size, SHA-256 and whatever provenance its sidecar records:

* ``{stem}_meta.json`` sidecars   (boundaries, outage raw intake, processed outputs)
* ``_manifest.json`` per-directory (weather, worldbank downloader records)

The result is written to ``data/manifest.json`` (kept next to the data, unlike
the per-directory sidecars which travel with their files). It answers "what is
this file, where did it come from, and has it changed?" without opening the file.

Usage:
    python -m src.ingestion.data_manifest               # rebuild data/manifest.json
    python -m src.ingestion.data_manifest --out /tmp/x.json
    python -m src.ingestion.data_manifest --quiet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from src.config import DATA_RAW, PROJECT_ROOT
from src.ingestion import utils

DEFAULT_OUT = DATA_RAW.parent / "manifest.json"

# Files that describe other files rather than being datasets themselves.
PROVENANCE_SUFFIXES = ("_meta.json", ".provenance.json")


def _is_dataset_file(path: Path) -> bool:
    name = path.name
    return bool(
        path.is_file()
        and not name.startswith("_")
        and not name.endswith(PROVENANCE_SUFFIXES)
        and name != ".gitkeep"
        and not name.endswith((".part", ".tmp"))
    )


def _sidecar_for(path: Path) -> dict[str, Any]:
    """``{stem}_meta.json`` next to the file, or {}."""
    sidecar = path.with_name(f"{path.stem}_meta.json")
    if sidecar.exists():
        meta = utils.read_json(sidecar, default={})
        return meta if isinstance(meta, dict) else {}
    return {}


def _manifest_entry(path: Path) -> dict[str, Any]:
    """Entry for this file in the directory's ``_manifest.json``, or {}."""
    manifest = utils.read_json(path.parent / "_manifest.json", default={})
    if isinstance(manifest, dict):
        entry = manifest.get(path.name)
        if isinstance(entry, dict):
            return entry
    return {}


def _provenance_for(path: Path) -> dict[str, Any]:
    """Merged provenance for one file (per-file sidecar wins over manifest)."""
    merged: dict[str, Any] = {}
    merged.update(_manifest_entry(path))
    merged.update(_sidecar_for(path))
    return merged


def build_manifest(data_root: Path, out_path: Path | None = None) -> dict[str, Any]:
    """Scan ``data_root`` and return (optionally write) the provenance index."""
    data_root = Path(data_root)
    out_path = Path(out_path) if out_path else Path(DEFAULT_OUT)
    roots = [data_root / name for name in ("raw", "processed", "external")]

    datasets: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not _is_dataset_file(path):
                continue
            provenance = _provenance_for(path)
            datasets.append(
                {
                    "path": str(path.relative_to(data_root)),
                    "bytes": path.stat().st_size,
                    "sha256": provenance.get("sha256") or utils.sha256_file(path),
                    "provenance": provenance or None,
                }
            )

    manifest = {
        "generated_utc": utils.utc_now_iso(),
        "generator": "python -m src.ingestion.data_manifest",
        # Stored relative to the project root so the manifest stays portable
        # between machines (an absolute path makes every checkout's manifest
        # differ and breaks byte-for-byte reproducibility of the artifact).
        "data_root": _portable_root(data_root),
        "file_count": len(datasets),
        "total_bytes": int(sum(d["bytes"] for d in datasets)),
        "files": datasets,
    }
    utils.write_json(out_path, manifest)
    return manifest


def _portable_root(data_root: Path) -> str:
    """Return a project-relative, POSIX-style path for ``data_root``."""
    data_root = Path(data_root).resolve()
    try:
        relative = data_root.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return data_root.as_posix()
    return relative.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=str(DATA_RAW.parent),
                        help="data directory to inventory (default: project data/)")
    parser.add_argument("--out", default=None,
                        help="output path (default: <data-root>/manifest.json)")
    parser.add_argument("--quiet", action="store_true",
                        help="do not print the summary")
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    if not data_root.exists():
        print(f"Data root {data_root} does not exist.", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else data_root / "manifest.json"
    manifest = build_manifest(data_root, out_path)
    if not args.quiet:
        with_prov = sum(1 for d in manifest["files"] if d["provenance"])
        print(f"Indexed {manifest['file_count']} dataset file(s) "
              f"({manifest['total_bytes'] / 1e6:.1f} MB), "
              f"{with_prov} with recorded provenance.")
        print(f"Manifest written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
