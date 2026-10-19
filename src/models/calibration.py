"""Probability calibration for outage-risk forecasts.

Uncalibrated classifiers are routinely overconfident — and the project must
not publish unsupported probabilities. A calibrator learns a monotone map
from raw scores to reliable probabilities:

    "sigmoid"   Platt scaling: logistic regression on the log-odds of the
                raw probability (smooth, safe on small samples).
    "isotonic"  isotonic regression: non-parametric, needs more data.

Critical rule: fit the calibrator on probabilities from rows the base model
never trained on — out-of-fold predictions from
``src.models.cv.walk_forward_oof`` are the right input. Calibrating on
training predictions only measures how well the model memorized itself.
"""

from __future__ import annotations

import numbers

import numpy as np

__all__ = ["ProbabilityCalibrator", "CALIBRATION_METHODS"]

CALIBRATION_METHODS: tuple[str, ...] = ("sigmoid", "isotonic")


def _check_proba(values, *, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got ndim={arr.ndim}")
    if len(arr) == 0:
        raise ValueError(f"{name} is empty")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} contains NaN or infinite values")
    if ((arr < 0) | (arr > 1)).any():
        raise ValueError(f"{name} must lie in [0, 1]")
    return arr


def _check_binary_labels(y) -> np.ndarray:
    arr = np.asarray(y)
    if arr.ndim != 1:
        raise ValueError(f"y must be 1-D, got ndim={arr.ndim}")
    if not np.isin(arr, (0, 1)).all():
        raise ValueError("y must be binary (values in {0, 1})")
    if len(np.unique(arr)) < 2:
        raise ValueError("calibration needs both classes present in y")
    return arr.astype(float)


class ProbabilityCalibrator:
    """Map raw P(outage) to calibrated P(outage).

    Usage::

        cal = ProbabilityCalibrator(method="isotonic")
        cal.fit(oof_raw_proba, y_valid)      # held-out rows only
        p_calibrated = cal.transform(raw_proba)
    """

    def __init__(self, method: str = "sigmoid", *, clip: float = 1e-6):
        if method not in CALIBRATION_METHODS:
            raise ValueError(
                f"method must be one of {CALIBRATION_METHODS}, got {method!r}"
            )
        if not isinstance(clip, numbers.Real) or isinstance(clip, bool):
            raise ValueError(f"clip must be a number in [0, 0.5), got {clip!r}")
        if not 0.0 <= float(clip) < 0.5:
            raise ValueError(f"clip must be in [0, 0.5), got {clip}")
        self.method = method
        self.clip = float(clip)
        self._fitted = False

    # -- fitting -----------------------------------------------------------
    def fit(self, raw_proba, y) -> "ProbabilityCalibrator":
        """Learn the raw→calibrated map from held-out predictions."""
        raw = _check_proba(raw_proba, name="raw_proba")
        labels = _check_binary_labels(y)
        if len(raw) != len(labels):
            raise ValueError(
                f"length mismatch: raw_proba has {len(raw)}, y has {len(labels)}"
            )

        if self.method == "sigmoid":
            from sklearn.linear_model import LogisticRegression

            logits = self._logit(raw).reshape(-1, 1)
            # Large C ≈ unregularized Platt scaling; deterministic solver.
            model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
            model.fit(logits, labels)
            self._model = model
        else:  # isotonic
            from sklearn.isotonic import IsotonicRegression

            model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            model.fit(raw, labels)
            self._model = model

        self._fitted = True
        return self

    def transform(self, raw_proba) -> np.ndarray:
        """Apply the learned map; output stays in [0, 1]."""
        if not self._fitted:
            raise RuntimeError(
                f"{type(self).__name__} is not fitted; call fit() first"
            )
        raw = _check_proba(raw_proba, name="raw_proba")
        if self.method == "sigmoid":
            logits = self._logit(raw).reshape(-1, 1)
            calibrated = self._model.predict_proba(logits)[:, 1]
        else:
            calibrated = self._model.predict(raw)
        return np.clip(np.asarray(calibrated, dtype=float), 0.0, 1.0)

    def fit_transform(self, raw_proba, y) -> np.ndarray:
        """``fit`` + ``transform`` on the same held-out sample (convenience;
        prefer separate calibration/evaluation samples when you have them)."""
        return self.fit(raw_proba, y).transform(raw_proba)

    # -- internals ---------------------------------------------------------
    def _logit(self, raw: np.ndarray) -> np.ndarray:
        p = np.clip(raw, self.clip, 1.0 - self.clip)
        return np.log(p) - np.log1p(-p)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "fitted" if self._fitted else "unfitted"
        return f"ProbabilityCalibrator(method={self.method!r}, {state})"
