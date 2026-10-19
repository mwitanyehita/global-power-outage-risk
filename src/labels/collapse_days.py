"""NGA secondary labels: national grid-collapse days from the NERC record.

Source: ``data/raw/outages/nerc/events_extracted.csv`` (Day 10 extraction from
NERC annual reports; every row carries source document + page). NERC is the
official regulator; the raw PDFs are not republished (see Day 10 report).

Two rules drive everything here:

1. **Unknown is not zero.** NERC's 2020/2022 reports give only yearly *counts*
   (11+4+4+6 = 25 collapses in 2019-2022) and the 2019/2021 reports were never
   published — so 2019-2022 are **outside the labelable window** and are
   excluded from the daily frame rather than being zero-filled. Only
   2023-01-01 .. 2025-12-31 has fully dated events (3 + 9 + 2 = 14, reconciled
   against NERC Table 24 on Day 10).
2. **Times are WAT as printed** (Africa/Lagos) and conversion to UTC uses the
   Day 8 explicit ``assume_tz`` rule.

Output frames: ``data/processed/nga_collapse_events.csv`` (event rows) and
``data/processed/nga_daily_collapse.csv`` (daily binary labels over the
labelable window only).

Usage:
    python -m src.labels.collapse_days
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.cleaning.standardize import normalize_timestamp
from src.config import DATA_PROCESSED, DATA_RAW, REPORTS_DIR

EVENTS_CSV = DATA_RAW / "outages" / "nerc" / "events_extracted.csv"
WINDOW_START = pd.Timestamp("2023-01-01")
WINDOW_END = pd.Timestamp("2025-12-31")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_events(events_csv: Path | None = None) -> pd.DataFrame:
    """Load NERC events, attach WAT->UTC datetimes where a time is known."""
    path = events_csv or EVENTS_CSV
    df = pd.read_csv(path)
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    # Time is printed as WAT (UTC+1); empty stays missing — never invented.
    has_time = df["event_time"].notna() & df["event_time"].astype(str).str.strip().ne("")
    stamp = df["event_date"].dt.strftime("%Y-%m-%d") + " " + \
        df["event_time"].fillna("").astype(str).str.strip()
    utc = normalize_timestamp(stamp.where(has_time, None),
                              assume_tz="Africa/Lagos")
    df["event_datetime_utc"] = utc
    df["country_iso3"] = "NGA"
    return df


def labelable_events(events: pd.DataFrame) -> pd.DataFrame:
    """Event rows inside the fully-dated window (2023-2025)."""
    in_window = events["event_date"].between(WINDOW_START, WINDOW_END)
    return events[in_window].sort_values(["event_date", "event_time"])\
        .reset_index(drop=True)


def daily_collapse_labels(events: pd.DataFrame) -> pd.DataFrame:
    """Daily binary labels over the labelable window only.

    days outside the window are absent by construction — a model must not
    read 'no row' as 'no collapse'.
    """
    window = labelable_events(events)
    days = pd.DataFrame({"date": pd.date_range(WINDOW_START, WINDOW_END, freq="D")})
    pos = (window.assign(date=window["event_date"].dt.normalize())
           .groupby("date")
           .agg(collapse_count=("event_type", "size"),
                collapse_types=("event_type", lambda s: "|".join(sorted(set(s)))))
           .reset_index())
    out = days.merge(pos, on="date", how="left")
    out["collapse_count"] = out["collapse_count"].fillna(0).astype(int)
    out["collapse_types"] = out["collapse_types"].fillna("")
    out["collapse_any"] = out["collapse_count"] > 0
    out["country_iso3"] = "NGA"
    return out


def write_outputs(events: pd.DataFrame, daily: pd.DataFrame,
                  out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = out_dir or DATA_PROCESSED
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_path = out_dir / "nga_collapse_events.csv"
    daily_path = out_dir / "nga_daily_collapse.csv"

    ev = labelable_events(events).copy()
    ev["event_date"] = ev["event_date"].dt.strftime("%Y-%m-%d")
    ev["event_datetime_utc"] = ev["event_datetime_utc"].dt.strftime(
        "%Y-%m-%dT%H:%M%z")
    ev.to_csv(ev_path, index=False)

    d = daily.copy()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    d.to_csv(daily_path, index=False)
    return {"events": ev_path, "daily": daily_path}


def write_report(events: pd.DataFrame, daily: pd.DataFrame,
                 out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day17_nga_labels.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    window = labelable_events(events)
    by_year = (window.assign(year=window["event_date"].dt.year)
               .groupby("year")
               .agg(events=("event_type", "size"),
                    partial=("event_type", lambda s: int((s == "partial").sum())),
                    full=("event_type", lambda s: int((s == "full").sum())),
                    timed=("event_time", lambda s: int(s.notna().sum())))
               .reset_index())
    n_pos = int(daily["collapse_any"].sum())
    n_days = len(daily)
    prevalence = round(100 * n_pos / n_days, 2)

    text = f"""# Day 17 — NGA secondary labels: national grid-collapse days

Generated by `python -m src.labels.collapse_days` on {_utc_now()}.

**Source**: `data/raw/outages/nerc/events_extracted.csv` (Day 10, NERC annual
reports; counts reconciled with NERC Table 24 — 2023: 3, 2024: 9, 2025: 2).

## Labelable window (the honesty rule)

| period | status | why |
|---|---|---|
| 2019-2022 | **NOT labelable** | NERC 2020/2022 reports carry yearly counts only (25 collapses total); 2019/2021 reports were never published (Day 10 Wayback-verified). Excluded from the daily frame — "no row" must never read as "no collapse". |
| 2023-01-01 .. 2025-12-31 | **labelable** | fully dated events, per-event provenance (document + page) |

## Window events

{by_year.to_markdown(index=False)}

- {len(window)} events across {n_days} days: **{n_pos} positive days
  (prevalence {prevalence}%)** — a rare-event target; accuracy is meaningless
  here, PR-AUC against the prevalence baseline is the honest metric (Day 20).
- Times are WAT as printed in NERC tables; UTC timestamps are attached only
  where a time exists ({int(by_year['timed'].sum())} of {len(window)} events).

## Outputs

- `data/processed/nga_collapse_events.csv` — {len(window)} dated events with
  type, restoration time, UTC stamp, source document/page provenance.
- `data/processed/nga_daily_collapse.csv` — {n_days} daily rows (window only)
  with `collapse_any`, `collapse_count`, `collapse_types`.

## Caveats

- National-level only: a collapse is a whole-grid event, so these labels say
  nothing about which neighborhood lost power (see KEN/ZAF for locality).
- `event_type` partial/full follows NERC's own tables; 2023 types were
  inferred from the summary table (Day 10 caveat list).
- Collapses are not the only Nigerian outages (load reductions, plant trips,
  distribution faults) — this target is "grid collapse days", stated narrowly.
- Never mix with planned KPLC notices (KEN) or ZAF MLR hours in one label
  column; each target keeps its own definition and window.
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 17 (NGA) label report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=None, help="events_extracted.csv path")
    args = parser.parse_args(argv)

    events = load_events(Path(args.events) if args.events else None)
    daily = daily_collapse_labels(events)
    paths = write_outputs(events, daily)
    for k, p in paths.items():
        print(f"wrote {k}: {p}")
    write_report(events, daily)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
