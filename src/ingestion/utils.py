"""Shared ingestion utilities: HTTP with retry, provenance I/O, schema checks.

Every ingestion module (boundaries, weather, outages, ...) uses these so
retries, provenance sidecars and validation behave identically everywhere.

Contents:
  * ``get_with_retries``      — GET with exponential backoff on 429/5xx and
                                connection errors (never retries other 4xx).
  * ``write_bytes_atomic``    — write via a ``.part`` temp file so an
                                interrupted download never leaves a truncated
                                file under the final name (partial downloads
                                are re-fetched instead of silently skipped).
  * ``write_json`` /          — provenance sidecar and manifest helpers
    ``write_provenance``         (``*_meta.json`` per file, ``_manifest.json``
                                per directory — same conventions as the
                                boundary and weather downloaders).
  * ``parse_time``            — tolerant timestamp parsing that distinguishes
                                tz-aware, tz-naive (assumed UTC, flagged) and
                                invalid values.
  * ``missing_columns`` /     — small schema-validation helpers that raise
    ``require_tz_aware``         ``SchemaError`` with a precise message.

No network access happens at import time; tests stub ``requests.get``.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import pandas as pd
import requests

DEFAULT_TIMEOUT = 300  # seconds, large file downloads
SMALL_TIMEOUT = 60  # seconds, metadata/API calls

DEFAULT_TRIES = 4
DEFAULT_BASE_DELAY = 1.0  # seconds; doubles per attempt
MAX_DELAY = 30.0

# Sentinel returned by parse_time for unparseable values.
INVALID = "invalid"


class SchemaError(ValueError):
    """Raised when a frame or record does not match the expected schema."""


def utc_now_iso() -> str:
    """Current UTC time, ISO 8601 with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 hex digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# HTTP with retry / backoff
# ---------------------------------------------------------------------------

def _retryable(exc: requests.RequestException) -> bool:
    """Connection-level failures and 429/5xx are worth retrying; other 4xx are not."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(
        exc,
        (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ),
    )


def get_with_retries(
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    timeout: int = SMALL_TIMEOUT,
    tries: int = DEFAULT_TRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = MAX_DELAY,
    headers: Optional[Mapping[str, str]] = None,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """GET ``url`` with exponential backoff.

    Retries connection errors, timeouts, HTTP 429 and 5xx up to ``tries``
    times with delays ``base_delay, 2x, 4x, ...`` capped at ``max_delay``.
    Other 4xx responses raise immediately (retrying cannot help).
    The final attempt's exception propagates.
    """
    if tries < 1:
        raise ValueError("tries must be >= 1")
    get = session.get if session is not None else requests.get
    delay = base_delay
    last_exc: Optional[requests.RequestException] = None
    for attempt in range(1, tries + 1):
        try:
            resp = get(url, params=params, timeout=timeout, headers=headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} for {url}", response=resp
                )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == tries or not _retryable(exc):
                raise
            print(f"  [warn] attempt {attempt}/{tries} failed ({exc}); "
                  f"retrying in {delay:g}s")
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
    raise last_exc if last_exc else RuntimeError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Atomic file writes (no partial downloads under final names)
# ---------------------------------------------------------------------------

def write_bytes_atomic(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` atomically (temp file + rename).

    A crash mid-write leaves at most a ``*.part`` file, never a truncated
    file that later runs would treat as complete.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)  # os.replace semantics: atomic on same filesystem
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


# ---------------------------------------------------------------------------
# Provenance / JSON helpers
# ---------------------------------------------------------------------------

def ensure_parent(path: Path) -> Path:
    """Create the parent directory of ``path`` if needed; return ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> Path:
    """Write pretty-printed UTF-8 JSON to ``path`` (parent dirs created)."""
    ensure_parent(path)
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return Path(path)


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from ``path``; return ``default`` when the file is absent."""
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_provenance(path: Path, meta: Mapping[str, Any], key: Optional[str] = None) -> Path:
    """Write a provenance sidecar.

    * ``key is None`` — write ``meta`` as a standalone sidecar file
      (``{stem}_meta.json`` convention used by the boundary downloader).
    * ``key`` given — merge ``meta`` under ``key`` into the JSON object at
      ``path`` (``_manifest.json`` convention used by the weather downloader).
    """
    path = Path(path)
    if key is None:
        return write_json(path, dict(meta))
    data = read_json(path, default={})
    if not isinstance(data, dict):
        raise SchemaError(f"{path} is not a JSON object; cannot merge provenance")
    data[key] = dict(meta)
    return write_json(path, data)


def sanitize_filename(name: str) -> str:
    """Keep only alphanumerics, ``-``, ``_``, ``.``; never return empty.

    Path separators and other unsafe characters become ``_``, and leading
    dots are neutralised so a sanitised name can never be a relative path
    component (``..``) or a hidden file. Interior dots are preserved so
    names like ``KEN_ADM0.geojson`` survive.
    """
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)
    cleaned = cleaned.lstrip(".") or "unnamed"
    return cleaned or "unnamed"


# ---------------------------------------------------------------------------
# Timestamp parsing (timezone discipline)
# ---------------------------------------------------------------------------

def parse_time(value: Any) -> tuple[Optional[pd.Timestamp], bool, bool]:
    """Parse a timestamp into ``(utc_timestamp, assumed_utc, invalid)``.

    * tz-aware input   → converted to UTC, ``assumed_utc=False``
    * tz-naive input   → localized as UTC, ``assumed_utc=True`` (caller must
      surface this as a warning — naive source times are ambiguous)
    * empty / missing  → ``(None, False, False)``
    * unparseable      → ``(None, False, True)``

    Strings ending in ``Z``/``z`` are accepted; ``pd.NaT``/NaN count as empty.
    """
    if value is None or value is pd.NaT:
        return None, False, False
    if isinstance(value, float) and pd.isna(value):
        return None, False, False
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None, False, False
        if value.endswith(("Z", "z")):
            value = value[:-1] + "+00:00"
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None, False, True
    if ts is pd.NaT or pd.isna(ts):
        return None, False, False
    if ts.tzinfo is None:
        return ts.tz_localize("UTC"), True, False
    return ts.tz_convert("UTC"), False, False


# ---------------------------------------------------------------------------
# Schema-validation helpers
# ---------------------------------------------------------------------------

def missing_columns(df: pd.DataFrame, required: Sequence[str]) -> list[str]:
    """Names from ``required`` that are absent from ``df``."""
    return [c for c in required if c not in df.columns]


def require_columns(df: pd.DataFrame, required: Sequence[str]) -> None:
    """Raise ``SchemaError`` listing every missing column."""
    missing = missing_columns(df, required)
    if missing:
        raise SchemaError(f"missing required columns: {', '.join(missing)}")


def is_tz_aware(series: pd.Series) -> bool:
    """True when ``series`` is a datetime column with a timezone."""
    return bool(
        pd.api.types.is_datetime64_any_dtype(series)
        and getattr(series.dtype, "tz", None) is not None
    )


def require_tz_aware(series: pd.Series, name: str) -> None:
    """Raise ``SchemaError`` unless ``series`` is a tz-aware datetime column."""
    if not pd.api.types.is_datetime64_any_dtype(series):
        raise SchemaError(f"{name}: expected datetime column, got {series.dtype}")
    if getattr(series.dtype, "tz", None) is None:
        raise SchemaError(f"{name}: column is timezone-naive; localize first")


def coerce_int(value: Any) -> Optional[int]:
    """Best-effort int coercion; ``None`` when impossible (bools included)."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def frame_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Compact, JSON-safe summary of a frame (rows/columns/empty columns)."""
    return {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "empty_columns": [c for c in df.columns if df[c].isna().all()],
    }
