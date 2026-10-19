# Project data inventory

**Created:** Day 1 · **Owner:** project · **Status legend:** `CANDIDATE` (found, not yet
downloaded) · `TO VERIFY` (entry unconfirmed — check URL/license before use) · `TO ACQUIRE`
(confirmed source, download scheduled) · `REJECTED` (documented, will not use)

This inventory is the register of every dataset the project may use. Each entry is
promoted to *acquired* only after its download lands in the agreed folder and passes
validation (Days 2–5). Licenses marked **verify** must be read before any download and
re-checked before publishing derived data.

**Folder convention**

| Folder | Contents |
|---|---|
| `data/raw/` | Verbatim downloads, byte-identical to source, never edited |
| `data/external/` | Reference data: boundaries, country/region lookups |
| `data/processed/` | Cleaned and derived tables built by `src/` modules |

---

## A. Administrative boundaries (Day 2)

| ID | Source | Provider | Coverage | Detail | License | Access | Status |
|---|---|---|---|---|---|---|---|
| ADM-1 | geoBoundaries | William & Mary / geoBoundaries (open) | Global, per country | ADM0–ADM3, GeoJSON/Shapefile | CC BY 4.0 (verify) | `https://www.geoboundaries.org` / API, no key | CANDIDATE |
| ADM-2 | GADM 4.x | UC Davis | Global | ADM0–ADM3, GeoPackage | Free for academic/non-commercial; **redistribution restricted (verify)** | `https://geodata.ucdavis.edu`, no key | CANDIDATE |
| ADM-3 | Natural Earth (admin-0, admin-1) | NACIS / Natural Earth | Global | Country + first-level units only | Public domain | `https://www.naturalearthdata.com`, no key | CANDIDATE |
| ADM-4 | OSM admin boundaries via Geofabrik/HDX | OpenStreetMap contributors | Per country extracts | Varies by region | ODbL 1.0 (attribution, share-alike) | `https://download.geofabrik.de` | CANDIDATE |

**Preference order:** geoBoundaries (license clarity, consistent ADM levels) → Natural Earth
(admin-1 only) → GADM (only if geoBoundaries lacks a needed level, non-commercial use only).
**Decision due Day 2 end.**

**Pilot countries:** Kenya (KEN), South Africa (ZAF), Nigeria (NGA). Download all three for
each chosen source so comparison is possible.

---

## B. Weather (Days 3, 16)

### Historical (features + label construction)

| ID | Source | Provider | Resolution | Period | License | Access | Status |
|---|---|---|---|---|---|---|---|
| WX-1 | Open-Meteo Historical Archive (ERA5/ERA5-Land reanalysis) | Open-Meteo / ECMWF | Hourly, point or grid | 1940–present | Free for non-commercial use (verify terms) | HTTPS API, no key | CANDIDATE |
| WX-2 | ERA5 / ERA5-Land | Copernicus CDS | Hourly, ~0.25°/0.1° grid | 1940–present | CC BY 4.0 | CDS API, free account | CANDIDATE |
| WX-3 | NASA POWER | NASA SEDAC | Hourly/daily point API, 0.5° | 1981–present | Free, no key (terms of use) | HTTPS API | CANDIDATE |
| WX-4 | GHCN-Daily | NOAA NCEI | Daily station observations | 1900s–present | Public domain | FTP/HTTPS download | CANDIDATE |
| WX-5 | CHIRPS | UC Santa Barbara / Climate Hazards Center | Daily, 0.05°, 55°S–50°N | 1981–present | Free for research (verify) | `https://data.chc.ucsb.edu` | CANDIDATE |
| WX-6 | TAMSAT | University of Reading | Daily, 0.0375°, Africa | 1983–present | Free for research (verify) | `https://research.reading.ac.uk` | CANDIDATE |

**Working plan:** point-based hourly history from **WX-1** for pilot cities (fast, keyless),
with **WX-2/WX-4** used later to validate timestamps and values (Day 3). Rainfall-specific
sources (WX-5/WX-6) only if convective rain proves important for the pilot regions.

### Forecast (usable only for *future* prediction, never for training labels)

| ID | Source | Provider | Resolution | Horizon | License | Access | Status |
|---|---|---|---|---|---|---|---|
| FC-1 | Open-Meteo Forecast API (ECMWF/GFS/mix) | Open-Meteo | Hourly point forecast | Up to 16 days | Free non-commercial (verify) | HTTPS API, no key | CANDIDATE |
| FC-2 | NOAA GFS | NOAA / NOMADS | Global grid | 16 days | Public domain | NOMADS/THREDDS | CANDIDATE |

**Leakage rule:** forecast variables carry an issue timestamp; only forecasts issued *before*
the prediction cutoff may enter features (enforced Day 18).

---

## C. Outage records and official notices (Days 4, 10, 17)

The label source. Everything else fails if this is weak — hence it is investigated early.

| ID | Source | Country | What it holds | Granularity | License / terms | Access | Status |
|---|---|---|---|---|---|---|---|
| OUT-1 | Kenya Power (KPLC) outage notices / outage map | Kenya | Planned interruption notices, restoration updates | Location names (feeder/area), times | Government utility terms (verify) | Web scraping / public pages | TO VERIFY |
| OUT-2 | EPRA Kenya reports and performance statistics | Kenya | Regulator reliability metrics | National / utility-level, quarterly | Public (verify) | `https://www.epra.org.ke` | TO VERIFY |
| OUT-3 | EskomSePush API | South Africa | Load-shedding stages, historical block schedule, notices | Suburb/area and metro level | Freemium API, paid tiers for history (verify pricing/ToS) | REST API, key required | TO VERIFY |
| OUT-4 | Eskom Dataviz / official load-reduction notices | South Africa | Stage history, planned maintenance | National/metro | Public web (verify) | `https://dataviz.eskom.co.za` and Eskom site | TO VERIFY |
| OUT-5 | City open-data portals (e.g. City of Cape Town) | South Africa | Load-reduction schedules/incidents | Municipal areas | Open data license (verify) | Portal CSV/API | TO VERIFY |
| OUT-6 | NERC Nigeria reports and orders | Nigeria | Regulator reliability/tariff filings, interruptions | National/zone, periodic | Public (verify) | `https://nerc.gov.ng` | TO VERIFY |
| OUT-7 | Distribution company (Disco) load-interruption filings | Nigeria | Feeders, interruption minutes | Disco/feeder | Embedded in filings (verify) | NERC/Discos, PDF | TO VERIFY |
| OUT-8 | OCHA HDX — power/outage datasets for pilot countries | Pan-Africa | Compiled outage/energy datasets | Mixed | Per-dataset license (CC BY typical) | `https://data.humdata.org` | CANDIDATE |

**Rules for this category (from README principles):**

- Keep *planned* and *unplanned* interruptions distinguishable at all times (Day 10).
- Store event start, restoration time, **and** notice publication time separately.
- Scraped content goes to `data/raw/` with the source URL and retrieval timestamp.

---

## D. World Bank and survey-based outage indicators (Day 5)

| ID | Source | Provider | What it holds | Granularity | License | Access | Status |
|---|---|---|---|---|---|---|---|
| WB-1 | Enterprise Surveys — power outage indicators (e.g. frequency of outages, losses from outages) | World Bank | Firm-reported outage experience | Country × year × sector — **no coordinates** | CC BY 4.0 (verify per indicator) | `https://indicator.enterprise-surveys.org` / API | CANDIDATE |
| WB-2 | World Bank Data Catalog — search "outage"/"electricity reliability" | World Bank | Any additional published series | Mixed | Per-dataset | `https://datacatalog.worldbank.org` | CANDIDATE |
| WB-3 | Access to electricity (SE4ALL / Sustainable Energy for All) | World Bank | National access rates (context, **not** outage labels) | Country-year | CC BY 4.0 | World Bank API | CANDIDATE |

**Expected limitation (check on Day 5):** WB-1 is survey-based and aggregated above locality
level — it can serve as a *national context feature* or qualitative cross-check, never as a
geolocated label. Geographic coverage of the three pilots must be recorded (which survey year
exists for KEN/ZAF/NGA).

---

## E. Context and exposure layers (Days 16, 22+)

| ID | Source | Provider | What it holds | License | Access | Status |
|---|---|---|---|---|---|---|
| CTX-1 | OSM power infrastructure (plants, substations, lines) | OpenStreetMap contributors | Grid exposure features | ODbL 1.0 (attribution) | Overpass API / Geofabrik | CANDIDATE |
| CTX-2 | WorldPop / GHSL gridded population | WorldPop (Univ. of Southampton) / JRC | Population at risk per area | CC BY 4.0 (verify per product) | `https://www.worldpop.org` / GHS portal | CANDIDATE |
| CTX-3 | VIIRS DNB night lights (Black Marble) | NASA Earthdata | Possible post-event outage proxy | Free, Earthdata account | Earthdata / Google Earth Engine | TO VERIFY |
| CTX-4 | Country lookup tables (ISO3, timezone, region) | Built in-project | Standardization | Project-owned | built locally, Day 8 | TO ACQUIRE |

---

## F. Known dead ends / risks to check early

| Risk | Why it matters | Mitigation |
|---|---|---|
| No public, machine-readable outage history for all three pilots | Caps model ambition (Day 7 decision) | Investigate Day 4; fall back to a documented subset of countries or to notice-based labels |
| EskomSePush history behind a paid tier | South Africa labels may be limited | Confirm free-tier depth on Day 4; consider stage-level (national) labels instead |
| Scraped utility pages change format / disappear | Reproducibility | Raw HTML/JSON snapshots with retrieval timestamps in `data/raw/` |
| World Bank indicators lack locality detail | Cannot label locations | Use as context feature only; documented on Day 5 and Day 27 |
| License incompatibility with publishing derived data (GADM, OSM, paid APIs) | Portfolio must be reproducible | Prefer CC BY / public-domain sources; attribute ODbL/CC BY in README (Day 29) |
| Timestamps without time zones | All label/feature alignment breaks | Enforce tz-aware UTC normalization on Day 8 |

---

## G. Day-by-day acquisition plan

| Day | Task | Inventory IDs touched |
|---|---|---|
| 2 | Administrative boundaries for KEN, ZAF, NGA | ADM-1 → ADM-4, decide |
| 3 | Historical weather for pilot cities, validate timestamps | WX-1 (primary), WX-2/WX-4 (check) |
| 4 | Outage records and official notices | OUT-1 → OUT-8 |
| 5 | World Bank outage indicators, coverage check | WB-1 → WB-3 |
| 6 | Fill licenses, dates, location detail, limitations per source | all |
| 7 | Data audit report; go/no-go per source | all |

## H. Source register summary

The full register — one row per **acquired** dataset with license text, download date,
spatial/temporal coverage and stated limitations — is built on Day 6 as
`reports/source_register.csv` (schema below) from this inventory.

```text
source_id, name, provider, url, license, license_verified_on, retrieved_on,
coverage_countries, spatial_detail, temporal_detail, raw_path, row_count,
intended_use, known_limitations
```
