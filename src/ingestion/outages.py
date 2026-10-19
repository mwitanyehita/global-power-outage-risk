"""Outage record intake: raw-file path + canonical schema (Day 4 scaffolding).

The label source. No single public, machine-readable outage API covers all
three pilots (see reports/data_inventory.md section C), so ingestion is built
around a **documented raw-file intake path**:

1. A source-specific adapter (future: KPLC notices, EskomSePush, NERC
   filings — see ``OutageAdapter`` below) fetches source-native records and
   writes them verbatim to ``data/raw/outages/{source}.json`` plus a
   ``{source}_meta.json`` provenance sidecar (source, license, URL, SHA-256,
   retrieval time) via ``write_raw_intake``.
2. ``intake()`` normalizes every raw file into the canonical schema,
   validates it and writes ``data/processed/outages.csv`` (+ meta sidecar).

Canonical schema — one row per outage record:

    record_id           str     stable id: "<source>:<native id>" or content hash
    source              str     dataset id (kplc, eskom, nerc, manual, ...)
    country_iso3        str     KEN | ZAF | NGA
    area_name           str     locality/feeder/suburb as published
    admin1 / admin2     str     state/province, district (nullable)
    outage_type         str     planned | unplanned | unknown  (never collapsed)
    event_start_utc     tz-aware datetime   supply interruption began
    event_end_utc       tz-aware datetime   restoration (null = ongoing)
    notice_published_utc tz-aware datetime  when the notice was published
    customers_affected  Int64   nullable
    status              str     ongoing / restored / cancelled / unknown
    cause               str     nullable, as published
    source_url          str     canonical URL of the record
    license             str     license/terms recorded at intake
    retrieved_utc       tz-aware datetime   when we fetched the record
    raw_file            str     provenance: which raw file it came from

Discipline enforced by ``validate``:
  - all timestamps are timezone-aware UTC (naive inputs are converted but
    counted in ``assumed_utc`` warnings — ambiguous source times flagged);
  - event_end >= event_start; null event_start flagged;
  - outage_type stays in {planned, unplanned, unknown} — planned and
    unplanned records are never merged;
  - duplicate record_ids flagged.

Usage:
    python -m src.ingestion.outages            # raw -> processed + summary
    python -m src.ingestion.outages --audit    # validate, write nothing
    python -m src.ingestion.outages --schema   # print the canonical schema
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

import pandas as pd

from src.config import DATA_PROCESSED, DATA_RAW, ensure_dirs
from src.ingestion import utils

RAW_DIR = DATA_RAW / "outages"
PROCESSED_PATH = DATA_PROCESSED / "outages.csv"
PROCESSED_META_PATH = DATA_PROCESSED / "outages_meta.json"

CANONICAL_COLUMNS: tuple[str, ...] = (
    "record_id",
    "source",
    "country_iso3",
    "area_name",
    "admin1",
    "admin2",
    "outage_type",
    "event_start_utc",
    "event_end_utc",
    "notice_published_utc",
    "customers_affected",
    "status",
    "cause",
    "source_url",
    "license",
    "retrieved_utc",
    "raw_file",
)

# Columns a record cannot be used without (everything else is nullable).
REQUIRED_COLUMNS: tuple[str, ...] = (
    "record_id",
    "source",
    "country_iso3",
    "outage_type",
    "event_start_utc",
)

TIME_COLUMNS: tuple[str, ...] = (
    "event_start_utc",
    "event_end_utc",
    "notice_published_utc",
    "retrieved_utc",
)

OUTAGE_TYPES: tuple[str, ...] = ("planned", "unplanned", "unknown")

PILOT_COUNTRIES: tuple[str, ...] = ("KEN", "ZAF", "NGA")

# Source-native field names -> canonical names (case-insensitive, after strip).
FIELD_ALIASES: dict[str, str] = {
    "id": "record_id",
    "notice_id": "record_id",
    "event_id": "record_id",
    "country": "country_iso3",
    "iso3": "country_iso3",
    "location": "area_name",
    "area": "area_name",
    "suburb": "area_name",
    "feeder": "area_name",
    "place": "area_name",
    "state": "admin1",
    "province": "admin1",
    "region": "admin1",
    "county": "admin1",
    "district": "admin2",
    "lga": "admin2",
    "subcounty": "admin2",
    "type": "outage_type",
    "kind": "outage_type",
    "start": "event_start_utc",
    "start_time": "event_start_utc",
    "event_start": "event_start_utc",
    "outage_start": "event_start_utc",
    "begin": "event_start_utc",
    "end": "event_end_utc",
    "end_time": "event_end_utc",
    "event_end": "event_end_utc",
    "outage_end": "event_end_utc",
    "restored": "event_end_utc",
    "restoration_time": "event_end_utc",
    "published": "notice_published_utc",
    "notice_time": "notice_published_utc",
    "notice_published": "notice_published_utc",
    "published_at": "notice_published_utc",
    "customers": "customers_affected",
    "customers_affected": "customers_affected",
    "affected_customers": "customers_affected",
    "url": "source_url",
    "link": "source_url",
    "description": "cause",
}

# Country names sometimes used where ISO3 is expected.
COUNTRY_TO_ISO3: dict[str, str] = {
    "kenya": "KEN",
    "south africa": "ZAF",
    "nigeria": "NGA",
}

# outage_type synonyms from source-native values -> canonical vocabulary.
OUTAGE_TYPE_SYNONYMS: dict[str, str] = {
    "planned": "planned",
    "planned outage": "planned",
    "scheduled": "planned",
    "scheduled outage": "planned",
    "maintenance": "planned",
    "load shedding": "unplanned",
    "unplanned": "unplanned",
    "unplanned outage": "unplanned",
    "unscheduled": "unplanned",
    "forced": "unplanned",
    "fault": "unplanned",
    "emergency": "unplanned",
    "unknown": "unknown",
    "": "unknown",
}


class OutageAdapter(Protocol):
    """Interface for a source-specific outage fetcher (Day 4+).

    Implementations (one per source: KPLC, EskomSePush, NERC, ...) fetch
    source-native records and hand them back untouched; normalization to the
    canonical schema is this module's job, not the adapter's.

    Contract:
      * ``source`` / ``license`` are stable dataset identifiers recorded in
        provenance (license must come from the source's actual terms);
      * ``fetch()`` returns raw records as dicts and performs its own network
        access — nothing else in this package calls the network for outages;
      * callers persist the result with ``write_raw_intake`` so raw bytes and
        provenance land in ``data/raw/outages/`` before any processing.
    """

    source: str
    license: str

    def fetch(self) -> list[dict[str, Any]]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Raw-file intake (adapters and manual drops share this path)
# ---------------------------------------------------------------------------

def write_raw_intake(
    records: Sequence[Mapping[str, Any]],
    name: str,
    *,
    source: str,
    license: str = "unknown",
    country_iso3: str | None = None,
    source_url: str | None = None,
    notes: str | None = None,
    raw_dir: Path | None = None,
) -> Path:
    """Persist raw records verbatim plus a ``{name}_meta.json`` sidecar.

    Returns the JSON path. Re-running with the same name overwrites both
    files (adapters are expected to re-fetch whole snapshots, not merge).
    """
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe = utils.sanitize_filename(name)
    payload = {
        "source": source,
        "country_iso3": country_iso3,
        "records": [dict(r) for r in records],
    }
    raw_path = raw_dir / f"{safe}.json"
    utils.write_json(raw_path, payload)
    utils.write_provenance(
        raw_dir / f"{safe}_meta.json",
        {
            "raw_file": raw_path.name,
            "source": source,
            "country_iso3": country_iso3,
            "record_count": len(records),
            "license": license,
            "source_url": source_url,
            "retrieved_utc": utils.utc_now_iso(),
            "sha256": utils.sha256_file(raw_path),
            "notes": notes,
        },
    )
    return raw_path


def discover_raw_files(raw_dir: Path | None = None) -> list[Path]:
    """Raw intake files in a deterministic order (sidecars/manifests excluded)."""
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    if not raw_dir.exists():
        return []
    return sorted(
        p
        for p in raw_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".json", ".csv"}
        and not p.name.startswith("_")
        and not p.name.endswith("_meta.json")
    )


def _load_raw_records(path: Path) -> list[dict[str, Any]]:
    """Read one raw intake file (JSON list, {"records": [...]} object, or CSV)."""
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path).to_dict("records")
    payload = utils.read_json(path, default=None)
    if payload is None:
        raise utils.SchemaError(f"{path}: empty or unreadable JSON")
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        records = payload["records"]
    else:
        raise utils.SchemaError(
            f"{path}: expected a JSON list or an object with a 'records' list"
        )
    if not all(isinstance(r, dict) for r in records):
        raise utils.SchemaError(f"{path}: every record must be a JSON object")
    return records


def _sidecar_for(path: Path) -> dict[str, Any]:
    """Provenance sidecar for a raw file, or {} when absent."""
    if path.suffix.lower() != ".json":
        return {}
    meta = utils.read_json(path.with_name(f"{path.stem}_meta.json"), default={})
    return meta if isinstance(meta, dict) else {}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _canonical_key(key: Any) -> str:
    k = str(key).strip().lower().replace(" ", "_")
    return FIELD_ALIASES.get(k, k)


def _canonical_outage_type(value: Any) -> str:
    if isinstance(value, bool):
        return "planned" if value else "unplanned"
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "unknown"
    key = str(value).strip().lower()
    if key in OUTAGE_TYPE_SYNONYMS:
        return OUTAGE_TYPE_SYNONYMS[key]
    if "plan" in key or "schedul" in key or "maintenance" in key:
        return "planned"
    if "unplan" in key or "unschedul" in key:
        return "unplanned"
    return "unknown"


def _canonical_country(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    iso = COUNTRY_TO_ISO3.get(text.lower(), text.upper())
    return iso if len(iso) == 3 else None


def normalize_record(
    raw: Mapping[str, Any],
    *,
    source: str,
    license: str = "unknown",
    raw_file: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Normalize one source-native record to the canonical schema.

    Returns ``(row, warnings)``. Timestamps are parsed with
    ``utils.parse_time``: aware values convert to UTC, naive values are
    localized to UTC but warned about (source times are ambiguous), and
    unparseable values become NaT with a warning.
    """
    warnings: list[str] = []
    row: dict[str, Any] = {c: None for c in CANONICAL_COLUMNS}
    seen_keys: set[str] = set()

    for key, value in raw.items():
        ckey = _canonical_key(key)
        if ckey in CANONICAL_COLUMNS and ckey not in seen_keys:
            seen_keys.add(ckey)
            row[ckey] = value

    row["source"] = source
    row["license"] = license
    if raw_file is not None:
        row["raw_file"] = raw_file

    row["country_iso3"] = _canonical_country(row.get("country_iso3"))
    row["outage_type"] = _canonical_outage_type(row.get("outage_type"))

    # Stable id: prefer the source's own id; otherwise hash the content so a
    # re-run of the same raw file yields the same id (idempotent re-intake).
    native = row.get("record_id")
    if native is not None and not (isinstance(native, float) and pd.isna(native)) and str(native).strip():
        row["record_id"] = f"{source}:{native}"
    else:
        basis = json.dumps(
            {
                "source": source,
                "country": row.get("country_iso3"),
                "area": str(row.get("area_name") or ""),
                "start": str(row.get("event_start_utc") or ""),
            },
            sort_keys=True,
        )
        row["record_id"] = f"{source}:h{utils.sha256_bytes(basis.encode('utf-8'))[:12]}"

    for col in TIME_COLUMNS:
        value = row.get(col)
        if value is None:
            row[col] = pd.NaT
            continue
        ts, assumed, invalid = utils.parse_time(value)
        if invalid:
            warnings.append(f"{row['record_id']}: unparseable {col}={value!r}")
            row[col] = pd.NaT
        else:
            if assumed:
                warnings.append(
                    f"{row['record_id']}: naive {col}={value!r} assumed UTC"
                )
            row[col] = ts if ts is not None else pd.NaT

    if row["retrieved_utc"] is None or pd.isna(row["retrieved_utc"]):
        row["retrieved_utc"] = pd.Timestamp(utils.utc_now_iso())

    customers = row.get("customers_affected")
    if customers is None or (isinstance(customers, float) and pd.isna(customers)):
        row["customers_affected"] = pd.NA
    else:
        try:
            row["customers_affected"] = int(customers)
        except (TypeError, ValueError):
            warnings.append(
                f"{row['record_id']}: non-numeric customers_affected={customers!r}"
            )
            row["customers_affected"] = pd.NA

    for col in ("area_name", "admin1", "admin2", "status", "cause", "source_url"):
        value = row.get(col)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            row[col] = None
        else:
            row[col] = str(value)

    return row, warnings


def normalize_records(
    records: Iterable[Mapping[str, Any]],
    *,
    source: str,
    license: str = "unknown",
    raw_file: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Normalize many records; returns ``(canonical DataFrame, warnings)``.

    The frame always has exactly ``CANONICAL_COLUMNS`` in order, with
    tz-aware datetime dtypes even when empty.
    """
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for record in records:
        row, row_warnings = normalize_record(
            record, source=source, license=license, raw_file=raw_file
        )
        rows.append(row)
        warnings.extend(row_warnings)

    df = pd.DataFrame(rows, columns=list(CANONICAL_COLUMNS))
    for col in TIME_COLUMNS:
        df[col] = pd.to_datetime(df[col], utc=True)
    df["customers_affected"] = pd.array(df["customers_affected"], dtype="Int64")
    return df, warnings


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame) -> dict[str, Any]:
    """Structural checks on a canonical frame; returns a JSON-safe report.

    ``ok`` is True only when there are no errors (warnings such as naive
    source timestamps or non-pilot countries do not fail validation — they
    are surfaced so Day 7's audit can decide).
    """
    errors: list[str] = []
    warnings: list[str] = []

    missing = utils.missing_columns(df, CANONICAL_COLUMNS)
    if missing:
        return {
            "ok": False,
            "errors": [f"missing columns: {', '.join(missing)}"],
            "warnings": [],
            "counts": {},
        }

    for col in TIME_COLUMNS:
        if not utils.is_tz_aware(df[col]):
            errors.append(f"{col}: not a tz-aware datetime column")

    null_start = df["event_start_utc"].isna()
    if int(null_start.sum()):
        errors.append(f"{int(null_start.sum())} record(s) with null event_start_utc")

    bad_type = ~df["outage_type"].isin(OUTAGE_TYPES)
    if int(bad_type.sum()):
        values = sorted(df.loc[bad_type, "outage_type"].astype(str).unique())
        errors.append(f"outage_type outside {OUTAGE_TYPES}: {values}")

    both = df["event_end_utc"].notna() & df["event_start_utc"].notna()
    backwards = both & (df["event_end_utc"] < df["event_start_utc"])
    if int(backwards.sum()):
        ids = df.loc[backwards, "record_id"].head(5).tolist()
        errors.append(
            f"{int(backwards.sum())} record(s) with event_end < event_start (e.g. {ids})"
        )

    dupes = int(df["record_id"].duplicated().sum())
    if dupes:
        warnings.append(f"{dupes} duplicate record_id value(s)")

    off_pilot = df["country_iso3"].notna() & ~df["country_iso3"].isin(PILOT_COUNTRIES)
    if int(off_pilot.sum()):
        values = sorted(df.loc[off_pilot, "country_iso3"].unique())
        warnings.append(f"records outside pilot countries: {values}")

    open_outages = int(
        (df["event_end_utc"].isna() & df["event_start_utc"].notna()).sum()
    )
    if open_outages:
        warnings.append(f"{open_outages} record(s) without event_end_utc (ongoing)")

    counts = {
        "rows": int(len(df)),
        "by_outage_type": {
            str(k): int(v) for k, v in df["outage_type"].value_counts().items()
        },
        "by_country": {
            str(k): int(v)
            for k, v in df["country_iso3"].fillna("<null>").value_counts().items()
        },
        "by_source": {str(k): int(v) for k, v in df["source"].value_counts().items()},
        "null_event_end": open_outages,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "counts": counts}


# ---------------------------------------------------------------------------
# Intake pipeline (raw -> processed)
# ---------------------------------------------------------------------------

def load_processed(path: Path | None = None) -> pd.DataFrame:
    """Read ``data/processed/outages.csv`` back with tz-aware time columns."""
    path = Path(path) if path else PROCESSED_PATH
    df = pd.read_csv(path, dtype={"record_id": str, "source": str})
    for col in TIME_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    if "customers_affected" in df.columns:
        df["customers_affected"] = pd.array(
            df["customers_affected"], dtype="Int64"
        )
    return df


def intake(
    raw_dir: Path | None = None,
    out_path: Path | None = None,
    *,
    write: bool = True,
) -> dict[str, Any]:
    """Normalize every raw intake file, validate, and (optionally) write.

    Returns a summary dict: files processed, row counts, warnings, validation
    report, and output paths. With ``write=False`` nothing touches disk
    (used by ``--audit``).
    """
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    out_path = Path(out_path) if out_path else PROCESSED_PATH
    # Sidecar follows the output file (default: PROCESSED_META_PATH), so an
    # overridden --out never writes metadata over the real processed file.
    meta_path = out_path.with_name(out_path.stem + "_meta.json")
    ensure_dirs()

    files = discover_raw_files(raw_dir)
    if not files:
        return {
            "files": [],
            "rows": 0,
            "warnings": [],
            "validation": {"ok": False, "errors": ["no raw intake files found"],
                           "warnings": [], "counts": {}},
            "written": None,
            "meta": None,
        }

    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    inputs: list[dict[str, Any]] = []

    for path in files:
        meta = _sidecar_for(path)
        source = str(meta.get("source") or path.stem)
        license_ = str(meta.get("license") or "unknown")
        if not meta:
            warnings.append(f"{path.name}: no provenance sidecar; source assumed '{source}'")
        if license_ == "unknown":
            warnings.append(f"{path.name}: license not recorded")
        try:
            records = _load_raw_records(path)
        except utils.SchemaError as exc:
            warnings.append(str(exc))
            continue
        frame, frame_warnings = normalize_records(
            records, source=source, license=license_, raw_file=path.name
        )
        frames.append(frame)
        warnings.extend(frame_warnings)
        inputs.append(
            {
                "raw_file": path.name,
                "source": source,
                "license": license_,
                "records": len(records),
                "sha256": meta.get("sha256") or utils.sha256_file(path),
            }
        )

    if frames:
        df = pd.concat(frames, ignore_index=True)
        before = len(df)
        df = df.drop_duplicates(subset="record_id", keep="first").reset_index(drop=True)
        if before != len(df):
            warnings.append(f"dropped {before - len(df)} duplicate record_id row(s)")
    else:
        df, _ = normalize_records([], source="empty")

    report = validate(df)
    written: str | None = None
    if write and report["ok"]:
        utils.ensure_parent(out_path)
        df.to_csv(out_path, index=False, encoding="utf-8")
        utils.write_provenance(
            meta_path,
            {
                "generated_utc": utils.utc_now_iso(),
                "inputs": inputs,
                "rows": int(len(df)),
                "columns": list(df.columns),
                "validation": report,
            },
        )
        written = str(out_path)

    return {
        "files": [p.name for p in files],
        "rows": int(len(df)),
        "warnings": warnings,
        "validation": report,
        "written": written,
        "meta": str(meta_path) if written else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_summary(summary: Mapping[str, Any]) -> None:
    report = summary["validation"]
    print(f"files:   {', '.join(summary['files']) or 'none'}")
    print(f"rows:    {summary['rows']}")
    for kind in ("by_source", "by_outage_type", "by_country"):
        if report["counts"].get(kind):
            parts = ", ".join(f"{k}={v}" for k, v in sorted(report["counts"][kind].items()))
            print(f"{kind.replace('by_', ''):8s} {parts}")
    for warning in summary["warnings"]:
        print(f"  [warn] {warning}")
    for error in report["errors"]:
        print(f"  [FAIL] {error}", file=sys.stderr)
    if summary.get("written"):
        print(f"wrote:   {summary['written']}")
        print(f"meta:    {summary.get('meta')}")
    elif report["ok"]:
        print("wrote:   (skipped — --audit)")
    else:
        print("wrote:   (skipped — validation failed)", file=sys.stderr)


def _print_schema() -> int:
    descriptions = {
        "record_id": "str — stable id, '<source>:<native id>' or content hash",
        "source": "str — dataset id (kplc, eskom, nerc, manual, ...)",
        "country_iso3": "str — KEN | ZAF | NGA",
        "area_name": "str — locality/feeder/suburb as published",
        "admin1": "str — state/province (nullable)",
        "admin2": "str — district/LGA (nullable)",
        "outage_type": "str — planned | unplanned | unknown (never merged)",
        "event_start_utc": "datetime64[ns, UTC] — interruption began",
        "event_end_utc": "datetime64[ns, UTC] — restoration; null = ongoing",
        "notice_published_utc": "datetime64[ns, UTC] — notice publication time",
        "customers_affected": "Int64 — nullable",
        "status": "str — ongoing / restored / cancelled / unknown",
        "cause": "str — as published (nullable)",
        "source_url": "str — canonical URL of the record",
        "license": "str — license recorded at intake",
        "retrieved_utc": "datetime64[ns, UTC] — when we fetched the record",
        "raw_file": "str — provenance: source raw file",
    }
    print(f"{'column':22s} {'required':8s} description")
    for col in CANONICAL_COLUMNS:
        req = "yes" if col in REQUIRED_COLUMNS else "no"
        print(f"{col:22s} {req:8s} {descriptions[col]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true",
                        help="validate raw intake without writing processed output")
    parser.add_argument("--schema", action="store_true",
                        help="print the canonical outage schema and exit")
    parser.add_argument("--raw-dir", default=None, help="override raw intake directory")
    parser.add_argument("--out", default=None, help="override processed output path")
    args = parser.parse_args(argv)

    if args.schema:
        return _print_schema()

    summary = intake(args.raw_dir, args.out, write=not args.audit)
    _print_summary(summary)
    if not summary["files"]:
        return 1
    return 0 if summary["validation"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
