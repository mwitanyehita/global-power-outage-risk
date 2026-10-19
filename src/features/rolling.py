"""Leakage-safe lag and rolling features (strictly past only).

Every feature here is computed from rows *strictly before* the current
row: lags shift by >= 1, and rolling windows are applied to the shifted
series, so a row can never influence its own feature. Rolling windows are
counted in rows, which is only honest on a gap-free regular grid — hence
``assert_regular_grid`` (the Day 3 weather audit guarantees exactly that
grid for raw weather).

Frames must be chronological within each group; anything else raises
instead of silently producing "features" built from the future.
"""

from __future__ import annotations

import numbers

import pandas as pd

__all__ = [
    "sort_by_time",
    "assert_chronological",
    "assert_regular_grid",
    "add_lags",
    "add_past_rolling",
    "PAST_ROLLING_STATS",
]

#: Aggregations supported by ``add_past_rolling`` (pandas rolling methods).
PAST_ROLLING_STATS: tuple[str, ...] = (
    "mean", "sum", "min", "max", "std", "median", "count",
)


def _check_frame(
    frame: pd.DataFrame,
    time_col: str,
    columns: list[str],
    group_cols: list[str],
) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError(f"frame must be a DataFrame, got {type(frame).__name__}")
    missing = [c for c in [time_col, *columns, *group_cols] if c not in frame.columns]
    if missing:
        raise ValueError(f"missing columns {missing}; available: {list(frame.columns)}")
    if not isinstance(frame[time_col].dtype, pd.DatetimeTZDtype):
        raise ValueError(
            f"time column {time_col!r} must be timezone-aware UTC "
            "(normalize with src.features.times.to_utc first)"
        )
    if frame[group_cols].isna().any().any():
        raise ValueError(
            f"group columns {group_cols} contain nulls; groupby would drop "
            "those rows and silently misalign features"
        )
    for col in columns:
        if col in group_cols:
            raise ValueError(f"{col!r} cannot be both a feature column and a group column")


def _normalize_groups(group_cols) -> list[str]:
    if group_cols is None:
        return []
    if isinstance(group_cols, str):
        return [group_cols]
    groups = list(group_cols)
    if len(set(groups)) != len(groups):
        raise ValueError(f"duplicate group columns: {groups}")
    return groups


def _normalize_sequence(values, *, name: str, minimum: int) -> list[int]:
    """A non-empty sequence of integers >= minimum (bools rejected)."""
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise ValueError(f"{name} must be a sequence of integers, got {values!r}")
    out = list(values)
    if not out:
        raise ValueError(f"{name} must not be empty")
    for v in out:
        if not isinstance(v, numbers.Integral) or isinstance(v, bool):
            raise ValueError(f"{name} must contain integers, got {v!r}")
        if v < minimum:
            raise ValueError(f"{name} values must be >= {minimum}, got {v}")
    if len(set(out)) != len(out):
        raise ValueError(f"{name} contains duplicates: {out}")
    return out


def _normalize_columns(columns) -> list[str]:
    """Column name or sequence of unique string column names."""
    if isinstance(columns, str):
        cols = [columns]
    elif hasattr(columns, "__iter__"):
        cols = list(columns)
    else:
        raise ValueError(f"columns must be a column name or sequence, got {columns!r}")
    if not cols:
        raise ValueError("columns must not be empty")
    if any(not isinstance(c, str) for c in cols):
        raise ValueError(f"column names must be strings, got {cols}")
    if len(set(cols)) != len(cols):
        raise ValueError(f"duplicate columns: {cols}")
    return cols


def sort_by_time(
    frame: pd.DataFrame, time_col: str, group_cols=None
) -> pd.DataFrame:
    """Stable-sort copy by (group_cols..., time_col) — the canonical input
    order for the functions in this module."""
    groups = _normalize_groups(group_cols)
    _check_frame(frame, time_col, [], groups)
    by = [*groups, time_col] if groups else time_col
    return frame.sort_values(by=by, kind="stable")


def assert_chronological(
    frame: pd.DataFrame, time_col: str, group_cols=None
) -> None:
    """Raise unless rows are non-decreasing in time within every group.

    Feature functions call this so that a mis-ordered frame fails loudly
    instead of producing lag/rolling values computed from the future.
    """
    groups = _normalize_groups(group_cols)
    _check_frame(frame, time_col, [], groups)
    if frame[time_col].isna().any():
        raise ValueError(f"time column {time_col!r} contains NaT")
    if not groups:
        if not frame[time_col].is_monotonic_increasing:
            raise ValueError(
                f"rows are not sorted by {time_col!r}; "
                "call sort_by_time() first (lag/rolling features assume order)"
            )
        return

    flags = frame.groupby(groups, sort=False)[time_col].apply(
        lambda s: bool(s.is_monotonic_increasing)
    )
    bad = [idx for idx, ok in flags.items() if not ok]
    if bad:
        raise ValueError(
            f"rows are not sorted by {time_col!r} within group(s) {bad}; "
            "call sort_by_time() first"
        )


def assert_regular_grid(
    frame: pd.DataFrame, time_col: str, freq: str = "1h", group_cols=None
) -> None:
    """Raise unless consecutive timestamps are exactly ``freq`` apart.

    Guards row-count rolling windows: a lag of 24 rows means 24 hours only
    on a gap-free grid. Duplicate timestamps and gaps both raise.
    """
    groups = _normalize_groups(group_cols)
    assert_chronological(frame, time_col, groups)
    step = pd.Timedelta(freq)

    def _check(block: pd.DataFrame, where: str) -> None:
        diffs = block[time_col].diff().dropna()
        bad = diffs[diffs != step]
        if len(bad):
            first = bad.iloc[0]
            raise ValueError(
                f"time grid in {where} breaks {freq}: first bad step is {first} "
                f"({len(bad)} break(s))"
            )

    if groups:
        for keys, block in frame.groupby(groups, sort=False):
            _check(block, dict(zip(groups, keys if isinstance(keys, tuple) else (keys,))))
    else:
        _check(frame, time_col)


def add_lags(
    frame: pd.DataFrame,
    columns,
    lags,
    *,
    time_col: str,
    group_cols=None,
) -> pd.DataFrame:
    """Copy of ``frame`` with ``{column}_lag_{n}`` columns (n >= 1).

    Lags are computed *within* each group, so a group's first rows get NaN
    rather than another group's history. The input frame must be sorted
    chronologically (see ``sort_by_time``).
    """
    columns = _normalize_columns(columns)
    lags = _normalize_sequence(lags, name="lags", minimum=1)
    groups = _normalize_groups(group_cols)
    _check_frame(frame, time_col, columns, groups)
    assert_chronological(frame, time_col, groups)

    out = frame.copy()
    for col in columns:
        source = out[col]
        keyed = source.groupby([out[g] for g in groups], sort=False) if groups else None
        for lag in lags:
            shifted = keyed.shift(lag) if keyed is not None else source.shift(lag)
            out[f"{col}_lag_{lag}"] = shifted
    return out


def add_past_rolling(
    frame: pd.DataFrame,
    columns,
    windows,
    *,
    time_col: str,
    stat: str = "mean",
    group_cols=None,
    min_periods: int = 1,
) -> pd.DataFrame:
    """Copy of ``frame`` with ``{column}_past_{stat}_{window}`` columns.

    The window is applied to the *shifted* series, so it aggregates rows
    strictly before the current row (row 0 of each group is always NaN —
    it has no past). Windows count rows; call ``assert_regular_grid``
    first when rows do not represent uniform time steps.

    Args:
        stat: one of ``PAST_ROLLING_STATS``.
        min_periods: minimum past observations required per window
            (1 = use whatever past exists; ``window`` = require full history).
    """
    columns = _normalize_columns(columns)
    windows = _normalize_sequence(windows, name="windows", minimum=1)
    groups = _normalize_groups(group_cols)
    if stat not in PAST_ROLLING_STATS:
        raise ValueError(
            f"stat must be one of {PAST_ROLLING_STATS}, got {stat!r}"
        )
    if not isinstance(min_periods, numbers.Integral) or isinstance(min_periods, bool) or min_periods < 1:
        raise ValueError(f"min_periods must be an integer >= 1, got {min_periods!r}")
    if min_periods > max(windows):
        raise ValueError(
            f"min_periods={min_periods} exceeds the largest window "
            f"({max(windows)}); every window would be NaN"
        )
    _check_frame(frame, time_col, columns, groups)
    assert_chronological(frame, time_col, groups)

    def _roll(series: pd.Series, window: int) -> pd.Series:
        return getattr(
            series.shift(1).rolling(window, min_periods=min_periods), stat
        )()

    out = frame.copy()
    for col in columns:
        for window in windows:
            if groups:
                values = out.groupby(groups, sort=False)[col].transform(
                    lambda s, w=window: _roll(s, w)
                )
            else:
                values = _roll(out[col], window)
            out[f"{col}_past_{stat}_{window}"] = values
    return out
