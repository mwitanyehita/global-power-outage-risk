"""Tests for the dashboard state layer (src/dashboard/state.py).

Tests that need downloaded data skip when the data folders are absent, so the
suite stays green on a fresh clone.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.dashboard import state as S


# ---------------------------------------------------------------------------
# Plan tracking
# ---------------------------------------------------------------------------
def test_plan_status_covers_every_step():
    rows = S.plan_status()
    assert len(rows) == len(S.PLAN_STEPS)
    assert [r["day"] for r in rows] == sorted(r["day"] for r in rows)
    for row in rows:
        # "skipped" = a day the source plan never specified; it has no
        # deliverable and must not be reported as pending work.
        assert row["status"] in {
            "done", "in progress", "pending", "blocked", "skipped",
        }
        assert row["task"]


def test_current_day_is_first_unfinished_step():
    rows = S.plan_status()
    # Days that were never specified are skipped: they must not hold the
    # pointer back.
    unfinished = [
        r["day"] for r in rows if r["status"] not in ("done", "skipped")
    ]
    assert S.current_day(rows) == (unfinished[0] if unfinished else rows[-1]["day"])


def test_current_build_day_follows_the_commit_history():
    """The build day is the last committed day plus the one in flight."""
    committed = S.last_committed_build_day()
    if committed is None:
        pytest.skip("no git history available")
    assert S.current_build_day() == committed + 1
    assert committed >= 1


def test_plan_steps_are_unique_and_ordered():
    days = [d for d, _, _ in S.PLAN_STEPS]
    assert len(days) == len(set(days))
    assert days == sorted(days)


# ---------------------------------------------------------------------------
# Git / filesystem activity
# ---------------------------------------------------------------------------
def test_git_snapshot_shape():
    snap = S.git_snapshot()
    for key in ("branch", "commit", "short", "commits", "dirty_files", "subject"):
        assert key in snap
    assert snap["commits"] >= 0
    assert snap["dirty_files"] >= 0


def test_repo_activity_is_newest_first():
    rows = S.repo_activity(limit=5)
    assert len(rows) <= 5
    for row in rows:
        assert row["short"] and row["subject"]
    stamps = [row["committed"] for row in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_file_activity_sorted_by_mtime():
    rows = S.file_activity(limit=10)
    assert len(rows) <= 10
    mtimes = [r["mtime"] for r in rows]
    assert mtimes == sorted(mtimes, reverse=True)
    assert all("__pycache__" not in r["path"] for r in rows)


def test_data_totals_counts_files():
    totals = S.data_totals()
    assert totals["raw_files"] >= 0
    assert totals["external_mb"] >= 0


# ---------------------------------------------------------------------------
# Inventory parsing
# ---------------------------------------------------------------------------
def test_inventory_entries_parse_ids_and_statuses():
    entries = S.inventory_entries()
    if not entries:  # reports/data_inventory.md not present
        pytest.skip("no data inventory")
    ids = {e["id"] for e in entries}
    assert "ADM-1" in ids
    assert "WX-1" in ids
    for entry in entries:
        assert entry["source"]
        assert entry["status"]


# ---------------------------------------------------------------------------
# Eskom load-reduction parsing and episodes
# ---------------------------------------------------------------------------
def test_eskom_episodes_groups_contiguous_hours():
    times = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
    frame = pd.DataFrame({
        "time": times,
        "mlr_mw": [0.0, 100.0, 200.0, 0.0, 50.0, 0.0],
    })
    episodes = S.eskom_episodes(frame)
    assert len(episodes) == 2
    assert episodes.iloc[0]["hours"] == 2
    assert episodes.iloc[0]["peak_mw"] == 200.0
    assert episodes.iloc[1]["hours"] == 1
    # sorted by severity, descending
    assert episodes["peak_mw"].is_monotonic_decreasing


def test_eskom_episodes_empty_frame():
    assert S.eskom_episodes(pd.DataFrame()).empty


def test_eskom_episodes_ignores_zero_mlr():
    times = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    frame = pd.DataFrame({"time": times, "mlr_mw": [0.0, 0.0, 0.0]})
    assert S.eskom_episodes(frame).empty


@pytest.mark.skipif(
    not (S.OUTAGES_DIR / "zaf_eskom_hourly.sqlite").exists(),
    reason="Eskom export not downloaded",
)
def test_eskom_load_reduction_parses_real_export():
    frame = S.eskom_load_reduction()
    assert not frame.empty
    assert {"time", "mlr_mw", "residual_demand", "total_re"} <= set(frame.columns)
    assert frame["time"].is_monotonic_increasing
    assert frame["time"].dt.tz is not None
    assert frame["mlr_mw"].max() > 0


# ---------------------------------------------------------------------------
# Outage health
# ---------------------------------------------------------------------------
def test_outage_health_shape():
    out = S.outage_health()
    for key in ("files", "sqlite_files", "json_files", "events", "planned", "unplanned"):
        assert key in out
    assert out["files"] == len(out["sqlite_files"]) + len(out["json_files"])


# ---------------------------------------------------------------------------
# Weather health (needs the downloaded grid)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not S.weather_files(), reason="no weather downloads")
def test_weather_health_passes_on_downloaded_grid():
    health = S.weather_health()
    assert health["cities"]
    assert health["alignment_ok"] is True
    for city, res in health["cities"].items():
        assert res["gaps"] == 0, city
        assert res["duplicates"] == 0, city
        assert res["nulls"] == 0, city
        assert res["ok"] is True, city


# ---------------------------------------------------------------------------
# Source catalog
# ---------------------------------------------------------------------------
def test_source_catalog_lists_acquired_files():
    catalog = S.source_catalog()
    if catalog.empty:
        pytest.skip("no raw files")
    assert {"file", "kind", "bytes", "license"} <= set(catalog.columns)
    assert (catalog["bytes"] > 0).all()
