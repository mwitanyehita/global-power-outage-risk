"""Tests for src.experiments.zaf_daily_model (Days 18-20)."""

import numpy as np
import pandas as pd
import pytest

from src.datasets.zaf_daily import TIME_COL
from src.experiments import zaf_daily_model as exp
from src.experiments.zaf_daily_model import (
    CAL_FRACTION, MIN_TRAIN, model_factories, prepare_frame, run_experiment,
)


@pytest.fixture(scope="module")
def markov_frame():
    """Synthetic dataset where yesterday's label strongly predicts today's.

    1200 SAST days (enough for several min_train=730 folds), Markov
    persistence p(stay) = 0.85, plus one junk feature.
    """
    rng = np.random.default_rng(7)
    n = 1200
    y = np.zeros(n, dtype=int)
    y[0] = 1
    for t in range(1, n):
        y[t] = y[t - 1] if rng.random() < 0.85 else int(rng.random() < 0.3)
    dates = pd.date_range("2023-01-01", periods=n, freq="D").tz_localize(
        "Africa/Johannesburg")
    return pd.DataFrame({
        TIME_COL: dates,
        "any_load_shedding": y.astype(bool),
        "hours_active": y * 12,
        "mlr_mw_mean": y * 1000.0,
        "hours_active_lag_1": np.r_[0, y[:-1]] * 12,
        "mlr_mw_mean_lag_1": np.r_[0, y[:-1]] * 1000.0,
        "dow": dates.dayofweek,
        "junk_feature": rng.normal(size=n),
    })


def test_prepare_frame_drops_nan_lags_and_splits_xy(markov_frame):
    frame = markov_frame.copy()
    frame.loc[0, "hours_active_lag_1"] = np.nan  # a leading-NaN row
    X, y, n_dropped, dates = prepare_frame(frame)
    assert n_dropped == 1
    assert len(X) == len(y) == len(dates) == 1199
    assert set(np.unique(y)) <= {0, 1}
    assert "any_load_shedding" not in X.columns
    assert "hours_active" not in X.columns  # same-day label columns excluded


def test_factory_contains_per_fold_scaler():
    model = model_factories()["logistic"]()
    steps = dict(model.estimator.named_steps)
    assert "scaler" in steps and "clf" in steps


def test_run_experiment_smoke_and_invariants(markov_frame):
    result = run_experiment(markov_frame)
    assert result["n_folds"] > 3
    preds = result["predictions"]
    oof = preds["oof_logistic"]
    # untested early rows are NaN; tested range finite; dropped-remainder
    # tail (final partial fold) stays NaN by design
    assert oof.isna().iloc[:MIN_TRAIN].all()
    first_tested = MIN_TRAIN + 1  # min_train + purge gap
    assert oof.notna().iloc[first_tested:first_tested + result["n_tested"]].all()
    assert oof.isna().iloc[first_tested + result["n_tested"]:].all()
    # pinned reference = first-fold training prevalence, NOT the full sample
    y = preds["y"].to_numpy()
    assert result["ref_rate"] == pytest.approx(y[:MIN_TRAIN].mean())
    # persistence is learnable -> logistic must beat climatology
    m = result["metrics"]
    assert m["logistic"]["brier"] < m["climatology"]["brier"]
    assert m["logistic"]["brier_skill"] > 0


def test_calibration_protocol_held_out_rows(markov_frame):
    result = run_experiment(markov_frame)
    cal = result["calibration"]
    preds = result["predictions"]
    assert cal["cal_rows"] + cal["eval_rows"] == result["n_tested"]
    eval_mask = preds["oof_calibrated"].notna()
    eval_part = preds["oof_calibrated"][eval_mask].to_numpy()
    assert len(eval_part) == cal["eval_rows"]
    assert ((eval_part >= 0) & (eval_part <= 1)).all()
    # calibrated probabilities must be a real remap, not a passthrough alias
    raw_eval = preds["oof_logistic"][eval_mask].to_numpy()
    assert not np.allclose(raw_eval, eval_part, atol=1e-6)


def test_per_year_table_covers_tested_years_only(markov_frame):
    result = run_experiment(markov_frame)
    per_year = result["per_year"]
    assert set(per_year.columns) == {"year", "n_days", "positives",
                                     "brier_logistic", "brier_climatology",
                                     "pr_auc_logistic"}
    assert per_year["n_days"].sum() == result["n_tested"]
    assert per_year["year"].min() >= 2024  # first 730 days never tested


def test_single_class_window_reports_nan_not_crash():
    """A zero-positive window must yield NaN ranking metrics, not an error."""
    y = np.zeros(30, dtype=int)
    p = np.full(30, 0.1)
    block = exp.metrics_block(y, p, ref_rate=0.5)
    assert np.isnan(block["roc_auc"]) and np.isnan(block["pr_auc"])
    assert np.isfinite(block["brier"]) and np.isfinite(block["ece"])


# --- real dataset (skipped when absent) ---------------------------------------

def _real_ready():
    from src.datasets.zaf_daily import LABELS_CSV
    from src.ingestion.weather import WEATHER_DIR
    return LABELS_CSV.exists() and (WEATHER_DIR / "ZAF_johannesburg.json").exists()


@pytest.mark.skipif(not _real_ready(), reason="ZAF dataset inputs absent")
def test_real_run_beats_pinned_baseline():
    result = run_experiment()
    m = result["metrics"]
    assert result["n_tested"] > 500
    assert m["logistic"]["brier_skill"] > 0, \
        "day-ahead model must beat pinned climatology on Brier"
    # honest regime check: mean prediction within 15 pts of observed rate
    obs = m["logistic"]["positives"] / m["logistic"]["n"]
    assert abs(m["logistic"]["mean_pred"] - obs) < 0.15
