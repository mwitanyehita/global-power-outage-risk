# Project plan — 29-day build roadmap

**Status legend:** ✅ done · 🔄 in progress · ⬜ planned · ❓ TBD (no source
document specifies this day yet)

This roadmap consolidates every *explicit* day reference found in
`reports/data_inventory.md`, `reports/day02_boundaries_audit.md`, and the
`src/` module docstrings. Days marked **TBD** are genuinely unspecified at
Day 2 and will be defined as the build progresses — they are not invented
here. Source of each row is noted; update this file when a day is planned or
completed.

**Build days so far:** Days 1–4 committed (2026-10-19); Day 5 in flight.

## Phase 0 — Foundations (Days 1–3)

| Day | Task | Status | Source |
|---|---|---|---|
| 1 | Colab + local environment check (`notebooks/00_colab_setup.ipynb`); project data inventory with folder conventions (`reports/data_inventory.md`) | ✅ | git log, reports |
| 2 | Administrative boundaries for KEN/NGA/ZAF from geoBoundaries gbOpen; per-file license metadata; audit report (`reports/day02_boundaries_audit.md`); preference decision among ADM-1…ADM-4 sources | ✅ | inventory §A/§G, audit report |
| 3 | Historical weather for pilot cities (Open-Meteo archive), timestamp validation, cross-city grid alignment (`reports/day03_weather_audit.md` — 9/9 cities pass) | ✅ | inventory §B/§G |

## Phase 1 — Data acquisition (Days 4–7)

| Day | Task | Status | Source |
|---|---|---|---|
| 4 | Outage records and official notices (OUT-1…OUT-8) probed for all three pilots; free-tier depth checked (`reports/day04_outage_sources.md`). Follow-ups deferred to Day 10: KPLC Livewire notice list, kplc.project.co.ke archive depth, EPRA re-test from Colab | ✅ | inventory §C/§F/§G, day04 report |
| 5 | World Bank outage indicators; coverage check per pilot country; confirm they are context-only (no locality detail) | 🔄 | inventory §D/§F/§G |
| 6 | Fill licenses, dates, location detail, and limitations per source; build `reports/source_register.csv` (schema in inventory §H) | ⬜ | inventory §G/§H |
| 7 | Data audit report; **go/no-go decision per source** (including fallback if public outage history is missing) | ⬜ | inventory §F/§G |

## Phase 2 — Normalization & label structure (Days 8–11)

| Day | Task | Status | Source |
|---|---|---|---|
| 8 | Timezone-aware UTC normalization enforced; country lookup tables (ISO3, timezone, region) built in-project; resolve duplicate-name ambiguity prep | ⬜ | inventory §E/§F |
| 9 | TBD | ❓ | — |
| 10 | Planned vs unplanned interruptions kept distinguishable; store event start, restoration time, and notice publication time separately | ⬜ | inventory §C |
| 11 | Map outages to boundaries by **point-in-polygon** coordinates (names only as secondary check — 5 duplicate names in NGA ADM2); geometry repair with `make_valid` if needed | ⬜ | audit report findings, inventory |

## Phase 3 — Context layers, features & leakage control (Days 16–18)

| Day | Task | Status | Source |
|---|---|---|---|
| 12–14 | TBD | ❓ | — |
| 15 | Formalise the per-country label design implied by the Day 4 investigation (ZAF dense national hourly target; NGA sparse national events; KEN planned-notice labels) | ⬜ | day04 report §1 |
| 16 | Context/exposure layers (OSM power infrastructure, population at risk); weather feature refresh | ⬜ | inventory §B/§E |
| 17 | Outage-record consolidation for label construction | ⬜ | inventory §C |
| 18 | **Forecast leakage rule enforced:** only forecasts issued before the prediction cutoff may enter features | ⬜ | inventory §B |

## Phase 4 — Modelling & product (Days 22–27)

| Day | Task | Status | Source |
|---|---|---|---|
| 19–21 | TBD | ❓ | — |
| 22 | Streamlit dashboard (use simplified geoBoundaries geometry — full ADM2 files reach 32 MB) | ⬜ | audit report, inventory §E |
| 23–26 | TBD | ❓ | — |
| 27 | Documented limitations: World Bank indicators are national context only; cautious interpretation of grid-value weather features; final coverage notes | ⬜ | inventory §F, weather audit text in `src/ingestion/weather.py` |

## Phase 5 — Publication (Days 28–29)

| Day | Task | Status | Source |
|---|---|---|---|
| 28 | TBD | ❓ | — |
| 29 | Attribution strings present in README for every used source; license re-check before publishing derived data; supported-claims review | ⬜ (strings landed early in README) | audit report findings, inventory §F |

## Standing rules for every day

1. Raw downloads are verbatim into `data/raw/` / `data/external/` with
   provenance sidecars — never edited in place.
2. Licenses marked *verify* must be read before download and re-checked before
   publishing derived data.
3. Tests stay green: `python -m pytest -q` before each day's commit.
