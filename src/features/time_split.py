"""Leakage-safe temporal train/test splitting.

The project must never train on the future: every split here is defined by
*timestamps* (never row positions alone), both sides are tz-aware UTC, and
an optional embargo gap discards rows near the boundary so labels that
straddle the cutoff cannot leak into training.

Conventions:
    The frame is returned sorted by ``time_col`` (stable), index preserved.
    Rows inside the embargo gap belong to neither train nor test.
"""

from __future__ import annotations

import pandas as pd

from src.features.times import to_utc

__all__ = [
    "temporal_train_test_split",
    "assert_strictly_past",
]


def _require_time_column(frame: pd.DataFrame, time_col: str) -> pd.Series:
    if time_col not in frame.columns:
        raise ValueError(f"time column {time_col!r} not in frame columns {list(frame.columns)}")
    return to_utc(frame[time_col], label=time_col)


def _parse_cutoff(test_start) -> pd.Timestamp:
    """A split point must be explicit about its zone — no silent UTC guessing."""
    cutoff = pd.Timestamp(test_start)
    if cutoff.tzinfo is None:
        raise ValueError(
            "test_start must be timezone-aware "
            "(e.g. '2024-06-01T00:00:00+00:00'); naive cutoffs are ambiguous"
        )
    return cutoff


def _parse_gap(gap) -> pd.Timedelta:
    td = pd.Timedelta(gap)
    if td < pd.Timedelta(0):
        raise ValueError(f"gap/embargo must be >= 0, got {gap!r}")
    return td


def temporal_train_test_split(
    frame: pd.DataFrame,
    time_col: str,
    *,
    test_start=None,
    test_fraction: float | None = None,
    gap=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``frame`` into (train, test) with test strictly after train.

    Args:
        frame: rows with a time column.
        time_col: name of the tz-aware time column (naive input raises —
            normalize with ``src.features.times.to_utc`` first).
        test_start: first timestamp of the test set; must be tz-aware.
            Mutually exclusive with ``test_fraction``.
        test_fraction: fraction of rows (in time order) for the test set,
            in (0, 1). The cutoff is the first test timestamp; rows sharing
            that timestamp all land in the test set (no time overlaps).
        gap: embargo between the sets — training rows within ``gap`` before
            the cutoff are dropped from *both* sides, so a label window
            straddling the cutoff cannot leak into training.

    Returns:
        (train, test) copies, each sorted by ``time_col`` (stable), original
        index labels preserved for traceability.

    Raises:
        ValueError: missing/naive time column, invalid arguments, or an
            empty train/test side (a split must be usable for evaluation).
    """
    if (test_start is None) == (test_fraction is None):
        raise ValueError("provide exactly one of test_start or test_fraction")
    if not isinstance(frame, pd.DataFrame):
        raise ValueError(f"frame must be a DataFrame, got {type(frame).__name__}")
    if len(frame) == 0:
        raise ValueError("cannot split an empty frame")

    times = _require_time_column(frame, time_col)

    if test_start is not None:
        cutoff = _parse_cutoff(test_start)
    else:
        if not isinstance(test_fraction, (int, float)) or isinstance(test_fraction, bool):
            raise ValueError(f"test_fraction must be a float, got {test_fraction!r}")
        if not 0.0 < float(test_fraction) < 1.0:
            raise ValueError(f"test_fraction must be in (0, 1), got {test_fraction}")
        n_test = max(1, int(round(len(frame) * float(test_fraction))))
        cutoff = times.sort_values(kind="stable").iloc[len(frame) - n_test]

    gap_td = _parse_gap(gap) if gap is not None else pd.Timedelta(0)
    train_mask = times < (cutoff - gap_td)
    test_mask = times >= cutoff

    train = frame.loc[train_mask].sort_values(time_col, kind="stable")
    test = frame.loc[test_mask].sort_values(time_col, kind="stable")

    if len(train) == 0:
        raise ValueError(
            f"train side is empty: no rows strictly before cutoff {cutoff}"
            + (f" minus gap {gap_td}" if gap_td else "")
        )
    if len(test) == 0:
        raise ValueError(f"test side is empty: no rows at or after cutoff {cutoff}")
    return train, test


def assert_strictly_past(train_times, test_times) -> None:
    """Raise unless every training timestamp precedes every test timestamp.

    Guards folds before fitting: equal timestamps on both sides of a cut
    mean the model has already seen the evaluation moment.
    """
    train = pd.DatetimeIndex(pd.Series(train_times))
    test = pd.DatetimeIndex(pd.Series(test_times))
    if len(train) == 0 or len(test) == 0:
        raise ValueError("cannot compare empty train/test timestamp sets")
    if train.tz is None or test.tz is None:
        raise ValueError("train/test timestamps must be timezone-aware")
    if train.max() >= test.min():
        raise ValueError(
            f"leakage: latest training timestamp {train.max()} is not strictly "
            f"before earliest test timestamp {test.min()}"
        )
