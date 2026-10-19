"""Timezone-safe handling of event, notice and feature timestamps.

Data-loss risk in this project is mostly a *time* risk: outage events,
planned-outage notices and weather rows only line up if every timestamp is
tz-aware UTC. These helpers refuse to guess — naive timestamps raise unless
the caller states the source timezone explicitly.

Conventions:
    All normalized timestamps are pandas datetime64 with UTC tz.
    ``NaT`` passes through untouched (missing stays missing).
"""

from __future__ import annotations

import re

import pandas as pd

__all__ = [
    "to_utc",
    "local_time_parts",
    "notice_lead_time",
]

# "2024-03-01T10:00:00+03:00", "...+0300", "...Z"
_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)


def _has_explicit_offset(value) -> bool:
    """True when a single value carries an explicit UTC offset."""
    if isinstance(value, str):
        return bool(_OFFSET_RE.search(value.strip()))
    tzinfo = getattr(value, "tzinfo", None)
    return tzinfo is not None and tzinfo.utcoffset(value) is not None


def _finalize(dt: pd.Series, assume_tz: str | None, label: str | None) -> pd.Series:
    """Move a datetime Series to UTC, requiring an explicit source zone for
    naive values."""
    where = f" for {label!r}" if label else ""
    if isinstance(dt.dtype, pd.DatetimeTZDtype):
        return dt.dt.tz_convert("UTC")
    if assume_tz is None:
        raise ValueError(
            f"naive timestamps{where} cannot be interpreted safely; "
            "pass assume_tz='...' (e.g. 'Africa/Nairobi') or normalize to UTC "
            "at ingestion"
        )
    try:
        localized = dt.dt.tz_localize(assume_tz)
    except (TypeError, KeyError, ValueError) as exc:  # invalid/ambiguous local times
        raise ValueError(
            f"could not localize timestamps{where} to {assume_tz}: {exc}"
        ) from exc
    return localized.dt.tz_convert("UTC")


def to_utc(values, *, assume_tz: str | None = None, label: str | None = None) -> pd.Series:
    """Normalize a datetime-like Series/array to tz-aware UTC.

    Args:
        values: Series, DatetimeIndex, array or scalar of timestamps
            (datetimes or ISO strings, possibly mixed offsets).
        assume_tz: source IANA zone — **required** for naive input.
        label: column name used in error messages.

    Returns:
        A copy (Series, index preserved) in UTC; ``NaT`` passes through.

    Raises:
        ValueError: naive input without ``assume_tz``, or a column mixing
            naive and offset-aware values (ambiguous — fix upstream).
    """
    if isinstance(values, pd.Series):
        s = values.copy()
        label = label or values.name
    else:
        # List/array of strings stays object-dtype and is parsed below (that
        # path also handles mixed aware offsets); DatetimeIndex keeps its tz.
        s = pd.Series(values)

    # Parse strings/other objects first; datetime64 dtypes go straight on.
    if s.dtype.kind != "M":
        if s.empty:
            return pd.Series(
                pd.DatetimeIndex([], tz="UTC"), index=s.index, name=s.name
            )
        try:
            s = pd.to_datetime(s, errors="raise")
        except ValueError:
            # pandas refuses mixed *aware* offsets; allow that only when every
            # value carries an offset (naive+aware mixes are ambiguous).
            if not all(_has_explicit_offset(v) for v in s.dropna()):
                raise ValueError(
                    f"column {label!r} mixes naive and timezone-aware timestamps; "
                    "normalize upstream before calling to_utc()"
                ) from None
            s = pd.to_datetime(s, utc=True, errors="raise")

    if s.isna().all() and not isinstance(s.dtype, pd.DatetimeTZDtype):
        # all-missing input: return an empty-typed UTC column instead of raising
        return pd.Series(pd.DatetimeIndex([pd.NaT] * len(s), tz="UTC"), index=s.index, name=s.name)

    return _finalize(s, assume_tz, label)


def local_time_parts(times, tz: str) -> pd.DataFrame:
    """Calendar parts of *local* wall time (leakage-safe: derived from the
    timestamp alone).

    Args:
        times: tz-aware UTC timestamps (naive input raises via ``to_utc``).
        tz: IANA zone to interpret the timestamps in (e.g. the country's
            zone from ``src.config.COUNTRY_TIMEZONES``).

    Returns:
        DataFrame indexed like the input with integer columns:
        ``local_hour`` (0–23), ``local_dayofweek`` (0=Mon … 6=Sun),
        ``local_month`` (1–12), ``is_weekend`` (0/1).
    """
    utc = to_utc(times, label=getattr(times, "name", None))
    local = utc.dt.tz_convert(tz)
    dow = local.dt.dayofweek
    return pd.DataFrame(
        {
            "local_hour": local.dt.hour.astype("int16"),
            "local_dayofweek": dow.astype("int8"),
            "local_month": local.dt.month.astype("int8"),
            "is_weekend": (dow >= 5).astype("int8"),
        },
        index=utc.index,
    )


def notice_lead_time(event_times, notice_times) -> pd.Series:
    """How long before an event its notice was published (event − notice).

    Positive ⇒ the notice preceded the event (the expected direction for
    *planned* outages); negative ⇒ the notice appeared after the event
    started (worth flagging during label construction).

    Both inputs must be tz-aware (use ``to_utc`` first); length must match.
    """
    event = to_utc(event_times, label="event_times")
    notice = to_utc(notice_times, label="notice_times")
    if len(event) != len(notice):
        raise ValueError(
            f"length mismatch: {len(event)} event times vs {len(notice)} notice times"
        )
    lead = event.reset_index(drop=True) - notice.reset_index(drop=True)
    lead.name = "lead_time"
    return lead
