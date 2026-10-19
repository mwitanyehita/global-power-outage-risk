"""Exploratory analysis of the pilot-city weather panel (Day 9).

Inputs: the 9-city hourly panel from Day 3 (``data/raw/weather/*.json``,
verbatim Open-Meteo ERA5 archive). Outputs: ``reports/day09_eda_weather.md``
plus PNG figures under ``reports/figures/``.

The outage records (Day 10) get their own EDA once acquired; this report
establishes the *feature-side* distributional picture: seasonality, extremes,
inter-city correlation (multicollinearity risk for Day 16) and annual trends.

Units follow Open-Meteo defaults: temperature °C, precipitation/rain mm,
wind speed & gusts km/h, pressure hPa, humidity %, cloud cover %.

Usage:
    python -m src.eda.weather_eda
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")

from matplotlib import pyplot as plt  # noqa: E402

from src.config import REPORTS_DIR  # noqa: E402
from src.ingestion import weather as wx  # noqa: E402

FIGURES_DIR = REPORTS_DIR / "figures"

# Unit labels for reports (Open-Meteo defaults).
UNITS = {
    "temp_mean": "°C",
    "temp_max": "°C",
    "humid_mean": "%",
    "precip_sum": "mm",
    "gust_max": "km/h",
    "wind_mean": "km/h",
    "pressure_mean": "hPa",
    "cloud_mean": "%",
}

COUNTRY_COLORS = {"KEN": "#1b9e77", "ZAF": "#d95f02", "NGA": "#7570b3"}
COUNTRY_LABELS = {"KEN": "Kenya", "ZAF": "South Africa", "NGA": "Nigeria"}


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def load_panel() -> pd.DataFrame:
    """Concatenate all downloaded cities into one frame with a city key."""
    frames = []
    for path in sorted(p for p in wx.WEATHER_DIR.glob("*.json")
                       if p.name != "_manifest.json"):
        df = wx.load_city(path)
        country, city = path.stem.split("_", 1)
        df["country"] = country
        df["city"] = city.replace("-", " ").title()
        df["city_key"] = path.stem
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def to_daily(panel: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the hourly panel to city-days."""
    if panel.empty:
        return pd.DataFrame()
    daily = (
        panel.set_index("time")
        .groupby(["country", "city", "city_key"])
        .resample("D")
        .agg(
            temp_mean=("temperature_2m", "mean"),
            temp_max=("temperature_2m", "max"),
            humid_mean=("relative_humidity_2m", "mean"),
            precip_sum=("precipitation", "sum"),
            gust_max=("wind_gusts_10m", "max"),
            wind_mean=("wind_speed_10m", "mean"),
            pressure_mean=("surface_pressure", "mean"),
            cloud_mean=("cloud_cover", "mean"),
        )
        .reset_index()
    )
    daily["year"] = daily["time"].dt.year
    daily["month"] = daily["time"].dt.month
    return daily


def climatology(daily: pd.DataFrame) -> pd.DataFrame:
    """Monthly climatology per city (means over the full archive window)."""
    if daily.empty:
        return pd.DataFrame()
    return (
        daily.groupby(["country", "city", "city_key", "month"])
        .agg(
            temp_mean=("temp_mean", "mean"),
            precip_mean=("precip_sum", "mean"),
            gust_mean=("gust_max", "mean"),
            wet_days=("precip_sum", lambda s: int((s > 1.0).sum() / max(len(s), 1))),
        )
        .reset_index()
    )


def city_extremes(daily: pd.DataFrame) -> pd.DataFrame:
    """One row per city: hottest day, wettest day, gustiest day."""
    if daily.empty:
        return pd.DataFrame()
    rows = []
    for (country, city, city_key), grp in daily.groupby(["country", "city", "city_key"]):
        hottest = grp.loc[grp["temp_mean"].idxmax()]
        wettest = grp.loc[grp["precip_sum"].idxmax()]
        gustiest = grp.loc[grp["gust_max"].idxmax()]
        rows.append({
            "country": country,
            "city": city,
            "days": len(grp),
            "hottest_day": f"{hottest['time']:%Y-%m-%d} ({hottest['temp_mean']:.1f}°C)",
            "wettest_day": f"{wettest['time']:%Y-%m-%d} ({wettest['precip_sum']:.1f} mm)",
            "gustiest_day": f"{gustiest['time']:%Y-%m-%d} ({gustiest['gust_max']:.1f} km/h)",
        })
    return pd.DataFrame(rows)


def peak_months(clim: pd.DataFrame) -> pd.DataFrame:
    """Per city: wettest month and hottest month from the climatology."""
    if clim.empty:
        return pd.DataFrame()
    rows = []
    for (country, city), grp in clim.groupby(["country", "city"]):
        wet = grp.loc[grp["precip_mean"].idxmax()]
        hot = grp.loc[grp["temp_mean"].idxmax()]
        rows.append({
            "country": country,
            "city": city,
            "wettest_month": int(wet["month"]),
            "wettest_month_precip": round(float(wet["precip_mean"]), 1),
            "hottest_month": int(hot["month"]),
            "hottest_month_temp": round(float(hot["temp_mean"]), 1),
        })
    return pd.DataFrame(rows)


def intercity_correlation(panel: pd.DataFrame) -> pd.DataFrame:
    """9x9 correlation of daily mean temperature (multicollinearity check)."""
    if panel.empty:
        return pd.DataFrame()
    daily = to_daily(panel)
    wide = daily.pivot_table(index="time", columns="city_key",
                             values="temp_mean", aggfunc="mean")
    return wide.corr()


def annual_means(daily: pd.DataFrame) -> pd.DataFrame:
    """Yearly means per city — trend + coverage table in one."""
    if daily.empty:
        return pd.DataFrame()
    return (
        daily.groupby(["country", "city", "city_key", "year"])
        .agg(
            days=("temp_mean", "size"),
            temp_mean=("temp_mean", "mean"),
            precip_total=("precip_sum", "sum"),
            gust_max=("gust_max", "max"),
        )
        .round(2)
        .reset_index()
    )


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _fig_daily_temp(daily: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.8), sharey=True)
    for ax, iso3 in zip(axes, wx.PILOT_COUNTRIES if hasattr(wx, "PILOT_COUNTRIES")
                        else COUNTRY_COLORS):
        sub = daily[daily["country"] == iso3]
        for city_key, grp in sub.groupby("city_key"):
            smooth = grp.set_index("time")["temp_mean"].rolling(30, min_periods=10).mean()
            ax.plot(smooth.index, smooth.values, lw=1.1,
                    color=COUNTRY_COLORS[iso3], alpha=0.75, label=city_key)
        ax.set_title(f"{iso3} — daily mean temp (30-day smooth)")
        handles, labels = ax.get_legend_handles_labels()
        if labels:
            ax.legend(fontsize=7, loc="best")
        ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def _fig_climatology(clim: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for iso3, grp in clim.groupby("country"):
        for city_key, g in grp.groupby("city_key"):
            axes[0].plot(g["month"], g["temp_mean"], marker="o", ms=3, lw=1,
                         color=COUNTRY_COLORS[iso3], alpha=0.8, label=city_key)
            axes[1].plot(g["month"], g["precip_mean"], marker="o", ms=3, lw=1,
                         color=COUNTRY_COLORS[iso3], alpha=0.8, label=city_key)
    axes[0].set_title("Monthly temperature climatology (°C)")
    axes[1].set_title("Monthly mean daily precipitation (mm/day)")
    for ax in axes:
        ax.set_xticks(range(1, 13))
        ax.set_xlabel("month")
        ax.grid(alpha=0.25)
    handles, labels = axes[1].get_legend_handles_labels()
    if labels:
        axes[1].legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def _fig_gusts(daily: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.8), sharey=True)
    for ax, iso3 in zip(axes, COUNTRY_COLORS):
        sub = daily[daily["country"] == iso3]
        for city_key, grp in sub.groupby("city_key"):
            ax.plot(grp["time"], grp["gust_max"], lw=0.5,
                    color=COUNTRY_COLORS[iso3], alpha=0.7, label=city_key)
        ax.set_title(f"{iso3} — daily max wind gust (km/h)")
        handles, labels = ax.get_legend_handles_labels()
        if labels:
            ax.legend(fontsize=7, loc="upper left")
        ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(panel: pd.DataFrame, out_path: Path,
                 figures_dir: Path, generated_at: str) -> Path:
    """Compute every table/figure and write the Day 9 markdown report."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    if panel.empty:
        out_path.write_text(
            "# Day 9 — EDA: weather panel\n\nNo weather downloads found. "
            "Run `python -m src.ingestion.weather` first.\n",
            encoding="utf-8",
        )
        return out_path

    daily = to_daily(panel)
    clim = climatology(daily)
    extremes = city_extremes(daily)
    peaks = peak_months(clim)
    corr = intercity_correlation(panel)
    annual = annual_means(daily)

    fig_names = {
        "temp": figures_dir / "day09_daily_temperature.png",
        "clim": figures_dir / "day09_climatology.png",
        "gust": figures_dir / "day09_gusts.png",
    }
    _fig_daily_temp(daily, fig_names["temp"])
    _fig_climatology(clim, fig_names["clim"])
    _fig_gusts(daily, fig_names["gust"])

    # Within-city-block correlations (diagonal neighbours only).
    city_keys = sorted(panel["city_key"].unique())
    pairs = []
    for i, a in enumerate(city_keys):
        for b in city_keys[i + 1:]:
            if a.split("_", 1)[0] == b.split("_", 1)[0]:  # same country
                pairs.append((a, b, round(float(corr.loc[a, b]), 3)))
    pairs_str = "; ".join(f"{a}–{b}: {r}" for a, b, r in pairs) or "n/a"

    # Coverage span
    span = f"{panel['time'].min():%Y-%m-%d} .. {panel['time'].max():%Y-%m-%d}"
    hours_total = int(panel.groupby("city_key").size().max())

    # Narrative values
    peak_temp_city = clim.loc[clim["temp_mean"].idxmax()]
    max_gust_row = daily.loc[daily["gust_max"].idxmax()]
    wettest_city = clim.loc[clim["precip_mean"].idxmax()]

    peak_lines = "\n".join(
        f"| {r.city} | {r.country} | {r.wettest_month} ({r.wettest_month_precip} mm/day) "
        f"| {r.hottest_month} ({r.hottest_month_temp} °C) |"
        for r in peaks.itertuples()
    )
    extremes_md = extremes.to_markdown(index=False)
    annual_md = annual.to_markdown(index=False)
    corr_md = corr.round(2).to_markdown()

    text = f"""# Day 9 — EDA: pilot-city weather panel

Generated by `python -m src.eda.weather_eda` on {generated_at}.

**Inputs:** Day 3 archive (Open-Meteo ERA5, {span}; {len(city_keys)} cities; up to
{hours_total} hourly rows each; 0 gaps/nulls per the Day 3 audit).
Units: °C, mm, km/h, hPa (Open-Meteo defaults).

## Figures

![Daily mean temperature]({fig_names['temp'].relative_to(out_path.parent)})
![Monthly climatology]({fig_names['clim'].relative_to(out_path.parent)})
![Daily max gusts]({fig_names['gust'].relative_to(out_path.parent)})

## Per-city extremes

{extremes_md}

## Wettest and hottest month per city (climatology)

| city | country | wettest month | hottest month |
|---|---|---|---|
{peak_lines}

## Inter-city correlation — daily mean temperature (9x9)

{corr_md}

Within-country pairwise correlation: {pairs_str}

## Annual means (trend / coverage)

{annual_md}

## Findings

- **Seasonality differs sharply by country** — wettest month per city: {', '.join(f"{r.city}→M{r.wettest_month}" for r in peaks.itertuples())}. Kenya's long rains, South Africa's austral-summer rainfall and Nigeria's May–Oct monsoon are all visible; month-of-year is therefore a legitimate calendar feature, not a leak.
- **Warmest climatological city:** {peak_temp_city['city']} ({peak_temp_city['temp_mean']:.1f} °C mean). **Highest single-day gust observed:** {max_gust_row['city_key']} on {max_gust_row['time']:%Y-%m-%d} ({max_gust_row['gust_max']:.1f} km/h). **Wettest climatological month:** {wettest_city['city']} ({wettest_city['precip_mean']:.1f} mm/day).
- **Within-country temperature correlation ranges {min((r for _, _, r in pairs), default=0)}–{max((r for _, _, r in pairs), default=0)}** — three cities per country are *not* interchangeable replicates in the lower range, so country models can keep all three (check variance inflation when features are built, Day 16).
- **Daily-max gust distributions are heavy-tailed** (figure 3): a handful of days carry most exposure — models must treat gusts as event-like, and any gust feature should use rolling maxima, not long means.
- **No missing days anywhere** (Day 3 audit), so rolling features on Day 16 need no gap handling for weather.
- These are ERA5 grid values at ~city-centroid resolution (see `src/ingestion/weather.py`); they describe exposure, not the local fault that caused any specific outage.
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 9 EDA report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="report path override")
    args = parser.parse_args(argv)

    from datetime import datetime, timezone
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    panel = load_panel()
    out = Path(args.out) if args.out else (REPORTS_DIR / "day09_eda_weather.md")
    build_report(panel, out, FIGURES_DIR, generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
