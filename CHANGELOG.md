# Changelog

Notable changes to this project, recorded by build day.
Dates are commit dates (build started 2026-10-19).

## [Unreleased]

### Added

- `docs/project_plan.md` — 29-day roadmap consolidated from every explicit
  day reference in `reports/` and `src/` (unspecified days marked TBD, not
  invented).
- `docs/data_governance.md` — provenance, license status, coverage, time/label
  integrity, and publication rules in one note.
- This changelog.

### Changed

- `.gitignore` extended with tooling/OS cache patterns (`.pytest_cache/`,
  coverage, mypy/ruff caches, `.DS_Store`, …).

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
