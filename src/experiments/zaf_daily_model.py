"""Walk-forward training and honest evaluation for the ZAF target (Days 18-20).

Pipeline (every step chosen to close a Day 14 audit guard):

* Features come from ``src.datasets.zaf_daily.build_dataset`` — strictly
  past only (guard L2/L1); rows with NaN lags are dropped, never forward-
  filled.
* Cross-validation is the expanding window from ``src.models.cv`` (guard
  L7) with a 1-day purge; no shuffling exists anywhere in this path.
* The model is ``StandardScaler -> LogisticRegression`` inside one sklearn
  Pipeline wrapped by ``SklearnRiskModel``: the scaler is fitted **per fold
  on that fold's training rows only** (guard L6 — no full-data scaling).
* The no-skill reference rate is **pinned to the first fold's training
  prevalence** (guard L17) — headline skill scores are never measured
  against the evaluation sample's own event rate.
* Calibration (guard L16): a sigmoid calibrator is fit on the *first 40%*
  of out-of-fold rows and evaluated on the *last 60%*; raw vs calibrated
  metrics are reported on those same held-out rows only.
* ``ClimatologyBaseline`` (constant training-rate forecast) runs through
  identical folds — the model must beat it on Brier score or it has
  learned nothing.

The NGA collapse target is deliberately NOT modeled with weather here: the
physical link is speculative and the honest treatment is a prevalence
baseline + persistence, documented in the Day 20 report.

Usage:
    python -m src.experiments.zaf_daily_model
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.config import DATA_PROCESSED, REPORTS_DIR, SEED
from src.datasets.zaf_daily import TIME_COL, build_dataset, feature_columns
from src.evaluation.probabilistic import (
    brier_score, expected_calibration_error, log_loss, roc_auc,
)
from src.models.base import ClimatologyBaseline, SklearnRiskModel
from src.models.calibration import ProbabilityCalibrator
from src.models.cv import expanding_window_splits, walk_forward_oof

MIN_TRAIN = 730      # first two full years train the first fold
HORIZON = 90         # quarterly test windows
GAP = 1              # purged day between train and test
CAL_FRACTION = 0.4   # share of OOF rows used to fit the calibrator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def logistic_pipeline() -> Pipeline:
    """Scaler fitted inside each fold — the L6 guard lives in this object."""
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, random_state=SEED)),
    ])


def model_factories():
    return {
        "logistic": lambda: SklearnRiskModel(logistic_pipeline()),
        "climatology": lambda: ClimatologyBaseline(),
    }


def prepare_frame(frame: pd.DataFrame
                  ) -> tuple[pd.DataFrame, np.ndarray, int, pd.Series]:
    """Drop NaN-lag rows, split X (strict features), y (target) and dates."""
    feats = feature_columns(frame)
    clean = frame.dropna(subset=feats).reset_index(drop=True)
    n_dropped = len(frame) - len(clean)
    X = clean[feats]
    y = clean["any_load_shedding"].astype(int).to_numpy()
    return X, y, n_dropped, clean[TIME_COL]


def _two_class_or_nan(fn, y_true: np.ndarray, p: np.ndarray) -> float:
    """Ranking metrics are undefined on single-class windows (honest NaN).

    E.g. ZAF 2025-2026 test folds contain zero shedding days — reporting a
    roc_auc there would fabricate a number out of nothing.
    """
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(fn(y_true, p))


def metrics_block(y_true: np.ndarray, p: np.ndarray,
                  ref_rate: float) -> dict[str, float]:
    """Consistent metric set for every model/protocol comparison."""
    return {
        "n": int(len(y_true)),
        "positives": int(y_true.sum()),
        "brier": float(brier_score(y_true, p)),
        "log_loss": float(log_loss(y_true, p)),
        "roc_auc": _two_class_or_nan(roc_auc, y_true, p),
        "pr_auc": _two_class_or_nan(average_precision_score, y_true, p),
        "brier_skill": float(_brier_skill(y_true, p, ref_rate)),
        "ece": float(expected_calibration_error(y_true, p)),
        "mean_pred": float(np.mean(p)),
    }


def _brier_skill(y_true: np.ndarray, p: np.ndarray, ref_rate: float) -> float:
    brier_ref = brier_score(y_true, np.full_like(p, ref_rate, dtype=float))
    if brier_ref == 0:
        return float("nan")
    return 1.0 - float(brier_score(y_true, p)) / brier_ref


def run_experiment(frame: pd.DataFrame | None = None) -> dict:
    """Train + evaluate; returns metrics and artifacts (also for tests)."""
    if frame is None:
        frame = build_dataset()
    X, y, n_dropped, dates = prepare_frame(frame)
    n = len(X)

    splits = expanding_window_splits(n, min_train=MIN_TRAIN, horizon=HORIZON,
                                     gap=GAP)
    factories = model_factories()
    oof = {name: walk_forward_oof(factory, X, y, splits)
           for name, factory in factories.items()}

    tested = ~np.isnan(oof["logistic"])
    assert (tested == ~np.isnan(oof["climatology"])).all(), \
        "both models must cover identical test rows"

    # Pinned reference: FIRST fold's training prevalence (never the test mix).
    ref_rate = float(y[:MIN_TRAIN].mean())

    metrics = {name: metrics_block(y[tested], p[tested], ref_rate)
               for name, p in oof.items()}

    # --- calibration protocol (fit on early OOF rows, score on later rows)
    tested_idx = np.flatnonzero(tested)
    n_cal = int(CAL_FRACTION * len(tested_idx))
    if n_cal < 1 or len(tested_idx) - n_cal < 1:
        raise ValueError(
            f"not enough tested rows ({len(tested_idx)}) for the "
            f"calibration split (fit={n_cal}, eval={len(tested_idx) - n_cal})")
    cal_idx = tested_idx[:n_cal]
    eval_idx = tested_idx[n_cal:]

    calibrator = ProbabilityCalibrator(method="sigmoid")
    calibrator.fit(oof["logistic"][cal_idx], y[cal_idx])
    p_cal = calibrator.transform(oof["logistic"][eval_idx])

    calibration = {
        "cal_rows": len(cal_idx),
        "eval_rows": len(eval_idx),
        "raw_on_eval": metrics_block(y[eval_idx], oof["logistic"][eval_idx],
                                     ref_rate),
        "calibrated_on_eval": metrics_block(y[eval_idx], p_cal, ref_rate),
    }

    # --- per-year OOF table
    years = dates.dt.year.to_numpy()
    rows = []
    for year in sorted(set(years[tested])):
        m = years == year
        m = m & tested
        rows.append({
            "year": int(year),
            "n_days": int(m.sum()),
            "positives": int(y[m].sum()),
            "brier_logistic": float(brier_score(y[m], oof["logistic"][m])),
            "brier_climatology": float(brier_score(y[m], oof["climatology"][m])),
            "pr_auc_logistic": _two_class_or_nan(average_precision_score,
                                                 y[m], oof["logistic"][m]),
        })
    per_year = pd.DataFrame(rows)

    predictions = pd.DataFrame({
        TIME_COL: dates,
        "y": y,
        "oof_logistic": oof["logistic"],
        "oof_climatology": oof["climatology"],
    })
    predictions.loc[eval_idx, "oof_calibrated"] = p_cal

    return {
        "n_rows": n,
        "n_dropped_nan_lags": n_dropped,
        "n_folds": len(splits),
        "n_tested": int(tested.sum()),
        "test_span": (f"{dates[tested].min():%Y-%m-%d} .. "
                      f"{dates[tested].max():%Y-%m-%d}"),
        "ref_rate": ref_rate,
        "metrics": metrics,
        "calibration": calibration,
        "per_year": per_year,
        "predictions": predictions,
    }


def write_outputs(result: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or DATA_PROCESSED
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "zaf_oof_predictions.csv.gz"
    out = result["predictions"].copy()
    out[TIME_COL] = out[TIME_COL].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, compression="gzip")
    return path


def write_report(result: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (REPORTS_DIR / "day18_zaf_model.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    m = result["metrics"]
    cal = result["calibration"]

    def row(name: str, d: dict) -> str:
        return (f"| {name} | {d['n']} | {d['positives']} | {d['brier']:.4f} "
                f"| {d['log_loss']:.4f} | {d['roc_auc']:.3f} "
                f"| {d['pr_auc']:.3f} | {d['brier_skill']:+.3f} "
                f"| {d['ece']:.3f} | {d['mean_pred']:.3f} |")

    skill = m["logistic"]["brier_skill"]
    beats = "beats" if skill > 0 else "DOES NOT beat"

    text = f"""# Day 18 — ZAF day-ahead model: walk-forward training and evaluation

Generated by `python -m src.experiments.zaf_daily_model` on {_utc_now()}.

**Protocol**: expanding-window CV ({result['n_folds']} folds; first {MIN_TRAIN}
days train fold 1, {HORIZON}-day quarterly test windows, {GAP}-day purge).
OOF coverage: {result['n_tested']} tested days ({result['test_span']}).
Rows dropped for NaN lags (never forward-filled): {result['n_dropped_nan_lags']}.
**Reference rate pinned to fold-1 training prevalence** = {result['ref_rate']:.3f}
(not the evaluation sample's own rate — Day 14 guard L17).
Scaler fitted inside each fold's pipeline (guard L6).

## Headline: walk-forward out-of-fold metrics (all tested days)

| model | n | pos | brier | log_loss | roc_auc | pr_auc | brier_skill | ece | mean_pred |
|---|---|---|---|---|---|---|---|---|---|
{row('logistic (scaled)', m['logistic'])}
{row('climatology baseline', m['climatology'])}

The logistic model **{beats}** the climatology baseline on Brier score
(skill = {skill:+.3f}). Mean predicted probability {m['logistic']['mean_pred']:.3f}
vs observed prevalence {m['logistic']['positives'] / max(m['logistic']['n'], 1):.3f} —
probability outputs are in the right regime or the calibration section says why.

## Calibration protocol (sigmoid; fit on early OOF rows, scored on later rows)

- calibrator fit rows: {cal['cal_rows']} · evaluation rows: {cal['eval_rows']}
  (the evaluation rows never touch the calibrator)

| protocol (eval rows only) | brier | log_loss | ece | mean_pred |
|---|---|---|---|---|
| raw logistic | {cal['raw_on_eval']['brier']:.4f} | {cal['raw_on_eval']['log_loss']:.4f} | {cal['raw_on_eval']['ece']:.3f} | {cal['raw_on_eval']['mean_pred']:.3f} |
| sigmoid-calibrated | {cal['calibrated_on_eval']['brier']:.4f} | {cal['calibrated_on_eval']['log_loss']:.4f} | {cal['calibrated_on_eval']['ece']:.3f} | {cal['calibrated_on_eval']['mean_pred']:.3f} |

## Per-year OOF breakdown

{result['per_year'].to_markdown(index=False)}

## Honest reading

- Features are **strictly past** (Day 16): lagged weather + autoregressive
  label lags + advance-known calendar; no same-day weather anywhere.
- Skill is measured against a pinned training-prevalence reference, and the
  baseline model goes through the *same* folds.
- The strongest driver is expected to be the label autoregression (shedding
  is persistent); weather's marginal contribution should be read off a
  feature-ablation, not claimed from headline skill.
- Calibration is evaluated on rows the calibrator never saw; publishable
  probabilities are the calibrated ones on those rows, nothing weaker.
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Day 18 model report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    result = run_experiment()
    path = write_outputs(result)
    print(f"wrote OOF predictions: {path}")
    print(f"brier skill vs pinned climatology: "
          f"{result['metrics']['logistic']['brier_skill']:+.3f}")
    write_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
