"""Pure Platt calibration functions with JSON-safe coefficients."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
from sklearn.linear_model import LogisticRegression

_EPSILON = 1e-12


@dataclass(frozen=True)
class ProbabilityCalibrator:
    method: str
    coefficient: float
    intercept: float
    fitted_rows: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def probability_logit(probabilities: np.ndarray | list[float]) -> np.ndarray:
    p = np.asarray(probabilities, dtype=np.float64)
    if p.ndim != 1:
        raise ValueError("probabilities must be a vector")
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("probabilities must be finite values in [0, 1]")
    p = np.clip(p, _EPSILON, 1 - _EPSILON)
    return np.log(p / (1 - p))


def fit_platt_calibrator(
    probabilities: np.ndarray | list[float],
    labels: np.ndarray | list[int],
    *,
    seed: int = 0,
) -> ProbabilityCalibrator:
    """Fit logistic calibration to validation probabilities only."""

    scores = probability_logit(probabilities)
    y = np.asarray(labels, dtype=np.int8)
    if y.ndim != 1 or len(y) != len(scores) or len(y) == 0:
        raise ValueError("labels and probabilities must be non-empty equal-length vectors")
    if not np.isin(y, [0, 1]).all():
        raise ValueError("labels must be binary")
    if np.unique(y).size != 2:
        raise ValueError("Platt calibration requires both label classes")

    estimator = LogisticRegression(
        solver="lbfgs",
        C=1_000_000.0,
        random_state=seed,
        max_iter=1_000,
    )
    estimator.fit(scores.reshape(-1, 1), y)
    coefficients = np.asarray(estimator.coef_, dtype=np.float64)
    intercepts = np.asarray(estimator.intercept_, dtype=np.float64)
    return ProbabilityCalibrator(
        method="platt",
        coefficient=float(coefficients[0, 0]),
        intercept=float(intercepts[0]),
        fitted_rows=int(len(y)),
    )


def identity_calibrator() -> ProbabilityCalibrator:
    return ProbabilityCalibrator(
        method="none",
        coefficient=1.0,
        intercept=0.0,
        fitted_rows=0,
    )


def apply_calibrator(
    probabilities: np.ndarray | list[float],
    calibrator: ProbabilityCalibrator | Mapping[str, Any],
) -> np.ndarray:
    """Apply a JSON-round-trippable calibrator to raw probabilities."""

    p = np.asarray(probabilities, dtype=np.float64)
    if isinstance(calibrator, ProbabilityCalibrator):
        method = calibrator.method
        coefficient = calibrator.coefficient
        intercept = calibrator.intercept
    else:
        method = str(calibrator["method"])
        coefficient = float(calibrator["coefficient"])
        intercept = float(calibrator["intercept"])
    if method == "none":
        if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
            raise ValueError("probabilities must be finite values in [0, 1]")
        return p.copy()
    if method != "platt":
        raise ValueError(f"unknown calibration method {method!r}")
    scores = coefficient * probability_logit(p) + intercept
    calibrated = np.empty_like(scores)
    positive = scores >= 0
    calibrated[positive] = 1.0 / (1.0 + np.exp(-scores[positive]))
    negative_exp = np.exp(scores[~positive])
    calibrated[~positive] = negative_exp / (1.0 + negative_exp)
    return calibrated


fit_calibrator = fit_platt_calibrator
