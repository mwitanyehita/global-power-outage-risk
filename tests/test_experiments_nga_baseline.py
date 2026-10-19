"""Tests for src.experiments.nga_baseline (Day 20)."""

import numpy as np
import pandas as pd
import pytest

from src.experiments import nga_baseline as nb


@pytest.fixture
def synthetic_daily():
    dates = pd.date_range("2023-01-01", "2025-12-31", freq="D")
    y = np.zeros(len(dates), dtype=bool)
    # 5 positives in train years, 3 in the test year
    y[np.where(dates.year == 2023)[0][10]] = True
    y[np.where(dates.year == 2023)[0][200]] = True
    y[np.where(dates.year == 2024)[0][5]] = True
    y[np.where(dates.year == 2024)[0][100]] = True
    y[np.where(dates.year == 2024)[0][250]] = True
    y[np.where(dates.year == 2025)[0][3]] = True
    y[np.where(dates.year == 2025)[0][150]] = True
    y[np.where(dates.year == 2025)[0][300]] = True
    return pd.DataFrame({"date": dates, "collapse_any": y,
                         "collapse_count": y.astype(int)})


def test_evaluate_split_and_constant_brier(synthetic_daily):
    r = nb.evaluate(synthetic_daily, train_end_year=2024)
    assert r["train_days"] == 366 + 365       # 2024 is a leap year
    assert r["test_days"] == 365
    assert r["train_positives"] == 5
    assert r["test_positives"] == 3
    p = r["train_rate"]
    q = r["test_rate"]
    assert p == pytest.approx(5 / 731)
    # constant-c Brier against actual rate q is q(1-c)^2 + (1-q)c^2
    assert r["const_brier"] == pytest.approx(q * (1 - p) ** 2 + (1 - q) * p ** 2)
    assert r["const_log_loss"] > 0
    assert 0 < r["const_pr_auc"] <= 1
    # oracle rate forecast can never be worse than the constant one here
    assert r["oracle_brier"] <= r["const_brier"] + 1e-12


def test_report_states_no_model_claimed(synthetic_daily, tmp_path):
    r = nb.evaluate(synthetic_daily, train_end_year=2024)
    out = tmp_path / "day20.md"
    nb.write_report(r, out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "no NGA model is claimed" in text
    assert "prevalence" in text
    assert "3 positive days" in text


def test_load_daily_reads_csv(synthetic_daily, tmp_path):
    csv = tmp_path / "nga_daily_collapse.csv"
    df = synthetic_daily.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df.to_csv(csv, index=False)
    back = nb.load_daily(csv)
    assert len(back) == len(df)
    assert pd.api.types.is_datetime64_any_dtype(back["date"])


# --- real Day 17 labels (skipped when absent) --------------------------------

@pytest.mark.skipif(not nb.DAILY_CSV.exists(),
                    reason="Day 17 NGA daily labels not generated")
def test_real_labels_have_two_positives_in_2025():
    daily = nb.load_daily()
    assert len(daily) == 1096  # 2023-01-01 .. 2025-12-31 inclusive (2024 leap)
    r = nb.evaluate(daily)
    assert r["train_positives"] == 12
    assert r["test_positives"] == 2
    assert r["test_rate"] == pytest.approx(2 / 365)
