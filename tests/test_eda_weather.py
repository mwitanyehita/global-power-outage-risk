"""Tests for src.eda.weather_eda (Day 9).

Synthetic-frame tests run everywhere; report/figure tests also run
synthetically so a fresh clone without raw weather still gets full coverage.
"""

import pandas as pd
import pytest

from src.eda import weather_eda as eda


def _synthetic_panel(periods=24 * 366, start="2024-01-01"):
    """Two cities x one full year (2024, leap) of plausible hourly weather."""
    times = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    frames = []
    for country, city, offset in [("KEN", "Nairobi", 0.0),
                                  ("ZAF", "Johannesburg", 5.0)]:
        hours = pd.Series(times.hour, dtype="int64")
        day_i = pd.Series(range(len(times))) // 24
        day_term = 4.0 * ((day_i % 17) - 8) / 8  # day-to-day variation
        temp = 20 + offset + 5 * ((hours - 14) % 24 - 12).abs() / 12 + day_term
        df = pd.DataFrame({
            "time": times,
            "temperature_2m": temp,
            "relative_humidity_2m": 60.0,
            "precipitation": [0.0 if i % 37 else 4.5 for i in range(len(times))],
            "rain": [0.0 if i % 37 else 4.5 for i in range(len(times))],
            "weather_code": 0,
            "cloud_cover": 50.0,
            "wind_speed_10m": 15.0,
            "wind_gusts_10m": [30.0 if i % 53 else 95.0 for i in range(len(times))],
            "surface_pressure": 1010.0,
            "country": country,
            "city": city,
            "city_key": f"{country}_{city.lower()}",
        })
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def panel():
    return _synthetic_panel()


def test_to_daily_aggregates_to_one_row_per_city_day(panel):
    daily = eda.to_daily(panel)
    assert set(daily.columns) >= {"country", "city", "time", "temp_mean",
                                  "precip_sum", "gust_max", "year", "month"}
    assert len(daily) == 2 * 366  # 2 cities x 366 days (2024 leap year)
    assert daily["temp_mean"].notna().all()
    # hourly precipitation sums into the day
    day0 = daily.iloc[0]
    assert day0["precip_sum"] >= 0


def test_climatology_shape_and_months(panel):
    daily = eda.to_daily(panel)
    clim = eda.climatology(daily)
    assert sorted(clim["month"].unique().tolist()) == list(range(1, 13))
    assert clim.groupby("city_key").size().eq(12).all()


def test_city_extremes_one_row_per_city(panel):
    daily = eda.to_daily(panel)
    extremes = eda.city_extremes(daily)
    assert len(extremes) == 2
    assert extremes["hottest_day"].str.contains(r"\d{4}-\d{2}-\d{2}").all()
    # the synthetic 95 km/h gust event must be the gustiest day somewhere
    assert extremes["gustiest_day"].str.contains("95.0").any()


def test_intercity_correlation_is_symmetric_unit_diagonal(panel):
    corr = eda.intercity_correlation(panel)
    assert corr.shape == (2, 2)
    assert (corr.index == corr.columns).all()
    assert (corr.values.round(6) == corr.values.T.round(6)).all()
    assert (corr.values.diagonal().round(6) == 1.0).all()


def test_annual_means_counts_days(panel):
    daily = eda.to_daily(panel)
    annual = eda.annual_means(daily)
    assert set(annual["year"]) == {2024}
    assert annual["days"].min() >= 1


def test_build_report_writes_markdown_and_figures(panel, tmp_path):
    out = tmp_path / "day09_eda_weather.md"
    figures = tmp_path / "figures"
    eda.build_report(panel, out, figures, "2026-10-19T00:00:00+00:00")
    text = out.read_text(encoding="utf-8")
    assert "# Day 9 — EDA" in text
    assert "## Findings" in text
    assert "9x9" in text or "Inter-city correlation" in text
    pngs = list(figures.glob("day09_*.png"))
    assert len(pngs) == 3
    assert all(p.stat().st_size > 1000 for p in pngs)


def test_build_report_handles_empty_panel(tmp_path):
    out = tmp_path / "empty.md"
    eda.build_report(pd.DataFrame(), out, tmp_path / "fig", "now")
    assert "No weather downloads" in out.read_text(encoding="utf-8")
