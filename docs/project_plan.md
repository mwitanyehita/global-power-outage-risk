# Project plan — 30-day build roadmap

**Status legend:** ✅ done · 🔄 in progress · ⬜ planned · ❓ TBD (no source
document specifies this day yet)

This roadmap consolidates every *explicit* day reference found in
`reports/data_inventory.md`, `reports/day02_boundaries_audit.md`, and the
`src/` module docstrings. Days marked **TBD** are genuinely unspecified in the
source plan and are not invented here. Source of each row is noted; update
this file when a day is planned or completed.

**Build days so far:** Days 1–21 committed (2026-10-19), plus the Day 13
coverage atlas and the Day 19 calibration protocol. Status below is taken from
the committed history and the per-day reports in `reports/` — where the build
diverged from the original plan, the divergence is recorded in
[Divergences](#divergences-from-the-original-plan) rather than rewritten
away.

## Phase 0 — Foundations (Days 1–3)

| Day | Task | Status | Source |
|---|---|---|---|
| 1 | Colab + local environment check (`notebooks/00_colab_setup.ipynb`); project data inventory with folder conventions (`reports/data_inventory.md`) | ✅ | git log, reports |
| 2 | Administrative boundaries for KEN/NGA/ZAF from geoBoundaries gbOpen; per-file license metadata; audit report (`reports/day02_boundaries_audit.md`); preference decision among ADM-1…ADM-4 sources | ✅ | inventory §A/§G, audit report |
| 3 | Historical weather for pilot cities (Open-Meteo archive), timestamp validation, cross-city grid alignment (`reports/day03_weather_audit.md` — 9/9 cities pass) | ✅ | inventory §B/§G |

## Phase 1 — Data acquisition (Days 4–7)

| Day | Task | Status | Source |
|---|---|---|---|
| 4 | Outage records and official notices (OUT-1…OUT-8) probed for all three pilots; free-tier depth checked (`reports/day04_outage_sources.md`). Follow-ups deferred to Day 10: KPLC Livewire notice list, kplc.project.co.ke archive depth, EPRA re-test from Colab | ✅ | inventory §C/§F/§G, day04 report |
| 5 | World Bank outage indicators; coverage check per pilot country; confirm they are context-only (no locality detail) | ✅ | `reports/day05_worldbank_coverage.md`, `src/ingestion/worldbank.py` |
| 6 | Fill licenses, dates, location detail, and limitations per source; build `reports/source_register.csv` (schema in inventory §H) | ✅ | `reports/source_register.csv`, `reports/day06_source_register.md` |
| 7 | Data audit report; **go/no-go decision per source** (including fallback if public outage history is missing) | ✅ | `reports/day07_data_audit.md` |

## Phase 2 — Normalization & label structure (Days 8–11)

| Day | Task | Status | Source |
|---|---|---|---|
| 8 | Timezone-aware UTC normalization enforced; country lookup tables (ISO3, timezone, region) built in-project; resolve duplicate-name ambiguity prep | ✅ | `src/cleaning/standardize.py` (ambiguity itself resolved on Day 12) |
| 9 | Weather exploratory analysis with figures (`reports/day09_eda_weather.md`, `src/eda/weather_eda.py`) — the plan left this day unspecified; the weather EDA was chosen as the next useful step | ✅ | `reports/day09_eda_weather.md` |
| 10 | Planned vs unplanned interruptions kept distinguishable; store event start, restoration time, and notice publication time separately | ✅ | `src/ingestion/outages.py` (canonical schema), `src/ingestion/kplc.py` + `kplc_parse.py`, `src/ingestion/nerc.py`, `reports/day10_kenya_probes.md`, `reports/day10_nerc_events.md` |
| 11 | Map outages to boundaries by **point-in-polygon** coordinates (names only as secondary check — 5 duplicate names in NGA ADM2); geometry repair with `make_valid` if needed | ✅ | `src/geo/join.py`, `reports/day11_spatial_join.md` (no repair needed — 0 invalid geometries) |

## Phase 3 — Context layers, features & leakage control (Days 12–19)

| Day | Task | Status | Source |
|---|---|---|---|
| 12 | Canonical unit registry and duplicate-name analysis (the Day 8 "duplicate-name ambiguity prep" task) | ✅ | `src/geo/units.py`, `reports/day12_duplicate_units.md` |
| 13 | Coverage atlas — where data exists and where it does not, with counts derived from the files on disk | ✅ | `src/eda/coverage_maps.py`, `reports/day13_coverage_maps.md` |
| 14 | Leakage audit: every path by which future information or the label itself could enter features, splits, scaling, calibration or claims | ✅ | `reports/day14_leakage_audit.md` (17 vectors, 6 open guards) |
| 15 | Formalise the per-country label design implied by the Day 4 investigation — ZAF dense national hourly target | ✅ | `src/labels/eskom.py`, `reports/day15_zaf_labels.md` |
| 16 | ZAF day-ahead training dataset: labels + strictly-past features (lags and rolling windows) | ✅ | `src/datasets/zaf_daily.py`, `reports/day16_zaf_dataset.md` — *the context/exposure layers originally planned for this day (OSM power infrastructure, population at risk) are **not** built; see divergences* |
| 17 | Outage-record consolidation for label construction — NGA sparse national events | ✅ | `src/labels/collapse_days.py`, `reports/day17_nga_labels.md` |
| 18 | **Forecast leakage rule enforced:** only forecasts issued before the prediction cutoff may enter features. Plus the first walk-forward probabilistic model (ZAF day-ahead) | ✅ | `reports/day18_zaf_model.md`, `src/experiments/zaf_daily_model.py`, `src/models/`, `src/evaluation/probabilistic.py`; the rule itself is `reports/day14_leakage_audit.md` + the display-only live grid in `app/dashboard.py` |
| 19 | Calibration protocol, evidence and the publishing rule (the Day 18 run, documented); KPLC notice parsing hardened — typo-tolerant dates, area/date disambiguation, footer detection | ✅ | `reports/day19_calibration.md`, `src/ingestion/kplc_parse.py` |

## Phase 4 — Modelling & product (Days 20–27)

| Day | Task | Status | Source |
|---|---|---|---|
| 20 | NGA collapse-day baselines on a chronological split — establish the honest floor before claiming a model | ✅ | `src/experiments/nga_baseline.py`, `reports/day20_nga_baseline.md` (**no model claimed**) |
| 21 | ZAF feature ablation on identical folds — separate persistence from weather skill | ✅ | `src/experiments/zaf_ablation.py`, `reports/day21_zaf_ablation.md` (**persistence carries the skill**) |
| 22 | Streamlit dashboard (use simplified geoBoundaries geometry — full ADM2 files reach 32 MB) | 🔄 | `app/dashboard.py` + `src/dashboard/state.py` exist and run; the plan board now re-derives Day 1–29 status from the artefacts on disk; simplified geometry for the map layer is still open |
| 23–26 | TBD | ❓ | — |
| 27 | Documented limitations: World Bank indicators are national context only; cautious interpretation of grid-value weather features; final coverage notes | ✅ | `src/experiments/zaf_interpretation.py`, `reports/day27_model_interpretation.md` (descriptive fit only; all performance numbers stay walk-forward) |

## Phase 5 — Publication (Days 28–30)
| Day | Task | Status | Source |
|---|---|---|---|
| 28 | TBD | ❓ | — |
| 29 | Attribution strings present in README for every used source; license re-check before publishing derived data; supported-claims review | ✅ (strings landed early in README and are now guarded by `tests/test_readme_attribution.py`; the supported-claims review is part of Day 30) | audit report findings, inventory §F |
| 30 | Final summary: what the project delivers and what it refuses to claim, plus the reproducibility checklist | ✅ | `reports/day30_final_summary.md`, `reports/README.md`, `scripts/verify.sh` |

## Divergences from the original plan

Recorded so the roadmap stays truthful about what was actually built:

- **Day 9** was unspecified; the build used it for the weather EDA.
- **Days 13, 19, 20 and 21 were added** and are not in the original plan.
  They are honesty and coverage checks, not features: a coverage atlas that
  states where data is *absent*, the calibration protocol and the rule that
  follows from it, a baseline that must exist before a model is claimed, and
  an ablation that attributes the skill to the right input block. Together
  they changed the headline conclusion — the ZAF model's skill is
  **persistence**, not weather, and **no NGA model is claimed at all**. Any
  status table that omits them overstates the project.
- **The build runs to Day 30**, one day beyond the original 29-day plan, to
  carry a final summary, documented limitations and the reproducibility
  checklist.
- **Context/exposure layers (OSM power infrastructure, WorldPop, night lights)
  were not built.** They were planned for Day 16, which was instead used for
  the ZAF training dataset. They remain `CANDIDATE` / `TO VERIFY` entries in
  `reports/data_inventory.md` with unverified licenses — a licence review is
  required before any acquisition.
- **The Day 16 "weather feature refresh"** landed as the strictly-past feature
  build inside `src/features/`, not as a separate refresh of the Day 3 archive.
- **The dashboard arrived early** (Day 3) rather than on Day 22, and has been
  maintained since; the remaining Day 22 scope is the simplified geometry and
  the live-refresh layer.
- **The build runs to Day 30**, one day beyond the original 29-day plan, to
  carry a final summary, documented limitations and the reproducibility
  checklist.

## Standing rules for every day

1. Raw downloads are verbatim into `data/raw/` / `data/external/` with
   provenance sidecars — never edited in place.
2. Licenses marked *verify* must be read before download and re-checked before
   publishing derived data.
3. Tests stay green: `python -m pytest -q` before each day's commit.
4. Status tables in this file, `README.md` and `CHANGELOG.md` are updated in
   the same commit as the day they describe, so documentation never lags the
   code by more than one commit.
