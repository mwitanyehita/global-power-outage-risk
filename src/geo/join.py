"""Spatial joins: attach events and cities to admin units (Day 11).

Two join methods, in a strict order of authority (README risk register):

1. **Point-in-polygon** — for anything with coordinates (the 9 weather cities,
   any future geocoded event). Authoritative.
2. **Name candidate matching** — proposes candidates from the unit registry
   match keys (`src/geo/units.py`). A name NEVER decides on its own when its
   key is ambiguous (the 5 duplicated NGA_LGA names): parent context or
   coordinates must confirm. Ambiguity is surfaced, never silently resolved.

Kenya geography note: KPLC notices carry COUNTY headers and counties are
Kenya's ADM1 units, so notice counties join ADM1 directly; notice AREA names
are localities/estates, so ADM2 matches are candidate-only.

Usage:
    python -m src.geo.join        # writes reports/day11_spatial_join.md
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from src.cleaning.standardize import name_match_key
from src.config import DATA_PROCESSED, REPORTS_DIR
from src.geo.units import load_units
from src.ingestion.weather import CITIES

_KPLC_CSV = DATA_PROCESSED / "kplc_planned_outages.csv"
_UNITS_CACHE: dict[tuple[str, str], gpd.GeoDataFrame] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Unit access
# ---------------------------------------------------------------------------

def units_gdf(iso3: str, adm_level: str,
              units: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    """Boundary polygons for one (country, level), cached across calls."""
    key = (iso3, adm_level)
    if units is None:
        if key not in _UNITS_CACHE:
            _UNITS_CACHE[key] = load_units((adm_level,))
        units = _UNITS_CACHE[key]
    sub = units[(units["iso3"] == iso3) & (units["adm_level"] == adm_level)]
    return gpd.GeoDataFrame(sub.copy(), crs=units.crs)


def points_gdf(frame: pd.DataFrame, lon_col: str = "lon",
               lat_col: str = "lat") -> gpd.GeoDataFrame:
    """Build an EPSG:4326 GeoDataFrame from a lon/lat frame."""
    return gpd.GeoDataFrame(
        frame.copy(),
        geometry=[Point(xy) for xy in zip(frame[lon_col], frame[lat_col])],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Method 1: point-in-polygon (authoritative)
# ---------------------------------------------------------------------------

def points_to_units(points: gpd.GeoDataFrame, iso3: str, adm_level: str,
                    units: gpd.GeoDataFrame | None = None) -> pd.DataFrame:
    """Join points to polygons; misses get match_method='unmatched'.

    Returns the input columns (geometry dropped) plus unit_id, unit_name,
    unit_parent, match_method. Points falling in no unit are kept as rows —
    a dropped row would hide a coverage gap.
    """
    u = units_gdf(iso3, adm_level, units)
    if u.empty:
        out = pd.DataFrame(points.drop(columns=["geometry"], errors="ignore"))
        out["unit_id"] = None
        out["unit_name"] = None
        out["unit_parent"] = None
        out["match_method"] = "unmatched"
        return out

    pts = points.to_crs(u.crs) if points.crs != u.crs else points
    right = u[["unit_id", "name", "parent", "geometry"]].rename(
        columns={"name": "unit_name", "parent": "unit_parent"})
    joined = gpd.sjoin(pts, right, how="left", predicate="intersects")
    # Overlapping polygons would duplicate rows; keep the first hit.
    joined = joined[~joined.index.duplicated(keep="first")]
    out = pd.DataFrame(joined.drop(columns=["geometry", "index_right"],
                                   errors="ignore"))
    out["match_method"] = out["unit_id"].notna().map(
        {True: "point_in_polygon", False: "unmatched"})
    out["match_method"] = out["match_method"].fillna("unmatched")
    return out


# ---------------------------------------------------------------------------
# Method 2: name candidates (never authoritative alone)
# ---------------------------------------------------------------------------

def match_names_to_units(names, iso3: str, adm_level: str,
                         units: gpd.GeoDataFrame | None = None) -> pd.DataFrame:
    """Propose unit candidates for names; status is unique/ambiguous/unmatched/no_key.

    The 'candidates' column lists ``name [unit_id] (parent)`` for every unit
    sharing the match key. Callers must resolve 'ambiguous' rows with parent
    context or coordinates — never by picking the first candidate.
    """
    u = units_gdf(iso3, adm_level, units)
    by_key: dict[str, pd.DataFrame] = {
        k: g for k, g in u[u["match_key"] != ""].groupby("match_key")
    }
    rows = []
    for name in names:
        key = name_match_key(name)
        if not key:
            rows.append({"name": name, "match_key": key, "n_candidates": 0,
                         "status": "no_key", "candidates": ""})
            continue
        cand = by_key.get(key)
        if cand is None:
            rows.append({"name": name, "match_key": key, "n_candidates": 0,
                         "status": "unmatched", "candidates": ""})
        elif len(cand) == 1:
            r = cand.iloc[0]
            rows.append({"name": name, "match_key": key, "n_candidates": 1,
                         "status": "unique",
                         "candidates": f"{r['name']} [{r['unit_id']}] "
                                       f"(parent={r['parent'] or '-'})"})
        else:
            listing = "; ".join(
                f"{r['name']} [{r['unit_id']}] (parent={r['parent'] or '-'})"
                for _, r in cand.iterrows())
            rows.append({"name": name, "match_key": key,
                         "n_candidates": len(cand), "status": "ambiguous",
                         "candidates": listing})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Ready-made joins
# ---------------------------------------------------------------------------

def weather_cities_to_adm1(units: gpd.GeoDataFrame | None = None) -> pd.DataFrame:
    """Point-join the 9 pilot weather cities to their ADM1 unit.

    The key_agrees column records whether name matching *would* have found
    the same unit — evidence for why coordinates are the authoritative method.
    """
    rows = []
    for c in CITIES:
        pts = points_gdf(pd.DataFrame([{"city": c["city"], "country": c["country"],
                                        "lat": c["lat"], "lon": c["lon"]}]))
        j = points_to_units(pts, c["country"], "ADM1", units).iloc[0]
        unit_name = j["unit_name"]
        rows.append({
            "city": c["city"],
            "country": c["country"],
            "lat": c["lat"],
            "lon": c["lon"],
            "unit_id": j["unit_id"],
            "unit_name": unit_name,
            "match_method": j["match_method"],
            "key_agrees": (
                bool(unit_name)
                and name_match_key(c["city"]) == name_match_key(unit_name)
            ),
        })
    return pd.DataFrame(rows)


def kplc_join_summary(csv_path: Path | None = None,
                      units: gpd.GeoDataFrame | None = None
                      ) -> dict | None:
    """Join KPLC notice counties -> KEN ADM1 and areas -> KEN ADM2 (candidates).

    Returns None when the Day 11 extraction output is absent, so callers can
    report 'pending' instead of pretending to a result.
    """
    path = csv_path or _KPLC_CSV
    if not path.exists():
        return None
    df = pd.read_csv(path)
    counties = sorted({str(c).strip() for c in df.get("county", pd.Series(dtype=str)).dropna().unique() if str(c).strip()})
    areas = sorted({str(a).strip() for a in df.get("area", pd.Series(dtype=str)).dropna().unique() if str(a).strip()})

    county_matches = match_names_to_units(counties, "KEN", "ADM1", units) \
        if counties else pd.DataFrame()
    area_matches = match_names_to_units(areas, "KEN", "ADM2", units) \
        if areas else pd.DataFrame()
    return {
        "n_events": len(df),
        "counties": county_matches,
        "areas": area_matches,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(units: gpd.GeoDataFrame | None = None,
                 out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day11_spatial_join.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if units is None:
        units = load_units()
    if units.empty:
        out_path.write_text(
            "# Day 11 — Spatial joins\n\nNo boundaries found. Run "
            "`python -m src.ingestion.boundaries` first.\n", encoding="utf-8")
        return out_path

    cities = weather_cities_to_adm1(units)
    cities_md = cities[["city", "country", "unit_name", "match_method",
                        "key_agrees"]].to_markdown(index=False)
    all_pip = (cities["match_method"] == "point_in_polygon").all()
    n_agree = int(cities["key_agrees"].sum())

    kplc = kplc_join_summary(units=units)
    if kplc is None:
        kplc_md = ("_Pending — run `python -m src.ingestion.kplc_parse` to "
                   "produce `data/processed/kplc_planned_outages.csv`._")
        kplc_findings = "- KPLC extraction output not present yet; county/area join stats pending."
    else:
        cm, am = kplc["counties"], kplc["areas"]
        c_counts = cm["status"].value_counts().to_dict() if len(cm) else {}
        a_counts = am["status"].value_counts().to_dict() if len(am) else {}
        amb = am[am["status"] == "ambiguous"].head(10)
        un = am[am["status"] == "unmatched"].head(10)
        amb_md = amb.to_markdown(index=False) if len(amb) else "_none_"
        un_md = un.to_markdown(index=False) if len(un) else "_none_"
        kplc_md = (
            f"Events in extract: **{kplc['n_events']}**. "
            f"County -> ADM1 status counts: `{c_counts}`. "
            f"Area -> ADM2 candidate status counts: `{a_counts}`.\n\n"
            f"Ambiguous areas (first 10):\n\n{amb_md}\n\n"
            f"Unmatched areas (first 10):\n\n{un_md}\n"
        )
        kplc_findings = (
            f"- KPLC counties resolve against KEN ADM1 with status mix "
            f"`{c_counts}` — unmatched spellings are listed above rather than "
            f"fuzzy-forced.\n"
            f"- KPLC areas are localities/estates, not admin units: status mix "
            f"`{a_counts}` is expected to be mostly unmatched or ambiguous at "
            f"ADM2; they stay as free-text locality strings unless coordinates "
            f"are added.\n"
        )

    text = f"""# Day 11 — Spatial joins: events and cities to admin units

Generated by `python -m src.geo.join` on {_utc_now()}.

## Method hierarchy (binding)

1. **Point-in-polygon** (`points_to_units`) is authoritative.
2. **Name matching** (`match_names_to_units`) only proposes candidates;
   `status='ambiguous'` rows (the duplicated NGA_LGA keys from Day 12) must
   be resolved with parent context or coordinates. The pipeline never picks
   the first candidate silently.

## Pilot weather cities -> ADM1 (point-in-polygon)

{cities_md}

- All 9 cities joined by coordinates: {"yes" if all_pip else "NO — investigate"}
- Name matching would have agreed for {n_agree}/9 cities (Cape Town is in
  Western Cape, Nairobi is in "Nairobi City", etc.) — the gap is exactly why
  coordinates decide and names only propose.

## KPLC notice joins (Kenya, Day 11 extraction)

{kplc_md}

## Findings

- The join machinery is exercised end-to-end on the real 1,212-unit registry;
  unmatched rows are preserved as rows so coverage gaps stay visible.
- Kenya's counties ARE its ADM1 units, so notice COUNTY headers join ADM1
  directly; notice AREA names are estates/localities and are reported as
  candidate matches only.
- NGA duplicate keys (Bassa, Ifelodun, Irepodun, Obi, Surulere) will surface
  as `status='ambiguous'` for any NGA name joins — a documented, expected
  outcome, not an error.
{kplc_findings}"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 11 report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    units = load_units()
    if units.empty:
        print("No boundary files found — run python -m src.ingestion.boundaries")
        return 1
    write_report(units)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
