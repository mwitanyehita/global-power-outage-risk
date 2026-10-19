# Changelog

Notable changes to this project, recorded by build day.
Dates are commit dates (build started 2026-10-19).

## [Unreleased]

### Added

- `docs/project_plan.md` — 30-day roadmap consolidated from every explicit
  day reference in `reports/` and `src/` (unspecified days marked TBD, not
  invented).
- `docs/data_governance.md` — provenance, license status, coverage, time/label
  integrity, and publication rules in one note.
- This changelog.

### Changed

- `.gitignore` extended with tooling/OS cache patterns (`.pytest_cache/`,
  coverage, mypy/ruff caches, `.DS_Store`, …).

## Day 30 — 2026-10-19

### Added

- `src/experiments/zaf_interpretation.py` + `reports/day27_model_interpretation.md`
  (Day 27) — descriptive full-period fit, coefficient table, and the
  portfolio-safe wording / wording-to-avoid tables. States that coefficient
  *mass* is not skill and defers to the ablation for what predicts.
- `reports/day30_final_summary.md` (Day 30) — what the project delivers, what
  it deliberately does not claim, limitations, reproduction commands, ranked
  v2 roadmap.
- `reports/README.md` — report index: one line and one reproduce command per
  day, plus the standing rules (planned ≠ unplanned, unknown ≠ zero, …).
- `scripts/verify.sh` — one command that runs the full suite **and**
  regenerates every derived artifact and report (fail-soft per stage).
- `reports/day19_calibration.md` (Day 19) — calibration protocol, the
  held-out evidence, and the rule for when a probability may be published.
- `src/ingestion/kplc_parse.py` (Day 11 Kenya) — 110 official KPLC notice
  PDFs → 3,740 structured planned-outage events with a SHA-256 manifest;
  105/105 parseable PDFs plus 5 image-only ones recorded as missing.
- `src/eda/coverage_maps.py` + `reports/day13_coverage_maps.md` (Day 13) —
  coverage atlas with counts derived from the files on disk.

### Changed

- Dashboard plan steps now mirror the real 30-day schedule; plan status is
  re-derived from artefacts on disk and "skipped" days no longer hold the
  current-day pointer back.
- Data paths resolve through `src.config` (single source of truth) while the
  historical import paths keep working.
- README status, day table, phase table and test count synced to the
  finished build (566 tests green).

## Day 19 — 2026-10-19

### Added

- `reports/day19_calibration.md`: the calibration protocol the pipeline
  actually ran, the evidence behind it, and the **publishing rule** that
  follows — publish raw out-of-fold probabilities for days in-regime, and
  say so next to the number when the regime shifts; never fit a calibrator on
  the rows used to report its quality.
- `src/ingestion/kplc_parse.py` hardening: typo-tolerant dates
  (`DD.MM.YYYY`, missing dot, 3-digit year), area/date disambiguation by
  content rather than punctuation, and footer/boilerplate detection.

## Day 13 — 2026-10-19

### Added

- `src/eda/coverage_maps.py` + `reports/day13_coverage_maps.md`: the coverage
  atlas — unit maps (ADM1/ADM2 and the nine pilot cities), label-coverage
  panels from real per-year statistics, and a derived coverage table
  (boundaries / weather / World Bank / labels / events per country) whose
  counts come from the files on disk, not from assertions. Kenya's
  unplanned layer is recorded as **absent by design** (KPLC publishes planned
  notices only), Nigeria as partial/national 2023–25, South Africa as
  national MLR.

## Day 30 — 2026-10-19

### Added

- `reports/day30_final_summary.md`: the closing statement — **yes for one
  target** (national day-ahead load-shedding in South Africa) **and
  explicitly no for the other two** (Kenya has no unplanned label source that
  passes the go/no-go gate; Nigeria's 2025 test window holds 2 positive
  days). The "no" is recorded as a finding, not a gap.
- `reports/README.md`: the report index — what every report answers and the
  command that regenerates it.
- `scripts/verify.sh`: one-command verification — the full hermetic test
  suite plus regeneration of every derived artifact and report, in pipeline
  order, with a non-zero exit if any stage fails.

## Day 27 — 2026-10-19

### Added

- `src/experiments/zaf_interpretation.py` + `reports/day27_model_interpretation.md`:
  descriptive model interpretation with portfolio-safe wording. One logistic
  fit over the full labelled period (2 096 days, 122 features) is used
  **only** to read the model's structure — every performance number stays
  walk-forward. The report states explicitly that coefficient mass is not
  skill (weather's 81% share of coefficient mass is inflated by feature
  count and correlation) and defers to the Day 21 ablation.

## Day 21 — 2026-10-19
### Added

- `src/experiments/zaf_ablation.py` + `reports/day21_zaf_ablation.md`: ZAF
  feature ablation on **identical** walk-forward folds (730-day start,
  90-day windows, 1-day purge, 1 350 tested days) and the same pinned
  reference rate — so differences attribute to features alone:
  calendar-only Brier 0.2763, label-lags-only 0.0196, weather-only 0.1997,
  all features 0.0457.
- Verdict recorded honestly: **persistence carries the skill.** Label
  autoregression adds +0.2567; weather standalone +0.0766; weather on top of
  label lags **−0.0261** (no material skill). The claim is
  persistence-driven predictability of a national daily label — not a
  weather-driven outage forecast.

### Changed

- `src/dashboard/state.py`: the plan board now covers Days 1–29 with status
  re-derived from the artefacts on disk (it previously reported Days 5, 8–11
  and 16–18 as "pending" long after they landed) and `current_day` reads the
  build day from the commit history.

## Day 20 — 2026-10-19

### Added

- `src/experiments/nga_baseline.py` + `reports/day20_nga_baseline.md`: NGA
  collapse-day baselines on a chronological split (train 2023–2024, 12
  positives; test 2025, 2 positives) — constant training prevalence
  Brier 0.005569 / PR-AUC 0.005.
- **Verdict: no NGA model is claimed.** With 2 positive days in the test
  window any classifier would be memorizing; the report instead states the
  four conditions that would be needed to justify one (≥ 12 positives in
  every test year, back-filled 2019–2022 events, a sub-national target, and
  a documented causal mechanism test).

## Day 18 — 2026-10-19

### Added

- `src/experiments/zaf_daily_model.py`: end-to-end ZAF day-ahead experiment —
  expanding-window walk-forward CV (15 folds, 90-day test windows, 1-day
  purge), scaler fitted inside each fold's pipeline, reference rate pinned to
  the **fold-1 training prevalence** (Day 14 guard L17), and calibration on
  held-out folds only.
- `src/models/`: `base.py` (1-D `P(outage)` model contract + sklearn adapter +
  climatology baseline), `cv.py` (re-iterable expanding-window splits,
  walk-forward OOF and per-fold/pooled scoring, optional per-row timestamp
  guard that refuses folds that are not strictly in the past), `calibration.py`
  (Platt / isotonic on held-out probabilities only).
- `src/evaluation/probabilistic.py`: Brier, clipped log loss, climatology,
  Brier/log-loss skill scores, calibration curve + ECE, ROC AUC and
  `evaluation_report` — every input validated, invalid forecasts fail loudly.
- `src/features/`: `times.py` (timezone-safe event/notice time handling and
  notice lead time), `time_split.py` (temporal split with embargo +
  `assert_strictly_past`), `rolling.py` (strictly-past lags and rolling
  windows, group-aware, regular-grid guard).
- `reports/day18_zaf_model.md`: walk-forward OOF metrics over 1 350 tested
  days — logistic Brier 0.0457 / skill **+0.794** vs climatology baseline
  0.2338 / −0.053; ECE 0.028.

### Changed

- `src/ingestion/boundaries.py`: retries with backoff, payload validation
  before write, atomic `.part` + rename writes, idempotent skip only when the
  existing file parses, sidecar self-heal, SHA-256 in the sidecar, per-file
  failure collection with a non-zero CLI exit.
- `src/ingestion/outages.py`: canonical 17-column outage schema keeping
  planned/unplanned distinct, alias mapping, tz-aware UTC normalization,
  stable `source:id` keys, `OutageAdapter` protocol, raw intake with
  provenance sidecars, and a full CLI.
- `src/ingestion/weather_canonical.py`, `src/ingestion/data_manifest.py`:
  tidy canonical weather frame and a SHA-256 provenance index over `data/`.
- `README.md`, `CHANGELOG.md`, `docs/project_plan.md` refreshed to the
  Day 18 state (they had drifted to Day 4).

### Fixed

- `log_loss_skill_score` compared the reference score to exactly `0.0`, but
  log loss is clipped at `eps`, so a *perfect* reference scored `~1e-15` and
  the NaN branch was unreachable — skill against a perfect reference now
  returns `NaN` instead of a large negative number.
- `sanitize_filename` allowed leading dots, so a sanitised name could be a
  hidden file or a `..` component; leading dots are now neutralised.
- `pytest.ini` set `addopts = -q`, which combined with a CLI `-q` into `-qq`
  and hid the pass/fail summary CI relies on; replaced with
  `--strict-markers --strict-config`.
- The data manifest stored an **absolute** `data_root`, making the artifact
  machine-specific; it is now project-relative.

## Day 17 — 2026-10-19

### Added

- `src/labels/collapse_days.py` + `reports/day17_nga_labels.md`: NGA
  secondary labels — national grid-collapse days from the Day 10 NERC
  extraction, with per-event provenance and an explicit **not labelable**
  window for 2019–2022 ("no row" must never read as "no collapse").

## Day 16 — 2026-10-19

### Added

- `src/datasets/zaf_daily.py` + `reports/day16_zaf_dataset.md`: ZAF day-ahead
  training dataset — 2 099 SAST days (2021-01-01 → 2026-09-30), 715 positive
  days, 116 features, all strictly past (lags 1/2/3, rolling 7/28), regular
  grid verified before any window is counted.

## Day 15 — 2026-10-19

### Added

- `src/labels/eskom.py` + `reports/day15_zaf_labels.md`: ZAF primary labels
  from the Eskom hourly record — `load_shedding = (MLR > 0)` per hour,
  83 280 rows with 0 duplicates and 0 missing hours; missing MLR stays `<NA>`
  and is never scored as "no shedding".

## Day 14 — 2026-10-19

### Added

- `reports/day14_leakage_audit.md`: code-grounded audit of every path by
  which future information or the label itself could enter features, splits,
  scaling, calibration or claims — 17 vectors, 6 open guards logged (L6
  scaler, L17 reference rate, and others now enforced in code).

## Day 12 — 2026-10-19

### Added

- `src/geo/units.py` + `reports/day12_duplicate_units.md`: canonical,
  geometry-free unit registry (`data/processed/unit_registry.csv`) and the
  duplicate-key analysis — 10 duplicated NGA ADM2 match keys, resolved by
  point-in-polygon rather than name matching.

## Day 11 — 2026-10-19

### Added

- `src/geo/join.py` + `reports/day11_spatial_join.md`: spatial join
  machinery with a binding method hierarchy — point-in-polygon is
  authoritative, name matching only proposes candidates, and ambiguous rows
  are never silently resolved.

## Day 10 — 2026-10-19

### Added

- `src/ingestion/kplc.py`, `src/ingestion/kplc_parse.py`,
  `reports/day10_kenya_probes.md`: Kenya KPLC planned-notice acquisition
  paths proven and parsed.
- `src/ingestion/nerc.py` + `reports/day10_nerc_events.md`: Nigeria NERC
  annual reports → dated grid-collapse events with per-event provenance.

## Day 9 — 2026-10-19

### Added

- `src/eda/weather_eda.py` + `reports/day09_eda_weather.md`: weather
  exploratory analysis with figures (`reports/figures/`).

## Day 8 — 2026-10-19

### Added

- `src/cleaning/standardize.py`: standardization of unit codes, unit names
  and timestamps to tz-aware UTC, with the `assume_tz` rule that keeps naive
  source times flagged rather than silently reinterpreted.

## Day 7 — 2026-10-19

### Added

- `reports/day07_data_audit.md`: go / no-go decision per source with
  evidence — the binding contract for everything downstream.

## Day 6 — 2026-10-19

### Added

- `reports/source_register.csv` + `reports/day06_source_register.md`: the
  source register — licenses, coverage and status per source.

## Day 5 — 2026-10-19

### Added

- `src/ingestion/worldbank.py` + `reports/day05_worldbank_coverage.md`: World
  Bank outage-indicator series for KEN/ZAF/NGA with a `_manifest.json` and a
  coverage check. National context only — never a geolocated label.

## Day 4 — 2026-10-19

### Added

- `reports/day04_outage_sources.md`: every outage/notice source (OUT-1…OUT-8)
  probed for the three pilots with verdicts; South Africa's Eskom hourly MLR
  record acquired (`data/raw/outages/zaf_eskom_hourly.sqlite`, 83 280 rows,
  2017-04 → 2026-09) with a documented no-license-mirror provenance caveat.
- `src/ingestion/outages.py` + `src/ingestion/utils.py`: outage intake with
  `{source}_meta.json` provenance sidecars (source, license, URL, SHA-256)
  and normalization to a common event schema; tests.

## Day 3 — 2026-10-19

### Added

- `src/ingestion/weather.py`: Open-Meteo historical archive downloader +
  validator + audit CLI (`--audit`); nine pilot cities, hourly, UTC.
- `reports/day03_weather_audit.md`: 9/9 cities — 50 808 hourly rows each
  (2021-01-01 → 2026-10-18 UTC), zero gaps/duplicates, identical UTC grid,
  zero nulls, no physical-range violations.
- `notebooks/02_historical_weather.ipynb`.
- `app/dashboard.py` + `src/dashboard/` — live project dashboard.
- `tests/test_weather.py` (suite at 34 passing).
- Repo infrastructure: `README.md` rewritten for portfolio quality incl. the
  required **Data sources & licenses** attribution strings;
  `requirements.txt` pinned to the virtualenv's exact versions
  (Python 3.14.4, verified 2026-10-19) with `requirements-dev.txt` split;
  GitHub Actions workflow `.github/workflows/tests.yml`; `LICENSE` (MIT,
  code only — data retains upstream licenses); `.editorconfig`.

## Day 2 — 2026-10-19

### Added

- `src/ingestion/boundaries.py`: geoBoundaries gbOpen downloader +
  structural inspection + audit CLI (`--audit`).
- `notebooks/01_admin_boundaries.ipynb`.
- `reports/day02_boundaries_audit.md`: 9/9 boundary files (KEN/NGA/ZAF ×
  ADM0/1/2) pass count, CRS, geometry-validity, bbox, and license checks;
  flags 5 duplicate unit names in `NGA_ADM2.geojson` for Day 11 handling.
- Boundary downloads + verbatim API metadata under
  `data/external/boundaries/` (git-ignored).
- Tests for boundary inspection (parametrized over downloaded files, with
  skip-on-missing and synthetic fixtures).

## Day 1 — 2026-10-19

### Added

- Project skeleton: `src/config.py` (path/runtime single source of truth,
  Colab + local detection), package layout for `features`/`models`/
  `evaluation`.
- `notebooks/00_colab_setup.ipynb` (environment verification).
- `reports/data_inventory.md`: full source register plan (boundaries,
  weather, outage records, World Bank indicators, context layers), folder
  conventions, day-by-day acquisition plan, risks.
- `requirements.txt`, initial `README.md`, test scaffolding
  (`tests/test_config.py`).
