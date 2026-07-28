"""Validated experiment specification for deterministic model training."""

from __future__ import annotations

import math
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatasetReference(BaseModel):
    """Immutable reference to one assembled dataset artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    dataset_id: str = Field(min_length=1)


class UtcRange(BaseModel):
    """A start-inclusive, end-exclusive UTC range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_utc_range(self) -> UtcRange:
        for name, value in (("start", self.start), ("end", self.end)):
            offset = value.utcoffset() if value.tzinfo is not None else None
            if offset is None:
                raise ValueError(f"{name} must include a UTC offset")
            if offset.total_seconds() != 0:
                raise ValueError(f"{name} must be UTC (Z or +00:00)")
        if self.end <= self.start:
            raise ValueError("range end must be after start")
        return self


class SplitConfig(BaseModel):
    """Either three chronological fractions or three explicit UTC ranges."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    train_fraction: float | None = Field(default=None, gt=0, lt=1)
    validation_fraction: float | None = Field(default=None, gt=0, lt=1)
    test_fraction: float | None = Field(default=None, gt=0, lt=1)
    train: UtcRange | None = None
    validation: UtcRange | None = None
    test: UtcRange | None = None
    purge_seconds: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_shapes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        fractions = normalized.pop("fractions", None)
        if fractions is not None:
            if not isinstance(fractions, dict):
                raise ValueError("split.fractions must be a table")
            normalized = {**fractions, **normalized}
        ranges = normalized.pop("ranges", None)
        if ranges is not None:
            if not isinstance(ranges, dict):
                raise ValueError("split.ranges must be a table")
            normalized = {**ranges, **normalized}
        if "val_fraction" in normalized and "validation_fraction" not in normalized:
            normalized["validation_fraction"] = normalized.pop("val_fraction")
        if "val" in normalized and "validation" not in normalized:
            normalized["validation"] = normalized.pop("val")
        return normalized

    @model_validator(mode="after")
    def validate_mode(self) -> SplitConfig:
        fractions = (
            self.train_fraction,
            self.validation_fraction,
            self.test_fraction,
        )
        ranges = (self.train, self.validation, self.test)
        has_fraction = any(value is not None for value in fractions)
        has_range = any(value is not None for value in ranges)
        if has_fraction == has_range:
            raise ValueError("split must define exactly one of fractions or UTC ranges")
        if has_fraction:
            if any(value is None for value in fractions):
                raise ValueError("all train/validation/test fractions are required")
            if not math.isclose(sum(value for value in fractions if value is not None), 1.0):
                raise ValueError("train/validation/test fractions must sum to 1")
        else:
            if any(value is None for value in ranges):
                raise ValueError("all train/validation/test UTC ranges are required")
            assert self.train is not None
            assert self.validation is not None
            assert self.test is not None
            if self.train.end > self.validation.start:
                raise ValueError("train and validation ranges overlap or are out of order")
            if self.validation.end > self.test.start:
                raise ValueError("validation and test ranges overlap or are out of order")
        return self

    @property
    def uses_fractions(self) -> bool:
        return self.train_fraction is not None


class XGBoostConfig(BaseModel):
    """XGBoost parameters with deterministic execution controls reserved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int = Field(default=0, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)
    early_stopping_rounds: int | None = Field(default=20, gt=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_direct_params(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        params = normalized.pop("params", {})
        if not isinstance(params, dict):
            raise ValueError("xgboost.params must be a table")
        controls = {"seed", "early_stopping_rounds"}
        direct_params = {
            key: normalized.pop(key) for key in list(normalized) if key not in controls
        }
        duplicates = sorted(set(params).intersection(direct_params))
        if duplicates:
            raise ValueError("duplicate XGBoost params: " + ", ".join(duplicates))
        normalized["params"] = {**params, **direct_params}
        return normalized

    @model_validator(mode="after")
    def validate_params(self) -> XGBoostConfig:
        reserved = {
            "objective",
            "n_jobs",
            "nthread",
            "tree_method",
            "random_state",
            "seed",
            "device",
        }
        conflicts = sorted(reserved.intersection(self.params))
        if conflicts:
            raise ValueError(
                "determinism-controlled XGBoost params cannot be overridden: "
                + ", ".join(conflicts)
            )
        for key, value in self.params.items():
            if not isinstance(key, str) or not key:
                raise ValueError("XGBoost parameter names must be non-empty strings")
            if not isinstance(value, (str, int, float, bool)) or value is None:
                raise ValueError(f"XGBoost parameter {key!r} must be a TOML scalar")
        return self


class CalibrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["none", "platt"] = "none"


class ThresholdPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: str = Field(min_length=1)


class ExperimentSpec(BaseModel):
    """Complete behaviorally relevant trainer configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: DatasetReference
    features: Literal["all"] | list[str] = "all"
    asset_encoding: Literal["none", "one_hot"] = "none"
    split: SplitConfig
    xgboost: XGBoostConfig
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    threshold_policy: ThresholdPolicy

    @model_validator(mode="before")
    @classmethod
    def normalize_convenient_toml(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)

        dataset = normalized.get("dataset")
        if isinstance(dataset, dict):
            dataset = dict(dataset)
            if "id" in dataset and "dataset_id" not in dataset:
                dataset["dataset_id"] = dataset.pop("id")
            dataset_features = dataset.pop("features", dataset.pop("feature_columns", None))
            if dataset_features is not None and "features" not in normalized:
                normalized["features"] = dataset_features
            normalized["dataset"] = dataset

        features = normalized.get("features", "all")
        if isinstance(features, dict):
            if features.get("all") is True:
                features = "all"
            else:
                features = features.get("columns", features.get("selection"))
        normalized["features"] = features

        if "model" in normalized and "xgboost" not in normalized:
            normalized["xgboost"] = normalized.pop("model")

        calibration = normalized.get("calibration")
        if isinstance(calibration, str):
            normalized["calibration"] = {"method": calibration}

        threshold = normalized.get("threshold_policy")
        if isinstance(threshold, str):
            normalized["threshold_policy"] = {"reference": threshold}
        return normalized

    @model_validator(mode="after")
    def validate_features(self) -> ExperimentSpec:
        if isinstance(self.features, list):
            if not self.features:
                raise ValueError("feature subset cannot be empty")
            if any(not feature for feature in self.features):
                raise ValueError("feature names must be non-empty")
            if len(set(self.features)) != len(self.features):
                raise ValueError("feature subset contains duplicates")
        return self


def load_experiment_spec(path: Path) -> ExperimentSpec:
    """Load TOML and resolve the dataset path relative to the spec file."""

    path = path.resolve()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    spec = ExperimentSpec.model_validate(raw)
    dataset_path = spec.dataset.path
    if not dataset_path.is_absolute():
        dataset_path = (path.parent / dataset_path).resolve()
    return spec.model_copy(
        update={
            "dataset": spec.dataset.model_copy(update={"path": dataset_path}),
        }
    )


def spec_as_jsonable(spec: ExperimentSpec) -> dict[str, Any]:
    """Return a canonical JSON-compatible representation."""

    return spec.model_dump(mode="json")
