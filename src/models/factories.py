"""Shared model factories.

Training experiments and the serving dashboard must use the **same**
estimator configuration. Keeping it here means a dashboard probability can
always be traced to the protocol that produced the evaluation numbers,
instead of drifting into a second, silently different model.

The pipeline follows Day 18 exactly: standardise inside the pipeline so the
scaler is fitted per training window, then logistic regression. Calibration
remains a separate, held-out step (``src.models.calibration``).
"""

from __future__ import annotations

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.config import SEED
from src.models.base import SklearnRiskModel

__all__ = ["logistic_pipeline", "logistic_model"]


def logistic_pipeline() -> Pipeline:
    """Scaler + logistic regression; fit the scaler inside each training set."""
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, random_state=SEED)),
    ])


def logistic_model() -> SklearnRiskModel:
    """The project's baseline risk model wrapped in the 1-D probability contract."""
    return SklearnRiskModel(logistic_pipeline())
