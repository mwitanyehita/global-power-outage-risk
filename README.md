# GridRisk AI — Global Power Outage Risk Intelligence

Probabilistic power-outage risk intelligence for **Kenya, Nigeria, and South
Africa**, built from verified outage records, weather data, official notices,
 and administrative geography. The goal is honest, well-calibrated risk
forecasts — not hype: every number published by this project must be traceable
to a documented data source.

**Status:** Day 2 of a planned 29-day build · 24 tests passing · administrative
boundaries for all three pilot countries ingested and audited.

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
| 3 | Historical weather for pilot cities (`src/ingestion/weather.py`) | 🔄 in progress |

- **Tests:** `.venv/bin/python -m pytest -q` → 24 passing (Day 2).
- **Audit highlights:** all 9 boundary files are EPSG:4326 with zero
  null/empty/invalid geometries and unit counts matching both API metadata and
  expectations; one known issue — 5 duplicate unit names in `NGA_ADM2.geojson`,
  resolved on Day 11 by point-in-polygon matching instead of name matching.
- **Not built yet:** features, models, outage labels, evaluation, or the
  Streamlit dashboard. The `src/features/`, `src/models/`,
  `src/evaluation/`, and `app/` directories are placeholders by design.

## Repository layout

```text
.
├── README.md                  ← this file (incl. required attributions)
├── requirements.txt           ← pinned runtime dependencies
├── requirements-dev.txt       ← + test/notebook tooling
├── pytest.ini                 ← pytest configuration
├── .github/workflows/tests.yml← CI: install + pytest on every push/PR
├── app/                       ← Streamlit dashboard (Day 22, not started)
├── data/
│   ├── raw/                   ← verbatim downloads, never edited (git-ignored)
│   ├── external/              ← reference data: boundaries, lookups (git-ignored)
│   └── processed/             ← cleaned/derived tables (git-ignored)
├── docs/                      ← project plan & data governance notes
├── notebooks/                 ← Colab-compatible walkthroughs (Day 1, 2, …)
├── reports/                   ← data inventory, daily audit reports
├── src/
│   ├── config.py              ← single source of truth for paths/runtime
│   ├── ingestion/             ← boundaries.py (Day 2), weather.py (Day 3, in progress)
│   ├── features/              ← (planned)
│   ├── models/                ← (planned)
│   └── evaluation/            ← (planned)
└── tests/                     ← 24 tests, run on every push (CI)
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
python -m pytest -q          # 24 passing as of Day 2
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

# Day 3 — historical weather for the nine pilot cities (in progress)
python -m src.ingestion.weather
python -m src.ingestion.weather --audit
```

Downloads land in `data/` with verbatim API metadata alongside; audits are
written to `reports/dayNN_*.md`.

## Notebooks

| Notebook | Purpose |
|---|---|
| `notebooks/00_colab_setup.ipynb` | Day 1 — verify the project runs both locally and in Google Colab (runtime detection, deps, imports, data dirs, network access) |
| `notebooks/01_admin_boundaries.ipynb` | Day 2 — download and inspect administrative boundaries |

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
| Data acquisition | 3–7 | Historical weather, outage records/notices, World Bank indicators, source register (`reports/source_register.csv`), data audit with go/no-go per source |
| Normalization | 8–11 | Timezone-aware UTC normalization, lookup tables, planned/unplanned label separation, point-in-polygon mapping of outages to boundaries |
| Features & leakage control | 16–18 | Context/exposure layers, feature build, forecast issue-time leakage rule enforced |
| Modelling & product | 22–27 | Streamlit dashboard, evaluation, documented limitations |
| Publication | 29 | Final attribution strings in this README (done early — see below), supported-claims review |

Days not yet specified are marked as TBD in `docs/project_plan.md` and will be
firmed up as the build progresses; nothing there is invented.

## Data sources & licenses

Retrieved 2026-10-19 unless noted. Per-file provenance (license, source URL,
retrieval time) is stored verbatim in
`data/external/boundaries/*_meta.json` and `data/raw/weather/_manifest.json`.
Licenses marked **verify** in `reports/data_inventory.md` must be read and
re-checked before any download or publication of derived data.

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

### Weather (Day 3 ingestion in progress)

| Dataset | License / terms | Attribution string |
|---|---|---|
| Open-Meteo Historical Archive (ERA5 / ERA5-Land reanalysis), <https://open-meteo.com> | Free for non-commercial use — see <https://open-meteo.com/en/terms> (**verify before publishing derived data**) | Weather data by Open-Meteo (https://open-meteo.com), powered by ECMWF ERA5/ERA5-Land reanalysis. |

Raw responses are stored verbatim in `data/raw/weather/` with URL, request
parameters, SHA-256, and retrieval time in `_manifest.json`.

### Planned sources (not yet acquired)

Outage records and official notices, World Bank outage indicators, weather
forecasts, and context/exposure layers (OSM power infrastructure, WorldPop,
night lights) are catalogued as `CANDIDATE` / `TO VERIFY` entries in
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
