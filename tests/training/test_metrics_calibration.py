from __future__ import annotations

import json

import numpy as np
import pytest

from training.calibration import apply_calibrator, fit_platt_calibrator
from training.metrics import binary_metrics


def test_binary_metrics_include_auc_and_calibration() -> None:
    result = binary_metrics([0, 0, 1, 1], [0.1, 0.4, 0.6, 0.9], calibration_bins=2)
    assert result["accuracy"] == 1.0
    assert result["winrate_edge_over_50"] == 0.5
    assert result["auc"] == 1.0
    assert result["brier"] == pytest.approx(0.085)
    assert result["calibration"]["expected_calibration_error"] == pytest.approx(0.25)


def test_auc_is_null_for_one_class() -> None:
    result = binary_metrics([1, 1], [0.7, 0.8])
    assert result["auc"] is None


def test_platt_coefficients_round_trip_through_json() -> None:
    raw = np.array([0.1, 0.2, 0.35, 0.65, 0.8, 0.9])
    labels = np.array([0, 0, 0, 1, 1, 1])
    calibrator = fit_platt_calibrator(raw, labels, seed=4)
    serialized = json.loads(json.dumps(calibrator.as_dict()))
    expected = apply_calibrator(raw, calibrator)
    actual = apply_calibrator(raw, serialized)
    assert calibrator.method == "platt"
    np.testing.assert_allclose(actual, expected)
    assert ((actual >= 0) & (actual <= 1)).all()


def test_platt_rejects_single_class_validation() -> None:
    with pytest.raises(ValueError, match="both label classes"):
        fit_platt_calibrator([0.1, 0.2], [0, 0])
