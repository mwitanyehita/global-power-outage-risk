"""Standardize unit codes, names and timestamps across sources (Day 8).

Project rules this module enforces (README / data_inventory risks):

* **Timestamps are tz-aware UTC everywhere.** Raw weather arrives as UTC by
  construction (``timezone=UTC`` in the Day 3 request); any naive timestamp
  encountered during cleaning is *assumed* UTC and the assumption is explicit
  in :func:`normalize_timestamp` so it can be audited.
* **Local time is a presentation concern only.** Use :func:`to_local` with the
  pilot timezone map for display/plots; never mix local-time columns into
  model features.
* **Names are ambiguous, coordinates are not.** ``normalize_unit_name`` exists
  to *find* candidate matches (and to detect duplicates, Day 12) — the
  authoritative geography join on Day 11 is point-in-polygon. Name matching is
  a secondary check only.
* **Country codes**: everything normalizes to ISO3, validated against the
  pilot constant.

CTX-4 (country lookup tables) lives here: :data:`COUNTRIES`.

Usage:
    from src.cleaning.standardize import (
        COUNTRIES, to_iso3, normalize_unit_name, normalize_timestamp,
        to_local, hourly_grid,
    )
"""

from __future__ import annotations

import re
import unicodedata
from zoneinfo import ZoneInfo

import pandas as pd

# ---------------------------------------------------------------------------
# CTX-4: country lookup (built in-project, Day 8)
# ---------------------------------------------------------------------------

COUNTRIES: dict[str, dict[str, str]] = {
    "KEN": {
        "iso2": "KE",
        "numeric": "404",
        "name": "Kenya",
        "official_name": "Republic of Kenya",
        "timezone": "Africa/Nairobi",  # EAT, UTC+3, no DST
        "region": "Eastern Africa",
        "currency": "KES",
    },
    "ZAF": {
        "iso2": "ZA",
        "numeric": "710",
        "name": "South Africa",
        "official_name": "Republic of South Africa",
        "timezone": "Africa/Johannesburg",  # SAST, UTC+2, no DST
        "region": "Southern Africa",
        "currency": "ZAR",
    },
    "NGA": {
        "iso2": "NG",
        "numeric": "566",
        "name": "Nigeria",
        "official_name": "Federal Republic of Nigeria",
        "timezone": "Africa/Lagos",  # WAT, UTC+1, no DST
        "region": "Western Africa",
        "currency": "NGN",
    },
}

PILOT_ISO3: tuple[str, ...] = tuple(COUNTRIES)

# Lookup table for to_iso3(): lowercase aliases -> ISO3.
_COUNTRY_ALIASES: dict[str, str] = {}
for _iso3, _meta in COUNTRIES.items():
    for _key in (_iso3, _meta["iso2"], _meta["numeric"], _meta["name"],
                 _meta["official_name"]):
        _COUNTRY_ALIASES[_key.casefold()] = _iso3
# Common variants beyond the formal names.
_COUNTRY_ALIASES.update({
    "republic of south africa": "ZAF",
    "s. africa": "ZAF",
    "rsa": "ZAF",
    "federal republic of nigeria": "NGA",
    "naija": "NGA",
    "ken": "KEN",
    "zaf": "ZAF",
    "nga": "NGA",
})

# ---------------------------------------------------------------------------
# Codes and names
# ---------------------------------------------------------------------------


def to_iso3(value: object) -> str:
    """Normalize any ISO2/ISO3/numeric/common name to the pilot ISO3 code.

    Raises:
        ValueError: value is not a recognized country identifier.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("country value is missing")
    text = str(value).strip()
    if not text:
        raise ValueError("country value is empty")
    if text in COUNTRIES:  # already ISO3, exact
        return text
    iso3 = _COUNTRY_ALIASES.get(text.casefold())
    if iso3 is None:
        raise ValueError(f"unrecognized country identifier: {value!r}")
    return iso3


def standardize_countries(values) -> pd.Series:
    """Vector form of :func:`to_iso3`; invalid values become ``pd.NA``.

    Use when cleaning a frame; count the ``NA``s afterwards rather than
    letting one bad row abort the pipeline.
    """
    out = []
    for v in values:
        try:
            out.append(to_iso3(v))
        except ValueError:
            out.append(pd.NA)
    idx = values.index if isinstance(values, (pd.Series, pd.DataFrame)) else None
    return pd.Series(out, index=idx, dtype="object")


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize_unit_name(value: object) -> str:
    """Canonical matching key for administrative unit names.

    Casefolded, punctuation-free, whitespace-collapsed. Deliberately keeps
    diacritics' base letters via NFKD folding so e.g. "Mombasa" and
    "Mombasa County" still differ only by their suffix — see
    :func:`strip_admin_suffix` for the suffix-trimming pass.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.replace("&", " and ")
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip().casefold()
    return text


_ADMIN_SUFFIXES = (
    "county", "counties", "city", "municipality", "province", "provinces",
    "state", "states", "district", "districts", "region", "regions",
    "sub-county", "sub county", "metropolitan", "fct", "territory",
)


def strip_admin_suffix(normalized_name: str) -> str:
    """Drop trailing administrative qualifiers from an already-normalized name.

    ``normalize_unit_name("Nairobi City County")`` -> ``"nairobi city county"``
    -> ``"nairobi"``. Used only to widen *candidate* name matching; matches are
    confirmed by coordinates on Day 11.
    """
    words = normalized_name.split()
    while words and words[-1] in _ADMIN_SUFFIXES:
        words.pop()
    return " ".join(words)


def name_match_key(value: object) -> str:
    """One-shot key: normalize then strip admin suffixes (candidate matching)."""
    return strip_admin_suffix(normalize_unit_name(value))


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def normalize_timestamp(values, assume_tz: str = "UTC") -> pd.Series:
    """Coerce a time column to tz-aware UTC.

    Naive timestamps are localized to ``assume_tz`` (default ``UTC``) — the
    assumption is a parameter so an audit can see it, not hide it. Aware
    timestamps are converted. Unparseable values become ``NaT``.

    Args:
        values: anything ``pd.to_datetime`` accepts (str, Series, ...).
        assume_tz: timezone to assume for naive input. Raw weather is UTC by
            construction; KPLC/NERC documents are local time (EAT/WAT) and
            must be passed explicitly with ``assume_tz="Africa/Nairobi"`` etc.
    """
    idx = values.index if isinstance(values, (pd.Series, pd.DataFrame)) else None
    ts = pd.to_datetime(values, errors="coerce", format="mixed")
    ts = pd.Series(ts, index=idx)
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(assume_tz)
    return ts.dt.tz_convert("UTC")


def floor_hour(ts: pd.Series) -> pd.Series:
    """Floor tz-aware timestamps to the hour (UTC), keeping tz."""
    return ts.dt.floor("h")


def to_local(ts: pd.Series, iso3: str) -> pd.Series:
    """Convert UTC timestamps to the pilot country's local timezone.

    Presentation only — features stay in UTC (README: prevent leakage and
    ambiguous conversions).
    """
    tz_name = COUNTRIES[to_iso3(iso3)]["timezone"]
    return ts.dt.tz_convert(ZoneInfo(tz_name))


def hourly_grid(start: str, end: str) -> pd.DatetimeIndex:
    """Shared UTC hourly index for label/feature alignment."""
    return pd.date_range(start=start, end=end, freq="h", tz="UTC")


def assert_hourly_grid(times: pd.Series, grid: pd.DatetimeIndex | None = None) -> list[str]:
    """Return a list of timestamp problems (empty == clean).

    Checks: tz-aware, strictly increasing, no duplicates, uniform 1h spacing,
    and optionally exact equality with a reference ``grid``. Returns problems
    instead of raising so callers can log every issue in one pass.
    """
    problems: list[str] = []
    if str(times.dt.tz) != "UTC":
        problems.append("timestamps are not tz-aware UTC")
    if times.duplicated().any():
        problems.append(f"{int(times.duplicated().sum())} duplicate timestamps")
    if not times.is_monotonic_increasing:
        problems.append("timestamps are not monotonic increasing")
    diffs = times.diff().dropna()
    if len(diffs) and not (diffs == pd.Timedelta(hours=1)).all():
        bad = int((diffs != pd.Timedelta(hours=1)).sum())
        problems.append(f"{bad} gaps/non-hourly steps")
    if grid is not None and not times.reset_index(drop=True).equals(pd.Series(grid)):
        problems.append("timestamps do not match the reference hourly grid")
    return problems
