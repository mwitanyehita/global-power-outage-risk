"""ZAF day-ahead training dataset: labels + strictly-past weather features.

Assembles the primary modeling frame from two settled pieces:

* labels — ``data/processed/zaf_daily_loadshedding.csv`` (Day 15; SAST days)
* weather — the Day 3 9-city archive, ZAF cities only, aggregated to **SAST
  days** so label days and weather days are the same local calendar day

Day-ahead honesty rule: a forecast issued at the start of day D may only use
weather from days < D. Every weather-derived feature therefore goes through
``src.features.rolling.add_lags`` (>= 1) and ``add_past_rolling`` (windows
applied to the shifted series) — the other session's leakage-safe primitives.
Same-day weather is *never* a feature here; a nowcast variant would need to
say so explicitly. Calendar features (month, day of week, weekend) are known
in advance by construction and are allowed.

The time key is tz-aware SAST midnight (SAST has no DST, so the grid is
exactly 1D); ``assert_regular_grid`` enforces it before any rolling window
is counted in rows.

Outputs: ``data/processed/zaf_daily_dataset.csv.gz`` and
``reports/day16_zaf_dataset.md``.

Usage:
    python -m src.datasets.zaf_daily
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import DATA_PROCESSED, REPORTS_DIR
from src.features.rolling import (
    add_lags, add_past_rolling, assert_regular_grid, sort_by_time,
)
from src.ingestion import weather as wx

LABELS_CSV = DATA_PROCESSED / "zaf_daily_loadshedding.csv"
TIME_COL = "date_sast"

# Weather variables used as day-ahead inputs, per city.
WEATHER_VARS = ("temperature_2m", "wind_gusts_10m", "precipitation")
LAGS = (1, 2, 3)
ROLLING_WINDOWS = (7, 28)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Weather side
# ---------------------------------------------------------------------------

def _city_daily(city_key: str, frame: pd.DataFrame) -> pd.DataFrame:
    """One city's hourly rows -> SAST-day aggregates, prefixed by city."""
    sast = frame["time"].dt.tz_convert("Africa/Johannesburg")
    daily = (
        frame.assign(date_sast=sast.dt.normalize())
        .groupby("date_sast")
        .agg(
            temp_mean=("temperature_2m", "mean"),
            temp_max=("temperature_2m", "max"),
            gust_max=("wind_gusts_10m", "max"),
            precip_sum=("precipitation", "sum"),
            wind_mean=("wind_speed_10m", "mean"),
            humidity_mean=("relative_humidity_2m", "mean"),
            pressure_mean=("surface_pressure", "mean"),
        )
        .add_prefix(f"{city_key}_")
    )
    daily.index.name = TIME_COL
    return daily.reset_index()


def zaf_weather_daily(weather_dir: Path | None = None) -> pd.DataFrame:
    """SAST-day weather panel for the three ZAF cities, one row per day."""
    weather_dir = weather_dir or wx.WEATHER_DIR
    cities = [c for c in wx.CITIES if c["country"] == "ZAF"]
    frames = []
    for c in cities:
        key = c["city"].lower().replace(" ", "-")
        path = weather_dir / f"ZAF_{key}.json"
        if not path.exists():
            continue
        frames.append(_city_daily(key, wx.load_city(path)))
    if not frames:
        return pd.DataFrame(columns=[TIME_COL])
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=TIME_COL, how="outer")
    return out.sort_values(TIME_COL).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Labels side
# ---------------------------------------------------------------------------

def load_labels(labels_csv: Path | None = None) -> pd.DataFrame:
    """Daily label rows keyed by tz-aware SAST midnight."""
    path = labels_csv or LABELS_CSV
    df = pd.read_csv(path)
    df[TIME_COL] = pd.to_datetime(df["date"]).dt.tz_localize("Africa/Johannesburg")
    return df[[TIME_COL, "hours_active", "mlr_mw_mean", "any_load_shedding"]]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Advance-known calendar features (no fitting, hence no leakage)."""
    out = frame.copy()
    dow = out[TIME_COL].dt.dayofweek
    out["dow"] = dow
    out["month"] = out[TIME_COL].dt.month
    out["is_weekend"] = (dow >= 5).astype(int)
    doy = out[TIME_COL].dt.dayofyear
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return out


def _add_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach strictly-past lag/rolling/calendar features to an aligned panel.

    The input must be a chronological daily panel with the label columns and
    the three ZAF city weather columns present; the output carries **only**
    the whitelist below (same-day raw weather is consumed and dropped).
    """
    city_cols = sorted(
        c for c in frame.columns
        if c.split("_", 1)[0] in {"johannesburg", "cape-town", "durban"}
    )
    lag_cols = [f"{c}_lag_{n}" for c in city_cols for n in LAGS]
    rolling_cols = [f"{c}_past_mean_{w}" for c in city_cols
                    for w in ROLLING_WINDOWS]
    rolling_cols += [f"{c}_past_max_{w}" for c in city_cols
                     if c.endswith("gust_max") for w in ROLLING_WINDOWS]

    out = add_lags(frame, list(city_cols), list(LAGS), time_col=TIME_COL)
    # Autoregressive label lags: at the start of day D, day D-1's observed
    # shedding is known — operationally legitimate and the strongest honest
    # signal. Same-day label values stay label columns, never features.
    label_feats = ["hours_active", "mlr_mw_mean"]
    out = add_lags(out, label_feats, list(LAGS), time_col=TIME_COL)
    label_lag_cols = [f"{c}_lag_{n}" for c in label_feats for n in LAGS]
    out = add_past_rolling(out, list(city_cols), list(ROLLING_WINDOWS),
                           time_col=TIME_COL, stat="mean")
    gust_cols = [c for c in city_cols if c.endswith("gust_max")]
    out = add_past_rolling(out, gust_cols, list(ROLLING_WINDOWS),
                           time_col=TIME_COL, stat="max")
    out = _calendar_features(out)

    # Same-day weather is dropped AFTER the past features consume it, so the
    # frame carries zero same-day weather columns (leakage whitelist below).
    keep = [TIME_COL, "hours_active", "mlr_mw_mean", "any_load_shedding",
            "dow", "month", "is_weekend", "doy_sin", "doy_cos"]
    return out[keep + lag_cols + label_lag_cols + rolling_cols]


def build_dataset(weather_dir: Path | None = None,
                  labels_csv: Path | None = None) -> pd.DataFrame:
    """Inner-join labels and weather on SAST days, add strictly-past features."""
    weather = zaf_weather_daily(weather_dir)
    labels = load_labels(labels_csv)
    if weather.empty:
        raise FileNotFoundError(
            "no ZAF weather downloads found — run: python -m src.ingestion.weather")
    if labels.empty:
        raise FileNotFoundError(
            "no ZAF daily labels found — run: python -m src.labels.eskom")

    frame = labels.merge(weather, on=TIME_COL, how="inner")
    frame = sort_by_time(frame, TIME_COL)
    assert_regular_grid(frame, TIME_COL, freq="1D")
    return _add_features(frame)


def build_forecast_frame(weather_dir: Path | None = None,
                         labels_csv: Path | None = None) -> pd.DataFrame:
    """Latest feature-complete day in the panel — day-ahead mode.

    The outer join keeps weather days that have no label yet; their label
    lags stay missing (never forward-filled), so when the weather feed
    extends past the label cutoff only the first such day has a complete,
    strictly-past feature row. That row is the latest honest day-ahead
    prediction the snapshot supports. If the weather feed does not extend
    past the cutoff, the last labelled day is returned instead.

    Returns a one-row frame (label columns present but unset) or raises when
    the raw weather and label inputs are absent.
    """
    weather = zaf_weather_daily(weather_dir)
    labels = load_labels(labels_csv)
    if weather.empty:
        raise FileNotFoundError(
            "no ZAF weather downloads found — run: python -m src.ingestion.weather")
    if labels.empty:
        raise FileNotFoundError(
            "no ZAF daily labels found — run: python -m src.labels.eskom")

    frame = labels.merge(weather, on=TIME_COL, how="outer")
    frame = sort_by_time(frame, TIME_COL).reset_index(drop=True)
    assert_regular_grid(frame, TIME_COL, freq="1D")
    out = _add_features(frame)
    feats = feature_columns(out)
    complete = out.dropna(subset=feats)
    if complete.empty:
        raise ValueError(
            "no feature-complete day after the label cutoff — the weather "
            "feed does not extend past the last label day")
    return complete.tail(1).reset_index(drop=True)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    """Non-label columns (the modeling X)."""
    labels = {TIME_COL, "hours_active", "mlr_mw_mean", "any_load_shedding"}
    return [c for c in frame.columns if c not in labels]


def leakage_audit(frame: pd.DataFrame) -> list[str]:
    """Return violations of the day-ahead rule (empty list == clean)."""
    problems = []
    for col in feature_columns(frame):
        if not any(tag in col for tag in ("_lag_", "_past_")) \
                and col not in {"dow", "month", "is_weekend", "doy_sin", "doy_cos"}:
            problems.append(f"{col}: not a lag/rolling/calendar feature")
    return problems


def write_outputs(frame: pd.DataFrame,
                  out_dir: Path | None = None) -> Path:
    out_dir = out_dir or DATA_PROCESSED
    out_dir.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out[TIME_COL] = out[TIME_COL].dt.strftime("%Y-%m-%d")
    path = out_dir / "zaf_daily_dataset.csv.gz"
    out.to_csv(path, index=False, compression="gzip")
    return path


def write_report(frame: pd.DataFrame, out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day16_zaf_dataset.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    feats = feature_columns(frame)
    problems = leakage_audit(frame)
    n_active = int(frame["any_load_shedding"].sum())
    span = (f"{frame[TIME_COL].min():%Y-%m-%d} .. "
            f"{frame[TIME_COL].max():%Y-%m-%d}")
    nan_head = int(frame[feats].isna().any(axis=1).sum())

    text = f"""# Day 16 — ZAF day-ahead training dataset

Generated by `python -m src.datasets.zaf_daily` on {_utc_now()}.

**Rows**: {len(frame)} SAST days ({span}) · **positive days**: {n_active}
({100 * n_active / max(len(frame), 1):.1f}%) · **features**: {len(feats)}

## Composition

- Labels: Day 15 SAST daily aggregation (`hours_active`, `mlr_mw_mean`,
  `any_load_shedding`) — inner-joined on SAST calendar days.
- Weather: Johannesburg, Cape Town, Durban Day 3 archive aggregated to SAST
  days (temp mean/max, daily-max gust, precipitation sum, wind/humidity/
  pressure means).
- Features (all strictly past): lags {LAGS} and past-rolling windows
  {ROLLING_WINDOWS} (mean; gust also max) via
  `src.features.rolling.add_lags` / `add_past_rolling`, on a grid verified
  with `assert_regular_grid(freq='1D')` before any window was counted;
  plus autoregressive label lags (hours_active / mlr_mw_mean at lags
  {LAGS} — yesterday's observed shedding is known at forecast time).
- Calendar: dow, month, is_weekend, day-of-year sin/cos — known in advance
  by construction.

## Leakage invariants (enforced + tested)

- {len(problems)} same-day or future-derived feature(s)
  {"- clean" if not problems else "- " + str(problems)}
- Modifying day D's weather can change features only on days > D
  (perturbation test in `tests/test_datasets_zaf.py`).
- The dataset frame carries NO raw same-day weather columns at all
  (they are consumed into lag/rolling features and then dropped).

## Honest gaps

- {nan_head} leading rows carry NaN lags/rolling values (no past history);
  models must drop them, not fill them forward.
- Weather span starts 2021-01-01, so the dataset cannot reach back into
  2017-2020 even though labels do; the 2023 peak-shedding year is fully
  covered.
- Day-ahead means calendar + past weather only — no same-day temperature
  or gust. A same-day variant is a *nowcast* and must be labeled as such.

## Outputs

- `data/processed/zaf_daily_dataset.csv.gz` — {len(frame)} rows,
  {len(frame.columns)} columns (date key `date_sast` as tz-aware SAST
  midnight, labels, {len(feats)} features).
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 16 dataset report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    frame = build_dataset()
    problems = leakage_audit(frame)
    if problems:
        print("LEAKAGE:", problems)
        return 1
    path = write_outputs(frame)
    print(f"wrote dataset: {path} ({len(frame)} rows, "
          f"{len(feature_columns(frame))} features)")
    write_report(frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
