"""Weather ingestion module extensions/helpers.

This supplements the existing weather.py with additional utilities for
creating canonical weather schemas and provenance helpers. The original
weather.py remains the reference implementation and is not modified here.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.config import DATA_PROCESSED, DATA_RAW
from src.ingestion import utils, weather as wx

WEATHER_RAW_DIR = DATA_RAW / "weather"
WEATHER_PROCESSED_DIR = DATA_PROCESSED / "weather"


@dataclass(frozen=True)
class WeatherPoint:
    """Canonical hourly weather observation for a point location."""

    location_id: str  # e.g. "KEN_nairobi"
    country_iso3: str
    city: str
    lat: float
    lon: float
    time_utc: str  # ISO8601 tz-aware UTC
    temperature_2m_c: Optional[float] = None
    relative_humidity_2m_pct: Optional[float] = None
    precipitation_mm: Optional[float] = None
    rain_mm: Optional[float] = None
    weather_code: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    wind_speed_10m_kmh: Optional[float] = None
    wind_gusts_10m_kmh: Optional[float] = None
    surface_pressure_hpa: Optional[float] = None
    source: str = "open-meteo"
    retrieved_utc: Optional[str] = None


def city_to_location_id(entry: Dict[str, Any]) -> str:
    return wx.slugify(entry)


def normalize_weather_df(df: pd.DataFrame, entry: Dict[str, Any]) -> pd.DataFrame:
    """Normalize raw weather DataFrame to canonical columns."""
    out = pd.DataFrame()
    if "time" in df.columns:
        out["time_utc"] = df["time"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(df["time"].dt, "tz") else df["time"]
    mapping = {
        "temperature_2m": "temperature_2m_c",
        "relative_humidity_2m": "relative_humidity_2m_pct",
        "precipitation": "precipitation_mm",
        "rain": "rain_mm",
        "weather_code": "weather_code",
        "cloud_cover": "cloud_cover_pct",
        "wind_speed_10m": "wind_speed_10m_kmh",
        "wind_gusts_10m": "wind_gusts_10m_kmh",
        "surface_pressure": "surface_pressure_hpa",
    }
    for src, dst in mapping.items():
        if src in df.columns:
            out[dst] = df[src]
    out["location_id"] = city_to_location_id(entry)
    out["country_iso3"] = entry["country"]
    out["city"] = entry["city"]
    out["lat"] = entry["lat"]
    out["lon"] = entry["lon"]
    out["source"] = "open-meteo"
    out["retrieved_utc"] = utils.utc_now_iso()
    return out


def save_canonical_weather(
    entry: Dict[str, Any],
    raw_path: Optional[Path] = None,
    out_dir: Path = WEATHER_PROCESSED_DIR,
) -> Path:
    """Load raw weather and save canonical CSV."""
    utils.ensure_parent(out_dir / "dummy")
    out_dir.mkdir(parents=True, exist_ok=True)
    if raw_path is None:
        raw_path = WEATHER_RAW_DIR / f"{wx.slugify(entry)}.json"
    df_raw = wx.load_city(raw_path)
    df_canon = normalize_weather_df(df_raw, entry)
    out_path = out_dir / f"{wx.slugify(entry)}_hourly.csv"
    df_canon.to_csv(out_path, index=False, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args(argv)
    if args.audit:
        print("Weather enhanced module: OK (no-op audit)")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
