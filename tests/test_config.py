"""Tests for src.config."""

from pathlib import Path

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
