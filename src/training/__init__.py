"""Reproducible temporal XGBoost training framework."""

from .calibration import (
    ProbabilityCalibrator,
    apply_calibrator,
    fit_platt_calibrator,
    identity_calibrator,
)
from .metrics import binary_metrics, calibration_summary
from .spec import ExperimentSpec, load_experiment_spec
from .split import SplitResult, chronological_split
from .trainer import TrainingError, train_experiment

__all__ = [
    "ExperimentSpec",
    "ProbabilityCalibrator",
    "SplitResult",
    "TrainingError",
    "apply_calibrator",
    "binary_metrics",
    "calibration_summary",
    "chronological_split",
    "fit_platt_calibrator",
    "identity_calibrator",
    "load_experiment_spec",
    "train_experiment",
]
