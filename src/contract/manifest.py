"""Reproducible input, dataset, and experiment manifests."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, ClassVar

from pydantic import Field, field_validator, model_validator

from .base import CanonicalHashModel, canonical_json
from .identifiers import FeatureName, Sha256, VersionId


def _validate_json_object(value: dict[str, Any]) -> dict[str, Any]:
    try:
        canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical JSON: {exc}") from exc
    return value


class InputFileSnapshot(CanonicalHashModel):
    """Immutable identity and schema evidence for one assembler input file."""

    path: str = Field(min_length=1, max_length=2048)
    sha256: Sha256
    bytes: int = Field(ge=0)
    rows: int | None = Field(default=None, ge=0)
    schema_id: str = Field(min_length=1, max_length=256)
    schema_version: VersionId
    schema_sha256: Sha256 | None = None

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or value.endswith("/")
        ):
            raise ValueError("snapshot path must be a normalized relative file path")
        if str(path) != value or value in {"", "."}:
            raise ValueError("snapshot path must be normalized POSIX syntax")
        return value


class DatasetManifest(CanonicalHashModel):
    """Behavioral dataset identity plus optional materialization evidence."""

    behavior_exclude: ClassVar[frozenset[str]] = frozenset(
        {"dataset_name", "artifact_sha256", "row_count"}
    )

    manifest_version: VersionId
    dataset_name: str = Field(min_length=1, max_length=256)
    specification: dict[str, Any]
    input_files: tuple[InputFileSnapshot, ...]
    schema_versions: dict[str, VersionId]
    code_version: str = Field(min_length=1, max_length=256)
    feature_definition_hashes: dict[FeatureName, Sha256]
    seeds: dict[str, int]
    artifact_sha256: Sha256 | None = None
    row_count: int | None = Field(default=None, ge=0)

    _specification_json = field_validator("specification")(_validate_json_object)

    @model_validator(mode="after")
    def validate_files(self) -> DatasetManifest:
        paths = [item.path for item in self.input_files]
        if len(paths) != len(set(paths)):
            raise ValueError("input snapshot paths must be unique")
        return self

    def behavior_payload(self) -> dict[str, Any]:
        payload = super().behavior_payload()
        payload["input_files"] = [
            item.model_dump(mode="python", exclude_none=False)
            for item in sorted(self.input_files, key=lambda item: item.path)
        ]
        return payload

    @property
    def dataset_id(self) -> str:
        return self.behavior_hash


class ExperimentManifest(CanonicalHashModel):
    """Behavioral experiment identity plus optional resulting artifact hashes."""

    behavior_exclude: ClassVar[frozenset[str]] = frozenset(
        {"experiment_name", "model_artifact_sha256", "metrics"}
    )

    manifest_version: VersionId
    experiment_name: str = Field(min_length=1, max_length=256)
    dataset_id: Sha256
    feature_definition_hashes: dict[FeatureName, Sha256]
    model_parameters: dict[str, Any] = Field(alias="model_config")
    split_config: dict[str, Any]
    calibration_config: dict[str, Any] = Field(default_factory=dict)
    decision_policy: dict[str, Any]
    seeds: dict[str, int]
    code_version: str = Field(min_length=1, max_length=256)
    package_versions: dict[str, str]
    model_artifact_sha256: Sha256 | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)

    _model_parameters_json = field_validator("model_parameters")(_validate_json_object)
    _split_config_json = field_validator("split_config")(_validate_json_object)
    _calibration_config_json = field_validator("calibration_config")(_validate_json_object)
    _decision_policy_json = field_validator("decision_policy")(_validate_json_object)
    _metrics_json = field_validator("metrics")(_validate_json_object)

    @property
    def experiment_id(self) -> str:
        return self.behavior_hash
