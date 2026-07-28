from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from training.spec import ExperimentSpec, load_experiment_spec


def _base_spec() -> dict:
    return {
        "dataset": {"path": "artifact", "dataset_id": "dataset-v1"},
        "features": "all",
        "split": {
            "train_fraction": 0.6,
            "validation_fraction": 0.2,
            "test_fraction": 0.2,
            "purge_seconds": 30,
        },
        "xgboost": {"seed": 7, "params": {"n_estimators": 10}},
        "calibration": {"method": "none"},
        "threshold_policy": {"reference": "policy://v1"},
    }


def test_fraction_spec_and_reserved_xgboost_controls() -> None:
    spec = ExperimentSpec.model_validate(_base_spec())
    assert spec.split.uses_fractions
    assert spec.xgboost.seed == 7
    assert spec.asset_encoding == "none"

    encoded = _base_spec()
    encoded["asset_encoding"] = "one_hot"
    assert ExperimentSpec.model_validate(encoded).asset_encoding == "one_hot"

    invalid = _base_spec()
    invalid["xgboost"]["params"]["n_jobs"] = 8
    with pytest.raises(ValidationError, match="cannot be overridden"):
        ExperimentSpec.model_validate(invalid)


def test_explicit_ranges_must_be_utc_and_ordered() -> None:
    raw = _base_spec()
    raw["split"] = {
        "purge_seconds": 0,
        "ranges": {
            "train": {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-02-01T00:00:00Z",
            },
            "validation": {
                "start": "2026-02-01T00:00:00Z",
                "end": "2026-03-01T00:00:00Z",
            },
            "test": {
                "start": "2026-03-01T00:00:00Z",
                "end": "2026-04-01T00:00:00Z",
            },
        },
    }
    spec = ExperimentSpec.model_validate(raw)
    assert not spec.split.uses_fractions

    raw["split"]["ranges"]["test"]["start"] = "2026-03-01T00:00:00"
    with pytest.raises(ValidationError, match="UTC offset"):
        ExperimentSpec.model_validate(raw)


def test_loader_resolves_relative_dataset_path(tmp_path: Path) -> None:
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text(
        """
features = "all"
calibration = "none"
threshold_policy = "policy://v1"

[dataset]
path = "artifact"
id = "dataset-v1"

[split.fractions]
train_fraction = 0.6
validation_fraction = 0.2
test_fraction = 0.2

[xgboost]
seed = 1

[xgboost.params]
n_estimators = 5
""".strip(),
        encoding="utf-8",
    )
    spec = load_experiment_spec(spec_path)
    assert spec.dataset.path == (tmp_path / "artifact").resolve()
