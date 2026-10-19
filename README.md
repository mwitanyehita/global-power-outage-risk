# GridRisk AI — Global Power Outage Risk Intelligence

Probabilistic power-outage risk intelligence for **Kenya, Nigeria, and South
Africa**, built from verified outage records, weather data, official notices,
and administrative geography. The goal is honest, well-calibrated risk
forecasts — not hype: every number published by this project must be traceable
to a documented data source.

**Status:** Day 30 of 30 committed · boundaries, weather, outage records and
labels acquired for all three pilots; a walk-forward probabilistic model built
and **ablated** for South Africa, an honest **no-model** result for Nigeria, a
coverage atlas, a live dashboard, and a final summary with portfolio-safe
wording.

---

## Contents

- [Objective](#objective)
- [Pilot countries](#pilot-countries)
- [Current status](#current-status)
- [Repository layout](#repository-layout)
- [Setup](#setup)
- [Running the tests](#running-the-tests)
- [Running the ingestion CLIs](#running-the-ingestion-clis)
- [Notebooks](#notebooks)
- [Project principles](#project-principles)
- [30-day plan summary](#30-day-plan-summary)
- [Data sources & licenses](#data-sources--licenses)
- [License](#license)

## Objective

Investigate and predict power outage risk across African countries using
verified outage records, weather data, official notices, and geographic
information. The pipeline separates **planned** from **unplanned** outages,
preserves event times and notice publication times, and evaluates
probabilistic forecasts properly rather than reporting accuracy alone.

## Pilot countries

| Country | ISO3 | Administrative levels ingested (Day 2) |
|---|---|---|
| Kenya | KEN | ADM0 (1), ADM1 (47 counties), ADM2 (290 sub-counties) |
| Nigeria | NGA | ADM0 (1), ADM1 (37 states+FCT), ADM2 (774 LGAs) |
| South Africa | ZAF | ADM0 (1), ADM1 (9 provinces), ADM2 (52 district municipalities) |

Weather is sampled for three cities per country (see
`CITIES` in `src/ingestion/weather.py`).

## Current status

| Day | Deliverable | State |
|---|---|---|
| 1 | Colab + local environment check (`notebooks/00_colab_setup.ipynb`), project data inventory (`reports/data_inventory.md`) | ✅ committed |
| 2 | Administrative boundaries downloaded from geoBoundaries gbOpen and audited (`notebooks/01_admin_boundaries.ipynb`, `src/ingestion/boundaries.py`, `reports/day02_boundaries_audit.md`) | ✅ committed — 9/9 files pass every check |
| 3 | Historical weather for the nine pilot cities (`notebooks/02_historical_weather.ipynb`, `src/ingestion/weather.py`, `reports/day03_weather_audit.md`) | ✅ committed — all 9 cities pass timestamp/alignment checks |
| 4 | Outage-record source investigation for all three pilots (`reports/day04_outage_sources.md`), outage intake module (`src/ingestion/outages.py`) | ✅ committed |
| 5 | World Bank outage indicators + coverage check (`src/ingestion/worldbank.py`, `reports/day05_worldbank_coverage.md`) | ✅ committed |
| 6 | Source register with licenses, coverage and statuses (`reports/source_register.csv`, `reports/day06_source_register.md`) | ✅ committed |
| 7 | Data audit with a go / no-go decision per source (`reports/day07_data_audit.md`) | ✅ committed — binding contract for Days 8+ |
| 8 | Unit codes, names and timestamps standardized to UTC (`src/cleaning/standardize.py`) | ✅ committed |
| 9 | Weather EDA report with figures (`src/eda/weather_eda.py`, `reports/day09_eda_weather.md`) | ✅ committed |
| 10 | Kenya: KPLC notice acquisition paths proven (`src/ingestion/kplc.py`, `reports/day10_kenya_probes.md`) · Nigeria: NERC collapse events extracted with provenance (`src/ingestion/nerc.py`, `reports/day10_nerc_events.md`) | ✅ committed |
| 11 | Spatial join machinery — point-in-polygon authoritative, names only propose (`src/geo/join.py`, `reports/day11_spatial_join.md`) | ✅ committed |
| 12 | Canonical unit registry and duplicate analysis (`src/geo/units.py`, `reports/day12_duplicate_units.md`) | ✅ committed — 10 duplicate NGA ADM2 keys found |
| 13 | Coverage atlas: where data exists and where it does not (`src/eda/coverage_maps.py`, `reports/day13_coverage_maps.md`) | ✅ committed — counts derived from the files on disk, not asserted |
| 14 | Leakage audit — 17 vectors reviewed, 6 open guards logged (`reports/day14_leakage_audit.md`) | ✅ committed |
| 15 | ZAF primary labels from the Eskom hourly record (`src/labels/eskom.py`, `reports/day15_zaf_labels.md`) | ✅ committed — 83 280 hours, 0 gaps |
| 16 | ZAF day-ahead training dataset, labels + strictly-past features (`src/datasets/zaf_daily.py`, `reports/day16_zaf_dataset.md`) | ✅ committed — 2 099 days, 116 features |
| 17 | NGA secondary labels — national grid-collapse days (`src/labels/collapse_days.py`, `reports/day17_nga_labels.md`) | ✅ committed — 2019–22 excluded as not labelable |
| 18 | ZAF walk-forward model: out-of-fold evaluation, pinned baseline, held-out calibration (`src/experiments/zaf_daily_model.py`, `reports/day18_zaf_model.md`) | ✅ committed — Brier skill **+0.794** vs climatology |
| 19 | Calibration protocol, evidence and the publishing rule (`reports/day19_calibration.md`); KPLC notice parsing hardened | ✅ committed — publish raw OOF probabilities in-regime, and say so when the regime shifts |
| 20 | NGA collapse-day baselines — **no model claimed** (`src/experiments/nga_baseline.py`, `reports/day20_nga_baseline.md`) | ✅ committed — 2 positives in the 2025 test window, so no classifier is fit |
| 21 | ZAF feature ablation: persistence vs weather (`src/experiments/zaf_ablation.py`, `reports/day21_zaf_ablation.md`) | ✅ committed — persistence carries the skill; weather adds none on top |
| 22 | Live dashboard state layer + app (`src/dashboard/state.py`, `app/dashboard.py`) | ✅ committed — plan status re-derived from the artefacts on disk |
| 23–26 | TBD | ⬜ not specified in the source plan |
| 27 | Model interpretation + portfolio-safe wording (`src/experiments/zaf_interpretation.py`, `reports/day27_model_interpretation.md`) | ✅ committed — coefficient mass ≠ skill; wording-to-avoid table |
| 28 | TBD | ⬜ not specified in the source plan |
| 29 | Attribution strings in the README for every used source, guarded by `tests/test_readme_attribution.py` | ✅ strings landed early and are test-enforced; the supported-claims review is part of Day 30 |
| 30 | Final summary, limitations and reproducibility checklist (`reports/day30_final_summary.md`, `reports/README.md` report index) | ✅ committed |

- **Tests:** `.venv/bin/python -m pytest -q` — **566 passing** at the Day 30
  commit. CI (`.github/workflows/tests.yml`) runs the same command on every
  push and pull request. `bash scripts/verify.sh` runs the suite **and**
  regenerates every derived artifact and report.
- **Audit highlights:** all 9 boundary files are EPSG:4326 with zero
  null/empty/invalid geometries and unit counts matching both API metadata and
  expectations; one known issue — 5 duplicate unit names in `NGA_ADM2.geojson`
  (10 duplicated match keys after normalization), resolved by point-in-polygon
  matching instead of name matching (Days 11–12).
  Weather: 50 808 hourly rows per city (2021-01-01 → 2026-10-18 UTC), zero
  gaps, zero duplicates, identical UTC grid across all nine cities.
- **Label reality check (Day 4):** South Africa has a dense national hourly
  record (Eskom MLR, 2017-04 → 2026-09), Nigeria a sparse national event
  record (grid collapses, 2023–2025 only), Kenya planned-interruption notices
  only — model scope is capped accordingly (see
  `reports/day04_outage_sources.md`).
- **Model status (Days 18–21) — read this before quoting any number:**
  the ZAF day-ahead logistic model is a **baseline model on one country**,
  trained on 1 350 walk-forward tested days, calibrated on held-out folds and
  beating the pinned climatology baseline (Brier skill **+0.794**). But the
  Day 21 ablation shows that **the skill is persistence, not weather**: label
  lags alone score Brier 0.0196, weather alone 0.1997, and weather *on top of*
  label lags adds **no** material skill (−0.0261). The honest claim is
  *persistence-driven predictability of a national daily shedding label* —
  not a weather-driven outage forecast. For Nigeria, **no model is claimed at
  all** (Day 20): the 2025 test window holds 2 positive days, so any
  classifier would be memorizing. Nothing here is a published national
  outage probability.
- **Not built yet (deliberate, not accidental):** KEN models (no unplanned
  labels exist — Day 7), a sub-national target (no sub-national labels
  exist), forecast-weather features (WX-5/6 NO-GO — ERA5 reanalysis only),
  and a live-refresh layer — see
  [`docs/project_plan.md`](docs/project_plan.md) and
  [`reports/day30_final_summary.md`](reports/day30_final_summary.md).

## Repository layout

```text
.
├── README.md                  ← this file (incl. required attributions)
├── requirements.txt           ← pinned runtime dependencies
├── requirements-dev.txt       ← + test/notebook tooling
├── pytest.ini                 ← pytest configuration
├── .github/workflows/tests.yml← CI: install + pytest on every push/PR
├── app/                       ← Streamlit dashboard (app/dashboard.py)
├── data/
│   ├── raw/                   ← verbatim downloads, never edited (git-ignored)
│   ├── external/              ← reference data: boundaries, lookups (git-ignored)
│   ├── processed/             ← cleaned/derived tables (git-ignored)
│   └── manifest.json          ← SHA-256 provenance index of every dataset file
├── docs/                      ← project plan & data governance notes
├── notebooks/                 ← Colab-compatible walkthroughs (Day 1, 2, 3)
├── reports/                   ← data inventory + one audit/analysis report per day
├── src/
│   ├── config.py              ← single source of truth for paths/runtime
│   ├── ingestion/             ← boundaries, weather, outages, KPLC, NERC, World Bank
│   ├── cleaning/              ← unit-code / name / timestamp standardization
│   ├── geo/                   ← unit registry + spatial joins (points authoritative)
│   ├── eda/                   ← weather exploratory analysis
│   ├── labels/                ← ZAF (Eskom MLR) and NGA (grid-collapse) labels
│   ├── datasets/              ← analysis-ready training frames
│   ├── features/              ← strictly-past lags, rolling windows, time splits
│   ├── models/                ← model interface, walk-forward CV, calibration
│   ├── evaluation/            ← probabilistic metrics (Brier, log loss, skill, ECE)
│   ├── experiments/           ← end-to-end model runs (ZAF day-ahead)
│   └── dashboard/             ← dashboard state/helpers (app/ frontend)
└── tests/                     ← run on every push (CI)
```

Raw and reference data under `data/` are **git-ignored** (licensing and size);
they are reproducible by re-running the ingestion CLIs below, and every
download carries a sidecar metadata file recording its license, source URL,
and retrieval time (see [`docs/data_governance.md`](docs/data_governance.md)).

## Setup

Requires **Python 3.14** (the version this project is developed and tested
against; other 3.14.x-compatible versions may work but are not the tested
baseline).

```bash
git clone <this-repo>
cd global-power-outage-risk

python3.14 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt        # runtime only
python -m pip install -r requirements-dev.txt    # runtime + pytest + Jupyter
```

`requirements.txt` is pinned to the exact versions installed in the project
virtual environment (Python 3.14.4, verified 2026-10-19 via `pip freeze`).

## Running the tests

```bash
python -m pytest -q          # latest run is the source of truth
```

Geometry tests skip automatically on a fresh clone where `data/external/`
downloads are absent, so the suite is green before any data is fetched; run
the boundary ingestion first to exercise the full suite. CI
(`.github/workflows/tests.yml`) runs the same command on every push and pull
request to `main`.

## Running the ingestion CLIs

Run modules from the repository root with the virtualenv activated:

```bash
# Day 2 — administrative boundaries (KEN/ZAF/NGA × ADM0/1/2) + audit report
python -m src.ingestion.boundaries
python -m src.ingestion.boundaries --adm ADM1        # one level only
python -m src.ingestion.boundaries --audit           # re-inspect existing files

# Day 3 — historical weather for the nine pilot cities
python -m src.ingestion.weather
python -m src.ingestion.weather --audit

# Day 4/5 — outage-record intake and World Bank indicators
python -m src.ingestion.outages --audit
python -m src.ingestion.worldbank --audit

# Day 10 — Kenya KPLC notice workspace and Nigeria NERC event extraction
python -m src.ingestion.kplc --all
python -m src.ingestion.nerc

# Day 11/12 — spatial joins and the canonical unit registry
python -m src.geo.join
python -m src.geo.units

# Day 15/17 — labels (ZAF hourly MLR, NGA grid-collapse days)
python -m src.labels.eskom
python -m src.labels.collapse_days

# Day 16/18 — training dataset and the walk-forward model
python -m src.datasets.zaf_daily
python -m src.experiments.zaf_daily_model

# Provenance index over everything under data/ (run last)
python -m src.ingestion.data_manifest
```

Downloads land in `data/` with verbatim API metadata alongside; audits are
written to `reports/dayNN_*.md`. Every command is resumable and idempotent,
and `--audit` (where available) validates without writing.

## Live dashboard

A read-only Streamlit dashboard shows the project's current state: the plan
board, files as they land, live re-validation of the downloaded data, the
pilot-city grid, and the outage label signal.

```bash
streamlit run app/dashboard.py
```

It reads the workspace on every refresh (default every 15 s) and never writes
to it. The **Live grid** tab calls the Open-Meteo forecast API for current
conditions; those values are **display-only** and must never enter training
features or labels (leakage rule, Day 18).

## Notebooks

| Notebook | Purpose |
|---|---|
| `notebooks/00_colab_setup.ipynb` | Day 1 — verify the project runs both locally and in Google Colab (runtime detection, deps, imports, data dirs, network access) |
| `notebooks/01_admin_boundaries.ipynb` | Day 2 — download and inspect administrative boundaries |
| `notebooks/02_historical_weather.ipynb` | Day 3 — download and validate historical weather for the nine pilot cities |

All three are Colab-compatible; in Colab, use **File → Save a copy in Drive**
before editing.

## Project principles

- Separate planned outages from unplanned outages.
- Preserve event times and notice publication times.
- Prevent data leakage during model training.
- Evaluate probabilistic forecasts, not accuracy alone.
- Document data sources, licenses, and geographic coverage.
- Do not publish unsupported outage probabilities.

## 30-day plan summary

The authoritative inventory and day-by-day acquisition plan live in
[`reports/data_inventory.md`](reports/data_inventory.md); the consolidated
roadmap is in [`docs/project_plan.md`](docs/project_plan.md). In short:

| Phase | Days | Focus |
|---|---|---|
| Foundations | 1–2 ✅ | Environment check, data inventory, administrative boundaries + audit |
| Data acquisition | 3–7 ✅ | Historical weather + audit, outage records/notices, World Bank indicators, source register (`reports/source_register.csv`), data audit with go/no-go per source |
| Normalization | 8–13 ✅ | Timezone-aware UTC normalization, unit registry, point-in-polygon mapping of outages to boundaries, coverage atlas (Day 13) |
| Features & leakage control | 14–19 ✅ | Leakage audit (17 vectors, 6 open guards), ZAF/NGA labels, strictly-past feature build, walk-forward CV, held-out calibration, calibration protocol + publishing rule |
| Modelling & honesty checks | 20–21 ✅ | NGA baselines (no model claimed), ZAF feature ablation (persistence vs weather) |
| Modelling & product | 22–27 ✅ | Live dashboard, model interpretation with portfolio-safe wording (KEN models intentionally not built — no unplanned labels) |
| Publication | 29–30 ✅ | Supported-claims review; final summary and reproducibility checklist (attribution strings are already in this README) |

The report index with a one-line purpose and a reproduce command for every
day lives in [`reports/README.md`](reports/README.md).

Days not yet specified are marked as TBD in `docs/project_plan.md` and will be
firmed up as the build progresses; nothing there is invented.

## Data sources & licenses

Retrieved 2026-10-19 unless noted. Per-file provenance (license, source URL,
retrieval time) is stored verbatim in
`data/external/boundaries/*_meta.json`, `data/raw/weather/_manifest.json`, and
`data/raw/worldbank/_manifest.json` (see
[`docs/data_governance.md`](docs/data_governance.md) for every provenance
convention). Licenses marked **verify** in `reports/data_inventory.md` must be
read and re-checked before any download or publication of derived data.

### Administrative boundaries (acquired, Day 2)

Downloaded from **geoBoundaries gbOpen** (<https://www.geoboundaries.org>).
The gbOpen package is distributed under **CC BY 4.0**; individual boundary
sets carry the licenses below exactly as reported by the geoBoundaries API.

| Files | License | Attribution string |
|---|---|---|
| `KEN_ADM0`, `KEN_ADM1` | Public Domain | Contains boundaries from the RCMRD GeoPortal / Africa GeoPortal via geoBoundaries gbOpen (Kenya ADM0/ADM1, 2020), public domain. |
| `KEN_ADM2` | CC BY 3.0 IGO | Contains boundaries from IEBC and OCHA ROSEA via geoBoundaries gbOpen (Kenya ADM2 sub-counties, 2020), licensed under Creative Commons Attribution 3.0 Intergovernmental Organisations (CC BY 3.0 IGO). |
| `ZAF_ADM0`, `ZAF_ADM1`, `ZAF_ADM2` | CC BY 3.0 IGO | Contains boundaries from OCHA ROSEA and the South African Municipal Demarcation Board via geoBoundaries gbOpen (South Africa ADM0–ADM2, 2020), licensed under Creative Commons Attribution 3.0 Intergovernmental Organisations (CC BY 3.0 IGO). |
| `NGA_ADM0`, `NGA_ADM1`, `NGA_ADM2` | CC BY 4.0 | Contains boundaries from GRID3 (Nigeria, 2022) via geoBoundaries gbOpen, licensed under Creative Commons Attribution 4.0 International (CC BY 4.0). |

> **Attribution notice:** Contains information from geoBoundaries
> (https://www.geoboundaries.org), © William & Mary, licensed CC BY 4.0.

### Weather (acquired, Day 3)

| Dataset | License / terms | Attribution string |
|---|---|---|
| Open-Meteo Historical Archive (ERA5 / ERA5-Land reanalysis), <https://open-meteo.com> | Free for non-commercial use — see <https://open-meteo.com/en/terms> (**verify before publishing derived data**) | Weather data by Open-Meteo (https://open-meteo.com), powered by ECMWF ERA5/ERA5-Land reanalysis. |

Raw responses are stored verbatim in `data/raw/weather/` with URL, request
parameters, SHA-256, and retrieval time in `_manifest.json`.

### Outage records (acquired, Days 4–10)

| Data | License / terms | Note |
|---|---|---|
| South Africa — Eskom hourly grid metrics incl. Manual Load Reduction (MLR), 2017-04 → 2026-09, via an unofficial Eskom mirror (`data/raw/outages/zaf_eskom_hourly.sqlite`) | ⚠️ Mirror repo carries **no license file**; underlying data is Eskom portal output (Eskom website terms apply) | Research/portfolio use; redistribution not assured — full provenance caveat in `reports/day04_outage_sources.md` §3 |
| Nigeria — grid-collapse events extracted from NERC annual reports (`data/raw/outages/nerc/events_extracted.csv`) | Public (verify per report) | Acquired Day 10 with per-event provenance; 2023–2025 only — 2019–2022 are **not labelable** (see `reports/day17_nga_labels.md`) |
| Kenya — KPLC planned-interruption notices | Government utility terms (verify) | Acquisition paths proven Day 10 (`src/ingestion/kplc.py`, `src/ingestion/kplc_parse.py`); **planned-outage analysis only** |

### World Bank outage indicators (acquired, Day 5)

Indicator series downloaded via the World Bank API for KEN/ZAF/NGA with a
`_manifest.json`; license **CC BY 4.0 per indicator (verify per indicator)**.
National context only — no locality detail, never a geolocated label
(inventory §D).

### Planned sources (not yet acquired)

Weather **forecasts**, and context/exposure layers (OSM power infrastructure,
WorldPop, night lights) are catalogued as `CANDIDATE` / `TO VERIFY` entries in
[`reports/data_inventory.md`](reports/data_inventory.md) with their licenses
flagged for verification. The forecast API is used **only** for the dashboard's
display-only live grid and must never enter historical features or labels
(leakage rule, Day 14). **No entry in that inventory may be treated as
settled until it is acquired, validated, and recorded in
`reports/source_register.csv` (Day 6).**

### Rejected / restricted sources

The inventory explicitly prefers CC BY / public-domain sources;
**GADM** is documented as restricted for redistribution and will only be used
if geoBoundaries lacks a needed level (non-commercial use only). OSM-derived
layers, if acquired, require **ODbL 1.0** attribution.

## License

The code in this repository is released under the [MIT License](LICENSE).
Data files under `data/` are **not** covered by this license — they remain
under the licenses of their respective providers listed above.
