"""User-facing location intelligence for the GridRisk AGI dashboards.

This module answers the question a visitor actually has — *"is my area at
risk of a power interruption?"* — using only evidence the project has
validated, and it says **no** where no model is justified:

* **South Africa** — a national day-ahead load-shedding probability from the
  Day 18 logistic model. The target is national: the same probability applies
  everywhere in the country, and the panel says so.
* **Kenya** — planned interruption notices from the official KPLC archive,
  resolved to a county. These are *planned maintenance*, never unplanned
  outage labels, and an absent notice is not evidence of reliable supply.
* **Nigeria** — no model is claimed (the 2025 test window holds two positive
  days); the panel shows the dated collapse record instead of a fabricated
  per-location probability.

Everything here is read-only, plain Python (no Streamlit import except the
optional map builder) so the logic is unit-testable without a browser. The
Streamlit app in ``app/outage_risk.py`` only renders what this module
computes.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from src.config import BOUNDARIES_DIR, COUNTRY_NAMES, DATA_PROCESSED
from src.datasets.zaf_daily import (
    TIME_COL, build_dataset, build_forecast_frame, feature_columns,
)
from src.dashboard.state import WMO_LABELS
from src.evaluation.probabilistic import (
    brier_score, expected_calibration_error,
)
from src.models.factories import logistic_model

__all__ = [
    "COUNTRY_BBOX",
    "COUNTRY_CODE_TO_ISO3",
    "PILOT_COUNTRIES",
    "build_satellite_map",
    "county_matches",
    "fit_predict_latest",
    "haversine_km",
    "latest_zaf_forecast",
    "load_boundary_layer",
    "load_collapse_events",
    "load_planned_outages",
    "locate",
    "nga_summary",
    "normalize_name",
    "outages_for_admin1",
    "point_weather",
    "search_places",
    "upcoming_outages",
    "zaf_oof_metrics",
]

PILOT_COUNTRIES: tuple[str, ...] = ("KEN", "NGA", "ZAF")

#: Rough fallback bounds ``(lat_min, lat_max, lon_min, lon_max)`` used only
#: when the downloaded geoBoundaries files are absent. ``locate`` reports
#: ``method="bbox"`` in that case so the UI can flag the rough resolution.
COUNTRY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "KEN": (-4.9, 5.2, 33.8, 42.0),
    "NGA": (4.2, 14.0, 2.6, 14.8),
    "ZAF": (-34.9, -22.0, 16.3, 33.0),
}

COUNTRY_CODE_TO_ISO3: dict[str, str] = {"KE": "KEN", "NG": "NGA", "ZA": "ZAF"}

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (user-facing context only)."""
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def normalize_name(value: Any) -> str:
    """Uppercase, ASCII-fold punctuation and collapse whitespace.

    Used to compare KPLC county headers with geoBoundaries ADM1 names, where
    the same place appears as ``MURANG’A``, ``Murang'a`` and ``muranga``.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).replace("’", "").replace("‘", "").replace("'", "")
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().upper()


# ---------------------------------------------------------------------------
# Point -> administrative unit resolution
# ---------------------------------------------------------------------------
NAME_COLUMNS: tuple[str, ...] = ("shapeName", "shapeGroup", "name")


def _name_column(frame) -> str | None:
    return next((c for c in NAME_COLUMNS if c in frame.columns), None)


@lru_cache(maxsize=24)
def _read_layer(path_str: str, _mtime_ns: int):
    """Read a GeoJSON layer once per file version (mtime-keyed cache)."""
    import geopandas as gpd

    frame = gpd.read_file(path_str)
    if frame.crs is not None and frame.crs.to_epsg() != 4326:
        frame = frame.to_crs(4326)
    return frame


def load_boundary_layer(path: Path):
    """Load a boundary layer, returning ``None`` when it is unavailable."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return _read_layer(str(path), path.stat().st_mtime_ns)
    except Exception:
        # The dashboard degrades to a lower-detail location card rather than
        # failing the whole page when a boundary file is unreadable.
        return None


def _first_containing(layer, lat: float, lon: float) -> dict | None:
    """First unit whose polygon contains the point, with its geometry."""
    if layer is None or len(layer) == 0:
        return None
    from shapely.geometry import Point, mapping

    hits = layer[layer.geometry.contains(Point(lon, lat))]
    if hits.empty:
        return None
    row = hits.iloc[0]
    name_col = _name_column(layer)
    geometry = row.geometry
    return {
        "name": str(row[name_col]) if name_col else None,
        "geometry": mapping(geometry) if geometry is not None else None,
    }


def _bbox_country(lat: float, lon: float) -> str | None:
    for iso3, (lat_min, lat_max, lon_min, lon_max) in COUNTRY_BBOX.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return iso3
    return None


def _in_bbox(iso3: str, lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = COUNTRY_BBOX[iso3]
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _simplified_geometry(geometry: dict, tolerance: float) -> dict:
    """Simplify a single GeoJSON geometry for embedding in the map HTML."""
    from shapely.geometry import mapping, shape

    geom = shape(geometry)
    if tolerance > 0:
        geom = geom.simplify(tolerance, preserve_topology=True)
    return mapping(geom)


@lru_cache(maxsize=12)
def _simplified_layer_path(path_str: str, mtime_ns: int,
                           tolerance: float) -> dict:
    """Dissolved + simplified outline of a whole boundary layer (cached)."""
    from shapely.geometry import mapping

    layer = _read_layer(path_str, mtime_ns)
    geometry = layer.geometry.union_all()
    if tolerance > 0:
        geometry = geometry.simplify(tolerance, preserve_topology=True)
    return mapping(geometry)


def locate(lat: float, lon: float, *,
           boundaries_dir: Path = BOUNDARIES_DIR) -> dict:
    """Resolve a point to country / ADM1 / ADM2 (never guessed, never fuzzy).

    Uses point-in-polygon against the downloaded geoBoundaries layers when
    they are present; otherwise falls back to rough bounding boxes and marks
    ``method="bbox"`` so the UI can present the result as approximate.
    """
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f"lat/lon out of range: {lat}, {lon}")
    boundaries_dir = Path(boundaries_dir)

    iso3 = None
    adm0_name = None
    method = "bbox"
    in_bbox = [c for c in PILOT_COUNTRIES if _in_bbox(c, lat, lon)]
    for candidate in in_bbox:
        hit = _first_containing(
            load_boundary_layer(boundaries_dir / f"{candidate}_ADM0.geojson"),
            lat, lon,
        )
        if hit:
            iso3, adm0_name, method = candidate, hit["name"], "boundaries"
            break

    if iso3 is None:
        iso3 = _bbox_country(lat, lon)

    adm1 = adm2 = None
    adm1_geometry = adm2_geometry = None
    if iso3 is not None and method == "boundaries":
        hit1 = _first_containing(
            load_boundary_layer(boundaries_dir / f"{iso3}_ADM1.geojson"),
            lat, lon,
        )
        if hit1:
            adm1, adm1_geometry = hit1["name"], hit1["geometry"]
        hit2 = _first_containing(
            load_boundary_layer(boundaries_dir / f"{iso3}_ADM2.geojson"),
            lat, lon,
        )
        if hit2:
            adm2, adm2_geometry = hit2["name"], hit2["geometry"]

    return {
        "lat": lat,
        "lon": lon,
        "in_pilot": iso3 is not None,
        "country_iso3": iso3,
        "country": COUNTRY_NAMES.get(iso3) if iso3 else None,
        "adm0": adm0_name,
        "adm1": adm1,
        "adm2": adm2,
        "adm1_geometry": adm1_geometry,
        "adm2_geometry": adm2_geometry,
        "method": method if iso3 else None,
    }


# ---------------------------------------------------------------------------
# Kenya — KPLC planned interruptions
# ---------------------------------------------------------------------------
KPLC_FILENAME = "kplc_planned_outages.csv"

#: Known KPLC spelling/casing variants that do not normalize to the
#: geoBoundaries ADM1 name. Every entry is explicit and reviewable; the
#: parser never fuzzy-forces an unknown string.
COUNTY_ALIASES: dict[str, str] = {
    "KAKAMEG": "KAKAMEGA",
    "MAKEUNI": "MAKUENI",
    "NYANDADRUA": "NYANDARUA",
    "HOMABAY": "HOMA BAY",
    "LAIKIPIA NITHI": "LAIKIPIA",
    "PARS OF KIRINYAGA": "KIRINYAGA",
    "ELGEYO MARAKWET": "ELGEYO MARAKWET",
}


def _county_variants(value: Any) -> list[str]:
    """Normalized candidate names for a KPLC county cell.

    Compound cells (``BOMET/NAROK``) and known aliases expand to components;
    matching never crosses county boundaries by edit distance.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    raw = str(value)
    variants: list[str] = []
    for part in [raw, *re.split(r"[/,]", raw)]:
        text = normalize_name(part)
        if text:
            variants.append(COUNTY_ALIASES.get(text, text))
    return list(dict.fromkeys(variants))


def county_matches(kplc_county: Any, admin1: str | None) -> bool:
    """True when a KPLC county cell refers to the given ADM1 unit."""
    if not admin1:
        return False
    target = COUNTY_ALIASES.get(normalize_name(admin1), normalize_name(admin1))
    if not target:
        return False
    for variant in _county_variants(kplc_county):
        if variant == target:
            return True
        if variant and target and (variant in target or target in variant):
            return True
    return False


def load_planned_outages(processed_dir: Path = DATA_PROCESSED) -> pd.DataFrame:
    """KPLC planned-interruption events, typed and ready to filter.

    Returns an empty frame with the expected columns when the parsed archive
    is absent (fresh clone), so callers never special-case ``None``.
    """
    path = Path(processed_dir) / KPLC_FILENAME
    columns = [
        "country_iso3", "region", "county", "area", "event_date",
        "time_start_local", "time_end_local", "event_date_time_start_utc",
        "event_date_time_end_utc", "notice_class", "premises_text",
        "source_document",
    ]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    frame = pd.read_csv(path)
    for column in ("event_date_time_start_utc", "event_date_time_end_utc"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    if "event_date" in frame.columns:
        frame["event_date"] = pd.to_datetime(frame["event_date"], errors="coerce")
    if "notice_class" in frame.columns:
        frame["notice_class"] = frame["notice_class"].fillna("").astype(str)
    return frame


def upcoming_outages(frame: pd.DataFrame, *, horizon_days: int = 14,
                     today: date | None = None) -> pd.DataFrame:
    """Planned events in ``[today, today + horizon_days]``, soonest first."""
    if frame.empty:
        return frame.copy()
    today = today or datetime.now(timezone.utc).date()
    end = today + timedelta(days=max(int(horizon_days) - 1, 0))
    out = frame.copy()
    mask = (
        (out.get("notice_class", "") == "planned")
        & out["event_date"].notna()
        & (out["event_date"].dt.date >= today)
        & (out["event_date"].dt.date <= end)
    )
    out = out[mask]
    return out.sort_values(
        ["event_date", "event_date_time_start_utc"], kind="stable"
    ).reset_index(drop=True)


def outages_for_admin1(frame: pd.DataFrame, admin1: str | None) -> pd.DataFrame:
    """Planned events whose KPLC county *or region* header matches ADM1.

    KPLC leaves the county cell blank on some notices (including much of the
    Nairobi archive) while still carrying a region header, so both are
    checked; a missing county is never silently treated as a match.
    """
    if frame.empty or not admin1:
        return frame.iloc[0:0].copy()

    def _matches(row: pd.Series) -> bool:
        return (county_matches(row.get("county"), admin1)
                or county_matches(row.get("region"), admin1))

    return frame[frame.apply(_matches, axis=1)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# South Africa — day-ahead model
# ---------------------------------------------------------------------------
def _read_processed_dataset(processed_dir: Path) -> pd.DataFrame | None:
    path = Path(processed_dir) / "zaf_daily_dataset.csv.gz"
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if TIME_COL in frame.columns:
        frame[TIME_COL] = pd.to_datetime(frame[TIME_COL], errors="coerce")
        if frame[TIME_COL].dt.tz is None:
            # The persisted dataset keys SAST days; restore that meaning so
            # it can be concatenated with freshly built (tz-aware) rows.
            frame[TIME_COL] = frame[TIME_COL].dt.tz_localize("Africa/Johannesburg")
    return frame


def fit_predict_latest(frame: pd.DataFrame, *,
                       time_col: str = TIME_COL,
                       label_col: str = "any_load_shedding") -> dict:
    """Fit the Day 18 estimator and score the latest feature-complete row.

    If the final row already carries a label, it is withheld from training
    and predicted from strictly earlier rows, so the reported probability is
    always a genuine one-step-ahead score rather than a training-set fit.
    """
    if frame is None or frame.empty:
        raise ValueError("frame is empty")
    feats = feature_columns(frame)
    if not feats:
        raise ValueError("no feature columns found")

    clean = frame.dropna(subset=feats).sort_values(time_col, kind="stable")
    clean = clean.reset_index(drop=True)
    if len(clean) < 50:
        raise ValueError(f"need at least 50 feature-complete rows, got {len(clean)}")

    target = clean.iloc[[-1]]
    labelled = clean[clean[label_col].notna()].reset_index(drop=True)
    if labelled.empty:
        raise ValueError("no labelled rows to train on")

    if target[label_col].isna().all():
        train = labelled
        target_is_labelled = False
    else:
        if len(labelled) < 2:
            raise ValueError("need at least two labelled rows")
        train = labelled.iloc[:-1]
        target = labelled.iloc[[-1]]
        target_is_labelled = True

    y_train = train[label_col].astype(int).to_numpy()
    if len(np.unique(y_train)) < 2:
        raise ValueError("training window has a single class; no model can be fitted")

    model = logistic_model()
    model.fit(train[feats], y_train)
    probability = float(model.predict_proba(target[feats])[0])

    return {
        "probability": probability,
        "target_date": pd.Timestamp(target[time_col].iloc[0]),
        "train_rows": int(len(train)),
        "train_positives": int(y_train.sum()),
        "ref_rate": float(y_train.mean()),
        "target_is_labelled": target_is_labelled,
        "features": len(feats),
    }


def zaf_oof_metrics(processed_dir: Path = DATA_PROCESSED,
                    frame: pd.DataFrame | None = None) -> dict | None:
    """Headline walk-forward evidence (Day 18) recomputed from the OOF file."""
    path = Path(processed_dir) / "zaf_oof_predictions.csv.gz"
    if not path.exists():
        return None
    predictions = pd.read_csv(path)
    predictions = predictions.dropna(subset=["oof_logistic"])
    if predictions.empty:
        return None

    y = predictions["y"].astype(int).to_numpy()
    p = predictions["oof_logistic"].astype(float).to_numpy()

    ref_rate = float(y.mean())
    if frame is not None and not frame.empty and "any_load_shedding" in frame:
        feats = [c for c in feature_columns(frame) if c in frame.columns]
        history = frame.dropna(subset=[*feats, "any_load_shedding"])
        if len(history) >= 730:
            ref_rate = float(history["any_load_shedding"].astype(int).iloc[:730].mean())

    brier = float(brier_score(y, p))
    brier_ref = float(brier_score(y, np.full_like(p, ref_rate)))
    skill = 1.0 - brier / brier_ref if brier_ref > 0 else float("nan")

    return {
        "n": int(len(predictions)),
        "brier": brier,
        "brier_ref": brier_ref,
        "brier_skill": skill,
        "ece": float(expected_calibration_error(y, p)),
        "ref_rate": ref_rate,
        "start": str(predictions[TIME_COL].min()) if TIME_COL in predictions else None,
        "end": str(predictions[TIME_COL].max()) if TIME_COL in predictions else None,
    }


def latest_zaf_forecast(*,
                        processed_dir: Path = DATA_PROCESSED,
                        weather_dir: Path | None = None,
                        labels_csv: Path | None = None) -> dict:
    """Latest honest day-ahead ZAF forecast for the data snapshot on disk.

    Preferred path: train on the processed dataset, score the first
    feature-complete day after the label cutoff (built from the raw weather
    archive). When the raw archive is unavailable, fall back to the last
    labelled day and mark the result accordingly. Failures are returned as
    ``{"ok": False, "reason": ...}`` — the dashboard shows the reason rather
    than inventing a number.
    """
    processed_dir = Path(processed_dir)
    frame = _read_processed_dataset(processed_dir)
    raw_used = False
    raw_reason: str | None = None

    forecast_row = None
    try:
        forecast_row = build_forecast_frame(weather_dir=weather_dir, labels_csv=labels_csv)
        raw_used = True
    except (FileNotFoundError, ValueError) as exc:
        raw_reason = str(exc)

    if frame is None and forecast_row is None:
        return {"ok": False, "reason": raw_reason or "no ZAF dataset found"}

    if frame is None:
        try:
            frame = build_dataset(weather_dir=weather_dir, labels_csv=labels_csv)
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "reason": str(exc)}

    combined = frame
    if forecast_row is not None:
        combined = pd.concat([frame, forecast_row], ignore_index=True)
        combined = combined.drop_duplicates(subset=[TIME_COL], keep="last")

    try:
        result = fit_predict_latest(combined)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}

    labelled = combined[combined["any_load_shedding"].notna()]
    recent = labelled.tail(28).copy()
    recent["date"] = recent[TIME_COL].dt.strftime("%Y-%m-%d")
    recent = recent[["date", "hours_active", "mlr_mw_mean", "any_load_shedding"]]

    result.update({
        "ok": True,
        "label_cutoff": pd.Timestamp(labelled[TIME_COL].max()),
        "weather_extended": raw_used,
        "raw_note": raw_reason,
        "recent": recent.reset_index(drop=True),
        "metrics": zaf_oof_metrics(processed_dir, frame=combined),
    })
    return result


# ---------------------------------------------------------------------------
# Nigeria — dated record, explicitly no model
# ---------------------------------------------------------------------------
NGA_FILENAME = "nga_collapse_events.csv"


def load_collapse_events(processed_dir: Path = DATA_PROCESSED) -> pd.DataFrame:
    """Dated NERC grid-collapse events (national record)."""
    path = Path(processed_dir) / NGA_FILENAME
    if not path.exists():
        return pd.DataFrame(columns=[
            "event_date", "event_time", "event_type", "event_datetime_utc",
            "source_document", "source_page",
        ])
    frame = pd.read_csv(path)
    for column in ("event_date", "event_datetime_utc"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    return frame.sort_values("event_datetime_utc", kind="stable").reset_index(drop=True)


def nga_summary(processed_dir: Path = DATA_PROCESSED) -> dict:
    """Counts and span for the NGA panel, plus the explicit no-model verdict."""
    frame = load_collapse_events(processed_dir)
    if frame.empty:
        return {
            "ok": False, "events": 0, "span": None, "per_year": pd.DataFrame(),
            "reason": "NERC event extraction not present",
        }
    span = (frame["event_datetime_utc"].min(), frame["event_datetime_utc"].max())
    per_year = (
        frame.assign(year=frame["event_datetime_utc"].dt.year)
        .groupby("year").size().rename("events").reset_index()
    )
    return {
        "ok": True,
        "events": int(len(frame)),
        "span": span,
        "per_year": per_year,
        "reason": "2023–2025 only; 2 positives in the 2025 test window, "
                  "so no classifier is fit",
    }


# ---------------------------------------------------------------------------
# Place search and point weather (display-only)
# ---------------------------------------------------------------------------
def search_places(query: str, *, limit: int = 8,
                  timeout: int = 12) -> list[dict]:
    """Pilot-country place search via the Open-Meteo geocoding API.

    Results outside Kenya, Nigeria and South Africa are dropped: the project
    has no validated data for other countries, and a wrong-country result is
    worse than no result.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []
    try:
        response = requests.get(
            GEOCODE_URL,
            params={"name": query, "count": limit * 3, "language": "en",
                    "format": "json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    results = []
    for row in payload.get("results", []) or []:
        iso3 = COUNTRY_CODE_TO_ISO3.get(str(row.get("country_code", "")).upper())
        if not iso3:
            continue
        results.append({
            "name": row.get("name"),
            "lat": row.get("latitude"),
            "lon": row.get("longitude"),
            "country_iso3": iso3,
            "country": row.get("country"),
            "admin1": row.get("admin1"),
            "admin2": row.get("admin2"),
            "population": row.get("population") or 0,
            "timezone": row.get("timezone"),
        })
    results.sort(key=lambda r: r["population"], reverse=True)
    return results[:limit]


def point_weather(lat: float, lon: float, *, timeout: int = 12) -> dict:
    """Current + 3-day weather at a point — **display-only context**.

    Forecast values never enter features or labels (Day 18 leakage rule);
    the panel labels them as context so users do not read them as a risk
    input.
    """
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "current": (
            "temperature_2m,relative_humidity_2m,precipitation,weather_code,"
            "wind_speed_10m,wind_gusts_10m"
        ),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
        "forecast_days": 3,
        "timezone": "UTC",
    }
    try:
        response = requests.get(FORECAST_URL, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "reason": f"weather API unavailable: {exc}"}

    current = payload.get("current", {}) or {}
    code = current.get("weather_code")
    daily = payload.get("daily", {}) or {}
    daily_frame = pd.DataFrame({
        "date": daily.get("time", []),
        "temp_max": daily.get("temperature_2m_max", []),
        "temp_min": daily.get("temperature_2m_min", []),
        "precip_mm": daily.get("precipitation_sum", []),
    })
    return {
        "ok": True,
        "current": {
            "temperature_c": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "precip_mm": current.get("precipitation"),
            "wind_kmh": current.get("wind_speed_10m"),
            "gust_kmh": current.get("wind_gusts_10m"),
            "conditions": WMO_LABELS.get(code, f"code {code}"),
        },
        "daily": daily_frame,
        "observed_utc": current.get("time"),
    }


# ---------------------------------------------------------------------------
# Satellite map (Folium + Esri World Imagery)
# ---------------------------------------------------------------------------
ESRI_IMAGERY_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_REFERENCE_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
)
ESRI_IMAGERY_ATTR = (
    "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, "
    "and the GIS User Community"
)
ESRI_REFERENCE_ATTR = "Tiles © Esri — World Boundaries and Places"

_COUNTRY_COLOURS = {"KEN": "#38bdf8", "ZAF": "#4ade80", "NGA": "#fbbf24"}


def build_satellite_map(lat: float, lon: float, location: dict | None = None, *,
                        boundaries_dir: Path = BOUNDARIES_DIR,
                        horizon_days: int = 14,
                        show_boundaries: bool = True,
                        show_labels: bool = True,
                        planned_events: pd.DataFrame | None = None,
                        boundary_tolerance: float = 0.02,
                        highlight_tolerance: float = 0.005):
    """Folium map with satellite imagery and the selected-point overlays.

    Returns a ``folium.Map``. The satellite layer is Esri World Imagery with
    the Esri reference-label overlay on top; attribution is embedded in the
    tiles' layer control. Admin outlines and the selected ADM1 highlight are
    drawn from the project's own geoBoundaries files when present.
    """
    import folium

    location = location or {}
    zoom = 8 if location.get("in_pilot") else 4
    fmap = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None,
                      control_scale=True)

    folium.TileLayer(
        tiles=ESRI_IMAGERY_TILES, attr=ESRI_IMAGERY_ATTR,
        name="Satellite — Esri World Imagery", overlay=False, control=True,
        show=True,
    ).add_to(fmap)
    if show_labels:
        folium.TileLayer(
            tiles=ESRI_REFERENCE_TILES, attr=ESRI_REFERENCE_ATTR,
            name="Place labels", overlay=True, control=True, show=True,
        ).add_to(fmap)

    boundary_group = folium.FeatureGroup(name="Admin boundaries", show=show_boundaries)
    if show_boundaries:
        for iso3 in PILOT_COUNTRIES:
            path = Path(boundaries_dir) / f"{iso3}_ADM0.geojson"
            if not path.exists():
                continue
            try:
                outline = _simplified_layer_path(
                    str(path), path.stat().st_mtime_ns, boundary_tolerance)
            except Exception:
                continue
            folium.GeoJson(
                outline,
                name=iso3,
                style_function=lambda _feature, colour=_COUNTRY_COLOURS[iso3]: {
                    "fillColor": colour, "color": colour,
                    "weight": 1.2, "fillOpacity": 0.04,
                },
                tooltip=iso3,
            ).add_to(boundary_group)
    boundary_group.add_to(fmap)

    highlight = folium.FeatureGroup(name="Selected unit", show=True)
    for level, key, colour, weight in (
        ("ADM1", "adm1_geometry", "#f97316", 3),
        ("ADM2", "adm2_geometry", "#facc15", 2),
    ):
        geometry = location.get(key)
        if not geometry:
            continue
        try:
            simple = _simplified_geometry(geometry, highlight_tolerance)
        except Exception:
            simple = geometry
        folium.GeoJson(
            simple,
            style_function=lambda _feature, c=colour, w=weight: {
                "fillColor": c, "color": c, "weight": w, "fillOpacity": 0.08,
            },
            tooltip=f"{level}: {location.get(key.replace('_geometry', '')) or '?'}",
        ).add_to(highlight)
    highlight.add_to(fmap)

    folium.Marker(
        [lat, lon],
        tooltip="Selected location",
        popup=(
            f"{location.get('country') or 'Outside pilot countries'}<br>"
            f"{location.get('adm1') or ''}"
            f"{' · ' + location['adm2'] if location.get('adm2') else ''}<br>"
            f"{lat:.4f}, {lon:.4f}"
        ),
        icon=folium.Icon(color="red", icon="info-sign"),
    ).add_to(fmap)

    if location.get("country_iso3") == "KEN" and location.get("adm1_geometry"):
        events = outages_for_admin1(
            upcoming_outages(
                planned_events if planned_events is not None else load_planned_outages(),
                horizon_days=horizon_days,
            ),
            location.get("adm1"),
        )
        if not events.empty:
            from shapely.geometry import shape

            point = shape(location["adm1_geometry"]).representative_point()
            folium.CircleMarker(
                [point.y, point.x],
                radius=9,
                color="#38bdf8",
                fill=True,
                fill_opacity=0.85,
                tooltip=f"{len(events)} upcoming KPLC planned interruption(s)",
                popup=(
                    f"<b>{location.get('adm1')}</b><br>"
                    f"{len(events)} planned interruption(s) in the next "
                    f"{horizon_days} days.<br>Open the side panel for the list."
                ),
            ).add_to(fmap)

    folium.LayerControl(collapsed=True).add_to(fmap)
    return fmap
