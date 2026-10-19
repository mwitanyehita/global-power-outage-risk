# `data/` — layout, provenance conventions, regeneration

Everything under `data/raw/`, `data/processed/` and `data/external/` is
**git-ignored** reference data (only the `.gitkeep` files are tracked). This
README and `manifest.json` live at the `data/` root and are the two tracked
files here. Source licences and citation requirements are registered in
`reports/data_inventory.md`.

> `manifest.json` is a **reproducibility snapshot**: it records the SHA-256 of
> every dataset file, so anyone who re-downloads the same sources can verify
> they got identical bytes. It is regenerated on every ingestion run and so
> shows a diff (`generated_utc` plus new/changed hashes) each time — read that
> diff before committing, and do not hand-edit it.

## Layout

```
data/
├── README.md                 this file (tracked)
├── manifest.json             provenance index of every dataset file (tracked,
│                             regenerate after any ingestion run)
├── raw/                      verbatim source responses — never edited in place
│   ├── weather/              Open-Meteo archive, one {slug}.json per city +
│   │                         _manifest.json            → python -m src.ingestion.weather
│   ├── worldbank/            World Bank API, one {indicator}.json per series +
│   │                         _manifest.json            → python -m src.ingestion.worldbank
│   └── outages/              outage intake: {source}.json + {source}_meta.json
│       │                                                  → python -m src.ingestion.outages (intake side)
│       ├── kenya/            KPLC planned-notice crawl workspace (Day 10)
│       │                                                  → python -m src.ingestion.kplc
│       └── nerc/             NERC annual-report PDFs + events_extracted.csv
├── processed/                canonical, analysis-ready outputs (+ sidecars)
│   ├── weather_hourly.csv    one tidy hourly frame, all 9 cities, tz-aware UTC
│   │                                                  → python -m src.ingestion.weather_canonical
│   ├── outages.csv           canonical outage records (planned/unplanned kept)
│   │                                                  → python -m src.ingestion.outages
│   └── unit_registry.csv     admin-unit name ↔ boundary id mapping
└── external/                 static reference data (not time series)
    └── boundaries/           geoBoundaries {ISO3}_{ADM}.geojson + _meta.json
                                                           → python -m src.ingestion.boundaries
```

## Provenance conventions

Three layers, all JSON, all UTC ISO-8601 timestamps:

1. **Per-file sidecar** `{stem}_meta.json` next to the dataset file — written
   by the module that wrote the file: source, licence, retrieval time, record
   count and `sha256` of the bytes. Example: `KEN_ADM1.geojson` →
   `KEN_ADM1_meta.json`; `outages.csv` → `outages_meta.json`.
2. **Per-directory manifest** `_manifest.json` — filename → request metadata
   (URL, params, HTTP status, `sha256`) for directory-scoped downloaders:
   `raw/weather/`, `raw/worldbank/`.
3. **Aggregate index** `data/manifest.json` — lists every dataset file with
   byte size, SHA-256 and the provenance merged from layers 1–2. Rebuild it
   after any ingestion run:

   ```bash
   .venv/bin/python -m src.ingestion.data_manifest
   ```

Rules the ingestion modules follow (see `src/ingestion/utils.py`):

* raw bytes are written atomically (`.part` + rename) — a truncated download
  never lands under a final filename;
* sidecars are written **after** the bytes validate, so a sidecar's `sha256`
  always describes the file next to it;
* re-runs are idempotent: valid existing files are skipped, corrupt leftovers
  are re-downloaded (boundaries) or reported as warnings (weather/outages).

## Regenerating everything

| Command | Writes |
|---|---|
| `python -m src.ingestion.boundaries` | `external/boundaries/` + audit report |
| `python -m src.ingestion.weather` | `raw/weather/` + audit report |
| `python -m src.ingestion.weather_canonical` | `processed/weather_hourly.csv` + sidecar |
| `python -m src.ingestion.worldbank` | `raw/worldbank/` + coverage report |
| `python -m src.ingestion.outages` | `processed/outages.csv` + sidecar (reads `raw/outages/*.{json,csv}`) |
| `python -m src.ingestion.kplc --all` | `raw/outages/kenya/` crawl workspace |
| `python -m src.ingestion.data_manifest` | `manifest.json` (run last) |

Every command is resumable and idempotent; `--audit` (where available)
validates without writing.

## Notes for consumers

* Nested workspaces under `raw/outages/` (`kenya/`, `nerc/`) are
  **source-specific crawl outputs** — they are not yet wired into the
  canonical outage intake. `python -m src.ingestion.outages` only reads
  top-level `raw/outages/*.{json,csv}` files written by adapters via
  `write_raw_intake()` (which also drops the `{source}_meta.json` sidecar).
  Wire a new source by implementing `OutageAdapter` (documented in
  `src/ingestion/outages.py`), not by editing `outages.csv` by hand.
* All processed timestamps are tz-aware UTC; naive source times are converted
  but flagged as warnings at intake — see the sidecar's `validation` block.
* `planned` and `unplanned` outages are distinct vocabularies everywhere and
  must never be merged downstream.
* Tests for the ingestion layer: `python -m pytest tests/test_ingestion_*.py`
  (offline, no downloads).
