"""Tests for src.models.calibration — probability calibration."""

from __future__ import annotations

import numpy as np
import pytest

from src.evaluation.probabilistic import brier_score
from src.models.calibration import CALIBRATION_METHODS, ProbabilityCalibrator


def _overconfident_sample(n_neg: int = 160, n_pos: int = 40, raw: float = 0.9):
    """Raw forecasts all at ``raw``; true rate n_pos / total."""
    raw_proba = np.full(n_neg + n_pos, raw)
    y = np.array([0] * n_neg + [1] * n_pos, dtype=int)
    return raw_proba, y


def test_methods_constant():
    assert set(CALIBRATION_METHODS) == {"sigmoid", "isotonic"}


# -- construction --------------------------------------------------------------


def test_unknown_method_rejected():
    with pytest.raises(ValueError, match="method must be one of"):
        ProbabilityCalibrator(method="temperature")


@pytest.mark.parametrize("bad_clip", [0.5, 1.0, -0.1, "0.1", True, None])
def test_bad_clip_rejected(bad_clip):
    with pytest.raises(ValueError, match="clip"):
        ProbabilityCalibrator(clip=bad_clip)


def test_valid_clips_accepted():
    assert ProbabilityCalibrator(clip=0.0).clip == 0.0
    assert ProbabilityCalibrator(clip=0.25).clip == 0.25


def test_repr_shows_fit_state():
    cal = ProbabilityCalibrator(method="isotonic")
    assert "unfitted" in repr(cal)
    raw, y = _overconfident_sample()
    cal.fit(raw, y)
    assert "fitted" in repr(cal)


# -- fitting -------------------------------------------------------------------


def test_sigmoid_downgrades_overconfident_forecast_to_base_rate():
    raw, y = _overconfident_sample()  # all 0.9 but only 20% events
    cal = ProbabilityCalibrator(method="sigmoid").fit(raw, y)
    calibrated = cal.transform(np.array([0.9]))
    # the honest probability for this sample is ~0.2, not 0.9
    assert 0.15 <= calibrated[0] <= 0.25


def test_sigmoid_is_monotone_in_raw_probability():
    raw = np.concatenate([np.full(100, 0.2), np.full(100, 0.8)])
    y = np.array([0] * 80 + [1] * 20 + [0] * 20 + [1] * 80)  # positive correlation
    cal = ProbabilityCalibrator(method="sigmoid").fit(raw, y)
    probs = cal.transform(np.array([0.1, 0.3, 0.5, 0.7, 0.9]))
    assert np.all(np.diff(probs) >= 0)


def test_isotonic_never_worsens_brier_on_the_calibration_sample():
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.05, 0.95, size=400)
    # labels drawn around the raw probability -> calibration has signal to learn
    y = (rng.uniform(size=400) < raw).astype(int)
    y[:200] = 0  # ensure both classes regardless of draws
    y[200:] = 1

    raw_brier = brier_score(y, raw)
    cal = ProbabilityCalibrator(method="isotonic").fit(raw, y)
    calibrated = cal.transform(raw)
    assert brier_score(y, calibrated) <= raw_brier + 1e-12


def test_isotonic_output_is_monotone_non_decreasing():
    rng = np.random.default_rng(1)
    raw = rng.uniform(0.0, 1.0, size=200)
    y = (rng.uniform(size=200) < raw).astype(int)
    y[::7] = 1 - y[::7]  # label noise: a realistic, imperfect mapping
    cal = ProbabilityCalibrator(method="isotonic").fit(raw, y)
    probes = np.linspace(0.0, 1.0, 50)
    probs = cal.transform(probes)
    assert np.all(np.diff(probs) >= -1e-12)
    assert ((probs >= 0) & (probs <= 1)).all()


def test_fit_returns_self_and_fit_transform_matches_fit_then_transform():
    raw, y = _overconfident_sample()
    cal = ProbabilityCalibrator()
    assert cal.fit(raw, y) is cal
    manual = ProbabilityCalibrator().fit(raw, y).transform(raw)
    np.testing.assert_allclose(
        ProbabilityCalibrator().fit_transform(raw, y), manual
    )


def test_transform_stays_in_unit_interval_for_extreme_inputs():
    raw, y = _overconfident_sample()
    cal = ProbabilityCalibrator(method="sigmoid").fit(raw, y)
    out = cal.transform(np.array([0.0, 1e-12, 0.5, 1.0 - 1e-12, 1.0]))
    assert np.isfinite(out).all()
    assert ((out >= 0) & (out <= 1)).all()


# -- validation ----------------------------------------------------------------


def test_transform_before_fit_raises():
    with pytest.raises(RuntimeError, match="not fitted"):
        ProbabilityCalibrator().transform(np.array([0.5]))


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_single_class_labels_rejected(method):
    raw = np.full(10, 0.5)
    with pytest.raises(ValueError, match="both classes"):
        ProbabilityCalibrator(method=method).fit(raw, np.zeros(10, dtype=int))


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_nan_raw_proba_rejected(method):
    cal = ProbabilityCalibrator(method=method)
    with pytest.raises(ValueError, match="NaN or infinite"):
        cal.fit(np.array([0.5, np.nan]), np.array([0, 1]))
    # a fitted calibrator still validates inputs at transform time
    cal.fit(np.array([0.2, 0.4, 0.6, 0.8]), np.array([0, 0, 1, 1]))
    with pytest.raises(ValueError, match="NaN or infinite"):
        cal.transform(np.array([0.5, np.nan]))


def test_out_of_range_raw_proba_rejected():
    cal = ProbabilityCalibrator()
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        cal.fit(np.array([0.5, 1.4]), np.array([0, 1]))
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        cal.fit(np.array([0.5, -0.01]), np.array([0, 1]))


def test_length_mismatch_and_2d_rejected():
    cal = ProbabilityCalibrator()
    with pytest.raises(ValueError, match="length mismatch"):
        cal.fit(np.array([0.5, 0.6, 0.7]), np.array([0, 1]))
    with pytest.raises(ValueError, match="1-D"):
        cal.fit(np.full((4, 1), 0.5), np.array([0, 1, 0, 1]))
    # transform validates shape only after the fitted check
    with pytest.raises(RuntimeError, match="not fitted"):
        cal.transform(np.full((4, 1), 0.5))
    cal.fit(np.array([0.2, 0.8]), np.array([0, 1]))
    with pytest.raises(ValueError, match="1-D"):
        cal.transform(np.full((4, 1), 0.5))


def test_non_binary_or_empty_labels_rejected():
    raw = np.full(5, 0.5)
    cal = ProbabilityCalibrator()
    with pytest.raises(ValueError, match="binary"):
        cal.fit(raw, np.array([0, 1, 2, 0, 1]))
    with pytest.raises(ValueError, match="empty"):
        cal.fit(np.array([]), np.array([]))
