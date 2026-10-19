"""Leakage-safe expanding-window cross-validation for ordered data.

Training folds only ever *grow into the past*: fold k trains on rows
``[0, a)`` and tests on a strictly later window, optionally with a purged
``gap`` between train and test so labels that straddle the boundary cannot
leak. Shuffling is never an option for this project's time series.

Folds are positional, so callers must pass rows sorted chronologically
(``src.features.sort_by_time``) — the split itself never inspects values,
only order.
"""

from __future__ import annotations

import numbers

import numpy as np
import pandas as pd

from src.evaluation.probabilistic import evaluation_report
from src.features.time_split import assert_strictly_past

__all__ = [
    "ExpandingWindowSplits",
    "expanding_window_splits",
    "walk_forward_oof",
    "walk_forward_scores",
]


def _as_time_values(time_values, *, n_rows: int):
    """Normalise an optional per-row timestamp series to a DatetimeIndex."""
    if time_values is None:
        return None
    index = pd.DatetimeIndex(pd.Series(time_values))
    if len(index) != n_rows:
        raise ValueError(
            f"time_values has {len(index)} rows but the data has {n_rows}"
        )
    if index.tz is None:
        raise ValueError("time_values must be timezone-aware")
    return index


def _as_index(idx, *, name: str) -> np.ndarray:
    """Positional indices as int array (boolean masks accepted)."""
    arr = np.asarray(idx)
    if arr.dtype == bool:
        if arr.ndim != 1:
            raise ValueError(f"{name} mask must be 1-D, got ndim={arr.ndim}")
        arr = np.flatnonzero(arr)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got ndim={arr.ndim}")
    if not np.issubdtype(arr.dtype, np.integer):
        raise ValueError(f"{name} must contain integer positions, got dtype {arr.dtype}")
    return arr.astype(int)


def _as_int(value, *, name: str, minimum: int) -> int:
    if not isinstance(value, numbers.Integral) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return int(value)


class ExpandingWindowSplits:
    """Re-iterable container of expanding-window folds.

    Iteration yields fresh ``(train_idx, test_idx)`` arrays every time, so
    the same ``splits`` object can be passed to several functions (e.g.
    ``walk_forward_oof`` and then ``walk_forward_scores``).
    """

    def __init__(
        self,
        n_rows: int,
        *,
        min_train: int,
        horizon: int,
        step: int | None = None,
        gap: int = 0,
        drop_remainder: bool = True,
    ):
        self.n_rows = _as_int(n_rows, name="n_rows", minimum=1)
        self.min_train = _as_int(min_train, name="min_train", minimum=1)
        self.horizon = _as_int(horizon, name="horizon", minimum=1)
        self.gap = _as_int(gap, name="gap", minimum=0)
        self.step = self.horizon if step is None else _as_int(step, name="step", minimum=1)
        self.drop_remainder = bool(drop_remainder)
        if self.min_train + self.gap + self.horizon > self.n_rows:
            raise ValueError(
                f"cannot build one fold: min_train({self.min_train}) + "
                f"gap({self.gap}) + horizon({self.horizon}) exceeds "
                f"n_rows({self.n_rows})"
            )

    def __iter__(self):
        train_end = self.min_train
        while True:
            test_start = train_end + self.gap
            test_end = test_start + self.horizon
            if test_end > self.n_rows:
                if not self.drop_remainder and test_start < self.n_rows:
                    yield np.arange(0, train_end), np.arange(test_start, self.n_rows)
                return
            yield np.arange(0, train_end), np.arange(test_start, test_end)
            train_end += self.step

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ExpandingWindowSplits(n_rows={self.n_rows}, "
            f"min_train={self.min_train}, horizon={self.horizon}, "
            f"step={self.step}, gap={self.gap}, "
            f"drop_remainder={self.drop_remainder})"
        )


def expanding_window_splits(
    n_rows: int,
    *,
    min_train: int,
    horizon: int,
    step: int | None = None,
    gap: int = 0,
    drop_remainder: bool = True,
) -> ExpandingWindowSplits:
    """Build expanding-window folds over ``n_rows`` ordered rows.

    Fold layout::

        train = [0, train_end)                    only the past
        test  = [train_end + gap, + horizon)      strictly later, gap purged

    Args:
        n_rows: total ordered rows to split.
        min_train: first training window length (>= 1).
        horizon: test window length in rows (>= 1).
        step: rows the training window grows per fold (default ``horizon``).
        gap: purged rows between train and test (labels spanning the cut
            must not reach the model).
        drop_remainder: skip a final partial test window when True.

    Returns:
        A re-iterable :class:`ExpandingWindowSplits`; bad arguments raise
        immediately (validation is eager, not deferred to iteration).

    Raises:
        ValueError: parameters that cannot produce a single full fold.
    """
    return ExpandingWindowSplits(
        n_rows,
        min_train=min_train,
        horizon=horizon,
        step=step,
        gap=gap,
        drop_remainder=drop_remainder,
    )


def _take(obj, idx: np.ndarray):
    """Row subset by position for DataFrames, Series and arrays alike."""
    if hasattr(obj, "iloc"):
        return obj.iloc[idx]
    return np.asarray(obj)[idx]


def walk_forward_oof(model_factory, X, y, splits, *, time_values=None) -> np.ndarray:
    """Out-of-fold probabilities for every row a fold actually tested.

    For each fold: build a fresh model via ``model_factory()``, fit on the
    training rows, predict the test rows. Rows never tested (purged gaps,
    the period before the first fold) stay NaN — evaluation must drop them.

    The factory guarantees no fold inherits state from another (fitting one
    model repeatedly would leak across folds).

    ``time_values`` is optional but recommended: pass the timestamp of each
    row and every fold is additionally checked to ensure the training
    timestamps really are earlier than the test timestamps. Positional order
    alone cannot catch a frame that was not sorted chronologically, which is
    the most common silent leakage bug in a time series.
    """
    if not callable(model_factory):
        raise ValueError("model_factory must be callable, e.g. lambda: ClimatologyBaseline()")
    y_arr = np.asarray(y)
    if y_arr.ndim != 1:
        raise ValueError(f"y must be 1-D, got ndim={y_arr.ndim}")
    if len(X) != len(y_arr):
        raise ValueError(f"X has {len(X)} rows but y has {len(y_arr)}")
    times = _as_time_values(time_values, n_rows=len(y_arr))

    oof = np.full(len(y_arr), np.nan)
    covered = np.zeros(len(y_arr), dtype=bool)
    n_folds = 0

    for fold_no, (train_idx, test_idx) in enumerate(splits, start=1):
        train_idx = _as_index(train_idx, name=f"fold {fold_no} train_idx")
        test_idx = _as_index(test_idx, name=f"fold {fold_no} test_idx")
        n_folds = fold_no
        if len(train_idx) == 0 or len(test_idx) == 0:
            raise ValueError(f"fold {fold_no}: train and test must be non-empty")
        if covered[test_idx].any():
            raise ValueError(
                f"fold {fold_no}: test rows overlap a previous fold — "
                "walk-forward scores assume disjoint test windows"
            )
        if train_idx.max() >= test_idx.min():
            raise ValueError(
                f"fold {fold_no}: train rows are not strictly before test rows "
                "(not a walk-forward split)"
            )
        if times is not None:
            assert_strictly_past(times[train_idx], times[test_idx])

        model = model_factory()
        if not hasattr(model, "fit") or not hasattr(model, "predict_proba"):
            raise TypeError(
                "models from model_factory must provide fit() and predict_proba()"
            )
        model.fit(_take(X, train_idx), _take(y_arr, train_idx))
        proba = np.asarray(model.predict_proba(_take(X, test_idx)), dtype=float)

        if proba.shape != (len(test_idx),):
            raise ValueError(
                f"fold {fold_no}: predict_proba returned shape {proba.shape}, "
                f"expected ({len(test_idx)},) — did you wrap a raw sklearn "
                "estimator? Use SklearnRiskModel."
            )
        if not np.isfinite(proba).all() or ((proba < 0) | (proba > 1)).any():
            raise ValueError(
                f"fold {fold_no}: predict_proba must return finite values in [0, 1]"
            )
        oof[test_idx] = proba
        covered[test_idx] = True

    if n_folds == 0:
        raise ValueError("splits yielded no folds")
    return oof


def walk_forward_scores(
    model_factory,
    X,
    y,
    splits,
    *,
    threshold: float = 0.5,
    reference_rate: float | None = None,
    time_values=None,
) -> pd.DataFrame:
    """Per-fold and pooled probabilistic metrics for walk-forward folds.

    One row per fold (1-based) plus a final ``"pooled"`` row computed over
    every out-of-fold probability — pooled numbers are the honest headline;
    per-fold rows show drift. Rows never tested stay NaN and are excluded.

    Metrics come from ``src.evaluation.probabilistic.evaluation_report``:
    Brier, log loss, skill scores, calibration ECE, ROC AUC and the
    thresholded metrics at ``threshold``.

    ``time_values`` (per-row timestamps) is forwarded to
    :func:`walk_forward_oof` so every fold is checked for temporal leakage.
    """
    splits = list(splits)
    oof = walk_forward_oof(model_factory, X, y, splits, time_values=time_values)
    y_arr = np.asarray(y)

    rows = []
    for fold_no, (train_idx, test_idx) in enumerate(splits, start=1):
        test_idx = _as_index(test_idx, name=f"fold {fold_no} test_idx")
        if len(test_idx) == 0:
            raise ValueError(f"fold {fold_no}: empty test fold")
        rows.append(
            {
                "fold": fold_no,
                "n_train": len(_as_index(train_idx, name=f"fold {fold_no} train_idx")),
                **evaluation_report(
                    y_arr[test_idx],
                    oof[test_idx],
                    threshold=threshold,
                    reference_rate=reference_rate,
                ),
            }
        )

    pooled = np.isfinite(oof)
    if not pooled.any():
        raise ValueError("no out-of-fold predictions were produced")
    rows.append(
        {
            "fold": "pooled",
            "n_train": np.nan,
            **evaluation_report(
                y_arr[pooled],
                oof[pooled],
                threshold=threshold,
                reference_rate=reference_rate,
            ),
        }
    )
    return pd.DataFrame(rows)
