"""Tests for src.config.

Root detection is exercised through the pure ``_resolve_project_root``
helper (injectable cwd/module file), so path behavior from any working
directory is covered without re-importing the module.
"""

from pathlib import Path

import pytest

from src import config


def test_project_root_detected():
    assert (config.PROJECT_ROOT / "requirements.txt").is_file()
    assert (config.PROJECT_ROOT / "src").is_dir()


def test_data_dirs_are_paths_under_project():
    for d in config.DATA_DIRS:
        assert isinstance(d, Path)
        assert str(d).startswith(str(config.PROJECT_ROOT))


def test_ensure_dirs_creates_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_RAW", tmp_path / "raw")
    monkeypatch.setattr(config, "DATA_PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(config, "DATA_EXTERNAL", tmp_path / "external")
    monkeypatch.setattr(
        config, "DATA_DIRS", (tmp_path / "raw", tmp_path / "processed", tmp_path / "external")
    )
    config.ensure_dirs()
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "processed").is_dir()
    assert (tmp_path / "external").is_dir()


def test_runtime_summary_mentions_python():
    summary = config.runtime_summary()
    assert "Python" in summary
    assert "root=" in summary
    assert f"seed={config.SEED}" in summary


# -- root resolution ---------------------------------------------------------

def test_root_found_from_module_file_even_with_foreign_cwd(tmp_path):
    """A cwd outside the project must not derail root detection."""
    root = config._resolve_project_root(
        None, cwd=tmp_path, module_file=Path(config.__file__)
    )
    assert root == config.PROJECT_ROOT


def test_root_found_from_cwd_when_module_file_is_foreign(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "requirements.txt").write_text("pandas\n", encoding="utf-8")
    cwd = project / "notebooks" / "deep"
    cwd.mkdir(parents=True)

    root = config._resolve_project_root(
        None, cwd=cwd, module_file=tmp_path / "elsewhere" / "x.py"
    )
    assert root == project


def test_root_env_override_requires_existing_directory(tmp_path):
    with pytest.raises(RuntimeError, match="does not exist"):
        config._resolve_project_root(
            str(tmp_path / "nope"), cwd=tmp_path, module_file=Path(config.__file__)
        )


def test_root_env_override_requires_project_markers(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RuntimeError, match="requirements.txt"):
        config._resolve_project_root(str(plain), cwd=tmp_path, module_file=Path(config.__file__))


def test_root_env_override_wins(tmp_path):
    clone = tmp_path / "colab-clone"
    (clone / "src").mkdir(parents=True)
    (clone / "requirements.txt").write_text("pandas\n", encoding="utf-8")
    root = config._resolve_project_root(
        str(clone), cwd=tmp_path, module_file=Path(config.__file__)
    )
    assert root == clone.resolve()


def test_root_not_found_raises(tmp_path):
    cwd = tmp_path / "nowhere"
    cwd.mkdir()
    with pytest.raises(RuntimeError, match="Could not locate the project root"):
        config._resolve_project_root(
            None, cwd=cwd, module_file=tmp_path / "other" / "x.py"
        )


# -- seed handling -----------------------------------------------------------

def test_seed_default_and_env_override(monkeypatch):
    monkeypatch.delenv("GRIDRISK_SEED", raising=False)
    assert config._seed_from_env() == config.DEFAULT_SEED == 42
    monkeypatch.setenv("GRIDRISK_SEED", "123")
    assert config._seed_from_env() == 123
    monkeypatch.setenv("GRIDRISK_SEED", " 7 ")  # tolerant parsing
    assert config._seed_from_env() == 7


def test_seed_env_empty_falls_back(monkeypatch):
    monkeypatch.setenv("GRIDRISK_SEED", "")
    assert config._seed_from_env() == config.DEFAULT_SEED


@pytest.mark.parametrize("bad", ["abc", "-1", "3.5", "1,5"])
def test_seed_env_rejects_invalid(monkeypatch, bad):
    monkeypatch.setenv("GRIDRISK_SEED", bad)
    with pytest.raises(RuntimeError, match="GRIDRISK_SEED"):
        config._seed_from_env()


def test_module_seed_is_a_usable_non_negative_int():
    assert isinstance(config.SEED, int)
    assert config.SEED >= 0


# -- pilot constants ---------------------------------------------------------

def test_pilot_constants_match_boundaries_module():
    """config is the single source of truth; boundaries must not drift."""
    from src.ingestion import boundaries as bnd

    assert bnd.PILOT_COUNTRIES == config.PILOT_COUNTRIES
    assert bnd.DEFAULT_ADM_LEVELS == config.ADM_LEVELS


def test_country_tables_cover_exactly_the_pilots():
    assert set(config.COUNTRY_NAMES) == set(config.PILOT_COUNTRIES)
    assert set(config.COUNTRY_TIMEZONES) == set(config.PILOT_COUNTRIES)
    assert config.COUNTRY_NAMES["KEN"] == "Kenya"
    assert config.COUNTRY_NAMES["NGA"] == "Nigeria"
    assert config.COUNTRY_NAMES["ZAF"] == "South Africa"


def test_country_timezones_are_real_iana_zones():
    from zoneinfo import ZoneInfo

    for iso3, tz in config.COUNTRY_TIMEZONES.items():
        assert ZoneInfo(tz) is not None, iso3


def test_adm_levels_cover_the_expected_unit_counts():
    assert config.ADM_LEVELS == ("ADM0", "ADM1", "ADM2")
