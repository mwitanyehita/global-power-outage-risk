"""Probabilistic forecast evaluation for binary outage-risk predictions.

The project evaluates probabilistic forecasts, not accuracy alone: Brier
score and log loss for overall quality, Brier/log-loss skill scores against
a reference forecast, and a calibration curve with expected calibration
error (ECE) to keep published probabilities honest.

Every function validates its inputs and fails loudly (ValueError) rather
than silently coercing bad data — a forecast with NaNs or probabilities
outside [0, 1] must never be summarized into a report.

Conventions:
    y_true  binary 0/1 (bools accepted), one value per row.
    y_prob  predicted P(outage), one value per row, in [0, 1].
    Arrays, Series and lists are all accepted; outputs are plain Python
    floats / numpy arrays / DataFrames (never numpy scalar types).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "brier_score",
    "log_loss",
    "climatology_probability",
    "brier_skill_score",
    "log_loss_skill_score",
    "calibration_curve",
    "expected_calibration_error",
    "roc_auc",
    "evaluation_report",
]

DEFAULT_CALIBRATION_BINS: int = 10
# Probabilities are clipped before log loss so p=0/1 with the wrong label
# yields a large finite penalty instead of inf.
_LOG_LOSS_EPS: float = 1e-15


def _check_binary_targets(y_true, *, min_rows: int = 1) -> np.ndarray:
    """Validate and convert a binary target to a 1-D float array."""
    y = np.asarray(y_true)
    if y.ndim != 1:
        raise ValueError(f"expected a 1-D target, got ndim={y.ndim}")
    if len(y) < min_rows:
        raise ValueError(f"need at least {min_rows} rows, got {len(y)}")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("y_true must be binary (values in {0, 1})")
    return y.astype(float)


def _check_binary_arrays(y_true, y_prob, *, min_rows: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Validate and convert (y_true, y_prob) to 1-D numpy arrays."""
    y = _check_binary_targets(y_true, min_rows=min_rows)
    p = np.asarray(y_prob, dtype=float)

    if p.ndim != 1:
        raise ValueError(f"expected a 1-D forecast, got ndim={p.ndim}")
    if len(y) != len(p):
        raise ValueError(f"length mismatch: y_true has {len(y)}, y_prob has {len(p)}")
    if not np.isfinite(p).all():
        raise ValueError("y_prob contains NaN or infinite values")
    if ((p < 0) | (p > 1)).any():
        raise ValueError(
            f"y_prob must lie in [0, 1]; got min={p.min():.4g}, max={p.max():.4g}"
        )
    return y, p


def brier_score(y_true, y_prob) -> float:
    """Mean squared error of the probability forecast (lower is better)."""
    y, p = _check_binary_arrays(y_true, y_prob)
    return float(np.mean((p - y) ** 2))


def log_loss(y_true, y_prob, eps: float = _LOG_LOSS_EPS) -> float:
    """Binary cross-entropy, clipped at ``eps`` so the result stays finite."""
    if not 0 < eps < 0.5:
        raise ValueError(f"eps must be in (0, 0.5), got {eps}")
    y, p = _check_binary_arrays(y_true, y_prob)
    p = np.clip(p, eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log1p(-p)))


def climatology_probability(y_true) -> float:
    """Base rate of the sample — the naive reference forecast."""
    return float(_check_binary_targets(y_true).mean())


def brier_skill_score(y_true, y_prob, reference) -> float:
    """1 - Brier(model) / Brier(reference); 0 = no skill vs reference.

    ``reference`` is a scalar probability or one probability per row (e.g.
    the training-set climatology). Returns NaN when the reference forecast
    is itself perfect (division by zero) — skill is undefined then.
    """
    y, p = _check_binary_arrays(y_true, y_prob)
    ref = _broadcast_reference(reference, len(y))
    score = brier_score(y, p)
    ref_score = brier_score(y, ref)
    if ref_score == 0.0:
        return float("nan")
    return float(1.0 - score / ref_score)


def log_loss_skill_score(y_true, y_prob, reference) -> float:
    """1 - log loss(model) / log loss(reference); 0 = no skill vs reference.

    Returns NaN when the reference forecast is (numerically) perfect: skill
    against a perfect reference is undefined, not a large negative number.
    ``log_loss`` clips at ``_LOG_LOSS_EPS``, so a perfect reference scores
    about ``_LOG_LOSS_EPS`` rather than exactly 0 — the comparison therefore
    uses a tolerance instead of an exact equality test.
    """
    y, p = _check_binary_arrays(y_true, y_prob)
    ref = _broadcast_reference(reference, len(y))
    score = log_loss(y, p)
    ref_score = log_loss(y, ref)
    if ref_score <= _LOG_LOSS_EPS * 10:
        return float("nan")
    return float(1.0 - score / ref_score)


def _broadcast_reference(reference, n: int) -> np.ndarray:
    """Turn a scalar or sequence reference forecast into a 1-D array."""
    ref = np.asarray(reference, dtype=float)
    if ref.ndim == 0:
        ref = np.full(n, float(ref))
    if ref.ndim != 1 or len(ref) != n:
        raise ValueError(
            f"reference must be a scalar or {n} values, got shape {ref.shape}"
        )
    if not np.isfinite(ref).all() or ((ref < 0) | (ref > 1)).any():
        raise ValueError("reference probabilities must lie in [0, 1]")
    return ref


def calibration_curve(
    y_true, y_prob, n_bins: int = DEFAULT_CALIBRATION_BINS, strategy: str = "quantile"
) -> pd.DataFrame:
    """Reliability diagram data: one row per non-empty probability bin.

    Columns:
        lo, hi            bin edges for the predicted probability
        mean_predicted    average predicted probability in the bin
        observed_fraction empirical event rate in the bin
        count             rows in the bin

    ``strategy="quantile"`` gives equal-count bins (robust when forecasts
    cluster near 0 or 1); ``"uniform"`` gives equal-width bins on [0, 1].
    Empty bins are dropped. Perfect calibration means
    ``mean_predicted == observed_fraction`` in every row.
    """
    if not isinstance(n_bins, (int, np.integer)) or isinstance(n_bins, bool):
        raise ValueError(f"n_bins must be an integer, got {n_bins!r}")
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")
    if strategy not in ("quantile", "uniform"):
        raise ValueError(
            f"strategy must be 'quantile' or 'uniform', got {strategy!r}"
        )
    y, p = _check_binary_arrays(y_true, y_prob)

    if strategy == "uniform":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    else:
        edges = np.unique(np.quantile(p, np.linspace(0.0, 1.0, n_bins + 1)))
        if len(edges) < 2:  # all forecasts identical: single bin
            edges = np.array([p.min(), p.max() + np.finfo(float).eps])

    # Digitize into [0, n_bins); the right-most edge belongs to the last bin.
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)

    rows = []
    for b in range(len(edges) - 1):
        mask = idx == b
        count = int(mask.sum())
        if count == 0:
            continue
        rows.append(
            {
                "lo": float(edges[b]),
                "hi": float(edges[b + 1]),
                "mean_predicted": float(p[mask].mean()),
                "observed_fraction": float(y[mask].mean()),
                "count": count,
            }
        )
    return pd.DataFrame(
        rows, columns=["lo", "hi", "mean_predicted", "observed_fraction", "count"]
    )


def expected_calibration_error(
    y_true, y_prob, n_bins: int = DEFAULT_CALIBRATION_BINS, strategy: str = "quantile"
) -> float:
    """Weighted mean gap between predicted and observed frequency (lower is
    better; 0 = perfectly calibrated on this sample, worst case is 1)."""
    curve = calibration_curve(y_true, y_prob, n_bins=n_bins, strategy=strategy)
    if curve.empty:  # pragma: no cover - _check_binary_arrays guarantees rows
        return float("nan")
    weights = curve["count"].to_numpy(dtype=float)
    gaps = np.abs(
        curve["mean_predicted"].to_numpy() - curve["observed_fraction"].to_numpy()
    )
    return float(np.average(gaps, weights=weights))


def roc_auc(y_true, y_prob) -> float:
    """Area under the ROC curve (ranking quality; needs both classes)."""
    y, p = _check_binary_arrays(y_true, y_prob)
    if len(np.unique(y)) < 2:
        raise ValueError("roc_auc is undefined when only one class is present")
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, p))


def _confusion_counts(y: np.ndarray, p: np.ndarray, threshold: float) -> tuple[int, int, int, int]:
    """(tp, fp, fn, tn) at the given probability threshold."""
    pred = (p >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    return tp, fp, fn, tn


def evaluation_report(
    y_true, y_prob, threshold: float = 0.5, reference_rate: float | None = None
) -> dict:
    """One flat dict of probabilistic + thresholded metrics for reporting.

    Probabilistic metrics are primary (Brier, log loss, skill vs the
    reference climatology, calibration ECE, ROC AUC); the thresholded
    metrics at ``threshold`` are included for operational discussions only.
    ROC AUC is NaN when the sample holds a single class.

    ``reference_rate`` is the no-skill base rate for the skill scores;
    defaults to the sample's own event rate (document this when reporting).
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")
    y, p = _check_binary_arrays(y_true, y_prob)

    base_rate = float(y.mean()) if reference_rate is None else float(reference_rate)
    if not 0.0 <= base_rate <= 1.0:
        raise ValueError(f"reference_rate must be in [0, 1], got {reference_rate}")

    tp, fp, fn, tn = _confusion_counts(y, p, threshold)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )

    try:
        auc = roc_auc(y, p)
    except ValueError:
        auc = float("nan")

    return {
        "n": int(len(y)),
        "prevalence": float(y.mean()),
        "reference_rate": base_rate,
        "brier_score": brier_score(y, p),
        "log_loss": log_loss(y, p),
        "brier_skill_score": brier_skill_score(y, p, base_rate),
        "log_loss_skill_score": log_loss_skill_score(y, p, base_rate),
        "calibration_ece": expected_calibration_error(y, p),
        "roc_auc": auc,
        "threshold": float(threshold),
        "accuracy": (tp + tn) / len(y),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
    }
