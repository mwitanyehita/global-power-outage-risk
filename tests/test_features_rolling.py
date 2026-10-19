"""Tests for src.features.rolling — strictly-past lag and rolling features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.rolling import (
    PAST_ROLLING_STATS,
    add_lags,
    add_past_rolling,
    assert_chronological,
    assert_regular_grid,
    sort_by_time,
)


def _frame(values=None, groups=None, n=None) -> pd.DataFrame:
    """Chronological tz-aware frame; default 6 rows in two groups."""
    if values is None:
        values = [10, 20, 30, 100, 200, 300]
    times = pd.date_range("2024-01-01", periods=len(values), freq="1h", tz="UTC")
    data = {"ts": times, "v": values}
    if groups is not None:
        data["g"] = groups
    return pd.DataFrame(data)


def _two_group_8row() -> pd.DataFrame:
    """8 hourly rows, groups a (rows 0-3) and b (rows 4-7)."""
    return _frame(
        values=[1, 2, 3, 4, 10, 20, 30, 40],
        groups=["a"] * 4 + ["b"] * 4,
    )


# -- guards ------------------------------------------------------------------


def test_chronological_guard_rejects_unsorted_frame():
    frame = _frame().sort_values("v", ascending=False)
    with pytest.raises(ValueError, match="not sorted"):
        assert_chronological(frame, "ts")


def test_chronological_guard_rejects_unsorted_within_group():
    # group a's timestamps run backwards while group b is in order
    base = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {
            "ts": list(base[::-1]) + list(base),
            "v": [1, 2, 3, 10, 20, 30],
            "g": ["a"] * 3 + ["b"] * 3,
        }
    )
    with pytest.raises(ValueError, match="not sorted"):
        assert_chronological(frame, "ts", "g")


def test_chronological_guard_rejects_nat():
    frame = _frame()
    frame.loc[2, "ts"] = pd.NaT
    with pytest.raises(ValueError, match="NaT"):
        assert_chronological(frame, "ts")


def test_naive_time_column_rejected():
    frame = _frame()
    frame["ts"] = frame["ts"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        add_lags(frame, "v", [1], time_col="ts")


def test_missing_columns_rejected():
    with pytest.raises(ValueError, match="missing columns"):
        add_lags(_frame(), "nope", [1], time_col="ts")


def test_null_group_values_rejected():
    frame = _frame(groups=["a", None, "a", "b", "b", "b"])
    with pytest.raises(ValueError, match="nulls"):
        add_lags(frame, "v", [1], time_col="ts", group_cols="g")


def test_column_cannot_be_its_own_group():
    with pytest.raises(ValueError, match="both a feature column"):
        add_lags(_frame(groups=["a"] * 6), "g", [1], time_col="ts", group_cols="g")


def test_sort_by_time_orders_by_group_then_time():
    frame = _frame(groups=["b", "a", "b", "a", "b", "a"])
    frame["v"] = [1, 2, 3, 4, 5, 6]
    ordered = sort_by_time(frame, "ts", "g")
    assert list(ordered["g"]) == ["a"] * 3 + ["b"] * 3
    for _, block in ordered.groupby("g"):
        assert block["ts"].is_monotonic_increasing


# -- add_lags ----------------------------------------------------------------


def test_grouped_lag1_does_not_cross_group_boundary():
    frame = _frame(groups=["a"] * 3 + ["b"] * 3)
    out = add_lags(frame, "v", [1], time_col="ts", group_cols="g")
    assert out["v_lag_1"].isna().tolist() == [True, False, False, True, False, False]
    assert list(out["v_lag_1"].dropna()) == [10, 20, 100, 200]


def test_grouped_lag2():
    frame = _frame(groups=["a"] * 3 + ["b"] * 3)
    out = add_lags(frame, "v", [2], time_col="ts", group_cols="g")
    expect = [np.nan, np.nan, 10, np.nan, np.nan, 100]
    pd.testing.assert_series_equal(
        out["v_lag_2"], pd.Series(expect, name="v_lag_2"), check_names=False
    )


def test_ungrouped_lags_multiple_columns_and_lags():
    out = add_lags(_frame(), ["v"], [1, 3], time_col="ts")
    assert list(out.columns) == ["ts", "v", "v_lag_1", "v_lag_3"]
    assert out["v_lag_1"].iloc[0] != out["v_lag_1"].iloc[0]  # NaN
    assert out["v_lag_3"].iloc[3] == 10   # v[0]
    assert out["v_lag_3"].iloc[5] == 30   # v[2]


def test_add_lags_returns_a_copy_original_untouched():
    frame = _frame(groups=["a"] * 6)
    out = add_lags(frame, "v", [1], time_col="ts", group_cols="g")
    assert out is not frame
    assert "v_lag_1" not in frame.columns


def test_lag_zero_rejected():
    with pytest.raises(ValueError, match="lags"):
        add_lags(_frame(), "v", [0], time_col="ts")


def test_duplicate_lags_rejected():
    with pytest.raises(ValueError, match="duplicates"):
        add_lags(_frame(), "v", [1, 1], time_col="ts")


def test_non_integer_lags_rejected():
    with pytest.raises(ValueError, match="integers"):
        add_lags(_frame(), "v", ["1"], time_col="ts")


# -- add_past_rolling --------------------------------------------------------


def test_past_mean_window2_is_strictly_past():
    # ungrouped: window 2 over the *shifted* series -> row i averages rows i-2..i-1
    out = add_past_rolling(_frame(), "v", [2], time_col="ts", stat="mean")
    expect = [np.nan, 10, 15, 25, 65, 150]
    pd.testing.assert_series_equal(
        out["v_past_mean_2"], pd.Series(expect, name="v_past_mean_2"), check_names=False
    )


def test_grouped_past_mean_window2():
    frame = _frame(groups=["a"] * 3 + ["b"] * 3)
    out = add_past_rolling(frame, "v", [2], time_col="ts", stat="mean",
                           group_cols="g")
    # row 0 of each group always NaN (no past)
    assert out["v_past_mean_2"].isna().tolist() == [True, False, False, True, False, False]
    assert out["v_past_mean_2"].iloc[2] == 15.0
    assert out["v_past_mean_2"].iloc[5] == 150.0


def test_min_periods_window_requires_full_past_history():
    out = add_past_rolling(_two_group_8row(), "v", [3], time_col="ts",
                           stat="sum", group_cols="g", min_periods=3)
    expect = [np.nan, np.nan, np.nan, 6, np.nan, np.nan, np.nan, 60]
    pd.testing.assert_series_equal(
        out["v_past_sum_3"], pd.Series(expect, name="v_past_sum_3"), check_names=False
    )


def test_ungrouped_past_max_window2():
    out = add_past_rolling(_frame(), "v", [2], time_col="ts", stat="max")
    expect = [np.nan, 10, 20, 30, 100, 200]
    pd.testing.assert_series_equal(
        out["v_past_max_2"], pd.Series(expect, name="v_past_max_2"), check_names=False
    )


def test_never_includes_current_row():
    """The current row's own value must never enter its window (regression
    guard: shift(1) happens before rolling)."""
    frame = _frame(values=[5.0, 1.0, 1.0, 1.0])
    out = add_past_rolling(frame, "v", [3], time_col="ts", stat="max")
    assert out["v_past_max_3"].iloc[0] != out["v_past_max_3"].iloc[0]  # NaN
    assert out["v_past_max_3"].iloc[1] == 5.0
    # row 1..3 window max is 5 -> never a value only present at the current row
    frame2 = _frame(values=[0.0, 0.0, 0.0, 99.0])
    out2 = add_past_rolling(frame2, "v", [3], time_col="ts", stat="max")
    assert out2["v_past_max_3"].iloc[3] == 0.0  # 99 excluded: it is "now"


def test_invalid_stat_rejected():
    with pytest.raises(ValueError, match="stat must be one of"):
        add_past_rolling(_frame(), "v", [2], time_col="ts", stat="variance")
    assert "mean" in PAST_ROLLING_STATS


def test_min_periods_exceeding_window_rejected():
    with pytest.raises(ValueError, match="exceeds the largest window"):
        add_past_rolling(_frame(), "v", [2], time_col="ts", min_periods=3)


def test_bad_min_periods_rejected():
    with pytest.raises(ValueError, match="min_periods"):
        add_past_rolling(_frame(), "v", [2], time_col="ts", min_periods=0)
    with pytest.raises(ValueError, match="min_periods"):
        add_past_rolling(_frame(), "v", [2], time_col="ts", min_periods=True)


def test_window_zero_rejected():
    with pytest.raises(ValueError, match="windows"):
        add_past_rolling(_frame(), "v", [0], time_col="ts")


# -- assert_regular_grid -----------------------------------------------------


def test_regular_grid_accepts_hourly_rows():
    assert_regular_grid(_frame(), "ts", freq="1h")  # no raise


def test_regular_grid_rejects_gaps():
    frame = _frame()
    frame.loc[3, "ts"] = frame.loc[3, "ts"] + pd.Timedelta("30min")
    with pytest.raises(ValueError, match="breaks 1h"):
        assert_regular_grid(frame, "ts", freq="1h")


def test_regular_grid_rejects_duplicate_timestamps():
    frame = _frame()
    frame.loc[3, "ts"] = frame.loc[2, "ts"]
    with pytest.raises(ValueError):
        assert_regular_grid(frame, "ts", freq="1h")


def test_regular_grid_checks_each_group_separately():
    # two groups each on their own perfect hourly grid, offset by 30 min:
    # within-group checks pass even though the combined sequence interleaves
    times = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {"ts": list(times) + list(times), "g": ["a"] * 4 + ["b"] * 4, "v": range(8)}
    )
    assert_regular_grid(frame, "ts", freq="1h", group_cols="g")
