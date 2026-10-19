"""Model interface for outage-risk prediction.

A small contract every model satisfies:

    ``fit(X, y) -> self``                 train on binary 0/1 labels
    ``predict_proba(X) -> 1-D array``     P(outage) per row, in [0, 1]
    ``predict(X, threshold) -> 0/1 array`` derived from ``predict_proba``

The 1-D probability contract keeps evaluation code trivial and makes the
classic sklearn mistake (forgetting ``[:, 1]``) impossible. Implementations
validate inputs and fail loudly — an unfitted model, a wrong feature count
or non-binary labels must never silently produce numbers.
"""

from __future__ import annotations

import numbers
from abc import ABC, abstractmethod

import numpy as np

from src.config import SEED

__all__ = ["RiskModel", "SklearnRiskModel", "ClimatologyBaseline"]


def _as_2d(X, *, name: str = "X") -> np.ndarray:
    """Rows-of-features array from DataFrame/ndarray/list."""
    arr = X if isinstance(X, np.ndarray) else np.asarray(X)
    if arr.ndim != 2:
        raise ValueError(f"{name} must be 2-D (rows x features), got ndim={arr.ndim}")
    return arr


def _check_binary_y(y) -> np.ndarray:
    arr = np.asarray(y)
    if arr.ndim != 1:
        raise ValueError(f"y must be 1-D, got ndim={arr.ndim}")
    if len(arr) == 0:
        raise ValueError("y is empty")
    if not np.isin(arr, (0, 1)).all():
        raise ValueError("y must be binary (values in {0, 1})")
    return arr


def _check_same_length(X, y) -> None:
    if len(X) != len(y):
        raise ValueError(f"X has {len(X)} rows but y has {len(y)}")


class RiskModel(ABC):
    """Binary risk model: probabilities in, probabilities out."""

    @abstractmethod
    def fit(self, X, y) -> "RiskModel":
        """Train on binary labels; returns self."""

    @abstractmethod
    def predict_proba(self, X) -> np.ndarray:
        """P(y=1) per row: 1-D float array in [0, 1], one per row of X."""

    def predict(self, X, threshold: float = 0.5) -> np.ndarray:
        """Hard 0/1 calls by thresholding ``predict_proba`` (for operations
        discussions only — probabilistic metrics stay primary)."""
        if not isinstance(threshold, numbers.Real) or isinstance(threshold, bool):
            raise ValueError(f"threshold must be a number in [0, 1], got {threshold!r}")
        if not 0.0 <= float(threshold) <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {threshold}")
        proba = np.asarray(self.predict_proba(X), dtype=float)
        if proba.ndim != 1 or len(proba) != len(X):
            raise ValueError(
                "predict_proba must return one probability per row; "
                f"got shape {proba.shape} for {len(X)} rows"
            )
        return (proba >= float(threshold)).astype(int)


class SklearnRiskModel(RiskModel):
    """Adapts any sklearn-style classifier to the :class:`RiskModel` contract.

    The positive class is located via ``estimator.classes_`` after fitting,
    so column order can never be assumed. If the training labels never
    contained a 1, every probability is 0 (the model never saw an outage).
    """

    def __init__(self, estimator=None):
        if estimator is None:
            from sklearn.linear_model import LogisticRegression

            estimator = LogisticRegression(max_iter=1000, random_state=SEED)
        if not callable(getattr(estimator, "predict_proba", None)):
            raise TypeError(
                f"{type(estimator).__name__} does not implement predict_proba; "
                "wrap a classifier, not a regressor"
            )
        self.estimator = estimator
        self._fitted = False
        self._positive_index: int | None = None
        self.classes_: np.ndarray | None = None

    def fit(self, X, y) -> "SklearnRiskModel":
        y_arr = _check_binary_y(y)
        _check_same_length(X, y_arr)
        self.estimator.fit(X, y_arr)
        classes = np.asarray(self.estimator.classes_)
        self.classes_ = classes
        matches = np.flatnonzero(classes == 1)
        # classes_ [0, 1] -> col 1; [1] -> col 0; [0] -> no column (all zero)
        self._positive_index = int(matches[0]) if matches.size else None
        self._fitted = True
        return self

    def predict_proba(self, X) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError(
                f"{type(self).__name__} is not fitted; call fit() first"
            )
        arr = _as_2d(X)
        if self._positive_index is None:
            return np.zeros(arr.shape[0], dtype=float)
        proba = np.asarray(self.estimator.predict_proba(X), dtype=float)
        if proba.ndim != 2 or proba.shape[1] != len(self.classes_):
            raise ValueError(
                f"estimator returned predict_proba shape {proba.shape} "
                f"for {len(self.classes_)} classes"
            )
        return np.asarray(proba[:, self._positive_index], dtype=float)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SklearnRiskModel({type(self.estimator).__name__})"


class ClimatologyBaseline(RiskModel):
    """Constant forecast equal to the training event rate.

    The no-skill reference every skill score is measured against: if a
    trained model cannot beat this on Brier score, it has learned nothing.
    Works with a single class (rate 0 or 1), unlike most classifiers.
    """

    def __init__(self) -> None:
        self.base_rate_: float | None = None
        self.n_features_in_: int | None = None
        self._fitted = False

    def fit(self, X, y) -> "ClimatologyBaseline":
        y_arr = _check_binary_y(y)
        _check_same_length(X, y_arr)
        arr = _as_2d(X)
        self.base_rate_ = float(y_arr.astype(float).mean())
        self.n_features_in_ = int(arr.shape[1])
        self._fitted = True
        return self

    def predict_proba(self, X) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError(
                f"{type(self).__name__} is not fitted; call fit() first"
            )
        arr = _as_2d(X)
        if arr.shape[1] != self.n_features_in_:
            raise ValueError(
                f"X has {arr.shape[1]} features but the model was fitted "
                f"with {self.n_features_in_}"
            )
        return np.full(arr.shape[0], self.base_rate_, dtype=float)
