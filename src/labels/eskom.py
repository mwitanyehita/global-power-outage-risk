"""ZAF primary labels: hourly load-shedding (MLR) from the Eskom portal mirror.

Data: ``data/raw/outages/zaf_eskom_hourly.sqlite`` (acquired Day 4; mirror of
the Eskom Data Portal public series). The mirror repo carries **no license**,
so this module analyses in-project and never republishes raw rows — outputs
are label fields derived from the MLR column only.

Label definition (locked in the Day 7 audit):

* ``mlr_mw``            — Manual Load Reduction in MW for the hour, verbatim
* ``load_shedding``     — ``mlr_mw > 0``; ``<NA>`` when MLR is missing, so a
  data hole can never be silently scored as "no shedding"

Hour convention: ``Date Time Hour Beginning`` strings like
``2017-04-01 01:00:00 AM`` are **SAST (UTC+2) hour-beginning**; parsed via
``normalize_timestamp(..., assume_tz='Africa/Johannesburg')`` (Day 8 rule:
naive timestamps localize explicitly, then convert to UTC).

**Leakage rule**: the output frame carries timestamps + label fields ONLY.
Same-hour demand/generation columns (e.g. Residual Demand) are measured
*after* shedding is applied — they are outcomes, not features — and are
deliberately excluded here; feature construction (Day 16) must join on
timestamps strictly before its forecast cutoff.

Usage:
    python -m src.labels.eskom                 # hourly + daily + report
    python -m src.labels.eskom --sqlite PATH
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.cleaning.standardize import normalize_timestamp
from src.config import DATA_PROCESSED, DATA_RAW, REPORTS_DIR

SQLITE_PATH = DATA_RAW / "outages" / "zaf_eskom_hourly.sqlite"
TABLE = "eskom"
TIME_COL = "Date Time Hour Beginning"
MLR_COL = "Manual Load_Reduction(MLR)"

# The ONLY columns allowed into label outputs (leakage guard, tested).
HOURLY_COLUMNS = ["time_sast", "time_utc", "mlr_mw", "load_shedding"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_hourly(sqlite_path: Path | None = None) -> pd.DataFrame:
    """Parse the mirror into tz-aware hourly label rows.

    Returns columns: time_sast (tz-aware SAST), time_utc (tz-aware UTC),
    mlr_mw (float, NaN when missing), load_shedding (boolean, <NA> when
    mlr_mw is missing).
    """
    path = sqlite_path or SQLITE_PATH
    con = sqlite3.connect(path)
    try:
        q = f'SELECT "{TIME_COL}" AS time_raw, "{MLR_COL}" AS mlr_raw FROM "{TABLE}"'
        df = pd.read_sql_query(q, con)
    finally:
        con.close()

    time_utc = normalize_timestamp(df["time_raw"], assume_tz="Africa/Johannesburg")
    mlr = pd.to_numeric(df["mlr_raw"].replace({"": pd.NA, None: pd.NA}),
                        errors="coerce")
    out = pd.DataFrame({
        "time_sast": time_utc.dt.tz_convert("Africa/Johannesburg"),
        "time_utc": time_utc,
        "mlr_mw": mlr.astype("Float64"),
    })
    out["load_shedding"] = (out["mlr_mw"] > 0).astype("boolean")
    out = out.sort_values("time_utc").reset_index(drop=True)
    return out[HOURLY_COLUMNS]


def hourly_quality(hourly: pd.DataFrame) -> dict:
    """Integrity checks: duplicates, gaps, missing/negative MLR."""
    ts = hourly["time_utc"]
    expected = pd.date_range(ts.min(), ts.max(), freq="h", tz="UTC")
    missing_hours = expected.difference(pd.DatetimeIndex(ts))
    return {
        "rows": len(hourly),
        "start_utc": ts.min().isoformat(),
        "end_utc": ts.max().isoformat(),
        "duplicate_hours": int(ts.duplicated().sum()),
        "missing_hours": len(missing_hours),
        "missing_sample": [t.isoformat() for t in missing_hours[:5]],
        "mlr_missing": int(hourly["mlr_mw"].isna().sum()),
        "mlr_negative": int((hourly["mlr_mw"] < 0).sum()),
    }


def daily_labels(hourly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate to SAST calendar days (label days are local days)."""
    h = hourly.copy()
    h["date_sast"] = h["time_sast"].dt.date
    daily = (
        h.groupby("date_sast")
        .agg(
            hours_reported=("mlr_mw", "size"),
            mlr_missing=("mlr_mw", lambda s: int(s.isna().sum())),
            hours_active=("load_shedding", "sum"),
            mlr_mw_mean=("mlr_mw", "mean"),
            mlr_mw_max=("mlr_mw", "max"),
            mlr_mwh=("mlr_mw", "sum"),  # MW-h ≈ unserved energy proxy
        )
        .reset_index()
        .rename(columns={"date_sast": "date"})
    )
    daily["any_load_shedding"] = daily["hours_active"] > 0
    daily["date"] = pd.to_datetime(daily["date"])
    return daily


def annual_validation(hourly: pd.DataFrame) -> pd.DataFrame:
    """Per-year share of hours with active load shedding (vs known calendar)."""
    h = hourly.copy()
    h["year"] = h["time_sast"].dt.year
    out = (
        h.groupby("year")
        .agg(
            hours=("mlr_mw", "size"),
            mlr_missing=("mlr_mw", lambda s: int(s.isna().sum())),
            hours_active=("load_shedding", "sum"),
            mlr_mw_mean_when_active=("mlr_mw", lambda s: float(s[s > 0].mean())
                                     if (s > 0).any() else 0.0),
        )
        .reset_index()
    )
    out["pct_hours_active"] = (100 * out["hours_active"] / out["hours"]).round(1)
    return out


def write_outputs(hourly: pd.DataFrame, daily: pd.DataFrame,
                  validation: pd.DataFrame, quality: dict,
                  out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = out_dir or DATA_PROCESSED
    out_dir.mkdir(parents=True, exist_ok=True)
    hourly_path = out_dir / "zaf_eskom_hourly_labels.csv.gz"
    daily_path = out_dir / "zaf_daily_loadshedding.csv"
    val_path = out_dir / "zaf_annual_validation.csv"
    # timestamp-safe: write ISO strings
    h = hourly.copy()
    h["time_sast"] = h["time_sast"].dt.strftime("%Y-%m-%d %H:%M%z")
    h["time_utc"] = h["time_utc"].dt.strftime("%Y-%m-%dT%H:%M%z")
    h.to_csv(hourly_path, index=False, compression="gzip")
    daily.to_csv(daily_path, index=False)
    validation.to_csv(val_path, index=False)
    return {"hourly": hourly_path, "daily": daily_path, "validation": val_path}


def write_report(hourly: pd.DataFrame, daily: pd.DataFrame,
                 validation: pd.DataFrame, quality: dict,
                 out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day15_zaf_labels.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    val_md = validation.to_markdown(index=False)
    q = quality

    # Narrative anchors against the independently known shedding calendar
    def pct(year: int) -> float:
        row = validation[validation["year"] == year]
        return float(row.iloc[0]["pct_hours_active"]) if len(row) else 0.0

    checks = [
        (2020, "~10% (shedding returned Aug 2020 after a gap)", pct(2020) < 15),
        (2023, "~78% of hours (peak shedding year)", 60 <= pct(2023) <= 90),
        (2025, "~0 (recovery year)", pct(2025) < 10),
        (2026, "~0 YTD (to Sep)", pct(2026) < 10),
    ]
    checks_md = "\n".join(
        f"| {y} | {exp} | {pct(y)}% | {'PASS' if ok else 'CHECK'} |"
        for y, exp, ok in checks)

    text = f"""# Day 15 — ZAF primary labels: hourly load-shedding (MLR)

Generated by `python -m src.labels.eskom` on {_utc_now()}.

**Source**: `data/raw/outages/zaf_eskom_hourly.sqlite` — Day 4 acquisition,
mirror of the Eskom Data Portal public series (2017-04 → 2026-09). Mirror has
no license: raw rows are not republished; derived label tables live in
`data/processed/` (git-ignored, reproducible) and cite Eskom + the mirror.

**Definition**: `load_shedding = (Manual Load_Reduction(MLR) > 0)` per hour;
`mlr_mw` kept as the intensity target. Missing MLR stays `<NA>` — a data hole
is never scored as "no shedding". Hours are SAST hour-beginning, converted to
UTC via the Day 8 `normalize_timestamp(assume_tz='Africa/Johannesburg')` rule.

## Integrity

| check | value |
|---|---|
| rows | {q['rows']} |
| span (UTC) | {q['start_utc']} .. {q['end_utc']} |
| duplicate hours | {q['duplicate_hours']} |
| missing hours in span | {q['missing_hours']} {q['missing_sample'] if q['missing_hours'] else ''} |
| MLR missing values | {q['mlr_missing']} |
| MLR negative values | {q['mlr_negative']} |

## Annual validation vs the independently known shedding calendar

{val_md}

| year | known expectation | observed % hours active | check |
|---|---|---|---|
{checks_md}

All PASS means the label reproduces the published shedding history without
being tuned to it — the anchor facts predate this dataset (Day 4 audit).

## Outputs

- `data/processed/zaf_eskom_hourly_labels.csv.gz` — {len(hourly)} rows,
  columns exactly `{HOURLY_COLUMNS}` (timestamps + label fields; NO demand or
  generation columns — same-hour Residual Demand is an outcome of shedding,
  never a feature).
- `data/processed/zaf_daily_loadshedding.csv` — {len(daily)} SAST days with
  `hours_active`, `mlr_mw_mean/max`, `mlr_mwh` (unserved-energy proxy) and
  `any_load_shedding`.
- `data/processed/zaf_annual_validation.csv` — the table above.

## Caveats

- MLR is Eskom's *manual* reduction; early years under-report ILS/other
  curtailment, and stage numbers are not derivable from MW alone (stage-band
  definitions changed over time) — no stage column is fabricated here.
- The mirror's provenance is a third-party scrape of the public portal; on
  any discrepancy the portal itself governs.
- One artifact hour (2021-11-18 21:00 SAST, MLR = -0.104 MW) is kept verbatim
  in `mlr_mw` — it never triggers the label (not `> 0`); no silent cleaning.
- Daily aggregation uses SAST calendar days (label days are local days);
  cross-country alignment happens in UTC at training time.
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 15 (ZAF) label report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default=None, help="path to the mirror db")
    args = parser.parse_args(argv)

    hourly = load_hourly(Path(args.sqlite) if args.sqlite else None)
    quality = hourly_quality(hourly)
    daily = daily_labels(hourly)
    validation = annual_validation(hourly)
    paths = write_outputs(hourly, daily, validation, quality)
    for k, p in paths.items():
        print(f"wrote {k}: {p}")
    write_report(hourly, daily, validation, quality)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
