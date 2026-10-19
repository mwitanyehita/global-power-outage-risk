# Day 14 — Leakage audit (code-grounded)

**Scope:** every path by which information from *after* the prediction issue time, or from
the label itself, could enter features, splits, scaling, calibration or claims — for the
KEN / NGA / ZAF pilot (README principles: separate planned vs unplanned; preserve event
and notice timestamps; prevent leakage; evaluate probabilistic forecasts honestly; never
fabricate outage probabilities).

**Method:** read-only review of the sources listed below. No code was modified; the test
suite was **not** executed as part of this audit, so test pass/fail state at audit time is
**unverified** (the project rule "tests stay green before each commit" is in
`docs/project_plan.md`, standing rule 3).

**Audit-time caveat (Day 14):** `src/features/`, `src/models/`, `src/evaluation/`,
`src/dashboard/state.py`, `src/ingestion/outages.py` and `src/ingestion/utils.py` are
in-flight work by other sessions. Everything below describes those modules **as read at
audit time**, not as final. Settled modules: `src/ingestion/weather.py`,
`src/ingestion/worldbank.py`, `src/cleaning/standardize.py`, `src/geo/units.py`,
`src/geo/join.py`.

**Status vocabulary:** **prevented** = enforced by code or by a binding written decision ·
**guarded** = a helper/rule exists but end-to-end enforcement is not yet demonstrated ·
**open** = nothing yet stops the leak; must be built Days 16–20.

---

## 1. Leakage vector table

| ID | Vector | Mechanism | Sev | Status | Evidence |
|---|---|---|---|---|---|
| L1 | Weather measured during/after the outage window | Joining the ERA5 row at hour *t* (or *t+1…*) to a label at *t* makes the feature contemporaneous-or-future for that row; harmless only if the model is explicitly a nowcast | high | guarded | `src/features/rolling.py::add_lags` (min lag 1), `::add_past_rolling` (`shift(1)` before rolling ⇒ current row excluded), `::assert_chronological`; **no feature-frame builder exists yet** — `src/features/__init__.py` exports only `rolling`/`time_split`/`times`, so end-to-end cutoff enforcement is undemonstrated as of audit time |
| L2 | ERA5 reanalysis availability vs issue time (nowcast h=0 vs day-ahead) | Reanalysis for hour *t* is finalized days after *t*; using it in a prediction issued at *t−24 h* is look-ahead even though the value describes the past. Nowcast (h=0, features from ≤ *t*) is legitimate but must be labelled a nowcast, not a forecast | high | open | Inventory §B "Leakage rule: forecast variables carry an issue timestamp; only forecasts issued *before* the prediction cutoff may enter features (enforced Day 18)" (`reports/data_inventory.md`); `docs/project_plan.md` Day 18 row; `src/ingestion/weather.py::download_city` / `ARCHIVE_URL` / `default_end` (archive endpoint, no issue timestamp stored) |
| L3 | Open-Meteo **forecast/nowcast API** mixed into historical features | Forecast values retrieved today describe the future; retro-filling them onto 2021–2026 rows would manufacture features no issue-time had | high | prevented | Day 7 **GO\* (future only)** = NO-GO for history: "must never touch historical labels (leakage: forecasts issued after the fact are not features)" (`reports/day07_data_audit.md` §1, rows WX-5/6); FC-1/2 marked NO-GO this run (same table); `src/dashboard/state.py::live_conditions` docstring + module header: display-only, never a training feature or label; README "Live dashboard" note |
| L4 | World Bank survey-year misalignment | The API `year` is the survey/reference year, not the label year; forward-filling a survey value onto later hourly rows either mislabels vintage (backward join) or leaks a *later* survey into *earlier* rows | high | guarded | `reports/day05_worldbank_coverage.md` (survey years 2007–2025 per indicator, sparse: KEN 2007/2013/2018/2025, ZAF 2007/2020, NGA 2007/2014/2025): "should be attached as static country-level columns, not joined on time"; `src/ingestion/worldbank.py::write_audit` (same binding text) and `::OUTAGE_INDICATORS` "never as geolocated labels"; no join code exists yet → rule is written, enforcement open |
| L5 | Planned KPLC notices ↔ unplanned targets | Merging planned notices into an unplanned-outage label (or vice versa) silently changes the event being predicted | high | prevented | `src/ingestion/outages.py::validate` errors when `outage_type` leaves `{planned, unplanned, unknown}`; `::normalize_record` / `_canonical_outage_type` canonicalise without collapsing; `reports/day07_data_audit.md` §2.3: "Planned notices are never merged into unplanned labels"; KEN scope = planned-outage analysis only |
| L6 | Scaling / encoding fitted on the full dataset | `fit_transform` on the whole frame lets test-fold means/variances (and rare-category sets) shape training rows | high | open | No scaler/encoder anywhere in the repo as of audit time (only calibrator `fit_transform`, `src/models/calibration.py::ProbabilityCalibrator.fit_transform`); `src/models/base.py::SklearnRiskModel.fit` passes `X` straight to the estimator — no pipeline, no per-fold transform |
| L7 | Time-based CV violations (shuffling, random KFold) | Random folds put future rows in training; a time series scores spectacularly and meaninglessly | high | prevented | `src/models/cv.py::ExpandingWindowSplits.__iter__` (train = `[0, train_end)`, test strictly later, no shuffle parameter exists), `::walk_forward_oof` raises unless `train_idx.max() < test_idx.min()` and on overlapping test windows; `src/features/time_split.py::temporal_train_test_split` requires a tz-aware cutoff, `::assert_strictly_past` raises when `train.max() >= test.min()`; tests exist at `tests/test_models_cv.py` and `tests/test_features_split.py` (**not run in this audit**) |
| L8 | Target-derived rolling features (current/future row inside the window) | A rolling count of outages that includes row *t* reproduces the label at *t*; a window reaching past *t* uses the future | high | guarded | Prevented *mechanically* by `src/features/rolling.py::add_past_rolling` (`series.shift(1).rolling(...)`, row 0 always NaN) and `::add_lags` (`_normalize_sequence(..., minimum=1)`); nothing yet prevents a hand-rolled `rolling()` on the label column — feature build is Day 16 |
| L9 | The label itself (MLR hours) entering features via a join | `eskom_load_reduction()` returns `mlr_mw` next to candidate predictors; any feature join keyed only on `time` carries the target column into `X` | high | open | `src/dashboard/state.py::eskom_load_reduction` (columns `time`, `mlr_mw`, `residual_demand`, `total_re`); `reports/day07_data_audit.md` §2.1 defines the ZAF target from `MLR > 0`; no feature-frame builder exists that excludes label columns by name |
| L10 | Same-hour system state as a feature for the same-hour MLR label | `residual_demand` / `total_re` (and ILS/demand/generation columns in the raw Eskom export) are the *cause-side* of MLR at the same hour — a near-target proxy, tautological at h=0 and unavailable at day-ahead issue time | high | open | Same evidence as L9 plus `reports/day04_outage_sources.md` §3 (42-column Eskom export incl. `ILS Usage`, demand, generation mix); no lag rule exists yet |
| L11 | Calendar features derived from the label's own timestamps | Risk only if parts are taken from the *event* time of a selected positive row; harmless when derived from the prediction row's own timestamp | low | prevented | `src/features/times.py::local_time_parts` documented "leakage-safe: derived from the timestamp alone"; `reports/day09_eda_weather.md` Findings: "month-of-year is therefore a legitimate calendar feature, not a leak". Why it is safe: month, day-of-week and hour-of-day of issue time *t* are deterministic and known to any issue-time forecaster — no observation of the future is required; negatives must be sampled on the same time grid so the feature carries no label information |
| L12 | Normalization stats / climatology over the full 2021-2026 window | Means, stds, scalers or month-climatologies computed across all years let 2025–2026 distribution info shape features for 2021 rows (early test years) | med | open | `src/eda/weather_eda.py::climatology` — "monthly climatology per city (means over the full archive window)" — is fine as EDA but must not be reused as a feature; `src/ingestion/weather.py` window is `DEFAULT_START = "2021-01-01"` → `default_end()` (2026-10), so any full-window statistic straddles every split |
| L13 | Duplicate events double-counted across train/test | Same event ingested twice (two sources, two `record_id`s, or re-runs) lands once in train and once in test, or inflates positives; distinct from the *geography* problem | med | guarded | `src/ingestion/nerc.py` extraction deduplicated on `(event_date, event_time)` per `reports/day10_nerc_events.md` §3; **`src/ingestion/outages.py::validate` only *warns* on duplicate `record_id`** (does not drop or fail); no cross-source (nerc/manual/manual) dedup exists; NGA duplicate LGA names are geography only — resolved by point-in-polygon (`src/geo/join.py::points_to_units`, `src/geo/units.py::flag_duplicates`), not an event-dedup mechanism |
| L14 | Label window straddling the train/test cut | A multi-hour MLR episode (or a notice whose lead time crosses the cutoff) whose label interval spans the split leaks test-period hours into a training row | med | guarded | `src/features/time_split.py::temporal_train_test_split(gap=...)` drops boundary rows from *both* sides; `src/models/cv.py::ExpandingWindowSplits(gap=...)` purges rows between train and test; parameters exist but **no run has yet set `gap` ≥ label horizon** (Day 16–20) |
| L15 | Notice timestamps used before they existed | A notice-derived feature is only legal when `notice_published < issue time`; retro-published or post-event notices are look-ahead | med | guarded | `src/features/times.py::notice_lead_time` (flags notices that appeared *after* the event); `src/ingestion/outages.py` keeps `notice_published_utc` separate from `event_start_utc` (schema); no issue-time comparison is applied yet because no feature build exists |
| L16 | Calibrator fitted on in-sample predictions | Fitting Platt/isotonic on the base model's own training predictions measures memorization, not reliability — probabilities published from it would be unsupported | med | guarded | `src/models/calibration.py` module docstring: fit on out-of-fold predictions from `walk_forward_oof`; `ProbabilityCalibrator.fit` itself accepts **any** aligned arrays — the rule is documented, not enforced by code |
| L17 | Skill-score reference rate taken from the evaluation sample | `evaluation_report(..., reference_rate=None)` defaults to the sample's own event rate; if that sample is the test period, headline skill is measured against a baseline that saw the test labels | med | open | `src/evaluation/probabilistic.py::evaluation_report` (docstring: "defaults to the sample's own event rate — document this when reporting") and `::brier_skill_score`; `src/models/base.py::ClimatologyBaseline` correctly fits `base_rate_` on **training** `y` — the convention exists, the reporting default does not enforce it |

**Tally (17 vectors):** severity — **high 10** (L1–L10), **medium 6** (L12–L17), **low 1** (L11);
status — **prevented 4** (L3, L5, L7, L11), **guarded 7** (L1, L4, L8, L13, L14, L15, L16),
**open 6** (L2, L6, L9, L10, L12, L17).

---

## 2. What is already prevented where (as of audit time)

- **Strictly-past lags/rollings:** `src/features/rolling.py::add_lags` (lag ≥ 1 enforced),
  `::add_past_rolling` (window applied to the *shifted* series), `::assert_chronological`,
  `::assert_regular_grid`, `::sort_by_time`. Rows are counted, not timed, so
  `assert_regular_grid` is the precondition for honest hour-counts.
- **Timestamp discipline:** `src/features/times.py::to_utc` (naive input raises unless
  `assume_tz` given), `src/cleaning/standardize.py::normalize_timestamp` (assumption is an
  explicit parameter), `::to_local` (local time is presentation only),
  `src/ingestion/outages.py::validate` (all time columns must be tz-aware; naive values
  are *counted* as warnings by `normalize_record`).
- **Time-ordered splitting:** `src/features/time_split.py::temporal_train_test_split`
  (tz-aware cutoff, embargo `gap`, rows sharing the cutoff all go to test) and
  `::assert_strictly_past` (raises on `train.max() >= test.min()`).
- **Expanding-window CV, no shuffle:** `src/models/cv.py::ExpandingWindowSplits`,
  `::expanding_window_splits`, `::walk_forward_oof` (fresh model per fold — no state
  carried across folds; rejects overlapping or backwards folds; untested rows stay NaN),
  `::walk_forward_scores` (pooled OOF row is the honest headline).
- **Calibration discipline (documented):** `src/models/calibration.py` module docstring
  requires out-of-fold inputs; `src/models/base.py::ClimatologyBaseline` computes its rate
  from training `y` only.
- **Label/planned separation (schema):** `src/ingestion/outages.py::validate` /
  `OUTAGE_TYPES` / `CANONICAL_COLUMNS` (event start, event end and notice publication kept
  as separate columns).
- **Forecast sources excluded by decision:** `reports/day07_data_audit.md` §1 (WX-5/6
  future-only, FC-1/2 NO-GO); `src/dashboard/state.py::live_conditions` display-only.
- **Geography ambiguity surfaced, not resolved silently:** `src/geo/join.py::match_names_to_units`
  (`status='ambiguous'` rows never auto-picked), `src/geo/units.py::flag_duplicates`.
- **Evaluation honesty:** `src/evaluation/probabilistic.py::evaluation_report` fails loudly
  on non-binary labels / non-finite or out-of-range probabilities; ECE and skill scores are
  first-class.

**Not yet verified:** none of the above has been exercised end-to-end on a real feature
matrix, because the feature build (Day 16) and label build (Day 15/17) do not exist yet.
Test modules exist for all of it (`tests/test_features_times.py`,
`tests/test_features_split.py`, `tests/test_features_rolling.py`, `tests/test_models_cv.py`,
`tests/test_models_base.py`, `tests/test_models_calibration.py`,
`tests/test_evaluation.py`) but **this audit did not run them**; treat green/red as
unknown until the next scheduled `python -m pytest -q`.

---

## 3. Open guards Day 16–20 MUST implement (grading checklist)

1. **Issue-time cutoff on every feature join.** Feature frames join weather, context and
   notice sources on timestamps **strictly `<` the row's issue time** for day-ahead rows;
   h=0 rows may use same-hour ERA5 but must be tagged `horizon=0` and reported separately
   as a **nowcast**. Enforced by a test, not by convention (L1, L2).
2. **ERA5 availability rule.** Reanalysis columns never appear in a feature set whose
   claimed horizon is ≥ 1 day; issue time is stored as a column on every feature row, and
   the audit report prints `min(issue_time − feature_time)` per feature group (L2).
3. **Forecast-API firewall.** Any column whose provenance is `api.open-meteo.com/v1/forecast`
   (or WX-5/6/FC-1/2 sources) is rejected from historical feature/label frames; the
   dashboard's `live_conditions()` output may only be rendered (L3).
4. **Label columns excluded from `X` by name *and* by test.** Deny-list covering
   `mlr_mw`, MLR-hour/label columns, `event_start_utc`, `event_end_utc`,
   `notice_published_utc`, `outage_type`, `residual_demand`, `total_re`, ILS/demand/generation
   columns, plus any column suffixed `_label`/`_target`; assert in `fit()` that no column of
   `X` matches (L9, L10).
5. **Lag rule for cause-side system state.** Same-hour demand/generation/renewable columns
   are excluded, or lagged by ≥ `horizon + 1` hours, before entering features (L10).
6. **Fit scalers/encoders/imputers on training folds only**, inside the CV loop (pipeline
   `fit` per fold); forbid any `fit_transform` on the full frame before splitting (L6).
7. **Embargo ≥ label horizon.** `gap` in `temporal_train_test_split` / `ExpandingWindowSplits`
   set to at least the label window (ZAF hourly MLR episode, NGA event day, KEN notice lead),
   and `assert_strictly_past` called before every fit (L14).
8. **Climatology / normalization features fit on training years only** (per-fold or
   expanding stats with `shift(1)`); `weather_eda.climatology()`'s full-window means are
   EDA-only and must not be reused as features (L12).
9. **World Bank context: static or strictly as-of.** Survey indicators enter as static
   country columns *with their survey vintage recorded*, or as-of joined with
   `survey_year < issue year`; never forward-filled across the label year (L4).
10. **Planned/unplanned assertion at label build.** Each label frame asserts a single
    `outage_type`; KEN planned notices never enter an unplanned target; KEN/ZAF/NGA label
    frames are built from disjoint source sets (L5).
11. **Dedup before splitting.** Cross-source event dedup key defined; duplicate `record_id`
    **fails** validation instead of warning; assert no event key appears in both train and
    test for any fold (L13).
12. **CV restricted to walk-forward.** No `KFold`/`GroupKFold`/`shuffle=True` in modelling
    code — enforced by a test that greps the model entry points and by
    `walk_forward_oof`'s existing fold assertions (L7).
13. **Calibration on OOF probabilities only**, with a test asserting the calibrator's input
    equals `walk_forward_oof` output, never in-sample predictions (L16).
14. **Reference rate pinned for headline skill scores.** Pass `reference_rate=` from the
    *training* period prevalence explicitly; never publish `evaluation_report` defaults
    computed against the test sample (L17).
15. **Notice features gated on publication time:** `notice_published_utc < issue_time`, else
    the feature is null for that row (L15).

---

## 4. Claims discipline

Leakage control is what separates the sentences this portfolio may write from the ones it
may not:

- **Allowed only if guards 1–8 + 12–14 pass:** "we skilfully forecast outage risk" —
  requires strictly-past features relative to a stored issue time, walk-forward (never
  shuffled) CV, pooled out-of-fold metrics, positive Brier/log-loss skill against a
  *training-period* climatology, and a calibration curve with reported ECE.
- **Otherwise, phrase as association:** features drawn from the same hour/day as the label
  support a **nowcast** claim ("conditions associated with same-day outage risk"), not a
  forecast. Same-hour ERA5 + `MLR` is exactly this case (L1, L2).
- **Never claim:** locality-level risk from World Bank indicators (country-year context
  only, `reports/day05_worldbank_coverage.md` §Findings); unplanned-outage risk for Kenya
  (planned notices only — `reports/day07_data_audit.md` §2.3); accuracy numbers on NGA
  (~39 official collapses 2019–2025, only 14 dated — report PR-AUC against a prevalence
  baseline, never accuracy; `reports/day10_nerc_events.md` §4/§8).
- **No probability is published** without a documented source, a calibration check and a
  stated issue time — README: "Do not publish unsupported outage probabilities".
- **Limitation wording stays in the report, not in a footnote:** ERA5 grid values describe
  exposure at a city centroid, not the fault that caused any specific outage
  (`reports/day09_eda_weather.md` Findings; `src/ingestion/weather.py` audit text).
