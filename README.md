# GridRisk AI — Global Power Outage Risk Intelligence

Probabilistic power-outage risk intelligence for **Kenya, Nigeria, and South
Africa**, built from verified outage records, weather data, official notices,
and administrative geography. The goal is honest, well-calibrated risk
forecasts — not hype: every number published by this project must be traceable
to a documented data source.

**Status:** Day 4 of a planned 29-day build committed (Day 5 in flight) ·
administrative boundaries and historical weather for all three pilot
countries ingested and audited.

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
- [29-day plan summary](#29-day-plan-summary)
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

- **Tests:** `.venv/bin/python -m pytest -q` — 34 tests green at the Day 3
  commit; later days land in parallel, so treat the latest local run / CI
  result as the current count and state.
- **Audit highlights:** all 9 boundary files are EPSG:4326 with zero
  null/empty/invalid geometries and unit counts matching both API metadata and
  expectations; one known issue — 5 duplicate unit names in `NGA_ADM2.geojson`,
  resolved on Day 11 by point-in-polygon matching instead of name matching.
  Weather: 50 808 hourly rows per city (2021-01-01 → 2026-10-18 UTC), zero
  gaps, zero duplicates, identical UTC grid across all nine cities.
- **Label reality check (Day 4):** South Africa has a dense national hourly
  record (Eskom MLR, 2017-04 → 2026-09), Nigeria a sparse national event
  record (grid collapses), Kenya planned-interruption notices only —
  model scope is capped accordingly (see `reports/day04_outage_sources.md`).
- **Not built yet:** labels, models, and evaluation. `src/features/` and
  `src/evaluation/` are in flight; `src/models/` is still a placeholder —
  see [`docs/project_plan.md`](docs/project_plan.md).

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
│   └── processed/             ← cleaned/derived tables (git-ignored)
├── docs/                      ← project plan & data governance notes
├── notebooks/                 ← Colab-compatible walkthroughs (Day 1, 2, 3, …)
├── reports/                   ← data inventory, daily audit reports
├── src/
│   ├── config.py              ← single source of truth for paths/runtime
│   ├── ingestion/             ← boundaries.py (Day 2), weather.py (Day 3)
│   ├── dashboard/             ← dashboard state/helpers (app/ frontend)
│   ├── features/              ← (in flight)
│   ├── models/                ← (planned)
│   └── evaluation/            ← (in flight)
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
```

Downloads land in `data/` with verbatim API metadata alongside; audits are
written to `reports/dayNN_*.md`.

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

Both are Colab-compatible; in Colab, use **File → Save a copy in Drive** before
editing.

## Project principles

- Separate planned outages from unplanned outages.
- Preserve event times and notice publication times.
- Prevent data leakage during model training.
- Evaluate probabilistic forecasts, not accuracy alone.
- Document data sources, licenses, and geographic coverage.
- Do not publish unsupported outage probabilities.

## 29-day plan summary

The authoritative inventory and day-by-day acquisition plan live in
[`reports/data_inventory.md`](reports/data_inventory.md); the consolidated
roadmap is in [`docs/project_plan.md`](docs/project_plan.md). In short:

| Phase | Days | Focus |
|---|---|---|
| Foundations | 1–2 ✅ | Environment check, data inventory, administrative boundaries + audit |
| Data acquisition | 3–7 (3–4 ✅) | Historical weather + audit, outage records/notices, World Bank indicators, source register (`reports/source_register.csv`), data audit with go/no-go per source |
| Normalization | 8–11 | Timezone-aware UTC normalization, lookup tables, planned/unplanned label separation, point-in-polygon mapping of outages to boundaries |
| Features & leakage control | 16–18 | Context/exposure layers, feature build, forecast issue-time leakage rule enforced |
| Modelling & product | 22–27 | Streamlit dashboard, evaluation, documented limitations |
| Publication | 29 | Final attribution strings in this README (done early — see below), supported-claims review |

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

### Outage records (Day 4, partial acquisition)

| Data | License / terms | Note |
|---|---|---|
| South Africa — Eskom hourly grid metrics incl. Manual Load Reduction (MLR), 2017-04 → 2026-09, via an unofficial Eskom mirror (`data/raw/outages/zaf_eskom_hourly.sqlite`) | ⚠️ Mirror repo carries **no license file**; underlying data is Eskom portal output (Eskom website terms apply) | Research/portfolio use; redistribution not assured — full provenance caveat in `reports/day04_outage_sources.md` §3 |
| Nigeria — grid-collapse incidents extractable from NERC annual reports | Public (verify per report) | Investigated Day 4, not yet downloaded |
| Kenya — KPLC planned-interruption notices | Government utility terms (verify) | Image-only PDFs (OCR needed); depth to verify Day 10 |

### World Bank outage indicators (Day 5 acquisition in flight)

Indicator series downloaded via the World Bank API for KEN/ZAF/NGA with a
`_manifest.json`; license **CC BY 4.0 per indicator (verify per indicator)**.
National context only — no locality detail, never a geolocated label
(inventory §D).

### Planned sources (not yet acquired)

Weather **forecasts**, and context/exposure layers (OSM power infrastructure,
WorldPop, night lights) are catalogued as `CANDIDATE` / `TO VERIFY` entries in
[`reports/data_inventory.md`](reports/data_inventory.md) with their licenses
flagged for verification. **No entry in that inventory may be treated as
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
