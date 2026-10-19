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
| World Bank indicators (Day 5) | `data/raw/worldbank/_manifest.json` | Indicator code, requested URL and params, `retrieved_utc`, HTTP status, byte count |
| Outage records (from Day 4) | `data/raw/outages/{source}_meta.json` sidecar written by `src/ingestion/outages.py` | Source, license (from the source's actual terms), URL, **SHA-256**; the loader *warns* on raw files missing a sidecar |
| Scraped content (planned) | entry in the manifest/register | Source URL + retrieval timestamp (inventory §C) |

The Day 4 Eskom-mirror intake predates the sidecar convention; its provenance
(URL, row count, window, license caveat) is documented in
`reports/day04_outage_sources.md` §3 and should get a sidecar when the intake
is re-run.

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

### Acquired (Days 4–10) — outage records

| Data | License status | Note |
|---|---|---|
| South Africa: Eskom hourly grid metrics incl. Manual Load Reduction — `data/raw/outages/zaf_eskom_hourly.sqlite` (83 280 rows, 2017-04 → 2026-09) via an unofficial Eskom mirror | ⚠️ **No license file in the mirror's repo**; underlying data is Eskom portal output (Eskom's website terms apply) | Portfolio/research use only; redistribution not assured — provenance caveat documented in `reports/day04_outage_sources.md` §3, re-check before publishing derived data |
| Nigeria: grid-collapse events from NERC annual reports — `data/raw/outages/nerc/events_extracted.csv` | Public (verify per report) | Acquired Day 10 with per-event provenance (document + page); counts reconciled with NERC Table 24 |
| Kenya: KPLC planned-interruption notices — `data/raw/outages/kenya/` | Government utility terms (verify) | Acquisition paths proven Day 10; planned-outage analysis only |

### Acquired (Day 5) — World Bank indicators

Indicator JSON downloaded via the World Bank API for KEN/ZAF/NGA with
`_manifest.json` provenance. License is **CC BY 4.0 per indicator (verify per
indicator)** per inventory §D; usage remains national-context only — these
indicators have **no locality detail** and may never serve as geolocated
labels.

### Catalogued but not acquired — licenses NOT yet verified

Everything else in `reports/data_inventory.md` sections B–E (weather
forecasts, remaining outage/notice sources, context layers) is `CANDIDATE` or
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

Known coverage gaps to document, not hide (inventory §F; Day 4 interim
answers in `reports/day04_outage_sources.md`): no public machine-readable
outage history *at equal depth for all three pilots* — South Africa has a
dense national hourly record (via a no-license mirror), Nigeria sparse
national events, Kenya planned notices only. The formal go/no-go decision
per source is `reports/day07_data_audit.md` (Day 7) and binds everything
downstream. World Bank indicators have **no locality detail** (national
context only); South Africa EskomSePush history sits behind a gated/paid tier.

Label coverage that must not be overstated:

| Country | Label | Window | Caveat |
|---|---|---|---|
| South Africa | hourly load-shedding (`MLR > 0`) | 2017-04 → 2026-09 | Mirror with no license; a missing MLR value is a **data hole**, never "no shedding" |
| Nigeria | national grid-collapse day | 2023–2025 | 2019–2022 are **not labelable** — "no row" must never read as "no collapse" |
| Kenya | planned-interruption notices | depth verified Day 10 | Planned outages only — never mixed with unplanned events |

## 5. Time and label integrity

- **UTC by construction:** weather requested with `timezone=UTC`; local-time
  conversion and tz-aware UTC normalization enforced on Day 8
  (`src/cleaning/standardize.py`, no DST in the three pilot countries).
- **Planned vs unplanned** interruptions stay distinguishable at all times
  (Day 10); event start, restoration time, and notice publication time are
  stored as separate fields.
- **Leakage rule:** forecast variables carry an issue timestamp; only
  forecasts issued *before* the prediction cutoff may enter features
  (audited Day 14, enforced in the Day 18 experiment). Forecast data is never
  used for training labels.
- **Matching rule:** outages → boundaries by point-in-polygon on coordinates;
  unit names are only a secondary check (5 duplicate names in NGA ADM2).
- **"Unknown" is never "zero":** a missing label, an unsourced row or a
  non-labelable year is excluded from the frame and documented, never encoded
  as a negative class.

## 6. Publication rules

- Do not publish unsupported outage probabilities (project principle).
- Publish derived data only from sources whose licenses permit it, with the
  README attribution strings kept current.
- Keep raw snapshots (HTML/JSON + retrieval timestamps) so every published
  number is re-derivable.
