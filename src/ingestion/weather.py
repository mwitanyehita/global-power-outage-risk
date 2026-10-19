"""Download and validate historical weather for pilot cities (Day 3).

Source: Open-Meteo Historical Archive (https://open-meteo.com), a keyless
free API backed by ERA5/ERA5-Land reanalysis. Non-commercial use — see
https://open-meteo.com/en/terms. Requested with ``timezone=UTC`` so every
timestamp is unambiguous; local-time interpretation happens later (Day 8).

Layout:

    data/raw/weather/
        {slug}.json            verbatim API response bytes (one per city)
        _manifest.json         URL, params, SHA-256, retrieval time

Validation performed (reported in reports/day03_weather_audit.md):
  - hourly grid: no gaps, no duplicates, strictly monotonic
  - row count equals the exact number of hours in the requested window
  - all cities share an identical timestamp grid (cross-city alignment)
  - missing-value fraction per variable
  - coarse physical ranges for every variable

Usage:
    python -m src.ingestion.weather              # download + validate + report
    python -m src.ingestion.weather --audit      # validate existing files
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# `WEATHER_DIR` is imported from src.config so that every module resolves
# the data layout through one source of truth; it is re-exported here
# because `src.ingestion.weather.WEATHER_DIR` is the historical import path.
from src.config import DATA_RAW, REPORTS_DIR, WEATHER_DIR, ensure_dirs

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Nine pilot cities: three per country, covering the main demand centres.
CITIES = (
    {"city": "Nairobi", "country": "KEN", "lat": -1.292, "lon": 36.822},
    {"city": "Mombasa", "country": "KEN", "lat": -4.043, "lon": 39.665},
    {"city": "Kisumu", "country": "KEN", "lat": -0.0917, "lon": 34.768},
    {"city": "Johannesburg", "country": "ZAF", "lat": -26.204, "lon": 28.047},
    {"city": "Cape Town", "country": "ZAF", "lat": -33.925, "lon": 18.424},
    {"city": "Durban", "country": "ZAF", "lat": -29.859, "lon": 31.021},
    {"city": "Lagos", "country": "NGA", "lat": 6.524, "lon": 3.379},
    {"city": "Abuja", "country": "NGA", "lat": 9.058, "lon": 7.495},
    {"city": "Kano", "country": "NGA", "lat": 12.002, "lon": 8.592},
)

HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "surface_pressure",
)

DEFAULT_START = "2021-01-01"

# Coarse physical ranges used to catch corrupt values, not to filter weather.
VALUE_RANGES = {
    "temperature_2m": (-30.0, 55.0),
    "relative_humidity_2m": (0.0, 100.0),
    "precipitation": (0.0, 300.0),
    "rain": (0.0, 300.0),
    "weather_code": (0.0, 99.0),
    "cloud_cover": (0.0, 100.0),
    "wind_speed_10m": (0.0, 150.0),
    "wind_gusts_10m": (0.0, 250.0),
    "surface_pressure": (500.0, 1100.0),
}



def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_end() -> str:
    """Yesterday (UTC) — archives update with a short lag."""
    return (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()


def slugify(entry: dict) -> str:
    return f"{entry['country']}_{entry['city'].lower().replace(' ', '-')}"


def _get_with_retries(url: str, params: dict, tries: int = 6) -> requests.Response:
    """GET with exponential backoff — Open-Meteo rate-limits bursts (HTTP 429)."""
    delay = 5.0
    last_exc: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, params=params, timeout=300)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == tries:
                break
            print(f"  [warn] attempt {attempt} failed ({exc}); retrying in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 120.0)
    raise RuntimeError(f"request failed after {tries} attempts: {last_exc}")


def download_city(
    entry: dict,
    start: str = DEFAULT_START,
    end: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Fetch one city's hourly history; response bytes are stored verbatim."""
    ensure_dirs()
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    end = end or default_end()

    path = WEATHER_DIR / f"{slugify(entry)}.json"
    if path.exists() and not overwrite:
        print(f"  [skip] {path.name} already exists")
        return path

    params = {
        "latitude": entry["lat"],
        "longitude": entry["lon"],
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": "UTC",
    }
    resp = _get_with_retries(ARCHIVE_URL, params)

    path.write_bytes(resp.content)
    _record_manifest(path, resp, params, entry)
    n = len(resp.json().get("hourly", {}).get("time", []))
    print(f"  [get ] {path.name}: {n} hourly rows ({start}..{end})")
    return path


def _record_manifest(path: Path, resp: requests.Response, params: dict, entry: dict) -> None:
    manifest_path = WEATHER_DIR / "_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[path.name] = {
        "city": entry["city"],
        "country": entry["country"],
        "latitude": entry["lat"],
        "longitude": entry["lon"],
        "requested_url": resp.url.split("?")[0],
        "requested_params": {k: str(v) for k, v in params.items()},
        "retrieved_utc": _utc_now(),
        "http_status": resp.status_code,
        "bytes": len(resp.content),
        "sha256": hashlib.sha256(resp.content).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def load_city(path: Path) -> pd.DataFrame:
    """Load one raw response into a DataFrame with a tz-aware UTC hourly index."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    hourly = payload["hourly"]
    df = pd.DataFrame(hourly)
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize("UTC")
    return df.sort_values("time").reset_index(drop=True)


def validate_frame(df: pd.DataFrame, start: str, end: str) -> dict:
    """Timestamp and value checks for a single city's hourly frame."""
    times = df["time"]
    expected = pd.date_range(f"{start} 00:00", f"{end} 23:00", freq="h", tz="UTC")

    gaps = int((times.diff().dropna() != pd.Timedelta(hours=1)).sum())
    nulls = {c: int(df[c].isna().sum()) for c in HOURLY_VARIABLES}

    # NB: Series.equals(DatetimeIndex) returns False even for identical values,
    # so compare against a Series with a matching RangeIndex instead.
    grid_ok = times.reset_index(drop=True).equals(pd.Series(expected))

    out_of_range = {}
    for col, (lo, hi) in VALUE_RANGES.items():
        if col in df:
            s = df[col].dropna()
            out_of_range[col] = int(((s < lo) | (s > hi)).sum())

    return {
        "rows": len(df),
        "expected_rows": len(expected),
        "row_count_ok": len(df) == len(expected),
        "start": times.min(),
        "end": times.max(),
        "tz_utc": str(times.dt.tz) == "UTC",
        "monotonic": bool(times.is_monotonic_increasing),
        "duplicate_times": int(times.duplicated().sum()),
        "gap_count": gaps,
        "grid_matches_expected": grid_ok,
        "total_nulls": int(sum(nulls.values())),
        "nulls_by_var": nulls,
        "out_of_range_total": int(sum(out_of_range.values())),
        "out_of_range_by_var": out_of_range,
    }


def validate_all(start: str = DEFAULT_START, end: str | None = None) -> dict:
    """Validate every downloaded city plus cross-city grid alignment."""
    end = end or default_end()
    paths = sorted(p for p in WEATHER_DIR.glob("*.json") if p.name != "_manifest.json")
    if not paths:
        return {"cities": {}, "grid_alignment_ok": None}

    results = {}
    grids = {}
    for p in paths:
        df = load_city(p)
        results[p.stem] = validate_frame(df, start, end)
        grids[p.stem] = df["time"]

    first = next(iter(grids.values()))
    alignment = all(g.equals(first) for g in grids.values())
    return {"cities": results, "grid_alignment_ok": alignment, "window": (start, end)}


def write_audit(start: str = DEFAULT_START, end: str | None = None,
                out_path: Path | None = None) -> Path:
    """Write reports/day03_weather_audit.md."""
    end = end or default_end()
    report = validate_all(start, end)
    cities = report["cities"]
    out_path = out_path or (REPORTS_DIR / "day03_weather_audit.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not cities:
        out_path.write_text(
            "# Day 3 audit — Historical weather\n\nNo downloads found. "
            "Run `python -m src.ingestion.weather` first.\n",
            encoding="utf-8",
        )
        return out_path

    rows = []
    for stem, res in cities.items():
        rows.append({
            "city": stem,
            "rows": res["rows"],
            "expected_rows": res["expected_rows"],
            "count_ok": res["row_count_ok"],
            "tz_utc": res["tz_utc"],
            "monotonic": res["monotonic"],
            "duplicate_times": res["duplicate_times"],
            "gaps": res["gap_count"],
            "grid_ok": res["grid_matches_expected"],
            "nulls": res["total_nulls"],
            "out_of_range": res["out_of_range_total"],
            "span": f"{res['start']:%Y-%m-%d} .. {res['end']:%Y-%m-%d %H:%M}",
        })
    table = pd.DataFrame(rows).to_markdown(index=False)

    worst_nulls = max(cities.values(), key=lambda r: r["total_nulls"])
    null_detail = "; ".join(
        f"{k}={v}" for k, v in worst_nulls["nulls_by_var"].items() if v > 0
    ) or "none"
    range_detail_parts = []
    for stem, res in cities.items():
        bad = {k: v for k, v in res["out_of_range_by_var"].items() if v > 0}
        if bad:
            range_detail_parts.append(f"{stem}: {bad}")
    range_detail = "; ".join(range_detail_parts) or "none"

    all_ok = all(
        r["row_count_ok"] and r["tz_utc"] and r["monotonic"]
        and r["duplicate_times"] == 0 and r["gap_count"] == 0
        and r["grid_matches_expected"]
        for r in cities.values()
    )

    text = f"""# Day 3 audit — Historical weather

Generated by `python -m src.ingestion.weather --audit` on {_utc_now()}.

**Source:** Open-Meteo Historical Archive (ERA5/ERA5-Land), requested with
`timezone=UTC`, keyless, non-commercial terms. Raw responses stored verbatim in
`data/raw/weather/` with URL, parameters, SHA-256 and retrieval time in
`_manifest.json`.

**Window:** {start} 00:00 UTC .. {end} 23:00 UTC · hourly ·
variables: {", ".join(HOURLY_VARIABLES)}

## Per-city checks

{table}

## Cross-city alignment

All {len(cities)} cities share one identical UTC hourly grid:
 **{"yes" if report["grid_alignment_ok"] else "NO — investigate"}**.

## Findings

- Timestamp discipline: **{"PASS" if all_ok else "FAIL"}** — every city has exactly
  {cities[next(iter(cities))]["expected_rows"]} hourly rows, tz-aware UTC, strictly monotonic,
  zero duplicates, zero gaps, aligned to the requested grid.
- Missing values: worst city has {worst_nulls["total_nulls"]} null cells (detail: {null_detail}).
- Physical range violations (>300 mm/h rain, gusts >250 km/h, etc.): {range_detail}.
- Timestamps are **UTC by construction**; converting to local time (EAT/SAST/WAT, no DST in
  these countries) is deferred to Day 8 so raw files stay unambiguous.
- `weather_code` is WMO-coded (0–99) and must be treated as categorical, never continuous (Day 9).
- Gusts and wind are ERA5 grid values at 10 m — they describe exposure, not damage at a specific
  line; model interpretation must stay cautious (Day 27).
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Audit written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None, help="defaults to yesterday (UTC)")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--audit", action="store_true", help="validate existing downloads only")
    args = parser.parse_args(argv)

    if not args.audit:
        print(f"Weather window: {args.start} .. {args.end or default_end()}")
        failed = []
        for entry in CITIES:
            try:
                download_city(entry, args.start, args.end, args.overwrite)
            except (requests.RequestException, RuntimeError) as exc:
                print(f"  [FAIL] {entry['city']}: {exc}", file=sys.stderr)
                failed.append(entry)
                continue
            time.sleep(2)  # stay well inside the public API rate limit
        if failed:
            print(f"{len(failed)} city/cities failed: "
                  f"{[e['city'] for e in failed]}", file=sys.stderr)
            return 1

    write_audit(args.start, args.end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
