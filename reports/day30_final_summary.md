# Day 30 — Final summary: what this project delivers, and what it does not

The project asks one question: *can we forecast power-outage risk in Kenya,
South Africa and Nigeria from data that is actually obtainable and
auditable?* The honest answer, after 30 days of building:

**Yes for one target (national day-ahead load-shedding in South Africa), and
explicitly no for the other two** — and the "no" is a finding, not a gap.

## 1. What was built

| stage | days | outcome |
|---|---|---|
| acquisition & provenance | 1-7 | 9 boundary files, 9-city hourly weather (2021-2026), Eskom MLR series (83,280 h), NERC reports, KPLC notices, World Bank indicators; a 25-source register with licenses, and a go/no-go audit per source |
| cleaning & geography | 8-14 | tz-aware UTC normalization everywhere, 1,212-unit registry, 5 duplicate-name groups flagged, point-in-polygon joins, 17-vector leakage audit |
| labels | 15-17 | ZAF hourly+daily MLR labels reproducing the known shedding calendar; NGA collapse-day labels with a 2023-2025 labelable window; KPLC notices as **planned** events only |
| features & modelling | 16-21 | strictly-past day-ahead feature matrix (122 features), walk-forward OOF evaluation, held-out calibration, feature ablation, honest NGA refusal-to-model |
| product & proof | 22-30 | live dashboard state, coverage atlas, model interpretation with portfolio-safe wording, this summary |

## 2. The evidence (all walk-forward, all out-of-fold)

| claim | number | where |
|---|---|---|
| ZAF day-ahead model beats a pinned climatology baseline | Brier 0.0457 vs 0.2338, **skill +0.794**, ROC-AUC 0.981, 1,350 OOF days | `day18` |
| probabilities are calibrated, not asserted | sigmoid calibrator fit on early OOF rows, scored on held-out rows; ECE 0.043 raw | `day19` |
| what actually predicts shedding | persistence: label-lag-only Brier **0.0196** vs full model 0.0457 — weather adds **nothing** on top | `day21` |
| NGA model is **not** claimed | 14 collapse days total, 2 in the test year | `day20` |
| Kenya has no unplanned labels | KPLC publishes planned notices only | `day07`, `day11` |
| no leakage | same-day weather excluded; future-weather perturbation changes only later rows (tested) | `day16` |

## 3. What is deliberately NOT claimed

1. Not a Kenya or Nigeria outage model — no evaluated model exists for them.
2. Not a sub-national model — every label is national (or estate-level for
   Kenya's *planned* notices).
3. Not a weather-driven story — the ablation shows weather adds no skill
   beyond persistence on this target.
4. Not a "grid collapse risk" forecast for Nigeria — 2 test positives.
5. No outage probability is published without its calibration evidence and
   pinned reference rate.

## 4. Known limitations

- **ZAF label**: MLR is Eskom's *manual* reduction; early years under-report
  other curtailment; stages are not derivable from MW. One -0.104 MW
  artifact hour is kept verbatim and documented.
- **Weather**: ERA5 reanalysis, past only. Day-ahead *forecast* weather was a
  documented NO-GO (WX-5/6); with it, a genuinely predictive weather model
  might exist — we simply do not have it.
- **KPLC**: 5 image-only PDFs (no OCR here) → missing not at random; notice
  counts per year reflect archive growth, not outage frequency; a notice can
  be cancelled.
- **NERC**: 2019/2021 reports were never published; 2020/2022 carry counts
  only; one source-internal inconsistency (Table E.2 vs Table 24 for 2024) is
  documented.
- **Mirror licenses**: the Eskom mirror and the NERC PDFs carry no open
  license → analysed in-project, never republished.

## 5. Reproduce it

```bash
bash scripts/verify.sh        # full test suite + regenerate every artifact
.venv/bin/python -m pytest -q # 560+ hermetic tests
```

Headline commands:

```bash
python -m src.labels.eskom            # ZAF labels + calendar validation
python -m src.datasets.zaf_daily      # leakage-clean feature matrix
python -m src.experiments.zaf_daily_model   # walk-forward OOF + calibration
python -m src.experiments.zaf_ablation      # persistence vs weather
python -m src.eda.coverage_maps             # coverage atlas
```

## 6. What a v2 would need (ranked by value)

1. **Forecast weather** (Open-Meteo forecast API, archived) to test whether
   weather helps *day-ahead* rather than only in-sample — the single biggest
   open question from the ablation.
2. **Sub-national ZAF labels** (municipality-level load shedding) to make
   the product actionable for operators rather than national reporting.
3. **Dated NERC events back to 2019** — blocked by publication gaps, not by
   method; would enable a rare-event model with enough positives.
4. **A Kenya unplanned-outage source** that passes the same go/no-go gate as
   everything else.
5. **Probabilistic calibration for the rare-event case** with a proper
   prequential scoring rule.
