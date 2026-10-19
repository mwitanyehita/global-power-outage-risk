"""Tests for src.models.base — the RiskModel contract and its implementations."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LinearRegression

from src.models.base import ClimatologyBaseline, RiskModel, SklearnRiskModel

X = np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]])
Y = np.array([0, 0, 0, 1, 1, 1])


def _fit_default() -> SklearnRiskModel:
    return SklearnRiskModel().fit(X, Y)


# -- contract -----------------------------------------------------------------


def test_risk_model_is_abstract():
    with pytest.raises(TypeError):
        RiskModel()  # type: ignore[abstract]


def test_default_predict_proba_is_1d_and_in_unit_interval():
    model = _fit_default()
    proba = model.predict_proba(X)
    assert isinstance(proba, np.ndarray)
    assert proba.ndim == 1
    assert len(proba) == len(X)
    assert ((proba >= 0) & (proba <= 1)).all()


def test_predict_proba_raises_before_fit():
    for model in (SklearnRiskModel(), ClimatologyBaseline()):
        with pytest.raises(RuntimeError, match="not fitted"):
            model.predict_proba(X)


def test_fit_returns_self_enabling_method_chaining():
    model = ClimatologyBaseline()
    assert model.fit(X, Y) is model


def test_predict_thresholds_probabilities():
    base = ClimatologyBaseline().fit(X, np.array([0, 0, 0, 0, 1, 1]))  # rate 1/3
    assert set(base.predict(X, threshold=0.5)) == {0}
    assert set(base.predict(X, threshold=0.1)) == {1}


@pytest.mark.parametrize("bad", [1.5, -0.1, "0.5", True, None])
def test_predict_rejects_bad_threshold(bad):
    model = _fit_default()
    with pytest.raises(ValueError, match="threshold"):
        model.predict(X, bad)


def test_predict_rejects_2d_probasa_from_misbehaving_model():
    class Bad(RiskModel):
        def fit(self, X, y):
            return self

        def predict_proba(self, X):
            return np.column_stack([np.zeros(len(X)), np.ones(len(X))])

    with pytest.raises(ValueError, match="one probability per row"):
        Bad().predict(X)


# -- SklearnRiskModel ----------------------------------------------------------


def test_sklearn_adapter_locates_positive_class_column():
    model = _fit_default()
    assert list(model.classes_) == [0, 1]
    # column 1 of the wrapped estimator's predict_proba, not a hard-coded index
    raw = model.estimator.predict_proba(X)
    np.testing.assert_allclose(model.predict_proba(X), raw[:, 1])


def test_sklearn_adapter_single_class_zero_returns_all_zero():
    # y has no 1s -> the model never saw an outage -> P(outage) = 0
    dummy = DummyClassifier(strategy="most_frequent")
    model = SklearnRiskModel(dummy).fit(X, np.zeros(len(X), dtype=int))
    assert list(model.classes_) == [0]
    np.testing.assert_array_equal(model.predict_proba(X), np.zeros(len(X)))


def test_sklearn_adapter_single_class_one_uses_that_column():
    dummy = DummyClassifier(strategy="most_frequent")
    model = SklearnRiskModel(dummy).fit(X, np.ones(len(X), dtype=int))
    assert list(model.classes_) == [1]
    np.testing.assert_allclose(model.predict_proba(X), np.ones(len(X)))


def test_sklearn_adapter_accepts_dataframe_features():
    model = SklearnRiskModel().fit(pd.DataFrame(X, columns=["f"]), Y)
    proba = model.predict_proba(pd.DataFrame(X, columns=["f"]))
    assert proba.ndim == 1
    assert len(proba) == len(X)


def test_sklearn_adapter_rejects_regressor_without_predict_proba():
    with pytest.raises(TypeError, match="predict_proba"):
        SklearnRiskModel(LinearRegression())


def test_sklearn_adapter_rejects_non_binary_labels():
    model = SklearnRiskModel()
    with pytest.raises(ValueError, match="binary"):
        model.fit(X, np.array([0, 1, 2, 2, 3, 3]))


def test_sklearn_adapter_rejects_2d_y_and_length_mismatch():
    with pytest.raises(ValueError, match="1-D"):
        SklearnRiskModel().fit(X, Y.reshape(-1, 1))
    with pytest.raises(ValueError, match="rows but"):
        SklearnRiskModel().fit(X, Y[:-1])


def test_sklearn_adapter_rejects_1d_features_at_predict_time():
    model = _fit_default()
    with pytest.raises(ValueError, match="2-D"):
        model.predict_proba(np.array([1.0, 2.0]))


# -- ClimatologyBaseline --------------------------------------------------------


def test_climatology_baseline_returns_training_base_rate():
    y = np.array([0, 0, 0, 1, 1, 1])
    model = ClimatologyBaseline().fit(X, y)
    assert model.base_rate_ == pytest.approx(0.5)
    proba = model.predict_proba(X)
    np.testing.assert_allclose(proba, np.full(len(X), 0.5))


def test_climatology_baseline_works_with_single_class():
    zeros = ClimatologyBaseline().fit(X, np.zeros(len(X), dtype=int))
    assert zeros.base_rate_ == 0.0
    np.testing.assert_array_equal(zeros.predict_proba(X), np.zeros(len(X)))

    ones = ClimatologyBaseline().fit(X, np.ones(len(X), dtype=int))
    assert ones.base_rate_ == 1.0
    np.testing.assert_array_equal(ones.predict_proba(X), np.ones(len(X)))


def test_climatology_baseline_rejects_wrong_feature_count():
    model = ClimatologyBaseline().fit(X, Y)
    with pytest.raises(ValueError, match="features"):
        model.predict_proba(np.zeros((3, 2)))


def test_climatology_baseline_records_feature_count():
    model = ClimatologyBaseline().fit(X, Y)
    assert model.n_features_in_ == 1
