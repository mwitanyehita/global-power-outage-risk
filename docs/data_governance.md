# Data governance — sources, licenses, coverage, provenance

How GridRisk AI records where data came from, what it may be used for, and how
far it covers. Every rule below already exists in
`reports/data_inventory.md`, the Day 2 audit, or the `src/` ingestion modules;
this note consolidates them. **Nothing may be published (including derived
data) unless its source row here and in the source register is complete.**

## 1. Folder conventions

| Folder | Contents | Rule |
|---|---|---|
| `data/raw/` | Verbatim downloads (API responses, scraped pages) | Byte-identical to source, never edited |
| `data/external/` | Reference data: administrative boundaries, lookups | Verbatim; sidecar metadata per file |
| `data/processed/` | Cleaned and derived tables built by `src/` modules | Reproducible from raw/external + code |

All three are **git-ignored** (licensing and file size); only `.gitkeep`
placeholders are tracked. Fresh-clone reproducibility comes from re-running the
ingestion CLIs, not from committed data.

## 2. Provenance recording

Every acquisition writes its provenance next to the bytes:

| Data | Provenance file | Recorded fields |
|---|---|---|
| Administrative boundaries (Day 2) | `data/external/boundaries/{ISO3}_{ADM}_meta.json` | Verbatim geoBoundaries API metadata — `boundaryLicense`, `boundarySource`, `boundarySourceURL`, `boundaryYearRepresented`, `admUnitCount`, `gjDownloadURL` — plus `_api_url` and `_retrieved_utc` added by `src/ingestion/boundaries.py` |
| Historical weather (Day 3) | `data/raw/weather/_manifest.json` | Requested URL and parameters (incl. `timezone=UTC`), `retrieved_utc`, HTTP status, byte count, **SHA-256** per file |
| Scraped content (planned) | entry in the manifest/register | Source URL + retrieval timestamp (inventory §C) |

Machine-readable audits summarize these into `reports/dayNN_*.md`
(currently `day02_boundaries_audit.md`; generated — do not hand-edit).

The full register — one row per **acquired** dataset with license text,
verification date, download date, spatial/temporal coverage, raw path, row
count, intended use, and known limitations — is built on **Day 6** as
`reports/source_register.csv` with this schema (inventory §H):

```text
source_id, name, provider, url, license, license_verified_on, retrieved_on,
coverage_countries, spatial_detail, temporal_detail, raw_path, row_count,
intended_use, known_limitations
```

## 3. License status of what is currently held

### Acquired (Day 2) — administrative boundaries, geoBoundaries gbOpen

| Files | License | Upstream source | Year |
|---|---|---|---|
| `KEN_ADM0`, `KEN_ADM1` | Public Domain | RCMRD GeoPortal / Africa GeoPortal | 2020 |
| `KEN_ADM2` | CC BY 3.0 IGO | IEBC, OCHA ROSEA (HDX) | 2020 |
| `ZAF_ADM0/1/2` | CC BY 3.0 IGO | OCHA ROSEA, SA Municipal Demarcation Board | 2020 |
| `NGA_ADM0/1/2` | CC BY 4.0 | GRID3 | 2022 |

The geoBoundaries gbOpen package itself is **CC BY 4.0** (© William & Mary).
Required attribution strings live in the README *Data sources & licenses*
section (project requirement: present by Day 29 — landed early).

### Acquired (Day 3) — weather

Open-Meteo Historical Archive (ERA5/ERA5-Land): nine pilot cities, hourly,
2021-01-01 → 2026-10-18 UTC; all 9 pass the timestamp audit
(`reports/day03_weather_audit.md`). Free for **non-commercial** use under
<https://open-meteo.com/en/terms>; marked *verify* in the inventory — must be
re-read before publishing derived data or any commercial use.

### Catalogued but not acquired — licenses NOT yet verified

Everything in `reports/data_inventory.md` sections B–E (outage records,
World Bank indicators, forecasts, context layers) is `CANDIDATE` or
`TO VERIFY`. Rules:

- License marked **verify** → read the terms *before* download, re-check
  before publishing derived data.
- Preference order for boundaries/sources: CC BY / public domain first.
  **GADM** is restricted for redistribution (use only if a level is missing
  elsewhere, non-commercial); **OSM** layers carry **ODbL 1.0** attribution and
  share-alike obligations; paid/API-gated sources (e.g. EskomSePush history)
  follow their ToS.
- `reports/source_register.csv` is the gate: a source that never graduates
  from the inventory is never used.

## 4. Geographic coverage

Pilot countries: **Kenya, Nigeria, South Africa** — all three present at every
ingested level (see audit table in `reports/day02_boundaries_audit.md`):

- KEN: ADM0 = 1, ADM1 = 47 counties, ADM2 = 290 sub-counties
- NGA: ADM0 = 1, ADM1 = 37 states + FCT, ADM2 = 774 LGAs
- ZAF: ADM0 = 1, ADM1 = 9 provinces, ADM2 = 52 district municipalities

Weather coverage: three cities per country defined in `CITIES`
(`src/ingestion/weather.py`) — Nairobi/Mombasa/Kisumu, Johannesburg/Cape
Town/Durban, Lagos/Abuja/Kano — hourly from 2021-01-01, UTC.

Known coverage gaps to document, not hide (inventory §F): no confirmed public
machine-readable outage history for all three pilots (Day 7 go/no-go);
World Bank indicators have **no locality detail** (national context only);
South Africa outage history may be limited by paid API tiers.

## 5. Time and label integrity

- **UTC by construction:** weather requested with `timezone=UTC`; local-time
  conversion deferred to Day 8; tz-aware UTC normalization enforced on Day 8
  (no DST in the three pilot countries).
- **Planned vs unplanned** interruptions stay distinguishable at all times
  (Day 10); event start, restoration time, and notice publication time are
  stored as separate fields.
- **Leakage rule:** forecast variables carry an issue timestamp; only
  forecasts issued *before* the prediction cutoff may enter features
  (enforced Day 18). Forecast data is never used for training labels.
- **Matching rule:** outages → boundaries by point-in-polygon on coordinates;
  unit names are only a secondary check (5 duplicate names in NGA ADM2).

## 6. Publication rules

- Do not publish unsupported outage probabilities (project principle).
- Publish derived data only from sources whose licenses permit it, with the
  README attribution strings kept current.
- Keep raw snapshots (HTML/JSON + retrieval timestamps) so every published
  number is re-derivable.
