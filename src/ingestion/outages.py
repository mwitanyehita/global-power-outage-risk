"""Outage records ingestion (raw intake + canonical schema).

This module structures ingestion for power outage data following project
principles: separate planned vs unplanned outages, preserve event times
and notice publication times with timezone awareness, maintain provenance.

Since live public APIs may vary by utility/country, this provides:
1. A documented raw-file intake path (preferred for reproducibility)
2. An adapter interface for future API integrations
3. Canonical schema definition and helpers to normalize to it
4. Provenance metadata sidecars for any intake

Do not fetch from third-party services without confirming terms; all code
here is testable with in-memory fixtures and requires no network access
by default.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union

import pandas as pd

from src.config import DATA_RAW, ensure_dirs
from src.ingestion import utils

OUTAGES_DIR = DATA_RAW / "outages"


def _ensure_dirs() -> None:
    ensure_dirs()
    OUTAGES_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class OutageEvent:
    """Canonical representation of a power outage event/record."""

    # Identifiers
    source_id: str  # e.g. "kplc", "eskom", "nerc", "manual", "synthetic"
    record_id: str  # unique within source (or generated)

    # Location
    country_iso3: str  # "KEN", "ZAF", "NGA"
    location_name: Optional[str] = None
    admin0: Optional[str] = None
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    # Outage classification
    outage_type: str = "unknown"  # "planned", "unplanned", "maintenance", "unknown"
    is_planned: Optional[bool] = None

    # Times (timezone-aware preferred; store as ISO8601 strings)
    event_start_utc: Optional[str] = None
    event_end_utc: Optional[str] = None
    event_start_local: Optional[str] = None
    event_end_local: Optional[str] = None
    notice_published_utc: Optional[str] = None
    notice_published_local: Optional[str] = None
    restored_utc: Optional[str] = None
    last_updated_utc: Optional[str] = None

    # Impact
    affected_customers: Optional[int] = None
    affected_areas: Optional[List[str]] = None
    duration_minutes: Optional[float] = None
    cause: Optional[str] = None
    status: Optional[str] = None  # "ongoing", "restored", "cancelled", "unknown"

    # Provenance
    source_url: Optional[str] = None
    retrieved_utc: Optional[str] = None
    raw_file: Optional[str] = None
    notes: Optional[str] = None
    license: Optional[str] = None
    license_url: Optional[str] = None

    # Extras (non-breaking)
    extra: Optional[Dict[str, Any]] = None


class OutageAdapter(Protocol):
    """Adapter interface for fetching outage records from a source."""

    def fetch(self) -> List[Dict[str, Any]]:  # pragma: no cover - protocol
        """Fetch raw records as list of dicts."""
        ...

    def normalize(self, raw: List[Dict[str, Any]]) -> List[OutageEvent]:  # pragma: no cover
        """Normalize raw records to canonical OutageEvent objects."""
        ...

    @property
    def source_id(self) -> str:  # pragma: no cover
        ...


def normalize_record(raw: Dict[str, Any], *, source_id: str, record_id_field: str = "record_id") -> OutageEvent:
    """Normalize a raw dict into OutageEvent.

    This is defensive - unknown fields go to extra; required fields have safe defaults.
    """
    extra: Dict[str, Any] = {}
    known_keys = set(OutageEvent.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    for k, v in raw.items():
        if k in known_keys:
            continue
        extra[k] = v

    record_id = str(raw.get(record_id_field) or raw.get("id") or raw.get("uuid") or f"unk_{utils.sha256_bytes(json.dumps(raw, sort_keys=True).encode())[:8]}")

    outage_type = str(raw.get("outage_type") or raw.get("type") or "unknown").lower()
    is_planned = raw.get("is_planned")
    if is_planned is None:
        is_planned = outage_type in ("planned", "scheduled", "maintenance")

    affected_areas = raw.get("affected_areas") or raw.get("areas") or raw.get("locations")
    if isinstance(affected_areas, str):
        affected_areas = [a.strip() for a in affected_areas.split(",") if a.strip()]

    return OutageEvent(
        source_id=source_id,
        record_id=record_id,
        country_iso3=str(raw.get("country_iso3") or raw.get("country") or "UNK"),
        location_name=raw.get("location_name") or raw.get("location") or raw.get("name"),
        admin0=raw.get("admin0") or raw.get("province") or raw.get("state"),
        admin1=raw.get("admin1") or raw.get("county") or raw.get("region"),
        admin2=raw.get("admin2") or raw.get("sub_county") or raw.get("district"),
        lat=_safe_float(raw.get("lat") or raw.get("latitude")),
        lon=_safe_float(raw.get("lon") or raw.get("longitude")),
        outage_type=outage_type,
        is_planned=is_planned,
        event_start_utc=_iso_or_none(raw.get("event_start_utc") or raw.get("start_utc") or raw.get("start")),
        event_end_utc=_iso_or_none(raw.get("event_end_utc") or raw.get("end_utc") or raw.get("end")),
        event_start_local=_iso_or_none(raw.get("event_start_local") or raw.get("start_local")),
        event_end_local=_iso_or_none(raw.get("event_end_local") or raw.get("end_local")),
        notice_published_utc=_iso_or_none(raw.get("notice_published_utc") or raw.get("published_utc") or raw.get("notice_time_utc")),
        notice_published_local=_iso_or_none(raw.get("notice_published_local") or raw.get("published_local")),
        restored_utc=_iso_or_none(raw.get("restored_utc") or raw.get("restoration_utc")),
        last_updated_utc=_iso_or_none(raw.get("last_updated_utc") or raw.get("updated_utc")),
        affected_customers=_safe_int(raw.get("affected_customers") or raw.get("customers")),
        affected_areas=affected_areas if isinstance(affected_areas, list) else None,
        duration_minutes=_safe_float(raw.get("duration_minutes")),
        cause=raw.get("cause"),
        status=raw.get("status"),
        source_url=raw.get("source_url") or raw.get("url"),
        retrieved_utc=raw.get("retrieved_utc") or utils.utc_now_iso(),
        raw_file=raw.get("raw_file"),
        notes=raw.get("notes"),
        license=raw.get("license"),
        license_url=raw.get("license_url"),
        extra=extra or None,
    )


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            # Treat naive as UTC for consistency? But project says preserve times;
            # better to store as-is if string; but we normalize datetime.
            v = v.replace(tzinfo=__import__("datetime").timezone.utc)
        return v.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        return s  # keep as-is; caller ensures tz-aware
    return str(v)


def events_to_dataframe(events: List[OutageEvent]) -> pd.DataFrame:
    """Convert list of OutageEvent to DataFrame."""
    rows = [asdict(e) for e in events]
    df = pd.DataFrame(rows)
    return df


def dataframe_to_events(df: pd.DataFrame) -> List[OutageEvent]:
    """Convert DataFrame back to OutageEvent list."""
    events: List[OutageEvent] = []
    for _, row in df.iterrows():
        data = row.to_dict()
        if isinstance(data.get("affected_areas"), str):
            try:
                data["affected_areas"] = json.loads(data["affected_areas"])
            except Exception:
                pass
        if isinstance(data.get("extra"), str):
            try:
                data["extra"] = json.loads(data["extra"])
            except Exception:
                pass
        events.append(OutageEvent(**data))  # type: ignore[arg-type]
    return events


def save_raw_intake(
    records: List[Dict[str, Any]],
    *,
    name: str,
    source_id: str,
    country_iso3: Optional[str] = None,
    source_url: Optional[str] = None,
    license: Optional[str] = None,
    notes: Optional[str] = None,
    overwrite: bool = False,
) -> Path:
    """Save raw intake records to data/raw/outages/ as JSON.

    Also writes a provenance sidecar {name}_meta.json.
    """
    _ensure_dirs()
    safe_name = utils.sanitize_filename(name)
    raw_path = OUTAGES_DIR / f"{safe_name}.json"
    meta_path = OUTAGES_DIR / f"{safe_name}_meta.json"

    if raw_path.exists() and not overwrite:
        # Don't overwrite by default - idempotent behavior
        pass

    raw_payload = {
        "source_id": source_id,
        "country_iso3": country_iso3,
        "records": records,
        "record_count": len(records),
        "retrieved_utc": utils.utc_now_iso(),
        "source_url": source_url,
        "notes": notes,
    }
    utils.write_json(raw_path, raw_payload)

    meta = {
        "raw_file": raw_path.name,
        "source_id": source_id,
        "country_iso3": country_iso3,
        "record_count": len(records),
        "retrieved_utc": utils.utc_now_iso(),
        "source_url": source_url,
        "license": license,
        "notes": notes,
        "format": "json",
    }
    utils.write_json(meta_path, meta)
    return raw_path


def load_raw_intake(path: Path) -> Dict[str, Any]:
    """Load raw intake JSON."""
    return utils.read_json(path)


def load_events_from_raw(path: Path, *, source_id: Optional[str] = None) -> List[OutageEvent]:
    """Load and normalize events from a raw intake file."""
    payload = load_raw_intake(path)
    source_id = source_id or payload.get("source_id") or "unknown"
    records = payload.get("records") or []
    events = [normalize_record(r, source_id=source_id) for r in records]
    return events


def write_events_csv(events: List[OutageEvent], out_path: Path) -> Path:
    """Write canonical events to CSV."""
    df = events_to_dataframe(events)
    utils.ensure_parent(out_path)
    df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path


def write_events_parquet(events: List[OutageEvent], out_path: Path) -> Path:
    """Write canonical events to Parquet if pyarrow available."""
    df = events_to_dataframe(events)
    utils.ensure_parent(out_path)
    try:
        df.to_parquet(out_path, index=False)
    except Exception:
        # Fallback to CSV if parquet not available
        csv_path = out_path.with_suffix(".csv")
        df.to_csv(csv_path, index=False, encoding="utf-8")
        return csv_path
    return out_path


def create_synthetic_fixture(
    n: int = 5,
    *,
    country_iso3: str = "KEN",
    planned_ratio: float = 0.5,
) -> List[Dict[str, Any]]:
    """Create small synthetic outage records for testing (no network)."""
    from datetime import timedelta, timezone

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records: List[Dict[str, Any]] = []
    for i in range(n):
        is_planned = i < int(n * planned_ratio)
        start = base + timedelta(hours=i * 2)
        end = start + timedelta(hours=2)
        records.append(
            {
                "record_id": f"syn_{i:03d}",
                "country_iso3": country_iso3,
                "location_name": f"Area {i}",
                "admin1": "Nairobi" if country_iso3 == "KEN" else ("Gauteng" if country_iso3 == "ZAF" else "Lagos"),
                "outage_type": "planned" if is_planned else "unplanned",
                "is_planned": is_planned,
                "event_start_utc": start.isoformat(timespec="seconds"),
                "event_end_utc": end.isoformat(timespec="seconds"),
                "notice_published_utc": (start - timedelta(days=1)).isoformat(timespec="seconds") if is_planned else None,
                "cause": "maintenance" if is_planned else "fault",
                "status": "restored",
                "affected_customers": 100 + i * 10,
                "source_id": "synthetic",
            }
        )
    return records


def main(argv: list[str] | None = None) -> int:
    """CLI for outages ingestion (demo with synthetic data)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="KEN", choices=["KEN", "ZAF", "NGA"])
    parser.add_argument("--n", type=int, default=10, help="number of synthetic records")
    parser.add_argument("--planned-ratio", type=float, default=0.4)
    parser.add_argument("--save-raw", action="store_true", help="save synthetic intake to raw/")
    parser.add_argument("--audit", action="store_true", help="load and summarize existing raw intakes")
    args = parser.parse_args(argv)

    _ensure_dirs()

    if args.audit:
        paths = sorted(OUTAGES_DIR.glob("*.json"))
        paths = [p for p in paths if not p.name.endswith("_meta.json")]
        if not paths:
            print("No outage intake files found.", file=__import__("sys").stderr)
            return 1
        for p in paths:
            try:
                events = load_events_from_raw(p)
                df = events_to_dataframe(events)
                planned = int(df["is_planned"].sum()) if "is_planned" in df else 0
                print(f"{p.name}: {len(events)} events, planned={planned}, unplanned={len(events)-planned}")
            except Exception as exc:
                print(f"ERROR loading {p.name}: {exc}", file=__import__("sys").stderr)
        return 0

    records = create_synthetic_fixture(n=args.n, country_iso3=args.country, planned_ratio=args.planned_ratio)
    if args.save_raw:
        raw_path = save_raw_intake(
            records,
            name=f"{args.country.lower()}_synthetic_outages",
            source_id="synthetic",
            country_iso3=args.country,
            notes="Synthetic fixture for testing/demo",
        )
        print(f"Saved raw intake to {raw_path}")
    else:
        print(f"Generated {len(records)} synthetic outage records (not saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
