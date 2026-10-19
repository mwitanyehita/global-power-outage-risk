# Reports index — global power outage risk

Every report is **regenerable** from code; every number is derived from
files on disk. Generated reports live in this directory, git-ignored data
artifacts in `data/processed/`.

## Test the project (three levels)

```bash
# 1. everything (556+ tests, hermetic — skips where raw data is absent)
.venv/bin/python -m pytest -q

# 2. one stage
.venv/bin/python -m pytest tests/test_labels_eskom.py -q

# 3. regenerate every derived artifact + report, in pipeline order
bash scripts/verify.sh
```

## Report map

| Report | Answers | Reproduce |
|---|---|---|
| `data_inventory.md` | What exists, what's missing, risks | — (Day 1, prose) |
| `day02_boundaries_audit.md` | Do admin boundaries load cleanly? | `python -m src.ingestion.boundaries` |
| `day03_weather_audit.md` | Is the 9-city weather panel gap-free? | `python -m src.ingestion.weather` |
| `day04_outage_sources.md` | Where can outages actually come from? | — (probe notes) |
| `day05_worldbank_coverage.md` | Which WB indicators have pilot data? | `python -m src.ingestion.worldbank --audit` |
| `day06_source_register.md` | Canonical source + license register | `reports/source_register.csv` |
| `day07_data_audit.md` | Go/no-go per source; **locked label strategy** | — (decision record) |
| `day09_eda_weather.md` | Climatology/variability of the 9 cities | `python -m src.eda.weather_eda` |
| `day10_kenya_probes.md` | How to acquire KPLC notices compliantly | `python -m src.ingestion.kplc` |
| `day10_nerc_events.md` | Which NGA collapses are dated + provenance | `python -m src.ingestion.nerc` |
| `day11_spatial_join.md` | Do events/cities join admin units? | `python -m src.geo.join` |
| `day12_duplicate_units.md` | Which unit names are ambiguous? | `python -m src.geo.units` |
| `day13_coverage_maps.md` | Where does data exist? (atlas) | `python -m src.eda.coverage_maps` |
| `day14_leakage_audit.md` | 17 leakage vectors + open guards | — (audit record) |
| `day15_zaf_labels.md` | Do ZAF labels reproduce known shedding history? | `python -m src.labels.eskom` |
| `day16_zaf_dataset.md` | Is the day-ahead feature matrix leakage-clean? | `python -m src.datasets.zaf_daily` |
| `day17_nga_labels.md` | Which NGA days are labelable (and which never)? | `python -m src.labels.collapse_days` |
| `day18_zaf_model.md` | Does the day-ahead model beat pinned climatology? | `python -m src.experiments.zaf_daily_model` |
| `day20_nga_baseline.md` | Why **no** NGA model is claimed | `python -m src.experiments.nga_baseline` |
| `day21_zaf_ablation.md` | What actually predicts shedding (persistence vs weather)? | `python -m src.experiments.zaf_ablation` |

## Headline results (walk-forward, honestly evaluated)

- **ZAF day-ahead**: Brier 0.0457 vs pinned climatology 0.2338
  (skill **+0.794**), ROC-AUC 0.981, over 1,350 OOF days 2023-2026
  (`day18`). Ablation: persistence carries the skill; weather adds
  standalone signal but **no material gain on top of label lags**
  (`day21`) — stated as measured, not sold.
- **NGA**: 14 collapse days (2023-25), 2 in the test year → model
  deliberately **not** claimed (`day20`).
- **KEN**: planned KPLC notices only — never used as unplanned labels
  (`day07`, `day10_kenya_probes`, `day11`).

## Standing rules these reports enforce

1. Planned ≠ unplanned outages; never merged into one label.
2. Timestamps are tz-aware UTC everywhere (explicit `assume_tz`).
3. Features are strictly past; same-day weather is a nowcast, labeled so.
4. Unknown is never zero (NGA 2019-22, image-only PDFs, NaN MLR hours).
5. No unsupported outage probabilities are published: pinned reference
   rates, held-out calibration, per-day provenance.
