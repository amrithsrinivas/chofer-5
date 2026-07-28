"""Assembler artifact validation and hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

CORE_COLUMNS = (
    "row_id",
    "asset",
    "slug",
    "decision_at",
    "window_start",
    "window_end",
    "label_binary",
    "market_midprice",
    "quote_valid",
)
QUOTE_COLUMN_ALIASES = {
    "up_decision_ask": ("up_decision_ask", "decision_up_ask"),
    "down_decision_ask": ("down_decision_ask", "decision_down_ask"),
    "up_execution_ask": ("up_execution_ask", "execution_up_ask"),
    "down_execution_ask": ("down_execution_ask", "execution_down_ask"),
}


@dataclass(frozen=True)
class DatasetArtifact:
    path: Path
    manifest: dict[str, Any]
    rows: pd.DataFrame
    dataset_id: str
    feature_columns: tuple[str, ...]
    identity_quote_columns: tuple[str, ...]
    rows_sha256: str
    manifest_sha256: str
    dataset_hash: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _first_mapping_value(manifest: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> Any:
    for path in paths:
        value: Any = manifest
        for component in path:
            if not isinstance(value, dict) or component not in value:
                break
            value = value[component]
        else:
            return value
    return None


def _dataset_id(manifest: dict[str, Any]) -> str:
    value = _first_mapping_value(
        manifest,
        (
            ("dataset_id",),
            ("dataset", "dataset_id"),
            ("dataset", "id"),
        ),
    )
    if not isinstance(value, str) or not value:
        raise ValueError("assembler manifest must contain a non-empty dataset_id")
    return value


def _feature_columns(manifest: dict[str, Any]) -> tuple[str, ...]:
    value = _first_mapping_value(
        manifest,
        (
            ("feature_columns",),
            ("features",),
            ("dataset", "feature_columns"),
            ("dataset", "features"),
            ("schema", "feature_columns"),
        ),
    )
    if isinstance(value, dict):
        if isinstance(value.get("columns"), list):
            value = value["columns"]
        else:
            value = list(value)
    if not isinstance(value, list) or not value:
        raise ValueError("assembler manifest must list feature columns")
    columns: list[str] = []
    for item in value:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("column", item.get("name"))
        else:
            name = None
        if not isinstance(name, str) or not name:
            raise ValueError("manifest feature entries must contain non-empty names")
        columns.append(name)
    if len(set(columns)) != len(columns):
        raise ValueError("assembler manifest contains duplicate feature columns")
    return tuple(columns)


def _declared_rows_checksum(manifest: dict[str, Any]) -> str | None:
    direct = manifest.get("rows_sha256")
    if isinstance(direct, str):
        return direct
    for container_name in ("files", "checksums", "artifacts"):
        container = manifest.get(container_name)
        if not isinstance(container, dict):
            continue
        entry = container.get("rows.parquet")
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            checksum = entry.get("sha256", entry.get("checksum"))
            if isinstance(checksum, str):
                return checksum
    return None


def _normalize_rows(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    for column in ("decision_at", "window_start", "window_end"):
        source = cast(pd.Series, rows[column])
        try:
            parsed = pd.Series(pd.to_datetime(source, utc=True, errors="raise"), index=rows.index)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"column {column!r} contains invalid UTC timestamps") from exc
        if bool(parsed.isna().any()):
            raise ValueError(f"column {column!r} contains null timestamps")
        rows[column] = parsed
    if "resolution_available_at" in rows:
        try:
            rows["resolution_available_at"] = pd.to_datetime(
                rows["resolution_available_at"], utc=True, errors="raise"
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("resolution_available_at contains invalid timestamps") from exc

    row_ids = cast(pd.Series, rows["row_id"])
    slugs = cast(pd.Series, rows["slug"])
    assets = cast(pd.Series, rows["asset"])
    if bool(row_ids.isna().any()) or bool(row_ids.astype(str).duplicated().any()):
        raise ValueError("row_id must be non-null and unique")
    if bool(slugs.isna().any()) or bool(assets.isna().any()):
        raise ValueError("asset and slug must be non-null")

    label_source = cast(pd.Series, rows["label_binary"])
    labels = cast(pd.Series, pd.to_numeric(label_source, errors="coerce"))
    invalid_labels = label_source.notna() & ~labels.isin([0, 1])
    if bool(invalid_labels.any()):
        raise ValueError("label_binary must contain only 0, 1, or null")
    rows["label_binary"] = labels.astype("Int8")

    midpoint_source = cast(pd.Series, rows["market_midprice"])
    midpoint = cast(pd.Series, pd.to_numeric(midpoint_source, errors="coerce"))
    invalid_midpoint = midpoint_source.notna() & midpoint.isna()
    outside_probability = (midpoint.dropna() < 0) | (midpoint.dropna() > 1)
    if bool(invalid_midpoint.any()) or bool(outside_probability.any()):
        raise ValueError("market_midprice must contain probabilities in [0, 1] or null")
    rows["market_midprice"] = midpoint.astype(float)

    quote_values = cast(pd.Series, rows["quote_valid"])
    if bool(quote_values.isna().any()) or not bool(
        quote_values.isin([True, False, 0, 1]).all()
    ):
        raise ValueError("quote_valid must be non-null boolean")
    rows["quote_valid"] = quote_values.astype(bool)
    return rows


def load_dataset_artifact(path: Path, expected_dataset_id: str) -> DatasetArtifact:
    path = path.resolve()
    manifest_path = path / "manifest.json"
    rows_path = path / "rows.parquet"
    if not manifest_path.is_file() or not rows_path.is_file():
        raise FileNotFoundError(
            f"dataset artifact {path} must contain manifest.json and rows.parquet"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("assembler manifest.json is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("assembler manifest must be a JSON object")

    dataset_id = _dataset_id(manifest)
    if dataset_id != expected_dataset_id:
        raise ValueError(
            f"dataset ID guard failed: spec expects {expected_dataset_id!r}, "
            f"manifest contains {dataset_id!r}"
        )
    features = _feature_columns(manifest)
    rows_sha256 = sha256_file(rows_path)
    declared_checksum = _declared_rows_checksum(manifest)
    if declared_checksum is not None and declared_checksum != rows_sha256:
        raise ValueError(
            "rows.parquet checksum does not match the assembler manifest: "
            f"expected {declared_checksum}, got {rows_sha256}"
        )
    manifest_sha256 = sha256_file(manifest_path)

    rows = pq.read_table(rows_path).to_pandas()
    missing = sorted(set(CORE_COLUMNS).difference(rows.columns))
    if missing:
        raise ValueError(f"rows.parquet is missing required columns: {', '.join(missing)}")
    quote_columns: list[str] = []
    for semantic_name, aliases in QUOTE_COLUMN_ALIASES.items():
        present = [column for column in aliases if column in rows.columns]
        if not present:
            raise ValueError(
                f"rows.parquet is missing {semantic_name}; accepted names: "
                + ", ".join(aliases)
            )
        quote_columns.append(present[0])
    missing_features = sorted(set(features).difference(rows.columns))
    if missing_features:
        raise ValueError(
            "manifest feature columns missing from rows.parquet: "
            + ", ".join(missing_features)
        )
    rows = _normalize_rows(rows)
    dataset_hash = canonical_json_hash(
        {
            "dataset_id": dataset_id,
            "rows_sha256": rows_sha256,
            "manifest_sha256": manifest_sha256,
        }
    )
    identity_quote = [*CORE_COLUMNS]
    identity_quote.extend(quote_columns)
    if "resolution_available_at" in rows.columns:
        identity_quote.append("resolution_available_at")
    return DatasetArtifact(
        path=path,
        manifest=manifest,
        rows=rows,
        dataset_id=dataset_id,
        feature_columns=features,
        identity_quote_columns=tuple(dict.fromkeys(identity_quote)),
        rows_sha256=rows_sha256,
        manifest_sha256=manifest_sha256,
        dataset_hash=dataset_hash,
    )


def validate_selected_features(
    rows: pd.DataFrame,
    manifest_features: tuple[str, ...],
    requested: str | list[str],
) -> tuple[tuple[str, ...], pd.DataFrame]:
    selected = manifest_features if requested == "all" else tuple(requested)
    unknown = sorted(set(selected).difference(manifest_features))
    if unknown:
        raise ValueError(
            "experiment selects features absent from the assembler manifest: "
            + ", ".join(unknown)
        )

    numeric = pd.DataFrame(index=rows.index)
    all_null: list[str] = []
    for feature in selected:
        source = cast(pd.Series, rows[feature])
        converted = cast(pd.Series, pd.to_numeric(source, errors="coerce"))
        invalid = source.notna() & converted.isna()
        if bool(invalid.any()):
            raise ValueError(f"selected feature {feature!r} contains non-numeric values")
        values = converted.to_numpy(dtype=np.float64)
        finite_or_nan = np.isfinite(values) | np.isnan(values)
        if not bool(finite_or_nan.all()):
            raise ValueError(f"selected feature {feature!r} contains infinity")
        if bool(converted.isna().all()):
            all_null.append(feature)
        numeric[feature] = converted.astype(float)
    if all_null:
        raise ValueError("selected features are all-null: " + ", ".join(all_null))
    return selected, numeric
