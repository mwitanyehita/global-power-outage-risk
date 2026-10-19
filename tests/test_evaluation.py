"""Tests for src.evaluation.probabilistic — forecast quality metrics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.evaluation.probabilistic import (
    brier_score,
    brier_skill_score,
    calibration_curve,
    climatology_probability,
    evaluation_report,
    expected_calibration_error,
    log_loss,
    log_loss_skill_score,
    roc_auc,
)

Y = [0, 1, 0, 1]
P = [0.1, 0.9, 0.4, 0.6]


# -- proper scoring rules ----------------------------------------------------


def test_brier_score_reference_value():
    assert brier_score(Y, P) == pytest.approx(0.085)


def test_brier_score_perfect_and_worst():
    assert brier_score([0, 1], [0.0, 1.0]) == 0.0
    assert brier_score([0, 1], [1.0, 0.0]) == 1.0


def test_log_loss_reference_value():
    # -mean(0*ln .25 + 1*ln .75, 1*ln .75 + 0*ln .25) = -ln(0.75)
    assert log_loss([0, 1], [0.25, 0.75]) == pytest.approx(-math.log(0.75))


def test_log_loss_is_clipped_finite_at_extreme_probabilities():
    value = log_loss([1, 0], [0.0, 1.0])  # confidently wrong on both rows
    assert math.isfinite(value)
    assert value > 30  # ~ -ln(1e-15)


def test_log_loss_bad_eps_rejected():
    with pytest.raises(ValueError, match="eps"):
        log_loss([0, 1], [0.5, 0.5], eps=0.0)
    with pytest.raises(ValueError, match="eps"):
        log_loss([0, 1], [0.5, 0.5], eps=0.9)


def test_climatology_probability_is_sample_base_rate():
    assert climatology_probability(Y) == pytest.approx(0.5)
    assert climatology_probability([0, 0, 0, 1]) == pytest.approx(0.25)


# -- skill scores -------------------------------------------------------------


def test_brier_skill_score_vs_climatology_reference():
    # ref Brier (constant 0.5) = 0.25 -> 1 - 0.085/0.25 = 0.66
    assert brier_skill_score(Y, P, 0.5) == pytest.approx(0.66)


def test_brier_skill_score_accepts_per_row_reference():
    ref = [0.1, 0.5, 0.5, 0.9]
    expected = 1.0 - brier_score(Y, P) / brier_score(Y, ref)
    assert brier_skill_score(Y, P, ref) == pytest.approx(expected)


def test_brier_skill_score_zero_when_equal_to_reference():
    # a forecast identical to the reference has no skill by definition
    assert brier_skill_score(Y, [0.5] * 4, 0.5) == pytest.approx(0.0)


def test_skill_scores_nan_when_reference_is_perfect():
    # reference forecast nails every row -> skill undefined (not 1, not 0)
    assert math.isnan(brier_skill_score([0, 1], [0.5, 0.5], [0.0, 1.0]))


def test_log_loss_skill_score_nan_when_reference_is_perfect():
    # log loss is clipped at eps, so a perfect reference scores ~eps rather
    # than exactly 0: skill against it is undefined, not a huge negative.
    assert math.isnan(log_loss_skill_score([0, 1], [0.5, 0.5], [0.0, 1.0]))
    # An imperfect reference keeps a finite (and meaningful) skill score.
    assert math.isfinite(log_loss_skill_score([0, 1], [0.5, 0.5], [1.0, 1.0]))


def test_log_loss_skill_score_reference_value():
    ref = 0.5
    expected = 1.0 - log_loss(Y, P) / log_loss(Y, [ref] * 4)
    assert log_loss_skill_score(Y, P, ref) == pytest.approx(expected)


def test_skill_score_bad_reference_rejected():
    with pytest.raises(ValueError, match="reference"):
        brier_skill_score(Y, P, 1.5)
    with pytest.raises(ValueError, match="reference"):
        brier_skill_score(Y, P, [0.5, 0.5])  # wrong length


# -- calibration --------------------------------------------------------------


def test_calibration_curve_constant_forecast_single_quantile_bin():
    curve = calibration_curve([0, 1, 0, 1], [0.5] * 4, n_bins=10)
    assert len(curve) == 1
    assert int(curve["count"].iloc[0]) == 4
    assert curve["mean_predicted"].iloc[0] == pytest.approx(0.5)
    assert curve["observed_fraction"].iloc[0] == pytest.approx(0.5)


def test_calibration_curve_uniform_strategy_bins_by_width():
    curve = calibration_curve(Y, P, n_bins=4, strategy="uniform")
    assert list(curve.columns) == [
        "lo", "hi", "mean_predicted", "observed_fraction", "count",
    ]
    assert int(curve["count"].sum()) == 4
    assert (curve["hi"] > curve["lo"]).all()
    assert len(curve) == 4  # all four forecasts land in distinct width bins
    assert list(curve["lo"]) == pytest.approx([0.0, 0.25, 0.5, 0.75])


def _perfectly_calibrated_sample() -> tuple[list[int], list[float]]:
    """20 rows at p=0.25 with 25% events, 20 rows at p=0.75 with 75%."""
    y = [0] * 15 + [1] * 5 + [0] * 5 + [1] * 15
    p = [0.25] * 20 + [0.75] * 20
    return y, p


def test_calibration_curve_perfectly_calibrated_has_zero_gap():
    y, p = _perfectly_calibrated_sample()
    curve = calibration_curve(y, p, n_bins=4)
    gap = (curve["mean_predicted"] - curve["observed_fraction"]).abs().max()
    assert gap < 1e-9


def test_ece_worst_case_when_confidently_wrong():
    assert expected_calibration_error([0] * 10, [0.9] * 10) == pytest.approx(0.9)


def test_ece_zero_for_perfectly_calibrated_sample():
    y, p = _perfectly_calibrated_sample()
    assert expected_calibration_error(y, p) == pytest.approx(0.0, abs=1e-9)


def test_calibration_curve_bad_strategy_or_bins_rejected():
    with pytest.raises(ValueError, match="strategy"):
        calibration_curve(Y, P, strategy="equal_mass")
    with pytest.raises(ValueError, match="n_bins"):
        calibration_curve(Y, P, n_bins=0)
    with pytest.raises(ValueError, match="n_bins"):
        calibration_curve(Y, P, n_bins=2.5)
    with pytest.raises(ValueError, match="n_bins"):
        calibration_curve(Y, P, n_bins=True)


# -- discrimination ------------------------------------------------------------


def test_roc_auc_perfect_and_inverted():
    assert roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)
    assert roc_auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)


def test_roc_auc_single_class_rejected():
    with pytest.raises(ValueError, match="one class"):
        roc_auc([1, 1, 1], [0.2, 0.4, 0.6])


# -- validation ---------------------------------------------------------------


def test_non_binary_targets_rejected():
    with pytest.raises(ValueError, match="binary"):
        brier_score([0, 2], [0.5, 0.5])
    with pytest.raises(ValueError, match="binary"):
        climatology_probability([0.0, 0.7])


def test_nan_or_infinite_probabilities_rejected():
    with pytest.raises(ValueError, match="NaN or infinite"):
        brier_score([0, 1], [0.5, float("nan")])
    with pytest.raises(ValueError, match="NaN or infinite"):
        log_loss([0, 1], [0.5, float("inf")])


def test_probabilities_outside_unit_interval_rejected():
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        brier_score([0, 1], [0.5, 1.2])
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        brier_score([0, 1], [-0.01, 0.5])


def test_length_mismatch_and_ndim_rejected():
    with pytest.raises(ValueError, match="length mismatch"):
        brier_score([0, 1], [0.5])
    with pytest.raises(ValueError, match="1-D"):
        brier_score(np.zeros((2, 1)), np.full((2, 1), 0.5))


def test_empty_sample_rejected():
    with pytest.raises(ValueError, match="at least"):
        brier_score([], [])


# -- evaluation_report ---------------------------------------------------------


def test_evaluation_report_contains_probabilistic_and_threshold_metrics():
    report = evaluation_report(Y, P, threshold=0.5)
    expected_keys = {
        "n", "prevalence", "reference_rate", "brier_score", "log_loss",
        "brier_skill_score", "log_loss_skill_score", "calibration_ece",
        "roc_auc", "threshold", "accuracy", "precision", "recall", "f1",
    }
    assert set(report) == expected_keys
    assert report["n"] == 4
    assert report["brier_score"] == pytest.approx(0.085)
    assert report["roc_auc"] == pytest.approx(1.0)  # P ranks every positive above negatives
    assert report["accuracy"] == 1.0  # all P on the correct side of 0.5
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["f1"] == 1.0
    assert report["prevalence"] == pytest.approx(0.5)
    assert report["reference_rate"] == pytest.approx(0.5)  # defaults to prevalence


def test_evaluation_report_threshold_changes_only_thresholded_metrics():
    lo = evaluation_report(Y, P, threshold=0.0)  # call everything positive
    hi = evaluation_report(Y, P, threshold=1.0)  # call everything negative
    assert lo["brier_score"] == hi["brier_score"]  # probabilistic side untouched
    assert lo["accuracy"] == pytest.approx(0.5)    # 2 of 4 correct
    assert hi["accuracy"] == pytest.approx(0.5)
    assert lo["recall"] == 1.0 and lo["precision"] == pytest.approx(0.5)
    assert hi["recall"] == 0.0


def test_evaluation_report_single_class_yields_nan_auc():
    report = evaluation_report([1, 1], [0.4, 0.6])
    assert math.isnan(report["roc_auc"])
    assert report["n"] == 2
    assert report["prevalence"] == 1.0
    assert report["brier_score"] == pytest.approx(np.mean((np.array([0.4, 0.6]) - 1) ** 2))


def test_evaluation_report_skill_vs_explicit_reference_rate():
    report = evaluation_report(Y, P, reference_rate=0.9)
    assert report["reference_rate"] == 0.9
    assert report["brier_skill_score"] == pytest.approx(
        1.0 - brier_score(Y, P) / brier_score(Y, [0.9] * 4)
    )


def test_evaluation_report_rejects_bad_threshold_or_reference_rate():
    with pytest.raises(ValueError, match="threshold"):
        evaluation_report(Y, P, threshold=1.5)
    with pytest.raises(ValueError, match="threshold"):
        evaluation_report(Y, P, threshold=-0.1)
    with pytest.raises(ValueError, match="reference_rate"):
        evaluation_report(Y, P, reference_rate=1.2)


def test_evaluation_report_returns_plain_python_scalars():
    report = evaluation_report(Y, P)
    for key, value in report.items():
        if key == "n":
            assert isinstance(value, int)
        else:
            assert isinstance(value, float), f"{key} is {type(value)}"
