"""Canonical weather schema: raw per-city responses -> one tidy hourly frame.

``src.ingestion.weather`` owns downloading and auditing the raw Open-Meteo
responses (``data/raw/weather/{slug}.json`` + ``_manifest.json``). This module
adds the canonicalization step that everything downstream (Day 8 feature
builds, joins with outages) can rely on:

    data/processed/weather_hourly.csv       one tidy hourly frame, all cities
    data/processed/weather_hourly_meta.json provenance sidecar (inputs, SHA-256)

Canonical schema — one row per city-hour:

    time_utc        datetime64[ns, UTC]   tz-aware by construction
    city / country_iso3                    pilot registry values (KEN|ZAF|NGA)
    latitude / longitude                   requested coordinates
    <9 hourly variables>                   verbatim from weather.HOURLY_VARIABLES
    raw_file                               provenance: source response file

Discipline enforced by ``validate``:
  * ``time_utc`` is tz-aware UTC and never null;
  * no duplicate (city, time) rows; each city's grid is gap-free hourly;
  * every city's timestamp grid is compared for cross-city alignment
    (Day 8 joins assume one shared grid — differences are flagged);
  * values outside the coarse physical ranges are warnings, not silent drops.

Usage:
    python -m src.ingestion.weather_canonical           # build processed frame
    python -m src.ingestion.weather_canonical --audit   # validate, write nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from src.config import DATA_PROCESSED, ensure_dirs
from src.ingestion import utils
from src.ingestion import weather

CANONICAL_COLUMNS: tuple[str, ...] = (
    "time_utc",
    "city",
    "country_iso3",
    "latitude",
    "longitude",
    *weather.HOURLY_VARIABLES,
    "raw_file",
)

REQUIRED_COLUMNS: tuple[str, ...] = ("time_utc", "city", "country_iso3")

PILOT_COUNTRIES: tuple[str, ...] = ("KEN", "ZAF", "NGA")

OUT_PATH = DATA_PROCESSED / "weather_hourly.csv"

SOURCE_NAME = "Open-Meteo Historical Archive (ERA5/ERA5-Land)"
SOURCE_URL = "https://open-meteo.com/en/docs/historical-weather-api"
TERMS_URL = "https://open-meteo.com/en/terms"

# slug -> registry entry, built once from the downloader's city list.
CITY_BY_SLUG: dict[str, dict] = {weather.slugify(e): e for e in weather.CITIES}


def discover_raw_files(raw_dir: Path | None = None) -> list[Path]:
    """Raw weather responses in deterministic order (manifest excluded)."""
    raw_dir = Path(raw_dir) if raw_dir else Path(weather.WEATHER_DIR)
    if not raw_dir.exists():
        return []
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".json"
        and not p.name.startswith("_")
    )


def _entry_for(path: Path) -> tuple[dict, list[str]]:
    """Registry entry for a raw file; slug fallback when the city is unknown."""
    entry = CITY_BY_SLUG.get(path.stem)
    if entry is not None:
        return entry, []
    parts = path.stem.split("_", 1)
    country = parts[0].upper() if parts and len(parts[0]) == 3 else None
    city = parts[1].replace("-", " ").title() if len(parts) == 2 else path.stem
    return (
        {"city": city, "country": country, "lat": None, "lon": None},
        [f"{path.name}: city not in registry; derived city='{city}', "
         f"country={country or '?'}"],
    )


def load_city_canonical(
    path: Path, entry: Mapping[str, Any] | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """One raw response -> canonical rows for that city (tz-aware UTC).

    Missing hourly variables are added as nulls with a warning rather than
    dropping the file — a partially-observed city is still usable downstream.
    """
    warnings: list[str] = []
    if entry is None:
        entry, warnings = _entry_for(path)

    try:
        raw = weather.load_city(path)  # columns: time + requested hourly variables
    except KeyError as exc:
        field = exc.args[0] if exc.args else "?"
        raise utils.SchemaError(
            f"{path}: response is missing the '{field}' field"
        ) from exc
    if "time" not in raw.columns:
        raise utils.SchemaError(f"{path}: response has no 'time' field")

    df = raw.rename(columns={"time": "time_utc"})
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)

    missing = [c for c in weather.HOURLY_VARIABLES if c not in df.columns]
    if missing:
        warnings.append(f"{path.name}: missing variable(s) {', '.join(missing)}")
        for col in missing:
            df[col] = pd.NA

    extra = [c for c in df.columns
             if c not in CANONICAL_COLUMNS and c != "time_utc"]
    if extra:
        warnings.append(f"{path.name}: unexpected field(s) ignored: {', '.join(extra)}")

    df["city"] = str(entry["city"])
    df["country_iso3"] = entry.get("country")
    df["latitude"] = entry.get("lat")
    df["longitude"] = entry.get("lon")
    df["raw_file"] = path.name

    df = df.sort_values("time_utc").reset_index(drop=True)
    return df[list(CANONICAL_COLUMNS)], warnings


def validate(df: pd.DataFrame) -> dict[str, Any]:
    """Structural checks on a canonical weather frame (JSON-safe report).

    ``ok`` is True only without errors; null cells and out-of-range values are
    warnings — they describe observation quality, not pipeline corruption.
    """
    errors: list[str] = []
    warnings: list[str] = []

    missing = utils.missing_columns(df, CANONICAL_COLUMNS)
    if missing:
        return {
            "ok": False,
            "errors": [f"missing columns: {', '.join(missing)}"],
            "warnings": [],
            "counts": {},
        }

    if not utils.is_tz_aware(df["time_utc"]):
        errors.append("time_utc: not a tz-aware datetime column")
    null_time = int(df["time_utc"].isna().sum())
    if null_time:
        errors.append(f"{null_time} row(s) with null time_utc")

    dupes = int(df.duplicated(subset=["city", "time_utc"]).sum())
    if dupes:
        errors.append(f"{dupes} duplicate (city, time_utc) row(s)")

    # Per-city: hourly grid must be gap-free between its own min and max.
    grids: dict[str, pd.Series] = {}
    for city, group in df.groupby("city", sort=True):
        times = group["time_utc"].dropna().reset_index(drop=True)
        grids[city] = times
        if times.empty:
            continue
        expected = pd.date_range(times.min(), times.max(), freq="h")
        if len(times) != len(expected):
            errors.append(
                f"{city}: {len(times)} hourly rows but "
                f"{len(expected)} expected for {times.min():%Y-%m-%d}.."
                f"{times.max():%Y-%m-%d} (gap or duplicate hours)"
            )

    # Cross-city alignment: identical timestamp grid for every city.
    alignment_ok: bool | None = None
    if len(grids) > 1:
        first = next(iter(grids.values()))
        alignment_ok = all(g.equals(first) for g in grids.values())
        if not alignment_ok:
            unaligned = [c for c, g in grids.items() if not g.equals(first)]
            warnings.append(
                f"timestamp grids differ across cities: {unaligned} vs others "
                "(Day 8 joins must reindex first)"
            )

    out_of_range: dict[str, int] = {}
    nulls: dict[str, int] = {}
    for col, (lo, hi) in weather.VALUE_RANGES.items():
        series = df[col]
        nulls[col] = int(series.isna().sum())
        valid = series.dropna()
        count = int(((valid < lo) | (valid > hi)).sum())
        if count:
            out_of_range[col] = count
    if out_of_range:
        warnings.append(f"values outside physical range: {out_of_range}")
    total_nulls = int(sum(nulls.values()))
    if total_nulls:
        warnings.append(f"{total_nulls} null value cell(s) across hourly variables")

    off_pilot = (
        df["country_iso3"].notna()
        & ~df["country_iso3"].isin(PILOT_COUNTRIES)
    )
    if int(off_pilot.sum()):
        values = sorted(df.loc[off_pilot, "country_iso3"].dropna().unique())
        warnings.append(f"records outside pilot countries: {values}")

    counts = {
        "rows": int(len(df)),
        "cities": int(df["city"].nunique()),
        "by_country": {
            str(k): int(v)
            for k, v in df["country_iso3"].fillna("<null>").value_counts().items()
        },
        "hours_per_city": {
            str(k): int(v) for k, v in df.groupby("city").size().items()
        },
        "null_cells": total_nulls,
        "out_of_range_cells": int(sum(out_of_range.values())),
        "grid_alignment": alignment_ok,
        "window_start": None if df["time_utc"].isna().all()
        else str(df["time_utc"].min()),
        "window_end": None if df["time_utc"].isna().all()
        else str(df["time_utc"].max()),
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "counts": counts}


def load_processed(path: Path | None = None) -> pd.DataFrame:
    """Read the processed frame back with a tz-aware ``time_utc`` column."""
    path = Path(path) if path else OUT_PATH
    df = pd.read_csv(path)
    if "time_utc" in df.columns:
        df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    for col in ("latitude", "longitude"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _input_hashes(paths: Sequence[Path], raw_dir: Path) -> dict[str, str]:
    """SHA-256 per raw file, preferring the downloader's manifest record."""
    manifest = utils.read_json(raw_dir / "_manifest.json", default={})
    if not isinstance(manifest, dict):
        manifest = {}
    hashes: dict[str, str] = {}
    for path in paths:
        recorded = manifest.get(path.name, {})
        sha = recorded.get("sha256") if isinstance(recorded, dict) else None
        hashes[path.name] = sha or utils.sha256_file(path)
    return hashes


def build(
    raw_dir: Path | None = None,
    out_path: Path | None = None,
    *,
    write: bool = True,
) -> dict[str, Any]:
    """Canonicalize every raw weather response; optionally write the CSV.

    Mirrors ``outages.intake``: returns a summary dict with files, counts,
    warnings, validation report and output paths. With ``write=False``
    nothing touches disk (used by ``--audit``); output is only written when
    validation passes.
    """
    raw_dir = Path(raw_dir) if raw_dir else Path(weather.WEATHER_DIR)
    out_path = Path(out_path) if out_path else OUT_PATH
    meta_path = out_path.with_name(out_path.stem + "_meta.json")
    ensure_dirs()

    files = discover_raw_files(raw_dir)
    if not files:
        return {
            "files": [],
            "rows": 0,
            "warnings": [],
            "validation": {"ok": False,
                           "errors": ["no raw weather files found"],
                           "warnings": [], "counts": {}},
            "written": None,
            "meta": None,
        }

    hashes = _input_hashes(files, raw_dir)
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    inputs: list[dict[str, Any]] = []

    for path in files:
        try:
            entry, entry_warnings = _entry_for(path)
            frame, frame_warnings = load_city_canonical(path, entry)
        except (OSError, ValueError, KeyError, utils.SchemaError) as exc:
            warnings.append(f"{path.name}: unreadable raw response ({exc})")
            continue
        warnings.extend(entry_warnings)
        warnings.extend(frame_warnings)
        frames.append(frame)
        inputs.append(
            {
                "raw_file": path.name,
                "city": entry["city"],
                "sha256": hashes[path.name],
                "bytes": path.stat().st_size,
            }
        )

    if frames:
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["city", "time_utc"]).reset_index(drop=True)
    else:
        df = pd.DataFrame(columns=list(CANONICAL_COLUMNS))

    report = validate(df)
    written: str | None = None
    if write and report["ok"] and len(df):
        utils.ensure_parent(out_path)
        df.to_csv(out_path, index=False, encoding="utf-8")
        utils.write_provenance(
            meta_path,
            {
                "generated_utc": utils.utc_now_iso(),
                "source": SOURCE_NAME,
                "source_url": SOURCE_URL,
                "terms": TERMS_URL,
                "inputs": inputs,
                "rows": int(len(df)),
                "columns": list(df.columns),
                "window_start": report["counts"]["window_start"],
                "window_end": report["counts"]["window_end"],
                "validation": report,
            },
        )
        written = str(out_path)

    return {
        "files": [p.name for p in files],
        "rows": int(len(df)),
        "warnings": warnings,
        "validation": report,
        "written": written,
        "meta": str(meta_path) if written else None,
    }


def _print_summary(summary: Mapping[str, Any]) -> None:
    report = summary["validation"]
    print(f"files:   {', '.join(summary['files']) or 'none'}")
    print(f"rows:    {summary['rows']}")
    counts = report["counts"]
    if counts.get("cities"):
        print(f"cities:  {counts['cities']}  "
              f"({', '.join(f'{k}={v}' for k, v in counts['by_country'].items())})")
    if counts.get("window_start"):
        print(f"window:  {counts['window_start']} .. {counts['window_end']}")
    for warning in summary["warnings"]:
        print(f"  [warn] {warning}")
    for error in report["errors"]:
        print(f"  [FAIL] {error}", file=sys.stderr)
    if summary.get("written"):
        print(f"wrote:   {summary['written']}")
        print(f"meta:    {summary['meta']}")
    elif report["ok"]:
        print("wrote:   (skipped — --audit)")
    else:
        print("wrote:   (skipped — validation failed)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true",
                        help="validate raw weather without writing output")
    parser.add_argument("--raw-dir", default=None,
                        help="override raw weather directory")
    parser.add_argument("--out", default=None,
                        help="override processed output path")
    args = parser.parse_args(argv)

    summary = build(args.raw_dir, args.out, write=not args.audit)
    _print_summary(summary)
    if not summary["files"]:
        return 1
    return 0 if summary["validation"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
