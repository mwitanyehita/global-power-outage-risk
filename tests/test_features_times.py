"""Tests for src.features.times — timezone-safe timestamp handling."""

from __future__ import annotations

import pandas as pd
import pytest

from src.features.times import local_time_parts, notice_lead_time, to_utc


def _aware(*vals) -> pd.Series:
    return pd.Series(pd.to_datetime(list(vals), utc=True))


def test_to_utc_converts_aware_input_to_utc():
    s = pd.Series(pd.to_datetime(["2024-03-01T10:00:00+03:00"]).tz_convert("UTC"))
    out = to_utc(s)
    assert isinstance(out.dtype, pd.DatetimeTZDtype)
    assert str(out.dt.tz) == "UTC"
    assert out.iloc[0] == pd.Timestamp("2024-03-01T07:00:00Z")


def test_to_utc_accepts_mixed_aware_offsets():
    out = to_utc(["2024-03-01T10:00:00+03:00", "2024-03-01T04:00:00-05:00"])
    assert list(out) == [
        pd.Timestamp("2024-03-01T07:00:00Z"),
        pd.Timestamp("2024-03-01T09:00:00Z"),
    ]
    assert str(out.dt.tz) == "UTC"


def test_to_utc_rejects_naive_without_assume_tz():
    with pytest.raises(ValueError, match="assume_tz"):
        to_utc(pd.Series(pd.to_datetime(["2024-03-01 10:00:00"])))


def test_to_utc_naive_with_assume_tz_localizes_then_converts():
    naive = pd.Series(pd.to_datetime(["2024-01-01T00:00:00"]))
    out = to_utc(naive, assume_tz="Africa/Nairobi")
    assert out.iloc[0] == pd.Timestamp("2023-12-31T21:00:00Z")


def test_to_utc_naive_with_invalid_assume_tz_raises_value_error():
    naive = pd.Series(pd.to_datetime(["2024-01-01T00:00:00"]))
    with pytest.raises(ValueError, match="could not localize"):
        to_utc(naive, assume_tz="Not/AZone")


def test_to_utc_rejects_mixed_naive_and_aware_and_names_the_column():
    with pytest.raises(ValueError, match="event_time"):
        to_utc(
            pd.Series(["2024-03-01T10:00:00", "2024-03-01T10:00:00+03:00"]),
            label="event_time",
        )


def test_to_utc_passes_nat_through():
    out = to_utc(pd.Series([pd.NaT, pd.Timestamp("2024-03-01T10:00:00Z")]))
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == pd.Timestamp("2024-03-01T10:00:00Z")


def test_to_utc_all_missing_returns_utc_typed_column():
    out = to_utc(pd.Series([pd.NaT, pd.NaT]))
    assert isinstance(out.dtype, pd.DatetimeTZDtype)
    assert str(out.dt.tz) == "UTC"
    assert out.isna().all()


def test_to_utc_preserves_index_and_name():
    idx = pd.Index([10, 20], name="row")
    s = pd.Series(pd.to_datetime(["2024-03-01T10:00:00Z", "2024-03-01T11:00:00Z"]),
                  index=idx, name="event_time")
    out = to_utc(s)
    assert list(out.index) == [10, 20]
    assert out.index.name == "row"
    assert out.name == "event_time"


def test_local_time_parts_nairobi_weekend_hour():
    # 2024-01-06 22:00 UTC = 2024-01-07 01:00 in Nairobi (Sunday)
    parts = local_time_parts(_aware("2024-01-06T22:00:00Z"), tz="Africa/Nairobi")
    row = parts.iloc[0]
    assert row["local_hour"] == 1
    assert row["local_dayofweek"] == 6
    assert row["local_month"] == 1
    assert row["is_weekend"] == 1


def test_local_time_parts_lagos_weekday_hour():
    # 2024-01-05 10:00 UTC = 11:00 in Lagos (Friday)
    parts = local_time_parts(_aware("2024-01-05T10:00:00Z"), tz="Africa/Lagos")
    row = parts.iloc[0]
    assert row["local_hour"] == 11
    assert row["local_dayofweek"] == 4
    assert row["is_weekend"] == 0


def test_local_time_parts_int_dtypes_and_index_alignment():
    times = _aware("2024-01-05T10:00:00Z", "2024-01-06T10:00:00Z")
    times.index = pd.Index([7, 8], name="ridx")
    parts = local_time_parts(times, tz="Africa/Nairobi")
    assert list(parts.index) == [7, 8]
    assert list(parts.columns) == [
        "local_hour", "local_dayofweek", "local_month", "is_weekend",
    ]
    assert all(pd.api.types.is_integer_dtype(parts[c]) for c in parts.columns)


def test_notice_lead_time_positive_when_notice_precedes_event():
    event = pd.Series(pd.to_datetime(["2024-03-01T12:00:00+03:00"]).tz_convert("UTC"))
    notice = pd.Series(pd.to_datetime(["2024-03-01T08:00:00Z"]))
    lead = notice_lead_time(event, notice)
    assert lead.name == "lead_time"
    assert list(lead) == [pd.Timedelta(hours=1)]


def test_notice_lead_time_negative_flags_late_notice():
    event = pd.Series(pd.to_datetime(["2024-03-01T12:00:00Z"]))
    notice = pd.Series(pd.to_datetime(["2024-03-01T13:00:00Z"]))
    lead = notice_lead_time(event, notice)
    assert list(lead) == [pd.Timedelta(hours=-1)]


def test_notice_lead_time_length_mismatch_raises():
    event = _aware("2024-03-01T12:00:00Z", "2024-03-01T13:00:00Z")
    notice = _aware("2024-03-01T08:00:00Z")
    with pytest.raises(ValueError, match="length mismatch"):
        notice_lead_time(event, notice)
