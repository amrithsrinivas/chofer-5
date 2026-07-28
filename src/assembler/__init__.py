"""Manifest-driven, point-in-time dataset assembly."""

from .builder import BuildResult, DatasetAssembler, build_dataset
from .source import LakeDataSource, PointInTimeContext, Selector
from .spec import DatasetSpec, load_dataset_spec

__all__ = [
    "BuildResult",
    "DatasetAssembler",
    "DatasetSpec",
    "LakeDataSource",
    "PointInTimeContext",
    "Selector",
    "build_dataset",
    "load_dataset_spec",
]
