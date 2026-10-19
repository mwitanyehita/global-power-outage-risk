"""Live project-state layer for the GridRisk dashboard.

Everything here is plain Python (no Streamlit import) so the same code can be
unit tested, imported from notebooks, or reused by a plain script. The
dashboard layer in ``app/dashboard.py`` only renders what this module computes.

The module answers the questions the dashboard asks on every refresh:

1. *Where is the project?*   -> :func:`plan_status`
2. *Is the data sound?*      -> :func:`weather_health`, :func:`boundary_health`
3. *What is happening now?*  -> :func:`repo_activity`, :func:`file_activity`
4. *What is the grid doing?* -> :func:`live_conditions`

Design rules
------------
* Nothing in this module writes to disk — the dashboard is read-only.
* Every network call is best effort: a failure degrades one panel, never the
  whole app.
* :func:`live_conditions` uses the Open-Meteo *forecast* API. It exists for
  display only and must never be used as a training feature or a label
  (the project leakage rule, Day 18).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src.config import DATA_EXTERNAL, DATA_RAW, PROJECT_ROOT, REPORTS_DIR

WEATHER_DIR = DATA_RAW / "weather"
BOUNDARY_DIR = DATA_EXTERNAL / "boundaries"
OUTAGES_DIR = DATA_RAW / "outages"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Pilot cities, mirrored from src.ingestion.weather.CITIES so the dashboard can
# label the raw files without opening them.
PILOT_CITIES = (
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

# Short WMO weather codes -> human labels, for the live panel.
WMO_LABELS = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
    81: "Heavy showers", 82: "Violent showers", 85: "Snow showers",
    86: "Heavy snow showers", 95: "Thunderstorm", 96: "Thunderstorm, hail",
    99: "Thunderstorm, heavy hail",
}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# ---------------------------------------------------------------------------
# Git / filesystem activity
# ---------------------------------------------------------------------------
def _git(args: list[str], root: Path = PROJECT_ROOT) -> str | None:
    """Run a git command; return stripped stdout or None on any failure."""
    try:
        res = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip()


def git_snapshot(root: Path = PROJECT_ROOT) -> dict:
    """Current branch, commit, dirty-file count and total commit count."""
    head = _git(["log", "-1", "--format=%H|%h|%an|%cI|%s"], root)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root) or "?"
    status = _git(["status", "--porcelain"], root) or ""
    count = _git(["rev-list", "--count", "HEAD"], root) or "0"

    info = {
        "branch": branch,
        "commit": "unknown",
        "short": "unknown",
        "author": "?",
        "committed_utc": None,
        "subject": "",
        "dirty_files": len([ln for ln in status.splitlines() if ln.strip()]),
        "commits": int(count) if count.isdigit() else 0,
    }
    if head:
        parts = head.split("|", 4)
        if len(parts) == 5:
            full, short, author, committed, subject = parts
            info.update(
                commit=full, short=short, author=author,
                committed_utc=committed, subject=subject,
            )
    return info


def repo_activity(root: Path = PROJECT_ROOT, limit: int = 12) -> list[dict]:
    """Recent commits, newest first."""
    out = _git(
        ["log", f"-{limit}", "--format=%h|%cI|%an|%s"], root
    )
    if not out:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        rows.append({
            "short": parts[0],
            "committed": parts[1],
            "author": parts[2],
            "subject": parts[3],
        })
    return rows


def file_activity(root: Path = PROJECT_ROOT, limit: int = 15) -> list[dict]:
    """Most recently modified files under data/ and reports/, newest first.

    This is the pulse of the project: when a worker lands a new download or
    regenerates an audit, the file shows up here within one refresh cycle.
    """
    roots = [root / "data", root / "reports", root / "src", root / "tests",
             root / "notebooks", root / "app"]
    rows: list[dict] = []
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in {".pyc", ".parquet"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append({
                "path": str(path.relative_to(root)),
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                "size_kb": round(stat.st_size / 1024, 1),
            })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:limit]


def data_totals(root: Path = PROJECT_ROOT) -> dict:
    """Row/byte totals for the raw and external data folders."""
    def _folder_stats(base: Path) -> tuple[int, int]:
        files = 0
        size = 0
        if base.exists():
            for path in base.rglob("*"):
                if path.is_file():
                    files += 1
                    size += path.stat().st_size
        return files, size

    raw_files, raw_bytes = _folder_stats(root / "data" / "raw")
    ext_files, ext_bytes = _folder_stats(root / "data" / "external")
    proc_files, proc_bytes = _folder_stats(root / "data" / "processed")
    return {
        "raw_files": raw_files, "raw_mb": round(raw_bytes / 1e6, 1),
        "external_files": ext_files, "external_mb": round(ext_bytes / 1e6, 1),
        "processed_files": proc_files, "processed_mb": round(proc_bytes / 1e6, 1),
    }


# ---------------------------------------------------------------------------
# Weather: raw downloads and live validation
# ---------------------------------------------------------------------------
def weather_manifest() -> dict:
    """Parsed data/raw/weather/_manifest.json (empty dict when absent)."""
    path = WEATHER_DIR / "_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def weather_files() -> list[Path]:
    """Raw city response files, excluding the manifest."""
    if not WEATHER_DIR.exists():
        return []
    return sorted(p for p in WEATHER_DIR.glob("*.json") if p.name != "_manifest.json")


def load_weather_frame(path: Path) -> pd.DataFrame:
    """Load one raw Open-Meteo response into a tz-aware UTC hourly frame."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    df = pd.DataFrame(payload["hourly"])
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize("UTC")
    return df.sort_values("time").reset_index(drop=True)


def weather_health() -> dict:
    """Re-validate every downloaded city against the manifest window.

    The dashboard recomputes these checks on each refresh instead of reading
    the static audit report, so a broken download shows up immediately.
    """
    files = weather_files()
    manifest = weather_manifest()
    if not files:
        return {"cities": {}, "alignment_ok": None, "window": None, "rows": 0}

    start, end = None, None
    if manifest:
        first = next(iter(manifest.values()))
        params = first.get("requested_params", {})
        start, end = params.get("start_date"), params.get("end_date")

    cities: dict[str, dict] = {}
    grids: dict[str, pd.Series] = {}
    total_rows = 0
    for path in files:
        try:
            df = load_weather_frame(path)
        except (json.JSONDecodeError, KeyError, OSError, ValueError) as exc:
            cities[path.stem] = {"error": str(exc)}
            continue

        times = df["time"]
        gaps = int((times.diff().dropna() != pd.Timedelta(hours=1)).sum())
        dups = int(times.duplicated().sum())
        nulls = int(df.isna().sum().sum())
        span_ok = True
        if start and end:
            expected = pd.date_range(
                f"{start} 00:00", f"{end} 23:00", freq="h", tz="UTC"
            )
            span_ok = len(df) == len(expected) and times.reset_index(
                drop=True
            ).equals(pd.Series(expected))

        cities[path.stem] = {
            "rows": len(df),
            "start": times.min(),
            "end": times.max(),
            "gaps": gaps,
            "duplicates": dups,
            "nulls": nulls,
            "monotonic": bool(times.is_monotonic_increasing),
            "tz_utc": str(times.dt.tz) == "UTC",
            "grid_ok": span_ok,
            "ok": bool(span_ok and gaps == 0 and dups == 0 and nulls == 0
                       and times.is_monotonic_increasing),
        }
        grids[path.stem] = times
        total_rows += len(df)

    alignment = None
    if grids:
        first_grid = next(iter(grids.values()))
        alignment = all(g.equals(first_grid) for g in grids.values())

    return {
        "cities": cities,
        "alignment_ok": alignment,
        "window": (start, end),
        "rows": total_rows,
    }


def live_conditions(timeout: int = 25) -> tuple[pd.DataFrame, str | None]:
    """Current conditions for the nine pilot cities, straight from the API.

    Uses the Open-Meteo forecast API (``current=`` only). **Display only** —
    these are forecast values and must never enter training features or
    labels, per the project leakage rule.

    Returns the frame and an error message (frame is empty on failure).
    """
    lats = ",".join(str(c["lat"]) for c in PILOT_CITIES)
    lons = ",".join(str(c["lon"]) for c in PILOT_CITIES)
    params = {
        "latitude": lats,
        "longitude": lons,
        "current": (
            "temperature_2m,relative_humidity_2m,precipitation,"
            "weather_code,wind_speed_10m,wind_gusts_10m,cloud_cover"
        ),
        "timezone": "UTC",
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return pd.DataFrame(), f"live API unavailable: {exc}"

    records = payload if isinstance(payload, list) else [payload]
    rows = []
    for entry, city in zip(records, PILOT_CITIES):
        cur = entry.get("current", {}) or {}
        code = cur.get("weather_code")
        rows.append({
            "city": city["city"],
            "country": city["country"],
            "lat": city["lat"],
            "lon": city["lon"],
            "observed_utc": cur.get("time"),
            "temperature_c": cur.get("temperature_2m"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "precip_mm": cur.get("precipitation"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "gust_kmh": cur.get("wind_gusts_10m"),
            "cloud_pct": cur.get("cloud_cover"),
            "weather_code": code,
            "conditions": WMO_LABELS.get(code, f"code {code}"),
        })
    return pd.DataFrame(rows), None


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------
def boundary_files() -> list[Path]:
    """Downloaded boundary GeoJSON files, ADM0 first."""
    if not BOUNDARY_DIR.exists():
        return []
    return sorted(
        BOUNDARY_DIR.glob("*.geojson"),
        key=lambda p: (p.stem.split("_")[-1], p.stem.split("_")[0]),
    )


def boundary_meta(path: Path) -> dict:
    """geoBoundaries metadata sidecar for one file (empty dict when absent)."""
    meta_path = path.with_name(f"{path.stem}_meta.json")
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def boundary_health() -> list[dict]:
    """Per-file boundary facts read from disk and the metadata sidecars."""
    rows = []
    for path in boundary_files():
        meta = boundary_meta(path)
        iso3, adm = (path.stem.split("_") + [""])[:2]
        try:
            units = int(meta.get("admUnitCount"))
        except (TypeError, ValueError):
            units = None
        rows.append({
            "file": path.name,
            "country": iso3,
            "level": adm,
            "canonical": meta.get("boundaryCanonical", "?"),
            "units": units,
            "license": meta.get("boundaryLicense", "?"),
            "year": meta.get("boundaryYearRepresented", "?"),
            "retrieved_utc": meta.get("_retrieved_utc"),
            "size_mb": round(path.stat().st_size / 1e6, 2),
        })
    return rows


# ---------------------------------------------------------------------------
# Outage records (the label source)
# ---------------------------------------------------------------------------
def outage_files() -> list[Path]:
    """Raw outage-label downloads held in data/raw/outages/."""
    if not OUTAGES_DIR.exists():
        return []
    return sorted(
        p for p in OUTAGES_DIR.iterdir()
        if p.is_file() and not p.name.endswith("_meta.json")
    )


def outage_health() -> dict:
    """Summarise the outage label material on disk.

    Two shapes are recognised:

    * ``*.sqlite`` — the Eskom system-operations hourly export, whose
      ``Manual Load_Reduction(MLR)`` column is the South African
      load-shedding label signal.
    * ``*.json``   — canonical raw intakes in the ``OutageEvent`` shape.
    """
    files = outage_files()
    summary = {
        "files": len(files),
        "sqlite_files": [p.name for p in files if p.suffix == ".sqlite"],
        "json_files": [p.name for p in files if p.suffix == ".json"],
        "bytes": sum(p.stat().st_size for p in files),
        "events": 0,
        "planned": 0,
        "unplanned": 0,
    }
    for path in files:
        if path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        records = payload.get("records") or []
        summary["events"] += len(records)
        for rec in records:
            if rec.get("is_planned") or rec.get("outage_type") in ("planned", "maintenance"):
                summary["planned"] += 1
            else:
                summary["unplanned"] += 1
    return summary


def eskom_load_reduction() -> pd.DataFrame:
    """Parse the Eskom hourly export into a timestamped load-reduction series.

    The raw export stores 12-hour clock times (``01:00:00 PM``) in hour-major
    order; this returns a clean UTC-aware frame with one row per hour. The
    source clock is South African local time (SAST, UTC+2, no DST), so every
    timestamp is localised before conversion to UTC.

        time            tz-aware UTC timestamp
        mlr_mw          manual load reduction in MW (0 when none was applied)
        residual_demand residual demand in MW
        total_re        total renewable generation in MW

    Returns an empty frame when the file is absent.
    """
    import sqlite3

    path = OUTAGES_DIR / "zaf_eskom_hourly.sqlite"
    if not path.exists():
        return pd.DataFrame()

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        raw = pd.read_sql_query("SELECT * FROM eskom", conn)

    times = pd.to_datetime(
        raw["Date Time Hour Beginning"],
        format="%Y-%m-%d %I:%M:%S %p",
        errors="coerce",
    )
    # The export is in South African local time (SAST, UTC+2, no DST).
    # Normalise to tz-aware UTC before any analysis or display.
    times = times.dt.tz_localize("Africa/Johannesburg").dt.tz_convert("UTC")
    frame = pd.DataFrame({
        "time": times,
        "mlr_mw": pd.to_numeric(raw.get("Manual Load_Reduction(MLR)"), errors="coerce"),
        "residual_demand": pd.to_numeric(raw.get("Residual Demand"), errors="coerce"),
        "total_re": pd.to_numeric(raw.get("Total RE"), errors="coerce"),
    }).dropna(subset=["time"])
    frame = frame.sort_values("time").reset_index(drop=True)
    return frame


def eskom_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    """Group contiguous load-reduction hours into discrete events.

    An episode is a run of consecutive hours with ``mlr_mw > 0``. Episodes
    separated by more than ``gap_hours`` are kept apart, so a short lull
    does not merge two separate load-shedding events.

    Returns columns: start, end, hours, peak_mw, mean_mw, gap_hours.
    """
    if frame.empty:
        return pd.DataFrame()

    active = frame["mlr_mw"] > 0
    if not active.any():
        return pd.DataFrame()

    gap = frame["time"].diff().dt.total_seconds().div(3600)
    breaks = (~active) | (gap > 1.5)
    groups = breaks.cumsum()[active]

    rows = []
    for _, grp in frame[active].groupby(groups):
        rows.append({
            "start": grp["time"].min(),
            "end": grp["time"].max(),
            "hours": len(grp),
            "peak_mw": float(grp["mlr_mw"].max()),
            "mean_mw": float(grp["mlr_mw"].mean()),
        })
    episodes = pd.DataFrame(rows).sort_values("peak_mw", ascending=False)
    return episodes.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Plan tracking
# ---------------------------------------------------------------------------
# The plan rows below are the project's own day-by-day schedule (see
# reports/data_inventory.md section G and the audit reports). Status is not
# hard-coded: :func:`plan_status` re-derives it from the artefacts on disk
# every time the dashboard refreshes.
PLAN_STEPS = (
    (1, "Colab setup check and project data inventory", "Setup"),
    (2, "Administrative boundaries for KEN, ZAF, NGA", "Boundaries"),
    (3, "Historical weather for pilot cities", "Weather"),
    (4, "Outage records and official notices", "Labels"),
    (5, "World Bank outage indicators, coverage check", "Context"),
    (6, "Source register: licenses, dates, limitations", "Governance"),
    (7, "Data audit report, go/no-go per source", "Governance"),
    (8, "tz-aware UTC normalization and country lookup", "Transform"),
    (9, "Feature build, weather_code treated as categorical", "Features"),
    (10, "Separate planned from unplanned interruptions", "Labels"),
    (11, "Point-in-polygon mapping of outages to boundaries", "Transform"),
    (16, "Weather and exposure feature matrix", "Features"),
    (17, "Label construction from verified outage records", "Labels"),
    (18, "Leakage enforcement for forecast features", "Modelling"),
    (22, "Live dashboard", "Product"),
    (27, "Model interpretation and limitation wording", "Modelling"),
    (29, "Attribution, README, reproducibility", "Governance"),
    (30, "Portfolio write-up", "Product"),
)


def _step_evidence(day: int, root: Path) -> tuple[str, str]:
    """Derive a step's status and evidence string from the workspace."""
    reports = root / "reports"

    if day == 1:
        path = reports / "data_inventory.md"
        if path.exists():
            return "done", f"{path.name} written"
        return "pending", "no data inventory yet"

    if day == 2:
        files = boundary_files()
        if files:
            units = sum(r["units"] or 0 for r in boundary_health())
            return "done", f"{len(files)} boundary files, {units} units"
        return "pending", "no boundary downloads"

    if day == 3:
        files = weather_files()
        if files:
            return "done", f"{len(files)} city files + manifest"
        return "pending", "no weather downloads"

    if day == 4:
        out = outage_health()
        if out["files"]:
            detail = f"{out['files']} raw file(s), {out['bytes'] / 1e6:.0f} MB"
            if out["sqlite_files"]:
                detail += f" ({', '.join(out['sqlite_files'])})"
            return "in progress", detail
        return "pending", "label source not yet acquired"

    if day == 5:
        return "pending", "survey indicators not yet acquired"

    if day == 6:
        path = reports / "source_register.csv"
        if path.exists():
            return "done", path.name
        return "pending", "source register not built"

    if day == 7:
        audits = sorted(reports.glob("day*_audit.md"))
        if audits:
            return "in progress", f"{len(audits)} audit reports present"
        return "pending", "no audit reports"

    if day in (8, 9, 10, 11):
        if day == 10:
            out = outage_health()
            if out["events"]:
                return "in progress", (
                    f"{out['events']} canonical events "
                    f"({out['planned']} planned / {out['unplanned']} unplanned)"
                )
        return "pending", "transform layer not built yet"

    if day in (16, 17, 18):
        return "pending", "no model or feature pipeline yet"

    if day == 22:
        path = root / "app" / "dashboard.py"
        if path.exists():
            return "in progress", "app/dashboard.py present"
        return "pending", "dashboard not started"

    if day in (27, 29, 30):
        return "pending", "not started"

    return "pending", ""


def plan_status(root: Path = PROJECT_ROOT) -> list[dict]:
    """The full plan with live, evidence-derived status per step."""
    rows = []
    for day, task, category in PLAN_STEPS:
        status, evidence = _step_evidence(day, root)
        rows.append({
            "day": day, "task": task, "category": category,
            "status": status, "evidence": evidence,
        })
    return rows


def current_day(rows: list[dict] | None = None) -> int:
    """The lowest plan day that is not finished — the project's current stage."""
    rows = rows if rows is not None else plan_status()
    for row in rows:
        if row["status"] != "done":
            return row["day"]
    return rows[-1]["day"] if rows else 0


# ---------------------------------------------------------------------------
# Inventory / provenance
# ---------------------------------------------------------------------------
def inventory_entries() -> list[dict]:
    """Parse the dataset tables out of reports/data_inventory.md.

    Each entry keeps its section, ID, source, coverage and declared status so
    the dashboard can show what is acquired against what is still planned.
    """
    path = REPORTS_DIR / "data_inventory.md"
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    section = ""
    entries: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("## "):
            section = line[3:].strip()
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and set(
            lines[i + 1].replace("|", "").replace(" ", "")
        ) <= set("-:"):
            headers = [h.strip() for h in line.strip("|").split("|")]
            if "Status" in headers and "ID" in headers:
                id_idx = headers.index("ID")
                status_idx = headers.index("Status")
                src_idx = headers.index("Source") if "Source" in headers else 1
                j = i + 2
                while j < len(lines) and lines[j].strip().startswith("|"):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    if len(cells) == len(headers):
                        entries.append({
                            "section": section.split("(")[0].strip(),
                            "id": cells[id_idx],
                            "source": cells[src_idx],
                            "status": cells[status_idx],
                        })
                    j += 1
                i = j
                continue
        i += 1
    return entries


def source_catalog() -> pd.DataFrame:
    """One row per acquired raw file, with provenance from the manifests."""
    rows = []
    manifest = weather_manifest()
    for path in weather_files():
        meta = manifest.get(path.name, {})
        rows.append({
            "file": path.name,
            "kind": "weather (Open-Meteo archive)",
            "bytes": path.stat().st_size,
            "retrieved_utc": meta.get("retrieved_utc"),
            "sha256": meta.get("sha256"),
            "license": "Free for non-commercial use (Open-Meteo terms)",
            "url": meta.get("requested_url"),
        })
    for path in boundary_files():
        meta = boundary_meta(path)
        rows.append({
            "file": path.name,
            "kind": f"boundaries ADM{path.stem.split('_')[-1]} (geoBoundaries)",
            "bytes": path.stat().st_size,
            "retrieved_utc": meta.get("_retrieved_utc"),
            "sha256": None,
            "license": meta.get("boundaryLicense", "?"),
            "url": meta.get("gjDownloadURL"),
        })
    for path in outage_files():
        rows.append({
            "file": path.name,
            "kind": "outage labels (label source)",
            "bytes": path.stat().st_size,
            "retrieved_utc": None,
            "sha256": None,
            "license": "see docs/data_governance.md",
            "url": None,
        })
    return pd.DataFrame(rows)
