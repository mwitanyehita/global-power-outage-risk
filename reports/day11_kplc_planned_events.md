# Day 11 (Kenya) — KPLC interruption notices → structured planned-outage events

Generated from `python -m src.ingestion.kplc_parse` (module
`src/ingestion/kplc_parse.py`).

## What was acquired

| item | value |
|---|---|
| notice list | 110 official notices (Day 10: `kplc.co.ke/customer-support#powerschedule`) |
| PDFs downloaded | 110 (51.3 MB), each with SHA-256 in `data/raw/outages/kenya/pdfs_manifest.json` |
| PDFs parsed into events | 105 |
| unparseable (image-only, no OCR available) | 5 — **missing not at random** (older scans) |
| event rows (`AREA:` blocks) | **3,740** (3,735 planned · 5 special/fault-caused) |
| date span | 2023-10-27 → 2026-10-24 (31 distinct months) |
| events per year | 2023: 1 · 2024: 384 · 2025: 1,946 · 2026: 1,399 |
| geography | 13 KPLC regions, 55 distinct county headers |
| timestamps | 3,728 rows carry a UTC start (EAT → UTC, Day 8 rule); 10 rows lack a date, 3 lack a time |

## How a notice is parsed

```
NAIROBI REGION
AREA: PART OF PARKLANDS
DATE: Sunday 04.10.2026            TIME: 9.00 A.M. – 5.00 P.M.
4th Parklands, Masari Rd, ... & adjacent customers.
```

* `AREA:` blocks become one event row each (estate/locality granularity).
* `REGION` / `COUNTY` headers above a block are attached to it.
* `notice_class = "planned"` (Rule-27 maintenance) unless the document carries
  a SPECIAL header or `was caused by` prose → `"special"`.
* Times accept `9.00 A.M.`, `9:00 a.m.`, `9.00A.M.`, `0900Hrs`; unknown
  times stay empty (never guessed).
* Overnight ranges (`10.00 P.M. – 6.00 A.M.`) keep their raw local times —
  **no date shift is invented**.
* Local time is EAT (UTC+3) → UTC via `normalize_timestamp(assume_tz="Africa/Nairobi")`.

## Spatial join outcome (Day 11 integration)

| join | result |
|---|---|
| county → KEN ADM1 | **44/55 county strings match uniquely**, 11 unresolved spellings listed for aliasing, **0 false ambiguity** |
| area → KEN ADM2 | 42 candidate matches, 3,008 unmatched — expected: areas are estates/estates & localities, not admin units |

Area strings stay free-text locality labels unless coordinates are added;
counties are Kenya's ADM1 units and join directly. Unresolved spellings are
listed explicitly rather than fuzzy-forced.

## Caveats (the honest version)

1. **These are NOT unplanned-outage labels.** KPLC publishes planned supply
   interruption notices (plus 5 fault-caused special notices). A notice is a
   *scheduled* interruption, not a measurement of unserved energy, and a
   notice can be cancelled. They must never be used as an unplanned-outage
   target, and they are never merged with the ZAF MLR or NGA collapse labels.
2. **Locality granularity, not admin units.** Areas are estates, markets and
   neighbourhoods; the county header is the only admin-level anchor, and it
   is absent in 624 of 3,740 rows.
3. **Image-only PDFs (5) are missing-not-at-random** — no OCR tool is
   available in this environment, so those notices are absent from the
   event table rather than guessed.
4. **Coverage grows over time** (1 event in 2023, 1,946 in 2025) — the
   notice archive itself expanded; event counts per year are not a measure
   of outage frequency.
5. **A small number of parse artifacts remain** (10 rows without a date,
   3 without a time, 7 rows where a boilerplate line was captured as an
   area) — under 0.5% of rows, all flagged in the source table.
6. **Third-party mirror pages were not used** for events (only the official
   KPLC domain), per the Day 10 robots/compliance probe.

## Outputs

- `data/processed/kplc_planned_outages.csv` — 3,740 rows with
  `country_iso3, region, county, area, event_date, time_start_local,
  time_end_local, event_date_time_start_utc, event_date_time_end_utc,
  notice_class, premises_text, source_document` (git-ignored, regenerable).
- `data/raw/outages/kenya/pdfs_manifest.json` — per-PDF URL + SHA-256.
- `tests/test_kplc_parse.py` — hermetic parser tests + real-PDF integration
  tests that skip when the archive is absent.
