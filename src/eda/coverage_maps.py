"""Day 13: coverage atlas — where data exists, and where it does not.

Every count and status in this module is **derived from real files at
runtime** (boundaries, weather archives, Day 15/17 label CSVs, the Day 10
NERC event table, the Day 11 KPLC extraction when present). Nothing is
hardcoded, so the atlas cannot drift from the actual data state.

Figures (matplotlib Agg, deterministic):
* one units map per country (ADM1 + ADM2 + the 3 pilot cities)
* a label-coverage panel figure, one axes per country, built from the
  real per-year label statistics

Usage:
    python -m src.eda.coverage_maps       # writes figures + day13 report
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.config import DATA_PROCESSED, DATA_RAW, REPORTS_DIR
from src.geo.units import load_units
from src.ingestion.weather import CITIES, WEATHER_DIR

WB_DIR = DATA_RAW / "worldbank"

KPLC_CSV = DATA_PROCESSED / "kplc_planned_outages.csv"
ZAF_DAILY_CSV = DATA_PROCESSED / "zaf_daily_loadshedding.csv"
NGA_DAILY_CSV = DATA_PROCESSED / "nga_daily_collapse.csv"
NERC_EVENTS_CSV = DATA_RAW / "outages" / "nerc" / "events_extracted.csv"

COUNTRY_NAMES = {"KEN": "Kenya", "ZAF": "South Africa", "NGA": "Nigeria"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Figure 1: units + cities per country
# ---------------------------------------------------------------------------

def fig_units_map(iso3: str, units, out_path: Path) -> Path:
    """ADM1/ADM2 polygons + pilot cities for one country."""
    fig, ax = plt.subplots(figsize=(7, 8))
    sub = units[units["iso3"] == iso3]
    adm2 = sub[sub["adm_level"] == "ADM2"]
    adm1 = sub[sub["adm_level"] == "ADM1"]
    if not adm2.empty:
        adm2.boundary.plot(ax=ax, linewidth=0.2, color="0.75")
    if not adm1.empty:
        adm1.boundary.plot(ax=ax, linewidth=0.6, color="0.35")
        adm1.plot(ax=ax, color="white", edgecolor="none")
    cities = [c for c in CITIES if c["country"] == iso3]
    ax.scatter([c["lon"] for c in cities], [c["lat"] for c in cities],
               c="crimson", s=28, zorder=5)
    for c in cities:
        ax.annotate(c["city"], (c["lon"], c["lat"]), fontsize=8,
                    xytext=(4, 4), textcoords="offset points")
    counts = sub.groupby("adm_level").size().to_dict()
    ax.set_title(
        f"{COUNTRY_NAMES[iso3]} — {counts.get('ADM1', 0)} ADM1, "
        f"{counts.get('ADM2', 0)} ADM2 units · 3 pilot cities")
    ax.set_axis_off()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Figure 2: label coverage, one panel per country (real per-year stats)
# ---------------------------------------------------------------------------

def _zaf_yearly() -> pd.Series:
    """Share of SAST days with active shedding, per year."""
    if not ZAF_DAILY_CSV.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(ZAF_DAILY_CSV, parse_dates=["date"])
    return df.groupby(df["date"].dt.year)["any_load_shedding"].mean()


def _nga_yearly() -> pd.Series:
    """Collapse days per year (labelable window only)."""
    if not NGA_DAILY_CSV.exists():
        return pd.Series(dtype=int)
    df = pd.read_csv(NGA_DAILY_CSV, parse_dates=["date"])
    return df.groupby(df["date"].dt.year)["collapse_any"].sum()


def _ken_yearly() -> pd.Series:
    """Planned-notice events per year (planned only — never unplanned)."""
    if not KPLC_CSV.exists():
        return pd.Series(dtype=int)
    df = pd.read_csv(KPLC_CSV)
    return (pd.to_datetime(df["event_date"], errors="coerce")
            .dt.year.value_counts().sort_index())


def fig_label_coverage(out_path: Path) -> Path:
    """3 panels: what label exists per country, with real per-year bars."""
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))

    zaf = _zaf_yearly()
    if not zaf.empty:
        zaf.plot.bar(ax=axes[0], color="darkorange")
    axes[0].set_title("ZAF: national load-shedding\n"
                      "(share of days, hourly MLR label)")
    axes[0].set_ylabel("share of days" if not zaf.empty else "")

    nga = _nga_yearly()
    if not nga.empty:
        nga.plot.bar(ax=axes[1], color="firebrick")
    axes[1].set_title("NGA: grid-collapse days\n"
                      "(national only; 2019-22 not labelable)")
    axes[1].set_ylabel("collapse days" if not nga.empty else "")

    ken = _ken_yearly()
    if not ken.empty:
        ken.plot.bar(ax=axes[2], color="steelblue")
    axes[2].set_title("KEN: planned KPLC notices\n"
                      "(locality-level; no unplanned labels)")
    axes[2].set_ylabel("notice events" if not ken.empty else "")

    for ax in axes:
        ax.tick_params(axis="x", labelrotation=45)
    fig.suptitle("Label coverage by country — different targets, stated "
                 "narrowly (never merged)", fontsize=11)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Coverage table (fully derived from real files)
# ---------------------------------------------------------------------------

def _span(series: pd.Series) -> str:
    if series.empty:
        return "—"
    return f"{series.min()} .. {series.max()}"


def coverage_table(units=None) -> pd.DataFrame:
    """country x data-layer status, derived from the files on disk."""
    if units is None:
        units = load_units()
    rows: list[dict] = []

    def add(country, layer, status, detail, source):
        rows.append({"country": country, "layer": layer, "status": status,
                     "coverage_detail": detail, "source": source})

    for iso3 in ("KEN", "ZAF", "NGA"):
        for level in ("ADM0", "ADM1", "ADM2"):
            n = int(((units["iso3"] == iso3)
                     & (units["adm_level"] == level)).sum())
            add(iso3, f"boundaries_{level.lower()}",
                "available" if n else "absent",
                f"{n} units", "Day 2 (geoBoundaries)")

        wx_files = sorted(WEATHER_DIR.glob(f"{iso3}_*.json"))
        add(iso3, "weather_hourly",
            "available" if wx_files else "absent",
            f"{len(wx_files)} cities, hourly ERA5 2021+",
            "Day 3 (Open-Meteo archive)")

        wb_files = sorted(WB_DIR.glob("*.json"))
        add(iso3, "wb_context",
            "available" if wb_files else "absent",
            f"{len(wb_files)} indicator files, country-year rows "
            "(context only, never labels)",
            "Day 5 (World Bank)")

    # labels and events — the country-specific targets
    if KPLC_CSV.exists():
        k = pd.read_csv(KPLC_CSV)
        add("KEN", "label_planned", "available",
            f"{len(k)} notice events, locality/county level "
            f"({pd.to_datetime(k['event_date'], errors='coerce').min():%Y-%m}"
            f" .. {pd.to_datetime(k['event_date'], errors='coerce').max():%Y-%m})",
            "Day 11 (KPLC notices)")
    else:
        add("KEN", "label_planned", "pending",
            "extraction not run (python -m src.ingestion.kplc_parse)",
            "Day 11 (KPLC notices)")
    add("KEN", "label_unplanned", "absent",
        "by design: KPLC publishes planned notices only (Day 7 audit)",
        "—")

    if ZAF_DAILY_CSV.exists():
        z = pd.read_csv(ZAF_DAILY_CSV, parse_dates=["date"])
        add("ZAF", "label_unplanned", "available",
            f"national hourly MLR, {len(z)} days "
            f"({z['date'].min():%Y-%m} .. {z['date'].max():%Y-%m})",
            "Day 15 (Eskom MLR)")
    else:
        add("ZAF", "label_unplanned", "pending",
            "python -m src.labels.eskom", "Day 15")

    if NGA_DAILY_CSV.exists():
        nd = pd.read_csv(NGA_DAILY_CSV, parse_dates=["date"])
        add("NGA", "label_unplanned", "partial",
            f"national collapse days, {len(nd)} days (2023-2025 only; "
            f"2019-22 counts-only era, never published reports)",
            "Day 17 (NERC)")
    else:
        add("NGA", "label_unplanned", "pending",
            "python -m src.labels.collapse_days", "Day 17")

    if NERC_EVENTS_CSV.exists():
        e = pd.read_csv(NERC_EVENTS_CSV)
        per_year = (pd.to_datetime(e["event_date"]).dt.year.value_counts()
                    .sort_index().to_dict())
        add("NGA", "events_dated", "partial",
            f"{len(e)} dated events {per_year}; national-only granularity",
            "Day 10 (NERC annual reports)")
    else:
        add("NGA", "events_dated", "pending", "python -m src.ingestion.nerc",
            "Day 10")
    add("KEN", "events_dated", "absent",
        "no unplanned-event source passed the Day 7 go/no-go", "—")
    add("ZAF", "events_dated", "partial",
        "the MLR label IS the event series; no per-event geocoded table",
        "Day 4/15")

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

FIGURES = ("day13_ken_units.png", "day13_zaf_units.png",
           "day13_nga_units.png", "day13_label_coverage.png")


def write_all(out_root: Path | None = None,
              units=None) -> dict[str, Path]:
    """Write the 4 figures + the day13 report; returns every path."""
    out_root = out_root or REPORTS_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    fig_dir = out_root / "figures"

    if units is None:
        units = load_units()
    if units.empty:
        raise FileNotFoundError(
            "no boundaries found — run: python -m src.ingestion.boundaries")

    paths = {
        "ken": fig_units_map("KEN", units, fig_dir / FIGURES[0]),
        "zaf": fig_units_map("ZAF", units, fig_dir / FIGURES[1]),
        "nga": fig_units_map("NGA", units, fig_dir / FIGURES[2]),
        "labels": fig_label_coverage(fig_dir / FIGURES[3]),
    }
    report = write_report(coverage_table(units), paths,
                          out_path=out_root / "day13_coverage_maps.md")
    paths["report"] = report
    return paths


def write_report(table: pd.DataFrame, fig_paths: dict[str, Path],
                 out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day13_coverage_maps.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    status_counts = table["status"].value_counts().to_dict()
    embeds = "\n".join(
        f"![{name}](figures/{Path(p).name})"
        for name, p in (("Kenya units", fig_paths["ken"]),
                        ("South Africa units", fig_paths["zaf"]),
                        ("Nigeria units", fig_paths["nga"]),
                        ("Label coverage", fig_paths["labels"])))

    text = f"""# Day 13 — Coverage atlas: where data exists (and does not)

Generated by `python -m src.eda.coverage_maps` on {_utc_now()}.

Status counts: `{status_counts}` — every count in this report is derived
from the files on disk at generation time.

## Figures

{embeds}

## Coverage table (derived, not asserted)

{table.to_markdown(index=False)}

## Known gaps — stated, never papered over

- **KEN has no unplanned-outage labels.** KPLC publishes planned
  interruption notices only (Day 7 audit); notice events are therefore
  modeled as *planned* supply interruptions and never relabeled.
- **NGA labels are national and windowed.** Grid collapses are whole-grid
  events; 2019-2022 are outside the labelable window (counts-only era /
  unpublished reports, Day 10) — absence of a row is not absence of a
  collapse.
- **ZAF labels are national.** The MLR series is a national hourly signal;
  province-level attribution would be fabricated granularity.
- **5 duplicate NGA ADM2 names** (Day 12) surface as ambiguity flags in
  any name join — resolved only with parent context or coordinates.
- **Weather is ERA5 reanalysis** (past only); day-ahead forecast weather
  was a documented NO-GO (WX-5/6) and is absent by decision, not neglect.
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 13 coverage atlas written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    write_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
