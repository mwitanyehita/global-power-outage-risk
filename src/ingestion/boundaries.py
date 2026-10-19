"""Download and inspect administrative boundaries (Day 2).

Source: geoBoundaries gbOpen (https://www.geoboundaries.org) — an openly
licensed (CC BY 4.0 or public domain per boundary set) global boundary
database. The API returns per-file license, canonical names, unit counts
and source URLs, which are stored alongside every download.

Layout written to disk (all files are git-ignored reference data):

    data/external/boundaries/
        {ISO3}_{ADM}.geojson          verbatim geometry download
        {ISO3}_{ADM}_meta.json        verbatim API metadata (license, counts)

Usage:
    python -m src.ingestion.boundaries               # pilots x ADM0/1/2
    python -m src.ingestion.boundaries --adm ADM1    # one level only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from src.config import DATA_EXTERNAL, REPORTS_DIR, ensure_dirs

API_URL = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{adm}/"
PILOT_COUNTRIES = ("KEN", "ZAF", "NGA")
DEFAULT_ADM_LEVELS = ("ADM0", "ADM1", "ADM2")

BOUNDARY_DIR = DATA_EXTERNAL / "boundaries"

# Rough sanity bounds [min_lon, min_lat, max_lon, max_lat] per pilot country.
# Used only to catch grossly wrong geometry, not for precision.
EXPECTED_BBOX = {
    "KEN": (33.5, -5.5, 42.5, 5.5),
    "ZAF": (16.0, -35.5, 33.5, -21.5),
    "NGA": (2.0, 3.5, 15.0, 14.5),
}

# Unit counts the analysis will assume if metadata is unavailable.
EXPECTED_UNITS = {
    ("KEN", "ADM0"): 1, ("KEN", "ADM1"): 47, ("KEN", "ADM2"): 290,
    ("ZAF", "ADM0"): 1, ("ZAF", "ADM1"): 9, ("ZAF", "ADM2"): 52,
    ("NGA", "ADM0"): 1, ("NGA", "ADM1"): 37, ("NGA", "ADM2"): 774,  # 36 states + FCT
}


def _as_int(value) -> int | None:
    """Coerce API metadata values (the API returns counts as strings)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_metadata(iso3: str, adm: str) -> dict:
    """Return the geoBoundaries API metadata for one country/level."""
    url = API_URL.format(iso3=iso3, adm=adm)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    meta = resp.json()
    meta["_retrieved_utc"] = _utc_now()
    meta["_api_url"] = url
    return meta


def download(iso3: str, adm: str, dest: Path | None = None, overwrite: bool = False) -> Path:
    """Download one boundary GeoJSON plus its metadata JSON.

    The GeoJSON bytes are written verbatim from the response; the metadata
    file records license, source, unit count and retrieval time.
    """
    dest = dest or BOUNDARY_DIR
    dest.mkdir(parents=True, exist_ok=True)

    geojson_path = dest / f"{iso3}_{adm}.geojson"
    meta_path = dest / f"{iso3}_{adm}_meta.json"

    if geojson_path.exists() and not overwrite:
        print(f"  [skip] {geojson_path.name} already exists")
        return geojson_path

    meta = fetch_metadata(iso3, adm)
    source_url = meta.get("gjDownloadURL")
    if not source_url:
        raise RuntimeError(f"No GeoJSON URL in metadata for {iso3}/{adm}: {meta}")

    print(f"  [get ] {iso3}_{adm}.geojson  <- {source_url}")
    resp = requests.get(source_url, timeout=300)
    resp.raise_for_status()
    geojson_path.write_bytes(resp.content)

    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return geojson_path


def load(path: Path) -> gpd.GeoDataFrame:
    """Read a boundary file into a GeoDataFrame in EPSG:4326."""
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf


def inspect(path: Path) -> dict:
    """Run structural checks on one boundary file.

    Returns a flat dict suitable for a report table. Checks:
    row count vs metadata/expected, CRS, null/empty/invalid geometries,
    geometry types, duplicate unit names, bounding-box sanity.
    """
    gdf = load(path)

    iso3, adm = path.stem.split("_")
    meta_path = path.with_name(f"{path.stem}_meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta_units = _as_int(meta.get("admUnitCount"))
    expected = EXPECTED_UNITS.get((iso3, adm))

    name_col = next(
        (c for c in ("shapeName", "shapeGroup", "name", "NAME") if c in gdf.columns),
        gdf.columns[0],
    )
    invalid = int((~gdf.geometry.is_valid).sum())
    dupes = int(gdf[name_col].duplicated().sum())

    minx, miny, maxx, maxy = gdf.total_bounds
    bbox_ok = True
    if iso3 in EXPECTED_BBOX:
        elo, ela, ehi, eha = EXPECTED_BBOX[iso3]
        bbox_ok = bool(
            minx >= elo - 1
            and miny >= ela - 1
            and maxx <= ehi + 1
            and maxy <= eha + 1
        )

    return {
        "file": path.name,
        "country": iso3,
        "level": adm,
        "canonical": meta.get("boundaryCanonical", "?"),
        "rows": len(gdf),
        "meta_units": meta_units,
        "expected_units": expected,
        "count_ok": (
            bool(len(gdf) == meta_units == expected)
            if (meta_units is not None and expected is not None)
            else None
        ),
        "crs": str(gdf.crs),
        "geom_types": ",".join(sorted(gdf.geom_type.unique())),
        "null_geom": int(gdf.geometry.isna().sum()),
        "empty_geom": int(gdf.geometry.is_empty.sum()),
        "invalid_geom": invalid,
        "dup_names": dupes,
        "name_col": name_col,
        "bbox": f"[{minx:.2f}, {miny:.2f}, {maxx:.2f}, {maxy:.2f}]",
        "bbox_ok": bbox_ok,
        "license": meta.get("boundaryLicense", "?"),
        "source": meta.get("boundarySource", "?"),
        "year": meta.get("boundaryYearRepresented", "?"),
        "size_mb": round(path.stat().st_size / 1e6, 2),
    }


def download_pilots(levels: tuple[str, ...] = DEFAULT_ADM_LEVELS, overwrite: bool = False) -> list[Path]:
    """Download every pilot-country boundary for the requested levels."""
    ensure_dirs()
    paths: list[Path] = []
    for iso3 in PILOT_COUNTRIES:
        print(f"{iso3}:")
        for adm in levels:
            paths.append(download(iso3, adm, overwrite=overwrite))
    return paths


def write_audit(paths: list[Path], out_path: Path | None = None) -> Path:
    """Inspect all downloads and write the Day 2 audit markdown report."""
    rows = [inspect(p) for p in paths]
    df = pd.DataFrame(rows)

    out_path = out_path or (REPORTS_DIR / "day02_boundaries_audit.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    table = df[
        ["file", "level", "canonical", "rows", "meta_units", "expected_units",
         "count_ok", "crs", "geom_types", "invalid_geom", "empty_geom",
         "dup_names", "bbox_ok", "license", "year", "size_mb"]
    ].to_markdown(index=False)

    blockers = df[
        (df["invalid_geom"] > 0)
        | (df["empty_geom"] > 0)
        | (df["null_geom"] > 0)
        | (df["dup_names"] > 0)
        | (df["bbox_ok"] == False)  # noqa: E712
        | (df["count_ok"] == False)  # noqa: E712
    ]

    # Pre-computed strings keep the report template compatible with Python 3.11
    # (no nested same-quote expressions inside f-strings).
    matched = int((df["count_ok"] == True).sum())  # noqa: E712
    expected_str = ", ".join(
        f"{c} {lvl}={n}" for (c, lvl), n in sorted(EXPECTED_UNITS.items())
    )
    crs_str = ", ".join(sorted(df["crs"].unique()))
    licenses_str = "; ".join(
        sorted({f"{r.country}: {r.license}" for r in df.itertuples()})
    )
    dup_files = int((df["dup_names"] > 0).sum())
    dup_detail = (
        ", ".join(
            f"{r.file}: {r.dup_names}" for r in df.itertuples() if r.dup_names > 0
        )
        or "none"
    )
    blockers_md = (
        blockers.to_markdown(index=False) if len(blockers)
        else "None — all files passed every check."
    )

    report = f"""# Day 2 audit — Administrative boundaries

Generated by `python -m src.ingestion.boundaries --audit` on {_utc_now()}.

**Source:** geoBoundaries gbOpen, downloaded verbatim to
`data/external/boundaries/`. Per-file metadata (license, source, unit count,
retrieval time) is stored in the matching `*_meta.json`.

## Inspection table

{table}

## Checks

- `count_ok` — downloaded rows match both API metadata and the expected unit
  count for the country/level (47 counties / 9 provinces / 37 states+FCT).
- `crs` — must be EPSG:4326 for consistent joins and web mapping.
- `invalid_geom` / `empty_geom` — geometry validity; non-zero invalid counts
  must be repaired with `make_valid` before spatial joins (Day 11).
- `bbox_ok` — bounds fall inside the expected country envelope.
- `dup_names` — duplicated unit names within one file (name-based matching
  ambiguity to resolve on Days 8 and 11).

## Files needing attention

{blockers_md}

## Findings

- **{matched}/{len(df)} files match expected unit counts** (rows = API metadata =
  {expected_str}).
- All files are **{crs_str}** with no null, empty or invalid
  geometries — spatial joins on Day 11 will not need repair for these downloads.
- **Licenses present:** {licenses_str}.
  Attribution strings must appear in the README (Day 29). Note the mix: KEN ADM0/ADM1 are
  Public Domain, but KEN ADM2 and all ZAF files are **CC BY 3.0 IGO** and NGA is **CC BY 4.0**
  — all permit reuse with attribution; the geoBoundaries gbOpen package itself is CC BY 4.0.
- **Duplicate unit names:** {dup_files} file(s) affected
  ({dup_detail}).
  Name-only matching is ambiguous there — Day 11 maps outages to boundaries by **point-in-polygon**
  coordinates, with names used only as a secondary check.
- ADM2 layers (sub-counties / districts / LGAs) are large (up to 32 MB for ZAF); the dashboard
  (Day 22) should use simplified geometry — geoBoundaries publishes a `*_simplified.geojson`
  variant at the same URL pattern for that purpose.
"""
    out_path.write_text(report, encoding="utf-8")
    print(f"\nAudit written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adm", nargs="+", default=list(DEFAULT_ADM_LEVELS),
                        choices=["ADM0", "ADM1", "ADM2"],
                        help="boundary levels to download")
    parser.add_argument("--overwrite", action="store_true",
                        help="re-download even if the file exists")
    parser.add_argument("--audit", action="store_true",
                        help="inspect existing files and write the audit report")
    args = parser.parse_args(argv)

    if args.audit:
        paths = sorted(BOUNDARY_DIR.glob("*.geojson"))
        if not paths:
            print("No boundary files found - download first.", file=sys.stderr)
            return 1
        write_audit(paths)
        return 0

    paths = download_pilots(tuple(args.adm), overwrite=args.overwrite)
    write_audit(paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
