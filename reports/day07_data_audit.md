# Day 7 — Data audit: go / no-go per source

Inputs: `reports/data_inventory.md` (Day 1), audits `day02`–`day05`, the source
register `reports/source_register.csv` (Day 6), and the outage investigation
`day04_outage_sources.md`. Decisions below are the contract for Week 2–3: any
source marked **NO-GO** must not appear in features, labels or claims.

Status vocabulary: **GO** = acquired and validated · **GO*** = conditional
(go when the stated condition is met) · **NO-GO** = excluded, with reason.

---

## 1. Decisions per source

| ID | Source | Decision | Evidence / condition |
|---|---|---|---|
| ADM-1/2/3 | geoBoundaries gbOpen ADM0–2 (KEN/ZAF/NGA) | **GO** | 9/9 files pass count/CRS/validity/bbox checks (`day02`); licenses: Public Domain (KEN ADM0/1), CC BY 3.0 IGO (KEN ADM2, ZAF), CC BY 4.0 (NGA) — attribution required in README |
| ADM-4 | GADM | **NO-GO** | Restricted-redistribution license; geoBoundaries covers the same need |
| WX-1 | Open-Meteo ERA5 archive, 9 cities | **GO** | 50,808 hourly rows × 9 cities, exact grid, 0 gaps/duplicates/nulls (`day03`); terms are non-commercial — research/portfolio use only |
| WX-2/3/4 | ERA5-Land direct / national met services / Meteostat | **NO-GO (this run)** | Redundant with WX-1 at pilot scale; ERA5-Land direct needs CDS credentials |
| WX-5/6 | Open-Meteo forecast / NOWCAST | **GO\*** (future) | Only for *live* refresh after Day 26; must never touch historical labels (leakage: forecasts issued after the fact are not features) |
| OUT-1 | KPLC planned interruption notices | **GO\*** | Reachable (HTTP 200), but notices are image PDFs / Livewire — condition: a machine-readable path proven on Day 10 (probe running). Use is **planned-outage analysis only** |
| OUT-2 | EPRA Kenya | **NO-GO (blocked)** | DNS/connection failure from this environment; re-probe from Colab (Day 5 task carried forward). Do not cite unverified figures |
| OUT-3 | EskomSePush API | **NO-GO (blocked)** | Token gate (50 req/day free tier) — no automated access. Becomes GO only if a human supplies a token; not required for the current target |
| OUT-4 | Eskom data (Data Portal + mirror sqlite) | **GO** | 83,280 hourly rows 2017-04 → 2026-09 incl. `Manual Load_Reduction(MLR)`; validated against the known shedding calendar year-by-year (`day04`). Provenance caveat: mirror repo has no license → analyse in-project, **do not republish raw**, cite Eskom Data Portal + mirror |
| OUT-5 | Municipal load-reduction portals (CoCT etc.) | **NO-GO (deferred)** | Only needed for sub-national ZAF labels; national MLR suffices for the prediction unit |
| OUT-6 | NERC Nigeria (annual reports) | **GO\*** | 2025 report text-extractable with dated collapse tables (Tables 24/25/E); condition: extraction from 2019–2024 reports completes with counts reconciled against Table 24 (Day 10, in progress). Monthly factsheets are image-only → excluded |
| OUT-7 | DisCo tariff filings | **NO-GO** | PDF-heavy, feeder-level detail not parseable at portfolio timescale; national collapse events cover the target |
| OUT-8 | HDX / third-party trackers (outageke, kplc.project.co.ke) | **GO\*** | HDX: no outage datasets for pilots (searched, `day04`) — GO only for the KPLC mirror *if* Day 10 probe confirms structured, robots-compliant history depth; otherwise NO-GO |
| WB-1 | Enterprise Surveys outage indicators (WB API) | **GO (context only)** | 8/10 indicators carry pilot data (`day05`); country-year, survey-sparse → static context columns, **never labels** |
| WB-2 | World Bank Enterprise Surveys firm micro-data | **NO-GO (this run)** | Requires registration; aggregates already available via WB-1 |
| WB-3 | Access-to-electricity WDI indicators | **GO (context only)** | Annual 2000–2024 for all pilots (`day05`) |
| CTX-1 | OSM power infrastructure (power towers/lines/substations) | **GO\*** | Overpass API, ODbL attribution; condition: kept coarse (counts per ADM1) — line-level geometry parsing is out of scope |
| CTX-2 | World Bank governance/doing-business context | **GO (context only)** | Via WB API; country-year static |
| CTX-3 | VIIRS night lights | **NO-GO** | Earthdata/EE accounts required; documented as extension (Day 27), not substituted |
| CTX-4 | Country lookup (ISO3/timezone/region) | **GO** | Built in-project, `src/cleaning/standardize.py` (`COUNTRIES`), tested Day 8 |
| FC-1/2 | ECMWF/GFS forecasts | **NO-GO (this run)** | Excluded from the register scope (see Day 6 note); revisit only for the live-refresh story |

## 2. Label strategy locked (input to Days 15/17)

1. **South Africa — primary target.** National daily/hourly "load-shedding active"
   from `MLR > 0` (+ MW intensity), 2017-04 → 2026-09 (GO: OUT-4). Dense positives.
2. **Nigeria — secondary target (sparse national events).** Dated grid-collapse
   days from NERC annual reports (GO\*: OUT-6). Honest rare-event treatment;
   PR-AUC against a prevalence baseline, never accuracy.
3. **Kenya — planned-outage analysis + geography case study.** No deep unplanned
   history is available (GO\*: OUT-1). **Planned notices are never merged into
   unplanned labels** (README principle); the gap is stated as a limitation,
   not filled with invented data.
4. Enterprise Surveys / WDI indicators are **context**, never targets.

## 3. Cross-cutting risks accepted

| Risk | Mitigation (day) |
|---|---|
| Name ambiguity (5 duplicate LGA names in NGA_ADM2) | point-in-polygon joins, Day 11; name match only secondary |
| Naive timestamps in notices (EAT/WAT) | explicit `assume_tz` in `normalize_timestamp`, Day 8 — done |
| Third-party Eskom mirror provenance/no license | analyse in-project, no raw republication, README attribution, Day 27 + 29 |
| Sparse positives for NGA | time-based CV (Day 18), calibration (Day 19), prevalence baseline (Day 20) |
| EPRA/ESP/Kaggle blocked | documented NO-GO; no substitute data invented |

## 4. Go/no-go tally

**GO: 8** (ADM-1/2/3, WX-1, OUT-4, WB-1, WB-3, CTX-1, CTX-2, CTX-4 — counting WB-1/WB-3 as context) ·
**GO\* conditional: 4** (OUT-1, OUT-6, OUT-8, WX-5/6 future-only) ·
**NO-GO: 9** (ADM-4, WX-2/3/4, OUT-2, OUT-3, OUT-5, OUT-7, WB-2, CTX-3, FC-1/2).

Every NO-GO has a written reason above and in the register; none will be
silently re-introduced later in the pipeline.
