"""Tests for src.cleaning.standardize (Day 8)."""

import pandas as pd
import pytest

from src.cleaning import standardize as std


# --- CTX-4 country lookup ---------------------------------------------------

def test_country_lookup_is_complete_and_consistent():
    assert set(std.PILOT_ISO3) == {"KEN", "ZAF", "NGA"}
    for iso3, meta in std.COUNTRIES.items():
        assert len(iso3) == 3 and len(meta["iso2"]) == 2
        # timezone must be a real IANA zone usable by zoneinfo
        pd.Timestamp("2024-01-01", tz=meta["timezone"])
        assert meta["numeric"].isdigit()


# --- to_iso3 ----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("KEN", "KEN"),
    ("ke", "KEN"),
    ("Kenya", "KEN"),
    ("republic of kenya", "KEN"),
    ("404", "KEN"),
    ("ZAF", "ZAF"),
    ("ZA", "ZAF"),
    ("South Africa", "ZAF"),
    ("rsa", "ZAF"),
    ("NGA", "NGA"),
    ("NG", "NGA"),
    ("Federal Republic of Nigeria", "NGA"),
    ("  nigeria ", "NGA"),
])
def test_to_iso3_accepts_common_identifiers(raw, expected):
    assert std.to_iso3(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "France", "KE2", None, float("nan")])
def test_to_iso3_rejects_unknowns(raw):
    with pytest.raises(ValueError):
        std.to_iso3(raw)


def test_standardize_countries_returns_na_for_bad_rows():
    s = std.standardize_countries(["Kenya", "???", "ZAF"])
    assert s.tolist() == ["KEN", pd.NA, "ZAF"]


# --- unit names -------------------------------------------------------------

def test_normalize_unit_name_case_punct_space():
    assert std.normalize_unit_name("  Nairobi CITY--County ") == "nairobi city county"
    assert std.normalize_unit_name("eThekwini & Durban") == "ethekwini and durban"
    assert std.normalize_unit_name(None) == ""
    assert std.normalize_unit_name(float("nan")) == ""


def test_strip_admin_suffix_and_match_key():
    assert std.strip_admin_suffix("nairobi city county") == "nairobi"
    assert std.strip_admin_suffix("western cape") == "western cape"
    assert std.name_match_key("Lagos State") == "lagos"
    assert std.name_match_key("Mombasa County") == "mombasa"


def test_match_keys_equate_known_aliases():
    pairs = [("Nairobi City County", "Nairobi"),
             ("Lagos State", "Lagos"),
             ("Kano State", "Kano")]
    for a, b in pairs:
        assert std.name_match_key(a) == std.name_match_key(b)


# --- timestamps -------------------------------------------------------------

def test_normalize_timestamp_assumes_utc_for_naive():
    s = std.normalize_timestamp(["2024-01-01 00:00", "2024-01-01 01:00"])
    assert str(s.dt.tz) == "UTC"
    assert s.iloc[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")


def test_normalize_timestamp_converts_aware_to_utc():
    sast = pd.Series(pd.to_datetime(["2024-01-01 02:00"]).tz_localize("Africa/Johannesburg"))
    s = std.normalize_timestamp(sast)
    # SAST is UTC+2 -> 00:00 UTC
    assert s.iloc[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")


def test_normalize_timestamp_explicit_local_assumption():
    # KPLC notices are EAT (UTC+3): 06:00 EAT == 03:00 UTC
    s = std.normalize_timestamp(["2024-01-01 06:00"],
                                assume_tz="Africa/Nairobi")
    assert s.iloc[0] == pd.Timestamp("2024-01-01 03:00", tz="UTC")


def test_normalize_timestamp_coerces_garbage_to_nat():
    s = std.normalize_timestamp(["2024-01-01 00:00", "not a date"])
    assert s.iloc[1] is pd.NaT or pd.isna(s.iloc[1])


def test_to_local_pilot_offsets():
    utc = pd.Series(pd.to_datetime(["2024-01-01 06:00"]).tz_localize("UTC"))
    assert std.to_local(utc, "KEN").iloc[0].hour == 9      # EAT  UTC+3
    assert std.to_local(utc, "ZAF").iloc[0].hour == 8      # SAST UTC+2
    assert std.to_local(utc, "NGA").iloc[0].hour == 7      # WAT  UTC+1
    assert std.to_local(utc, "nga").iloc[0].hour == 7      # accepts codes


def test_floor_hour_keeps_tz():
    s = pd.Series(pd.to_datetime(["2024-01-01 06:59:59.999"]).tz_localize("UTC"))
    assert std.floor_hour(s).iloc[0] == pd.Timestamp("2024-01-01 06:00", tz="UTC")


# --- grid assertion ---------------------------------------------------------

def test_hourly_grid_is_utc_hourly():
    g = std.hourly_grid("2024-01-01 00:00", "2024-01-01 05:00")
    assert str(g.tz) == "UTC"
    assert len(g) == 6
    assert (g.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()


def test_assert_hourly_grid_reports_every_problem():
    good = pd.Series(std.hourly_grid("2024-01-01 00:00", "2024-01-01 03:00"))
    assert std.assert_hourly_grid(good) == []
    assert std.assert_hourly_grid(good, grid=good) == []

    bad = pd.Series([good.iloc[0], good.iloc[1], good.iloc[1], good.iloc[3]])
    problems = std.assert_hourly_grid(bad, grid=good)
    joined = " ".join(problems)
    assert "duplicate" in joined
    assert "reference hourly grid" in joined

    naive = pd.Series(pd.to_datetime(["2024-01-01 00:00"]))
    assert any("tz-aware UTC" in p for p in std.assert_hourly_grid(naive))
