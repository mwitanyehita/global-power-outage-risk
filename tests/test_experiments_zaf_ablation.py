"""Tests for src.experiments.zaf_ablation (Day 21)."""

import numpy as np
import pandas as pd
import pytest

from src.datasets.zaf_daily import TIME_COL
from src.experiments.zaf_ablation import (
    VARIANTS, run_ablation, variant_columns,
)

FEATURES = ["dow", "month", "hours_active_lag_1", "mlr_mw_mean_lag_1",
            "johannesburg_temp_mean_lag_1", "durban_gust_max_past_mean_7",
            "cape-town_precip_sum_lag_2"]


def test_variant_columns_partition():
    cols = variant_columns(FEATURES, "calendar_only")
    assert cols == ["dow", "month"]
    lags = variant_columns(FEATURES, "label_lags_only")
    assert lags == ["hours_active_lag_1", "mlr_mw_mean_lag_1"]
    weather = variant_columns(FEATURES, "weather_only")
    assert set(weather) == {"johannesburg_temp_mean_lag_1",
                            "durban_gust_max_past_mean_7",
                            "cape-town_precip_sum_lag_2"}
    assert variant_columns(FEATURES, "all") == FEATURES
    with pytest.raises(ValueError):
        variant_columns(FEATURES, "banana")


@pytest.fixture(scope="module")
def markov_weather_frame():
    """Persistence is real, weather is pure noise — the ablation truth."""
    rng = np.random.default_rng(11)
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
        # pure noise "weather"
        "johannesburg_temp_mean_lag_1": rng.normal(size=n),
        "durban_gust_max_past_mean_7": rng.normal(size=n),
        "cape-town_precip_sum_lag_2": rng.normal(size=n),
    })


def test_variants_share_identical_test_rows(markov_weather_frame):
    result = run_ablation(markov_weather_frame)
    # shared coverage is enforced inside run_ablation (raises on divergence);
    # here we check the reported count is consistent and sane
    assert result["n_tested"] > 300
    for kind in VARIANTS:
        assert result["rows"][kind]["n"] == result["n_tested"]


def test_persistence_beats_calendar_floor(markov_weather_frame):
    result = run_ablation(markov_weather_frame)
    rows = result["rows"]
    assert rows["label_lags_only"]["brier"] < rows["calendar_only"]["brier"]
    assert result["marginal"]["label_lags_gain"] > 0.01


def test_noise_weather_adds_nothing_on_top(markov_weather_frame):
    result = run_ablation(markov_weather_frame)
    m = result["marginal"]
    # noise features cannot reduce Brier beyond a rounding-level wobble
    assert m["weather_on_top_of_label_lags"] < 0.01


# --- real dataset (skipped when absent) ---------------------------------------

def _real_ready():
    from src.datasets.zaf_daily import LABELS_CSV
    from src.ingestion.weather import WEATHER_DIR
    return LABELS_CSV.exists() and (WEATHER_DIR / "ZAF_johannesburg.json").exists()


@pytest.mark.skipif(not _real_ready(), reason="ZAF dataset inputs absent")
def test_real_ablation_answers_the_audit_question():
    result = run_ablation()
    rows = result["rows"]
    # the model must beat the no-information floor on real data
    assert rows["all"]["brier"] < rows["calendar_only"]["brier"]
    # weather's marginal contribution is REPORTED, whatever its sign
    assert np.isfinite(result["marginal"]["weather_on_top_of_label_lags"])
