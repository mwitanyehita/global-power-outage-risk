# Day 4 — Outage records and official notices: investigation

**Status legend:** ✅ verified reachable & machine-readable · ⚠️ reachable with caveats ·
⛔ blocked · 🔎 needs follow-up

All URLs probed on **2026-10-19** from this project's environment. "Verified" means an HTTP
request succeeded and the content was inspected — not that the full history has been acquired.

---

## 1. What the label problem looks like (summary)

| Country | Best available outage record | Level | History depth | Verdict |
|---|---|---|---|---|
| South Africa | Eskom hourly grid metrics incl. **Manual Load Reduction (MLR)** — the MW of load shedding implemented | National, hourly | **2017-04 → 2026-09** | ✅ **acquired** (see §3) |
| Nigeria | **Grid-collapse incidents** with date/time + restoration, published by NERC in annual reports | National, event | 2019 → 2025 (earlier via older reports) | ✅ official, extractable |
| Kenya | KPLC **planned interruption notices** (PDFs) + live third-party trackers | Area/county, notice | uncertain, likely months | ⚠️ planned-only, image PDFs |

**Consequence for the model (formalised on Day 15):** South Africa supports a dense
national hourly/day target; Nigeria supports a sparse national event target (≈40 dated
incidents 2019–2025); Kenya supports *planned* outage notices with location detail but not
a deep unplanned-outage history. The README's "subject to data availability" clause applies.

---

## 2. Kenya 🇰🇪

| # | Source | Probe result | Findings |
|---|---|---|---|
| OUT-1 | KPLC official notices — https://kplc.co.ke/power-interruptions | ✅ HTTP 200 | Server-rendered page is navigation only; the notice list is a **Laravel Livewire** component (`wire:snapshot` present, `POST /livewire/update`). Results are PDFs under `https://kplc.co.ke/storage/*.pdf`. |
| OUT-1a | Sample notice PDF (`01K09726RVCV6HJN2ZCCSMP4EQ.pdf`, 48 KB) | ⚠️ | **Image-only PDF — pypdf extracts 0 characters.** OCR (tesseract) required for text; date/area structure must be validated per document. |
| OUT-1b | KPLC "Power Maintenance Schedule" (`/customer-support#powerschedule`) | ⚠️ | Same dynamic rendering; no static tables. |
| OUT-2 | EPRA Kenya (regulator) | ⛔ | `epra.org.ke` / `www.epra.org.ke` **fail DNS/connection from this environment** (curl exit 6/000). Re-test from Colab (different network) on Day 5–6; EPRA statistical bulletins remain a candidate for reliability aggregates. |
| OUT-8a | https://outageke.co.ke (third-party tracker) | ✅ HTTP 200 | Live tracker mixing KPLC notices + community reports; county pages; **API/webhooks are a paid business product** — not usable as a free historical source. |
| OUT-8b | https://kplc.project.co.ke/power-interruptions (third-party) | 🔎 | Claims "scrapes KPLC for new notices every 15 minutes", browsable by date/region/area — structure and **history depth to verify on Day 10** (could give structured planned events without OCR). |

**Kenya follow-ups (Day 10):** (1) probe the Livewire `sections.search` component for a
JSON notice list; (2) test kplc.project.co.ke archive depth; (3) re-test EPRA from Colab.

---

## 3. South Africa 🇿🇦

| # | Source | Probe result | Findings |
|---|---|---|---|
| OUT-3 | EskomSePush API | ⚠️ Gated | Requires token from `eskomsepush.gumroad.com/l/api`; non-business **free plan ≈ 50 requests/day** (per third-party docs). Signup needs a human — cannot be automated. Historical depth undocumented → assume forward schedules only until proven otherwise. |
| OUT-4a | Legacy Eskom schedule API — `loadshedding.eskom.co.za/LoadShedding/GetStatus` | ✅ HTTP 200, returned `1` | **Still alive** (1 = no load shedding now, consistent with 2026). Also `GetMunicipalities`, `GetSurburbData`, `GetScheduleM` (HTML schedules). Forward-looking schedules only — no history. |
| OUT-4b | Eskom Data Portal — https://www.eskom.co.za/dataportal/ | ✅ | Official graphs for demand, supply, generation outages (UCLF/PCLF/OCLF), EAF; downloads are per-graph CSVs/PowerBI. |
| OUT-4c | **Unofficial Eskom mirror** — https://beta.unofficialeskom.com/eskom-source-data/ | ✅ **ACQUIRED** | Flattened CSV/SQLite/DuckDB of *every* portal graph. Downloaded `sources/eskom.sqlite` → `data/raw/outages/zaf_eskom_hourly.sqlite` (42 MB, **83,280 hourly rows, 2017-04-01 → 2026-09-30**, 42 columns incl. `Manual Load_Reduction(MLR)`, `ILS Usage`, demand, generation mix, PCLF/UCLF/OCLF). Pipeline: github.com/sixhobbits/v2.unofficialeskom.com. |
| OUT-5 | Municipal portals (e.g. City of Cape Town open data) | 🔎 | Deferred — only needed if sub-national SA labels are attempted; national MLR already suffices for the target unit. |
| — | Kaggle "SA electricity historical data" (2018-04→2022-07, ~40k rows) | ⛔ | Needs Kaggle credentials; not available in this environment. Note as optional cross-check. |

### Validation of the acquired SA data (evidence it measures real shedding)

Daily mean `MLR` on known reference days:

| Date | Known situation | MLR (MW) | ILS (MW) |
|---|---|---|---|
| 2020-04-15 | Lockdown, no shedding | **0.0** | 0.0 |
| 2023-01-12 | Extreme Stage 6 period | **4,077.8** | 0.0 |
| 2023-02-21 | Extreme Stage 6 period | **5,116.9** | 0.0 |
| 2022-06-21 | Stage-6 day | 334.2 | 18.6 |
| 2024-06-10 | Post-suspension relapse | 0.0 | 37.2 |
| 2026-09-30 | Present, no shedding | 0.0 | 0.0 |

Share of hours with `MLR > 0` by year: 2017 **0%** · 2018 1.8% · 2019 6.2% · 2020 9.9% ·
2021 13.3% · 2022 43.2% · 2023 **78.0%** · 2024 18.7% · 2025 2.3% · 2026 0%.
This matches the published national load-shedding calendar (suspended Mar–Sep 2024, absent
through 2025–2026) — **the column is a faithful national load-shedding signal.**

⚠️ **Provenance caveat:** the mirror's GitHub repo carries **no license file**. Underlying
data is Eskom's own portal output (Eskom's website terms apply). Use is limited to
analysis *in this portfolio*; **do not republish the raw file**. Cite both Eskom Data Portal
and the mirror project in the README (Day 29).

---

## 4. Nigeria 🇳🇬

| # | Source | Probe result | Findings |
|---|---|---|---|
| OUT-6a | NERC Annual Report 2025 (PDF, 11 MB / 216 pp) | ✅ **text-extractable** | Contains **Table 24: System Collapses 2019–2025** (counts, partial vs total), **Table 25: dated 2025 incidents** with exact time of collapse *and* time of full restoration (e.g. `10/09/2025 11:20Hrs Full → 11:48Hrs`; `29/12/2025 14:01Hrs Partial`), Tables E/E.2 for 2011–2024. **Downloaded to `/tmp` during probing; formal acquisition Day 10.** |
| OUT-6b | NERC monthly *Operational Performance Factsheets* (≈4 pages of listings, 2023→present) | ⚠️ image-only | Sample (Mar 2026) = 2 pages, **0 extractable characters** — OCR needed. Content is generation availability/load factor, not outage events → low priority. |
| OUT-6c | NERC "Key Operational & Financial Data of NESI Jan 2019 – Sep 2022" (ZIP of spreadsheets) | 🔎 | Machine-readable official package on the statistics page — **download and inspect Day 10** (possible monthly interruption/ATC&U fields per DisCo). |
| OUT-7 | DisCo load-interruption filings (tariff reviews) | 🔎 | PDF filings, per-feeder detail, heavy parsing — only pursue if national collapse events prove insufficient. |
| OUT-9 | NERC PORS outage-reporting web app | ⚠️ | Customer *reporting* tool (Abuja DisCo first); **no public data API found** — not a source. |
| — | Complaints statistics ("Interruption" = 14.82% of 2025 complaints) | ⚠️ | Aggregated complaint counts, not outage events — context feature only, if used at all. |

**Collapse counts from NERC Table 24** (official): 2019: 11 · 2020: 4 · 2021: 4 · 2022: 6 ·
2023: 3 · 2024: 9 · 2025: 2 → **≈39 national events 2019–2025** before extracting dated
rows from the individual annual reports (Day 10).

---

## 5. Pan-African / other

| Source | Probe result | Findings |
|---|---|---|
| OCHA HDX (searched: "load shedding", "grid collapse", "power outage", "electricity outage") | ✅ API | **No outage-event dataset for KEN/ZAF/NGA.** Hits were World Bank indicator packs (Day 5) and night-light–based outage *assessments* (Libya, Morocco) — useful as method precedent for satellite labels, not as data. |
| Satellite night lights (VIIRS) | 🔎 | Technically viable blackout detection, but Earthdata/AppEEARS/Earth Engine accounts required → **out of scope for this run**; recorded as an extension in Day 27 limitations. |

---

## 6. Label strategy recommendation (decided formally on Day 15)

1. **South Africa — primary modeling target.** National day/hourly "shedding active"
   label from `MLR > 0` (+ MW intensity), 2017→2026, aligned to weather at Johannesburg,
   Cape Town, Durban. Dense positives (~24% of days), clean hourly provenance.
2. **Nigeria — secondary target (sparse events).** National collapse-day label from NERC
   annual reports (dated incidents + restoration). Honest handling: rare events, class
   imbalance, PR-AUC vs a prevalence baseline.
3. **Kenya — coverage & planned-outage analysis.** Planned interruption notices (area-level)
   demonstrate the geography pipeline (Day 11) and the planned/unplanned split (Day 10);
   a deep unplanned-outage history is **not available** and will be stated as a limitation
   rather than substituted with fabricated labels.
4. **Never** mix planned notices into unplanned labels; never invent location-level labels
   for the national-level sources.

## 7. Actions carried forward

| Day | Action |
|---|---|
| 5 | Re-probe EPRA from a different network (Colab); pull World Bank packs from HDX/API |
| 10 | KPLC Livewire probe + kplc.project.co.ke archive depth; NERC annual reports 2019–2024 acquisition; NERC NESI ZIP inspection |
| 14 | Final "model-ready locations" decision feeds Day 15 prediction unit |
| 27 | Document provenance caveat for the Eskom mirror (no-license repo) and image-PDF/OCR limits |
