# Day 19 — Calibration: protocol, evidence, and the publishing rule

Calibration is not a nicety in this project — the README forbids publishing
unsupported outage probabilities. This note records the protocol the
pipeline actually ran (Day 18) and the rule that follows from it.

## Protocol (implemented in `src/experiments/zaf_daily_model.py`)

1. Base model: `StandardScaler -> LogisticRegression` inside a sklearn
   Pipeline, wrapped by `SklearnRiskModel` — the scaler is fitted **per
   expanding-window fold** on that fold's training rows only.
2. Out-of-fold (OOF) raw probabilities come from
   `src.models.cv.walk_forward_oof` — every evaluated day's probability is
   produced by a model that never saw that day.
3. The calibrator (`src.models.calibration.ProbabilityCalibrator`,
   sigmoid/Platt) is fit on the **first 40%** of OOF rows (2023-era,
   high-shedding) and evaluated on the **remaining 60%** — the evaluation
   rows never touch the calibrator in either direction.
4. Metrics on the shared evaluation rows (Day 18 report, held-out section):

   | protocol | brier | log_loss | ece | mean_pred |
   |---|---|---|---|---|
   | raw logistic | 0.0257 | 0.0991 | 0.043 | 0.061 |
   | sigmoid-calibrated | 0.0285 | 0.1143 | 0.060 | 0.081 |

## What the evidence says

- The raw model is already well-calibrated in regime (ECE 0.043); the
  sigmoid map fitted on 2023-era rows slightly **over-predicts** in the
  low-shedding 2025-26 evaluation window — a regime-shift artifact, shown
  as measured (`day18_zaf_model.md`, `day21_zaf_ablation.md`).
- Single-class windows (2026: zero shedding days) report NaN ranking
  metrics rather than fabricated values — tested in
  `tests/test_experiments_zaf.py`.

## The publishing rule

- Publish **raw OOF probabilities** for days in-regime; when regime shifts
  (as the calibration table shows it can), say so next to the number.
- Never fit a calibrator on the same rows used to report its quality
  (L16 of the Day 14 audit) — enforced by the 40/60 split above.
- Never quote a skill score against the evaluation sample's own event rate
  — the reference is pinned to fold-1 training prevalence (L17), and the
  climatology baseline runs through identical folds.
- An uncalibrated number is never published "because the metrics looked
  fine" — calibration evidence travels with the probability.
