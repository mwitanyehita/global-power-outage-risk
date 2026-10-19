# Day 10 — Nigeria: NERC annual-report grid-collapse events

**Status:** ✅ 5 annual reports acquired · 14 dated incidents extracted (2023–2025) ·
tests green (`13 passed`)

All URLs probed and downloaded on **2026-10-19** from this project's environment.

---

## 1. What was sought vs. what exists

Goal: the official, dated record of Nigerian national grid collapses from NERC
(*Nigerian Electricity Regulatory Commission*) annual reports, covering 2019–2025.

**Discovery route**

| Step | Endpoint | Result |
|---|---|---|
| 1 | `https://nerc.gov.ng/resource-category/nerc-reports/` (pages 1–7) | annual reports **2022, 2023, 2024, 2025** + quarterly reports |
| 2 | `https://nerc.gov.ng/?s=annual+report` (WP search) | same four reports |
| 3 | `https://nerc.gov.ng/resources/?…` (doc_year/doc_publication filters) | filters are client-side — no extra PDFs |
| 4 | Wayback CDX index of `nerc.gov.ng/wp-content/uploads/*` | **2020** report (`Annual_Report_2020_Final edit.pdf`, still live); 2017/2018 reports |
| 5 | Archived pre-2023 repository (`…/component/remository/NERC-Reports/`, snapshots 2019 & 2022) | listing contains annual reports **2017, 2018, 2020 only** |

**Finding: NERC never published annual reports for 2019 and 2021.** The old
repository listing (archived) and the current resource pages both lack them, and no
candidate PDF exists in the Wayback CDX index under any `annual*`/`*annual report*`
naming. So the requested "2019–2024 set" is acquirable only as
**2020, 2022, 2023, 2024** (+ 2025, already identified on Day 4).

---

## 2. Acquisition

All files verbatim (bytes untouched) under `data/raw/outages/nerc/`, named
`{report_year}_annual_report.pdf`. Existing files are skipped unless `--overwrite`.

| Report year | Source URL | Size | Pages | Extractable text | Image-only? |
|---|---|---:|---:|---:|---|
| 2020 | `https://nerc.gov.ng/wp-content/uploads/2022/08/Annual_Report_2020_Final%20edit.pdf` | 6.2 MB | 196 | 341,599 chars | no |
| 2022 | `https://nerc.gov.ng/wp-content/uploads/2025/09/2022_Annual_Report.pdf` | 3.9 MB | 159 | 275,775 chars | no |
| 2023 | `https://nerc.gov.ng/wp-content/uploads/2024/09/2023_Annual_Report.pdf` | 5.5 MB | 188 | 274,283 chars | no |
| 2024 | `https://nerc.gov.ng/wp-content/uploads/2025/07/2024-Annual-Report-1.pdf` | 9.6 MB | 211 | 326,620 chars | no |
| 2025 | `https://nerc.gov.ng/wp-content/uploads/2026/08/2025-Annual-Report_Pub.pdf` | 11.1 MB | 216 | 322,582 chars | no |

**Image-only PDFs: none.** Every report has a usable text layer, so no OCR was needed
(the image-only problem remains specific to the KPLC/Kenya sources and NERC's monthly
factsheets — see `reports/day04_outage_sources.md`).

---

## 3. Extraction method (`src/ingestion/nerc.py`)

Two table shapes exist across the reports:

1. **Dated rows** (what becomes events):
   * `dd/mm/yyyy HH:MMHrs Partial|Full dd/mm/yyyy HH:MMHrs` — 2024 (Table 7.3) and
     2025 (Table 25) reports; the date must sit directly before the time, so a
     restoration value such as `11:48Hrs Full Restoration` can never be mistaken for
     a second collapse. Restoration is searched only *between* consecutive collapse
     rows, so the next event's timestamp is never consumed as this event's.
   * `19th of September, 2023 (11:31hours)` — ordinal prose dates (2023 report,
     Table 20). Only parsed on pages that mention "collapse".
2. **Summary count tables** (`Category of Collapse 2019 2020 …` + `Partial/Total
   Collapses` rows) — parsed for cross-checking, and to infer the type of ordinal
   rows that do not state one (2023 has 0 partial / 3 total → all three inferred
   `full`; if the year's counts do not disambiguate, the type stays `unknown`).
   Tables whose columns cannot line up with the year header (the 2020 report's
   annual+quarterly table) are ignored rather than mis-mapped.

Output: `data/raw/outages/nerc/events_extracted.csv`
(columns `event_date, event_time, event_type, restoration_time, country_iso3,
source_document, source_page, extracted_utc`), deduplicated on
`(event_date, event_time)`, plus an `events_extracted_meta.json` provenance sidecar.

Run: `python -m src.ingestion.nerc` (downloads, skipping existing) · `--audit` to
re-extract offline · `--overwrite` to re-fetch.

---

## 4. Yearly counts: extracted vs NERC Table 24 (expected)

NERC *2025 Annual Report*, Table 24 (partial + total): 2019: **11** · 2020: **4** ·
2021: **4** · 2022: **6** · 2023: **3** · 2024: **9** · 2025: **2** (39 total).

| Year | Expected (Table 24) | Dated events extracted | Δ | Flag / explanation |
|---:|---:|---:|---:|---|
| 2019 | 11 | 0 | −11 | ⚠️ **no 2019 annual report exists** — dated rows unavailable from annual reports |
| 2020 | 4 | 0 | −4 | ⚠️ report exists but carries **counts only** (Table 4.1), no dated incident table |
| 2021 | 4 | 0 | −4 | ⚠️ **no 2021 annual report exists** |
| 2022 | 6 | 0 | −6 | ⚠️ report exists but carries **counts only** (Table 4.2), no dated incident table |
| 2023 | 3 | **3** | 0 | ✅ Table 20 (ordinal dates) |
| 2024 | 9 | **9** | 0 | ✅ Table 7.3 (date/time/type/restoration) |
| 2025 | 2 | **2** | 0 | ✅ Table 25 (date/time/type/restoration) |

**Mismatches: 2019, 2020, 2021, 2022 — all missing-dated-rows, not counting errors.**
Where dated rows exist (2023–2025) the extracted counts match NERC's own summary
tables exactly, and the partial/full split matches too (2023: 0/3 inferred from
Table 19 · 2024: 5 partial / 4 full = Table 7.2 · 2025: 1/1 = Table 24).

**Coverage: 14 of 39 official events (2019–2025) carry dates; 2019–2022 have
official yearly (and monthly — Table E.2) counts only.**

---

## 5. Extracted events

### 2023 — 3 events (from `2023_annual_report.pdf`, p. 143; type inferred, no restoration times published)

| Date | Time | Type | Restoration |
|---|---|---|---|
| 2023-09-14 | 00:41 | full (inferred) | — |
| 2023-09-19 | 11:31 | full (inferred) | — |
| 2023-12-11 | *(not stated in the date cell)* | full (inferred) | — |

### 2024 — 9 events (from `2024_annual_report.pdf`, pp. 163–164)

| Date | Time | Type | Restoration |
|---|---|---|---|
| 2024-02-04 | 11:21 | partial | 18:15 |
| 2024-03-28 | 16:28 | full | 20:27 |
| 2024-04-15 | 02:41 | partial | 10:55 |
| 2024-07-06 | 15:09 | partial | 23:56 |
| 2024-10-14 | 18:48 | partial | 16:14 *(next day, 15 Oct)* |
| 2024-10-19 | 08:15 | full | 17:57 |
| 2024-11-05 | 13:52 | partial | 23:37 |
| 2024-11-07 | 11:29 | full | 10:59 *(next day, 8 Nov)* |
| 2024-12-11 | 13:32 | full | 20:07 |

### 2025 — 2 events (from `2025_annual_report.pdf`, p. 166)

| Date | Time | Type | Restoration |
|---|---|---|---|
| 2025-09-10 | 11:20 | full | 11:48 |
| 2025-12-29 | 14:01 | partial | 14:30 |

---

## 6. Cross-checks inside the reports (one real inconsistency found)

* Table 7.3 (2024 dated rows) → 5 partial / 4 full = Table 7.2 / Table 24 (9) ✅
* Table 20 (2023 dated rows) → 3 rows = Table 19 (3 total, 0 partial) ✅
* Table 25 (2025 dated rows) → 1 full / 1 partial = Table 24 ✅
* **Appendix Table E.2 (2025 report, monthly counts 2011–2025) disagrees with Table 24
  on the 2024 split:** E.2 gives 2024 = 3 total + 6 partial (9 overall), Table 24 /
  Table 7.2 give 4 total + 5 partial (9 overall). Monthly detail shows E.2 counts a
  *partial* in December 2024 where Table 7.3 labels the 11/12/2024 event *Full*.
  Overall totals agree everywhere; only this partial/full label differs. Recorded as
  a known source-internal inconsistency (we follow the main tables 24/7.2/7.3).
* Table E.2's monthly rows for **2011–2025** are otherwise a useful side-product:
  they support *month-level* Nigeria collapse counts even for the years with no
  dated rows (candidate feature/label granularity for Day 15).

---

## 7. License / attribution

* **Source:** Nigerian Electricity Regulatory Commission (NERC), *Annual Report &
  Accounts* (report years 2020, 2022, 2023, 2024, 2025), published at
  `https://nerc.gov.ng/…` (individual URLs in §2 and in
  `data/raw/outages/nerc/events_extracted_meta.json`). Retrieved **2026-10-19**.
* **Status:** official publication of a Nigerian federal regulator (public-body
  document). The website carries a general *"© Nigerian Electricity Regulatory
  Commission. All rights reserved."* footer and **no explicit open licence** was
  found for the report PDFs.
* **Handling in this portfolio:** facts, figures and event dates are cited with
  document + page; the verbatim PDFs live under `data/raw/` for reproducibility and
  are **not republished**. If wider distribution is ever needed, link to the NERC URL
  instead of the file, and re-check licensing with NERC.

---

## 8. Caveats (honest limits of this dataset)

1. **Completeness — the big one.** Only 2023–2025 have dated incidents (14 of 39
   official events). For 2019–2022 the annual reports give counts only; for 2019 and
   2021 no annual report exists at all. Any model using Nigeria collapse-day labels
   is training on ~3 years of positives unless other NERC documents (quarterly
   reports — which do carry incident narratives — or TCN/SOA data) are added later.
2. **Partial vs full is not always stated.** 2023's rows carry no type column; types
   are *inferred* from that year's summary count (0 partial / 3 total → all `full`).
   NERC's own appendix (Table E.2) contradicts the main table for one 2024 event's
   type (see §6).
3. **Restoration times are stored as `HH:MM` only.** Two 2024 restorations happened
   the *next day* (14→15 Oct, 7→8 Nov); the CSV keeps the clock time as published,
   the crossed date lives only in the source PDF (flagged in §5).
4. **One event has no time.** 2023-12-11's date cell has no clock; the cause text
   says "between 13:48hrs & 13:49hrs" — deliberately not scraped into the time field
   (it is not in the date/time column). `event_time` is left empty.
5. **Times are as printed by NERC** (Nigerian local time, WAT/UTC+1); NERC does not
   label the timezone explicitly, so treat them as local wall-clock.
6. **National level only.** Grid collapses are system-wide events: there is **no
   sub-national location**, no feeder, no customer count. Do not derive state-level
   labels from this file (consistent with the Day 15 decision in
   `reports/day04_outage_sources.md`).
7. **Collapses ≠ all outages.** This is the extreme tail of the outage spectrum;
   routine DisCo-level interruptions never appear here.

---

## 9. Reproduce

```bash
.venv/bin/python -m src.ingestion.nerc            # download (skips existing) + extract
.venv/bin/python -m src.ingestion.nerc --audit    # re-extract offline
.venv/bin/python -m pytest tests/test_nerc.py -q   # 13 passed
```

Artifacts: `data/raw/outages/nerc/*.pdf` (5), `events_extracted.csv` (14 rows),
`events_extracted_meta.json`.
