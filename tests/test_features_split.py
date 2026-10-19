"""Tests for src.features.time_split — leakage-safe temporal splitting."""

from __future__ import annotations

import pandas as pd
import pytest

from src.features.time_split import assert_strictly_past, temporal_train_test_split


def _frame(n: int = 12) -> pd.DataFrame:
    """n hourly rows starting 2024-01-01 00:00 UTC, tz-aware."""
    times = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"ts": times, "value": range(n)})


def test_split_by_test_fraction_is_9_3():
    train, test = temporal_train_test_split(_frame(), "ts", test_fraction=0.25)
    assert len(train) == 9
    assert len(test) == 3


def test_split_by_test_start_uses_exact_cutoff():
    train, test = temporal_train_test_split(
        _frame(), "ts", test_start="2024-01-01T09:00:00+00:00"
    )
    assert train["ts"].max() == pd.Timestamp("2024-01-01T08:00:00Z")
    assert test["ts"].min() == pd.Timestamp("2024-01-01T09:00:00Z")
    assert len(train) + len(test) == 12


def test_gap_drops_boundary_rows_from_both_sides():
    train, test = temporal_train_test_split(
        _frame(), "ts",
        test_start="2024-01-01T09:00:00+00:00", gap="2h",
    )
    # rows 07:00 and 08:00 fall inside the embargo -> in neither set
    assert len(train) == 7
    assert train["ts"].max() == pd.Timestamp("2024-01-01T06:00:00Z")
    assert len(test) == 3
    assert test["ts"].min() == pd.Timestamp("2024-01-01T09:00:00Z")
    dropped = set(_frame()["ts"]) - set(train["ts"]) - set(test["ts"])
    assert dropped == {
        pd.Timestamp("2024-01-01T07:00:00Z"),
        pd.Timestamp("2024-01-01T08:00:00Z"),
    }


def test_rows_sharing_cutoff_timestamp_all_go_to_test():
    frame = _frame(6)
    # two extra rows exactly on the cutoff
    dup = pd.DataFrame({"ts": pd.to_datetime(
        ["2024-01-01T05:00:00Z", "2024-01-01T05:00:00Z"]), "value": [90, 91]})
    frame = pd.concat([frame, dup], ignore_index=True)
    train, test = temporal_train_test_split(
        frame, "ts", test_start="2024-01-01T05:00:00+00:00"
    )
    assert (test["ts"] == pd.Timestamp("2024-01-01T05:00:00Z")).sum() == 3
    assert len(train) == 5


def test_output_is_sorted_even_when_input_is_not():
    frame = _frame(8).sample(frac=1.0, random_state=0)
    train, test = temporal_train_test_split(frame, "ts", test_fraction=0.25)
    assert train["ts"].is_monotonic_increasing
    assert test["ts"].is_monotonic_increasing
    # original index labels survive for traceability
    assert set(train.index) | set(test.index) == set(frame.index)


def test_requires_exactly_one_of_test_start_and_fraction():
    frame = _frame()
    with pytest.raises(ValueError, match="exactly one"):
        temporal_train_test_split(frame, "ts")
    with pytest.raises(ValueError, match="exactly one"):
        temporal_train_test_split(
            frame, "ts", test_start="2024-01-01T09:00:00+00:00", test_fraction=0.25
        )


def test_naive_cutoff_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        temporal_train_test_split(_frame(), "ts", test_start="2024-01-01 09:00:00")


def test_naive_time_column_rejected():
    frame = _frame().assign(ts=lambda d: d["ts"].dt.tz_localize(None))
    with pytest.raises(ValueError, match="assume_tz"):
        temporal_train_test_split(frame, "ts", test_fraction=0.25)


def test_missing_time_column_rejected():
    with pytest.raises(ValueError, match="not in frame columns"):
        temporal_train_test_split(_frame(), "when", test_fraction=0.25)


@pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2, "0.25", True, None])
def test_bad_test_fraction_rejected(bad):
    frame = _frame()
    if bad is None:
        # both None -> "exactly one" error, still a ValueError
        with pytest.raises(ValueError):
            temporal_train_test_split(frame, "ts", test_fraction=None)
        return
    with pytest.raises(ValueError):
        temporal_train_test_split(frame, "ts", test_fraction=bad)


def test_negative_gap_rejected():
    with pytest.raises(ValueError, match="gap/embargo"):
        temporal_train_test_split(
            _frame(), "ts", test_start="2024-01-01T09:00:00+00:00", gap="-1h"
        )


def test_empty_frame_rejected():
    with pytest.raises(ValueError, match="empty frame"):
        temporal_train_test_split(_frame(0), "ts", test_fraction=0.25)


def test_cutoff_before_all_rows_rejected():
    with pytest.raises(ValueError, match="train side is empty"):
        temporal_train_test_split(
            _frame(), "ts", test_start="2023-01-01T00:00:00+00:00"
        )


def test_cutoff_after_all_rows_rejected():
    with pytest.raises(ValueError, match="test side is empty"):
        temporal_train_test_split(
            _frame(), "ts", test_start="2025-01-01T00:00:00+00:00"
        )


def test_gap_consuming_entire_train_side_rejected():
    with pytest.raises(ValueError, match="train side is empty"):
        temporal_train_test_split(
            _frame(), "ts",
            test_start="2024-01-01T03:00:00+00:00", gap="10h",
        )


# -- assert_strictly_past ----------------------------------------------------


def test_assert_strictly_past_accepts_clean_cut():
    train = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    test = pd.date_range("2024-01-01T05:00:00Z", periods=3, freq="1h", tz="UTC")
    assert_strictly_past(train, test)  # no raise


def test_assert_strictly_past_rejects_overlap():
    train = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    test = pd.date_range("2024-01-01T04:00:00Z", periods=3, freq="1h", tz="UTC")
    with pytest.raises(ValueError, match="leakage"):
        assert_strictly_past(train, test)


def test_assert_strictly_past_rejects_equal_boundary_timestamps():
    train = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    test = pd.date_range("2024-01-01T04:00:00Z", periods=3, freq="1h", tz="UTC")
    with pytest.raises(ValueError, match="leakage"):
        assert_strictly_past(train, test)


def test_assert_strictly_past_rejects_naive_timestamps():
    train = pd.date_range("2024-01-01", periods=5, freq="1h")
    test = pd.date_range("2024-01-01T05:00:00", periods=3, freq="1h")
    with pytest.raises(ValueError, match="timezone-aware"):
        assert_strictly_past(train, test)


def test_assert_strictly_past_rejects_empty_sides():
    aware = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    with pytest.raises(ValueError, match="empty"):
        assert_strictly_past(aware[:0], aware)
    with pytest.raises(ValueError, match="empty"):
        assert_strictly_past(aware, aware[:0])
