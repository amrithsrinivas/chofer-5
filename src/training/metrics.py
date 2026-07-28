"""Pure binary probability metrics used by trainer and baselines."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

_EPSILON = 1e-15


def calibration_summary(
    labels: np.ndarray | list[int],
    probabilities: np.ndarray | list[float],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    """Return an equal-width reliability curve and expected calibration error."""

    if bins < 1:
        raise ValueError("bins must be positive")
    y = np.asarray(labels, dtype=np.int8)
    p = np.asarray(probabilities, dtype=np.float64)
    if y.ndim != 1 or p.ndim != 1 or len(y) != len(p):
        raise ValueError("labels and probabilities must be equal-length vectors")
    if len(y) == 0:
        raise ValueError("metrics require at least one prediction")
    if not np.isin(y, [0, 1]).all():
        raise ValueError("labels must be binary")
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("probabilities must be finite values in [0, 1]")

    bin_ids = np.minimum((p * bins).astype(int), bins - 1)
    curve: list[dict[str, Any]] = []
    ece = 0.0
    for bin_id in range(bins):
        mask = bin_ids == bin_id
        count = int(mask.sum())
        lower = bin_id / bins
        upper = (bin_id + 1) / bins
        if count:
            mean_probability = float(p[mask].mean())
            observed_rate = float(y[mask].mean())
            ece += count / len(y) * abs(mean_probability - observed_rate)
        else:
            mean_probability = None
            observed_rate = None
        curve.append(
            {
                "lower": lower,
                "upper": upper,
                "count": count,
                "mean_probability": mean_probability,
                "observed_rate": observed_rate,
            }
        )
    return {"expected_calibration_error": float(ece), "bins": curve}


def binary_metrics(
    labels: np.ndarray | list[int],
    probabilities: np.ndarray | list[float],
    *,
    calibration_bins: int = 10,
) -> dict[str, Any]:
    """Calculate deterministic probability and threshold-at-0.5 metrics."""

    y = np.asarray(labels, dtype=np.int8)
    p = np.asarray(probabilities, dtype=np.float64)
    if y.ndim != 1 or p.ndim != 1 or len(y) != len(p):
        raise ValueError("labels and probabilities must be equal-length vectors")
    if len(y) == 0:
        raise ValueError("metrics require at least one prediction")
    if not np.isin(y, [0, 1]).all():
        raise ValueError("labels must be binary")
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("probabilities must be finite values in [0, 1]")

    clipped = np.clip(p, _EPSILON, 1 - _EPSILON)
    log_loss = -np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))
    brier = np.mean((p - y) ** 2)
    predictions = (p >= 0.5).astype(np.int8)
    accuracy = float(np.mean(predictions == y))
    auc = float(roc_auc_score(y, p)) if np.unique(y).size == 2 else None
    return {
        "rows": int(len(y)),
        "positive_rate": float(y.mean()),
        "logloss": float(log_loss),
        "brier": float(brier),
        "accuracy": accuracy,
        "winrate_edge_over_50": accuracy - 0.5,
        "auc": auc,
        "calibration": calibration_summary(y, p, bins=calibration_bins),
    }


compute_metrics = binary_metrics
