"""Tests for src.experiments.zaf_interpretation (Day 27)."""

import numpy as np
import pandas as pd
import pytest

from src.datasets.zaf_daily import TIME_COL
from src.experiments import zaf_interpretation as interp
from src.experiments.zaf_interpretation import (
    feature_group, fit_interpretation_model, write_report,
)


@pytest.fixture(scope="module")
def markov_frame():
    rng = np.random.default_rng(3)
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
        "dow": dates.dayofweek,
        "month": dates.month,
        "hours_active_lag_1": np.r_[0, y[:-1]] * 12,
        "mlr_mw_mean_lag_1": np.r_[0, y[:-1]] * 1000.0,
        "johannesburg_temp_mean_lag_1": rng.normal(size=n),
    })


def test_feature_group_buckets():
    assert feature_group("hours_active_lag_1") == "persistence (label lags)"
    assert feature_group("mlr_mw_mean_lag_3") == "persistence (label lags)"
    assert feature_group("durban_gust_max_past_mean_7") == "weather (lag/rolling)"
    assert feature_group("dow") == "calendar (advance-known)"


def test_coefficients_exclude_label_columns(markov_frame):
    result = fit_interpretation_model(markov_frame)
    feats = set(result["coefficients"]["feature"])
    assert "any_load_shedding" not in feats
    assert "hours_active" not in feats
    assert "mlr_mw_mean" not in feats
    assert result["n_features"] == len(feats)


def test_persistence_dominates_in_pure_persistence_data(markov_frame):
    result = fit_interpretation_model(markov_frame)
    top = result["coefficients"].iloc[0]
    assert top["group"] == "persistence (label lags)"
    share = result["group_share"]
    assert share["persistence (label lags)"] > 0.5


def test_report_states_portfolio_safe_and_avoid_wording(markov_frame, tmp_path):
    result = fit_interpretation_model(markov_frame)
    out = tmp_path / "day27.md"
    write_report(result, out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "Portfolio-safe wording" in text
    assert "Wording to avoid" in text
    assert "day18" in text and "day21" in text


# --- real data (skipped when absent) -----------------------------------------

def _real_ready():
    from src.datasets.zaf_daily import LABELS_CSV
    from src.ingestion.weather import WEATHER_DIR
    return LABELS_CSV.exists() and (WEATHER_DIR / "ZAF_johannesburg.json").exists()


@pytest.mark.skipif(not _real_ready(), reason="ZAF dataset inputs absent")
def test_real_model_is_persistence_dominated():
    result = fit_interpretation_model()
    top3 = result["coefficients"].head(3)
    # the strongest single features must be persistence features, and the
    # label column itself must never appear as a feature
    assert "persistence (label lags)" in set(top3["group"])
    assert "any_load_shedding" not in set(result["coefficients"]["feature"])
