"""Tests for src.models.cv — expanding-window splits and walk-forward scoring."""

from __future__ import annotations

import numpy as np
import pytest

from src.models.base import ClimatologyBaseline, SklearnRiskModel
from src.models.cv import (
    ExpandingWindowSplits,
    expanding_window_splits,
    walk_forward_oof,
    walk_forward_scores,
)


def _splits(**kwargs) -> list[tuple[np.ndarray, np.ndarray]]:
    defaults = dict(n_rows=10, min_train=4, horizon=2, step=2)
    defaults.update(kwargs)
    return list(expanding_window_splits(**defaults))


# -- fold layout ---------------------------------------------------------------


def test_fold_layout_is_purely_expanding_past_only():
    folds = _splits()
    assert [(len(tr), int(te[0])) for tr, te in folds] == [(4, 4), (6, 6), (8, 8)]


def test_every_test_window_is_strictly_after_its_training_window():
    for train_idx, test_idx in _splits():
        assert train_idx.max() < test_idx.min()
        assert len(np.intersect1d(train_idx, test_idx)) == 0


def test_training_windows_only_grow():
    folds = _splits()
    for (prev_train, _), (next_train, _) in zip(folds, folds[1:]):
        assert np.array_equal(prev_train, next_train[: len(prev_train)])
        assert len(next_train) > len(prev_train)


def test_gap_purges_rows_between_train_and_test():
    folds = _splits(gap=1)
    train_idx, test_idx = folds[0]
    assert train_idx.max() == 3
    assert test_idx.min() == 5  # row 4 purged from both sides
    assert len(folds) == 2  # the purged gap also costs the last full fold


def test_default_step_equals_horizon():
    folds = list(expanding_window_splits(10, min_train=4, horizon=2))
    assert [(len(tr), int(te[0])) for tr, te in folds] == [(4, 4), (6, 6), (8, 8)]


def test_drop_remainder_controls_final_partial_window():
    strict = _splits(n_rows=9, drop_remainder=True)
    keep = _splits(n_rows=9, drop_remainder=False)
    assert len(strict) == 2
    assert len(keep) == 3
    # the retained partial window still stops at the last available row
    assert keep[-1][1][-1] == 8


def test_large_monthly_layout_yields_four_folds_and_80_scored_rows():
    folds = list(expanding_window_splits(120, min_train=40, horizon=20, step=20))
    assert len(folds) == 4
    assert sum(len(te) for _, te in folds) == 80
    assert folds[0][0].max() == 39
    assert folds[-1][1][-1] == 119


def test_splits_are_reiterable_and_len_matches_iteration():
    splits = expanding_window_splits(10, min_train=4, horizon=2, step=2)
    first = [(list(tr), list(te)) for tr, te in splits]
    second = [(list(tr), list(te)) for tr, te in splits]
    assert first == second  # a generator would be exhausted here
    assert len(splits) == len(first)


def test_repr_mentions_key_parameters():
    text = repr(ExpandingWindowSplits(10, min_train=4, horizon=2, step=2))
    assert "n_rows=10" in text and "min_train=4" in text


def test_impossible_fold_configuration_fails_eagerly():
    with pytest.raises(ValueError, match="cannot build one fold"):
        expanding_window_splits(10, min_train=8, horizon=3)
    with pytest.raises(ValueError, match="cannot build one fold"):
        expanding_window_splits(5, min_train=3, horizon=1, gap=3)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(n_rows=0, min_train=1, horizon=1),
        dict(n_rows=1.5, min_train=1, horizon=1),
        dict(n_rows=True, min_train=1, horizon=1),
        dict(n_rows=10, min_train=0, horizon=2),
        dict(n_rows=10, min_train=4, horizon=0),
        dict(n_rows=10, min_train=4, horizon=2, step=0),
        dict(n_rows=10, min_train=4, horizon=2, gap=-1),
    ],
)
def test_bad_split_arguments_rejected_immediately(kwargs):
    with pytest.raises(ValueError):
        expanding_window_splits(**kwargs)


# -- walk_forward_oof -----------------------------------------------------------


def _linear_xy(n=40, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 2))
    y = (X[:, 0] + 0.5 * rng.normal(size=n) > 0).astype(int)
    return X, y


def test_oof_covers_every_fold_test_row_and_nan_everywhere_else():
    X, y = _linear_xy()
    splits = expanding_window_splits(len(y), min_train=20, horizon=5, step=5)
    oof = walk_forward_oof(lambda: SklearnRiskModel(), X, y, splits)

    assert oof.shape == (len(y),)
    covered = np.isfinite(oof)
    # first 20 rows never tested -> NaN; every later row tested exactly once
    assert not covered[:20].any()
    assert covered[20:].all()
    assert ((oof[covered] >= 0) & (oof[covered] <= 1)).all()


def test_oof_gap_rows_stay_nan():
    X, y = _linear_xy(30)
    splits = expanding_window_splits(
        len(y), min_train=10, horizon=4, step=4, gap=2
    )
    oof = walk_forward_oof(lambda: ClimatologyBaseline(), X, y, splits)
    # gap rows (positions 10-11 in the first fold) are never scored
    assert np.isnan(oof[10]) and np.isnan(oof[11])


def test_oof_uses_fresh_model_per_fold():
    created = []

    def factory():
        model = ClimatologyBaseline()
        created.append(model)
        return model

    X, y = _linear_xy(40)
    splits = expanding_window_splits(len(y), min_train=20, horizon=10, step=10)
    walk_forward_oof(factory, X, y, splits)
    assert len(created) == 2
    assert created[0] is not created[1]


def test_oof_values_match_refitting_by_hand():
    X, y = _linear_xy(40)
    splits = expanding_window_splits(len(y), min_train=20, horizon=10, step=10)
    oof = walk_forward_oof(lambda: ClimatologyBaseline(), X, y, splits)

    train_idx, test_idx = list(splits)[0]
    manual = ClimatologyBaseline().fit(X[train_idx], y[train_idx])
    np.testing.assert_allclose(oof[test_idx], manual.predict_proba(X[test_idx]))


def test_oof_rejects_overlapping_test_windows():
    X, y = _linear_xy(10)
    bad = [
        (np.arange(0, 4), np.arange(4, 8)),
        (np.arange(0, 6), np.arange(5, 9)),  # test rows 5-7 repeat fold 1
    ]
    with pytest.raises(ValueError, match="overlap"):
        walk_forward_oof(lambda: ClimatologyBaseline(), X, y, bad)


def test_oof_rejects_walk_backward_fold():
    bad = [(np.arange(2, 8), np.arange(0, 2))]
    X, y = _linear_xy(10)
    with pytest.raises(ValueError, match="strictly before"):
        walk_forward_oof(lambda: ClimatologyBaseline(), X, y, bad)


def test_oof_rejects_empty_fold_and_empty_split_list():
    X, y = _linear_xy(10)
    with pytest.raises(ValueError, match="non-empty"):
        walk_forward_oof(
            lambda: ClimatologyBaseline(), X, y,
            [(np.array([], dtype=int), np.array([0], dtype=int))],
        )
    with pytest.raises(ValueError, match="no folds"):
        walk_forward_oof(lambda: ClimatologyBaseline(), X, y, [])


def test_oof_rejects_non_factory_and_bad_models():
    X, y = _linear_xy(10)
    splits = [(np.arange(0, 4), np.arange(4, 8))]

    with pytest.raises(ValueError, match="callable"):
        walk_forward_oof("not callable", X, y, splits)

    class NoFit:  # missing fit/predict_proba contract
        pass

    with pytest.raises(TypeError, match="fit"):
        walk_forward_oof(lambda: NoFit(), X, y, splits)


def test_oof_rejects_raw_sklearn_estimator_returning_2d_proba():
    # the classic [:, 1] mistake, surfaced loudly instead of corrupting scores
    X, y = _linear_xy(10)
    splits = [(np.arange(0, 4), np.arange(4, 8))]

    class RawWrapper:  # predict_proba returns the raw 2-D sklearn shape
        def fit(self, X, y):
            from sklearn.linear_model import LogisticRegression

            self._m = LogisticRegression().fit(X, y)
            return self

        def predict_proba(self, X):
            return self._m.predict_proba(X)

    with pytest.raises(ValueError, match="SklearnRiskModel"):
        walk_forward_oof(lambda: RawWrapper(), X, y, splits)


def test_oof_rejects_non_1d_y_and_length_mismatch():
    X, y = _linear_xy(10)
    with pytest.raises(ValueError, match="1-D"):
        walk_forward_oof(lambda: ClimatologyBaseline(), X, y.reshape(-1, 1), [])
    with pytest.raises(ValueError, match="rows but"):
        walk_forward_oof(lambda: ClimatologyBaseline(), X, y[:-1], [])


def test_oof_accepts_boolean_masks_as_fold_indices():
    X, y = _linear_xy(10)
    train_mask = np.arange(10) < 4
    test_mask = (np.arange(10) >= 4) & (np.arange(10) < 8)
    oof = walk_forward_oof(
        lambda: ClimatologyBaseline(), X, y, [(train_mask, test_mask)]
    )
    assert np.isfinite(oof[4:8]).all()
    assert np.isnan(oof[:4]).all() and np.isnan(oof[8:]).all()


def test_oof_rejects_float_fold_indices():
    X, y = _linear_xy(10)
    with pytest.raises(ValueError, match="integer positions"):
        walk_forward_oof(
            lambda: ClimatologyBaseline(), X, y, [(np.array([0.0, 1.0]), np.array([4.0]))]
        )


# -- walk_forward_scores ----------------------------------------------------------


def test_walk_forward_scores_has_per_fold_rows_and_pooled_row():
    X, y = _linear_xy(120)
    splits = expanding_window_splits(120, min_train=40, horizon=20, step=20)
    scores = walk_forward_scores(lambda: ClimatologyBaseline(), X, y, splits)

    assert list(scores["fold"][:-1]) == [1, 2, 3, 4]
    assert scores["fold"].iloc[-1] == "pooled"
    assert len(scores) == 5
    # pooled row aggregates exactly the 80 out-of-fold rows
    assert int(scores["n"].iloc[-1]) == 80
    assert math_isnan(scores["n_train"].iloc[-1])
    for col in ("brier_score", "log_loss", "roc_auc", "calibration_ece"):
        assert col in scores.columns
        assert scores[col].notna().any()


def test_pooled_brier_matches_direct_oof_evaluation():
    from src.evaluation.probabilistic import brier_score

    X, y = _linear_xy(60)
    splits = expanding_window_splits(60, min_train=20, horizon=10, step=10)
    splits_list = list(splits)
    oof = walk_forward_oof(lambda: ClimatologyBaseline(), X, y, splits_list)
    scores = walk_forward_scores(lambda: ClimatologyBaseline(), X, y, splits_list)

    covered = np.isfinite(oof)
    expected = brier_score(y[covered], oof[covered])
    assert float(scores["brier_score"].iloc[-1]) == pytest.approx(expected)


def test_walk_forward_scores_respects_threshold_and_reference_rate():
    X, y = _linear_xy(40)
    splits = expanding_window_splits(40, min_train=20, horizon=10, step=10)
    scores = walk_forward_scores(
        lambda: ClimatologyBaseline(), X, y, splits,
        threshold=0.2, reference_rate=0.9,
    )
    assert (scores["threshold"] == 0.2).all()
    assert (scores["reference_rate"] == 0.9).all()


def test_walk_forward_scores_single_class_fold_reports_nan_auc():
    X = np.arange(12, dtype=float).reshape(-1, 1)
    y = np.ones(12, dtype=int)  # only outages in-sample
    splits = expanding_window_splits(12, min_train=4, horizon=4, step=4)
    scores = walk_forward_scores(lambda: ClimatologyBaseline(), X, y, splits)
    assert scores["roc_auc"].isna().all()  # undefined per fold and pooled


def math_isnan(value) -> bool:
    return value != value


# -- timestamp-aware leakage guard ---------------------------------------------


def _utc_times(n_rows: int):
    import pandas as pd

    return pd.date_range("2024-01-01", periods=n_rows, freq="h", tz="UTC")


def test_walk_forward_oof_accepts_matching_time_values():
    X = np.arange(10, dtype=float).reshape(-1, 1)
    y = (np.arange(10) % 2).astype(int)
    oof = walk_forward_oof(
        lambda: ClimatologyBaseline(), X, y, _splits(), time_values=_utc_times(10)
    )
    assert np.isfinite(oof).sum() > 0


def test_walk_forward_oof_rejects_out_of_order_timestamps():
    """Positional order alone must not hide a non-chronological frame."""
    X = np.arange(10, dtype=float).reshape(-1, 1)
    y = (np.arange(10) % 2).astype(int)
    times = _utc_times(10)[::-1]  # newest first: positionally "sorted" but wrong
    with pytest.raises(ValueError, match="leakage"):
        walk_forward_oof(
            lambda: ClimatologyBaseline(), X, y, _splits(), time_values=times
        )


def test_walk_forward_oof_rejects_naive_timestamps():
    X = np.arange(10, dtype=float).reshape(-1, 1)
    y = (np.arange(10) % 2).astype(int)
    naive = _utc_times(10).tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        walk_forward_oof(
            lambda: ClimatologyBaseline(), X, y, _splits(), time_values=naive
        )


def test_walk_forward_oof_rejects_mismatched_time_length():
    X = np.arange(10, dtype=float).reshape(-1, 1)
    y = (np.arange(10) % 2).astype(int)
    with pytest.raises(ValueError, match="time_values"):
        walk_forward_oof(
            lambda: ClimatologyBaseline(), X, y, _splits(), time_values=_utc_times(5)
        )


def test_walk_forward_scores_forwards_time_guard():
    X = np.arange(10, dtype=float).reshape(-1, 1)
    y = (np.arange(10) % 2).astype(int)
    with pytest.raises(ValueError, match="leakage"):
        walk_forward_scores(
            lambda: ClimatologyBaseline(), X, y, _splits(),
            time_values=_utc_times(10)[::-1],
        )
