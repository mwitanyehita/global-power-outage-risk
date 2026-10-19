# Day 6 — Source register

**Created:** 2026-10-19 · **Owner:** project · **Artifact:** `reports/source_register.csv`
(25 rows, one per source ID from `reports/data_inventory.md`: ADM-1..4, WX-1..6, OUT-1..8,
WB-1..3, CTX-1..4). The register supersedes the Day 1 status legend — every claim in it
traces to a Day 2–5 audit report.

## How the register is maintained

**Status vocabulary** (exactly these five values; a source may hold only one):

| status | Meaning | Promotion rule |
|---|---|---|
| `candidate` | Documented in the inventory; not yet downloaded or probed | → `verified` when an HTTP probe succeeds and content is inspected |
| `verified` | Reachable and content inspected; raw bytes **not** yet in `data/raw/` | → `acquired` when a download lands in the agreed folder and passes validation |
| `acquired` | Bytes in `data/raw/` (or `data/external/`), validation passed | Terminal for this run unless the source is superseded |
| `blocked` | Access denied in this environment (DNS failure, token/credential gate) | Re-probe at the scheduled follow-up day; never silently dropped |
| `deferred` | Technically viable but out of scope for this run (sub-national labels, satellite proxies) | Revisit only via a formal scope decision (Day 27 limitations log) |

**Update rules:**

- Edit the CSV only with evidence in hand: every status change cites a dated audit report,
  and `verified_on` is set to the probe/validation date (2026-10-19 for everything probed in
  Days 2–5). Sources never probed leave `verified_on` empty.
- No URL, license or coverage claim enters the register unless it appeared in a report or
  the source's own metadata file; anything unconfirmed says so in `notes` ("license text
  not read", "terms not read").
- Licenses marked unverified must be read before any download and re-checked before
  publishing derived data (per Day 1 rules).

**Re-verification cadence:**

- **Blocked sources:** re-probe at the follow-up already booked in Day 4 — EPRA (OUT-2)
  from a different network (Colab) Day 5–6, again Day 10; EskomSePush (OUT-3) at the
  Day 15 label decision if the OUT-4 national target needs supplementation.
- **Acquired API sources:** re-pull and re-validate weekly while ingestion runs
  (Open-Meteo WX-1, World Bank WB-1/WB-3); the Eskom mirror (OUT-4) is re-checked at each
  label refresh since it is the primary target's only feed.
- **Licenses:** re-verified before Day 27 (limitations) and Day 29 (README attribution);
  any per-product license (CTX-2) is confirmed at acquisition time.

**Where raw bytes live:**

| Location | Contents |
|---|---|
| `data/raw/weather/` | Open-Meteo responses verbatim + `_manifest.json` (URL, params, SHA-256, retrieval time) |
| `data/raw/outages/` | `zaf_eskom_hourly.sqlite` (Eskom mirror, 42 MB) |
| `data/raw/worldbank/` | World Bank API v2 responses verbatim |
| `data/external/boundaries/` | geoBoundaries GeoJSON + per-file `*_meta.json` |

Raw files are byte-identical to their source and never edited; all cleaning happens in
`src/` into `data/processed/`.

## Summary table

| source_id | name | status | license |
|---|---|---|---|
| ADM-1 | geoBoundaries (gbOpen) | acquired | CC BY 4.0 (package); per-file mix: Public Domain, CC BY 3.0 IGO, CC BY 4.0 |
| ADM-2 | GADM 4.x | candidate | Free academic/non-commercial; redistribution restricted (unverified) |
| ADM-3 | Natural Earth (admin-0/1) | candidate | Public domain |
| ADM-4 | OSM admin boundaries (Geofabrik/HDX) | candidate | ODbL 1.0 (attribution, share-alike) |
| WX-1 | Open-Meteo Historical Archive (ERA5) | acquired | Free for non-commercial use (terms unverified) |
| WX-2 | ERA5 / ERA5-Land via Copernicus CDS | candidate | CC BY 4.0 |
| WX-3 | NASA POWER | candidate | Free per NASA terms of use (unverified) |
| WX-4 | GHCN-Daily (NOAA NCEI) | candidate | Public domain |
| WX-5 | CHIRPS | candidate | Free for research (unverified) |
| WX-6 | TAMSAT | candidate | Free for research (unverified) |
| OUT-1 | KPLC outage notices | verified | Kenya Power utility terms (unverified) |
| OUT-2 | EPRA Kenya reports | blocked | Public (unverified) |
| OUT-3 | EskomSePush API | blocked | Freemium API; paid history tiers (unverified) |
| OUT-4 | Eskom Data Portal + mirror hourly data | acquired | No license on mirror repo; Eskom website terms apply |
| OUT-5 | City open-data portals (e.g. Cape Town) | deferred | Open data license (unverified) |
| OUT-6 | NERC Nigeria annual reports | verified | Public (unverified) |
| OUT-7 | Disco load-interruption filings | candidate | Embedded in filings (unverified) |
| OUT-8 | OCHA HDX power/outage datasets | verified | Per-dataset (CC BY typical) |
| WB-1 | Enterprise Surveys outage indicators | acquired | CC BY 4.0 (per indicator) |
| WB-2 | World Bank Data Catalog | candidate | Per-dataset |
| WB-3 | Access to electricity (SE4ALL) | acquired | CC BY 4.0 |
| CTX-1 | OSM power infrastructure | candidate | ODbL 1.0 (attribution) |
| CTX-2 | WorldPop / GHSL gridded population | candidate | CC BY 4.0 (verify per product) |
| CTX-3 | VIIRS DNB night lights (Black Marble) | deferred | Free with Earthdata account (unverified) |
| CTX-4 | Country lookup tables (ISO3/tz/region) | candidate | Project-owned |

**Counts:** 5 acquired · 3 verified · 13 candidate · 2 blocked · 2 deferred (25 total).

## Findings

- **Blocked sources (2):** **OUT-2 EPRA Kenya** — `epra.org.ke` and `www.epra.org.ke` fail
  DNS/connection from this environment (curl exit 6 / HTTP 000 on 2026-10-19); regulator
  reliability aggregates remain unavailable until a Colab re-test. **OUT-3 EskomSePush** —
  token gate: signup via Gumroad requires a human, the free plan allows ~50 requests/day,
  and historical depth is undocumented; the South Africa label therefore rests entirely on
  the acquired OUT-4 mirror. A third credential-gated option (Kaggle "SA electricity
  historical data", optional cross-check) is likewise unusable here — Kaggle credentials
  are not available in this environment (Day 4).
- **Licenses requiring attribution in the README (Day 29):** the acquired boundary files are
  a license mix, not a single license — KEN ADM0/ADM1 are Public Domain, but **KEN ADM2 and
  all ZAF files are CC BY 3.0 IGO** and **all NGA files are CC BY 4.0**. All permit reuse
  with attribution, and the geoBoundaries gbOpen package itself is CC BY 4.0, but each
  attribution string must appear verbatim. If ADM-4/CTX-1 (OSM) are ever used, **ODbL 1.0**
  adds attribution + share-alike obligations for derivatives.
- **Provenance caveat on the primary label source:** the OUT-4 sqlite comes from an
  unofficial Eskom mirror whose GitHub repo carries **no license file**; the underlying
  data is Eskom portal output. Use is limited to analysis in this portfolio — do not
  republish the raw file; cite both Eskom Data Portal and the mirror project (Day 27/29).
- **Kenya has no deep unplanned-outage history:** OUT-1 is reachable but planned-only, with
  image-only PDFs (OCR required) behind a dynamic Livewire page; this is recorded as a
  limitation, not substituted with fabricated labels. OUT-8 (HDX) was searched and holds no
  outage-event dataset for the three pilots.
- **Sparse-coverage caveats:** WB-1 survey years are a handful per country (ZAF's latest
  usable outage observation is 2020) and the Doing Business SAIDI series ended with that
  programme — both are static country-level context only, never labels or time joins.
- **Scope note:** the register covers the 25 IDs in the Day 1 inventory sections A–E.
  The two forecast sources (FC-1, FC-2) are excluded from this register because forecasts
  are never used for training labels (leakage rule, Day 1); they are governed by the
  Day 18 leakage enforcement instead.
