"""Canonical administrative unit registry + duplicate analysis (Day 12).

Builds one registry row per admin unit (ADM0–ADM2 × KEN/ZAF/NGA) with:

* ``unit_id``     — stable geoBoundaries ``shapeID``
* ``name``        — verbatim ``shapeName``
* ``name_norm``   — :func:`src.cleaning.standardize.normalize_unit_name` key
* ``match_key``   — normalized + admin-suffix-stripped candidate-match key
* ``rep_lon/rep_lat`` — point-on-surface representative point (used for
  centroid-distance tie-breaks and quick spatial sanity checks; the
  authoritative geography join is still point-in-polygon on Day 11)
* ``dup_key_count`` / ``is_duplicate_key`` — duplicate detection

Outputs: ``data/processed/unit_registry.csv`` and
``reports/day12_duplicate_units.md``.

Why this exists (README risk register): 5 duplicated LGA names exist in
NGA_ADM2 (Day 2 audit), and name-only matching across notices/boundaries is
ambiguous. The registry makes every ambiguity *visible* before Day 11 joins,
and records the resolution rule: **coordinates or parent context decide;
names only propose candidates.**

Usage:
    python -m src.geo.units                # registry + report
    python -m src.geo.units --registry-only
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.cleaning.standardize import name_match_key, normalize_unit_name
from src.config import DATA_PROCESSED, REPORTS_DIR, ensure_dirs
from src.ingestion import boundaries as bnd

LEVELS = ("ADM0", "ADM1", "ADM2")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_units(levels: tuple[str, ...] = LEVELS) -> gpd.GeoDataFrame:
    """Load every available pilot boundary into one annotated GeoDataFrame."""
    frames: list[gpd.GeoDataFrame] = []
    for iso3 in bnd.PILOT_COUNTRIES:
        for level in levels:
            path = bnd.BOUNDARY_DIR / f"{iso3}_{level}.geojson"
            if not path.exists():
                continue
            gdf = bnd.load(path)
            name_col = next((c for c in gdf.columns if c.lower() == "shapename"), None)
            id_col = "shapeID" if "shapeID" in gdf.columns else None
            parent_col = "shapeParent" if "shapeParent" in gdf.columns else None

            out = gpd.GeoDataFrame(
                {
                    "iso3": iso3,
                    "adm_level": level,
                    "unit_id": (
                        gdf[id_col].astype(str) if id_col
                        else pd.Series([f"{iso3}-{level}-{i:04d}" for i in range(len(gdf))])
                    ),
                    "name": gdf[name_col].astype(str) if name_col
                    else pd.Series([f"unit_{i}" for i in range(len(gdf))]),
                    "parent": (
                        gdf[parent_col].astype(str) if parent_col
                        else pd.Series([""] * len(gdf))
                    ),
                },
                geometry=gdf.geometry,
                crs=gdf.crs,
            )
            frames.append(out)

    if not frames:
        return gpd.GeoDataFrame(
            columns=["iso3", "adm_level", "unit_id", "name", "parent",
                     "name_norm", "match_key", "rep_lon", "rep_lat",
                     "dup_key_count", "is_duplicate_key", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    units = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    units["name_norm"] = units["name"].map(normalize_unit_name)
    units["match_key"] = units["name"].map(name_match_key)
    rep = units.geometry.representative_point()
    units["rep_lon"] = rep.x
    units["rep_lat"] = rep.y
    return flag_duplicates(units)


def flag_duplicates(units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Flag units sharing a match key within (iso3, adm_level).

    Empty match keys (missing names) are never grouped together — an absent
    name must not collapse every unnamed unit into one duplicate cluster.
    """
    units = units.copy()
    scope = ["iso3", "adm_level"]
    keyed = units["match_key"].ne("")
    counts = units.groupby(scope + ["match_key"])["unit_id"].transform("size")
    units["dup_key_count"] = counts.where(keyed, 0).fillna(0).astype(int)
    units["is_duplicate_key"] = (units["dup_key_count"] > 1) & keyed
    return units


def write_registry(units: gpd.GeoDataFrame,
                   out_path: Path | None = None) -> Path:
    """Write the geometry-free registry CSV (geometry stays in raw boundaries)."""
    ensure_dirs()
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out_path = out_path or (DATA_PROCESSED / "unit_registry.csv")
    columns = ["iso3", "adm_level", "unit_id", "name", "parent", "name_norm",
               "match_key", "rep_lon", "rep_lat", "dup_key_count",
               "is_duplicate_key"]
    units[columns].to_csv(out_path, index=False, encoding="utf-8")
    return out_path


def duplicate_report_frame(units: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per duplicated match key with the units that collide."""
    dup = units[units["is_duplicate_key"]]
    if dup.empty:
        return pd.DataFrame(columns=["iso3", "adm_level", "match_key",
                                     "n_units", "names", "parents"])
    rows = []
    for (iso3, level, key), grp in dup.groupby(["iso3", "adm_level", "match_key"]):
        rows.append({
            "iso3": iso3,
            "adm_level": level,
            "match_key": key,
            "n_units": len(grp),
            "names": " | ".join(sorted(grp["name"].unique())),
            "parents": " | ".join(sorted(p for p in grp["parent"].unique() if p)) or "-",
        })
    return pd.DataFrame(rows).sort_values(
        ["iso3", "adm_level", "n_units"], ascending=[True, True, False]
    ).reset_index(drop=True)


def key_collapses(units: gpd.GeoDataFrame) -> pd.DataFrame:
    """Distinct verbatim names that share one match key (suffix stripping
    collapses them) — the other ambiguity class besides true duplicates."""
    grouped = units[units["match_key"] != ""].groupby(
        ["iso3", "adm_level", "match_key"]
    )["name"].nunique()
    collapsed = grouped[grouped > 1]
    return collapsed.rename("n_distinct_names").reset_index()


def write_report(units: gpd.GeoDataFrame, out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day12_duplicate_units.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if units.empty:
        out_path.write_text(
            "# Day 12 — Unit registry and duplicates\n\nNo boundaries found. "
            "Run `python -m src.ingestion.boundaries` first.\n", encoding="utf-8")
        return out_path

    dup_groups = duplicate_report_frame(units)
    collapses = key_collapses(units)

    counts = (
        units.groupby(["iso3", "adm_level"])
        .agg(
            units=("unit_id", "size"),
            unique_keys=("match_key", lambda s: s[s != ""].nunique()),
            dup_units=("is_duplicate_key", "sum"),
            empty_names=("name_norm", lambda s: int((s == "").sum())),
            expected=("unit_id", "size"),  # placeholder replaced below
        )
        .reset_index()
    )
    # expected counts come from the Day 2 audit constants
    counts["expected"] = [
        bnd.EXPECTED_UNITS.get((r.iso3, r.adm_level)) for r in counts.itertuples()
    ]
    counts["count_ok"] = [
        (e is None) or (e == u) for e, u in zip(counts["expected"], counts["units"])
    ]
    counts = counts.drop(columns=["expected"]).rename(columns={})

    dup_md = dup_groups.to_markdown(index=False) if len(dup_groups) else \
        "_No duplicate match keys — every name group is unique within its level._"
    collapse_md = (
        collapses.to_markdown(index=False) if len(collapses)
        else "_No suffix-stripping collapses._"
    )

    match_status = (
        "all match" if bool(counts["count_ok"].all())
        else "MISMATCH somewhere — see count_ok"
    )
    n_dup_units = int(units["is_duplicate_key"].sum())
    n_dup_groups = len(dup_groups)
    nga_dup = int(((units["iso3"] == "NGA") & units["is_duplicate_key"]).sum())

    text = f"""# Day 12 — Unit registry and duplicate analysis

Generated by `python -m src.geo.units` on {_utc_now()}.

Registry: `data/processed/unit_registry.csv` (one row per unit, geometry-free;
geometry stays in `data/external/boundaries/*.geojson`). Match keys come from
`src/cleaning/standardize.py` (normalize + admin-suffix strip).

## Registry counts

{counts.to_markdown(index=False)}

## Duplicate match keys (same key within one country/level)

{dup_md}

## Suffix-stripping collapses (distinct names, one key)

{collapse_md}

## Findings

- **{len(units)} units** registered across ADM0–ADM2; per-level counts
  {match_status} the Day 2 expected counts.
- **{n_dup_groups} duplicate key group(s) covering {n_dup_units} units**
  ({nga_dup} in NGA). Expected: NGA_ADM2 carries the 5 duplicate LGA names found
  on Day 2 — every other level should be clean.
- **Resolution rule (binding for Day 11):** duplicated keys are resolved by
  *parent context* (state/county column) when present, otherwise by
  *coordinates* (point-in-polygon against the unit that actually contains the
  point). Names only ever produce candidates — a notice name alone never
  decides which duplicate it refers to.
- Suffix collapses like `nairobi` ↔ `nairobi city county` are **intentional**:
  they widen candidate matching; the candidate set is re-checked geometrically.
- Registry is geometry-free by design: keeps the CSV reviewable in the repo
  while raw GeoJSON (incl. 32 MB ZAF ADM2) stays git-ignored.
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 12 report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-only", action="store_true")
    args = parser.parse_args(argv)

    units = load_units()
    if units.empty:
        print("No boundary files found — run python -m src.ingestion.boundaries")
        return 1
    registry_path = write_registry(units)
    print(f"Registry written to {registry_path} ({len(units)} units)")
    if not args.registry_only:
        write_report(units)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
