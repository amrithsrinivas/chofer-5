"""Training experiment prediction artifact loading and validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq

from .models import PredictionCandidate


_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "row_id": ("row_id",),
    "decision_at": ("decision_at",),
    "window_end": ("window_end",),
    "label_binary": ("label_binary",),
    "calibrated_probability": ("calibrated_probability",),
    "quote_valid": ("quote_valid",),
    "execution_up_ask": (
        "execution_up_ask",
        "up_execution_ask",
        "up_execution_ask_price",
        "up_ask_at_execution",
    ),
    "execution_down_ask": (
        "execution_down_ask",
        "down_execution_ask",
        "down_execution_ask_price",
        "down_ask_at_execution",
    ),
    "decision_up_ask": (
        "decision_up_ask",
        "up_decision_ask",
        "up_decision_ask_price",
        "up_ask",
    ),
    "decision_down_ask": (
        "decision_down_ask",
        "down_decision_ask",
        "down_decision_ask_price",
        "down_ask",
    ),
    "market_midprice": ("market_midprice", "up_market_midprice"),
    "split": ("split", "split_name", "dataset_split"),
}

_REQUIRED = {
    "row_id",
    "decision_at",
    "window_end",
    "label_binary",
    "calibrated_probability",
    "quote_valid",
    "market_midprice",
}


@dataclass(frozen=True, slots=True)
class ExperimentPredictions:
    experiment_dir: Path
    predictions_path: Path
    manifest_path: Path
    manifest: dict[str, Any]
    predictions_sha256: str
    manifest_sha256: str
    candidates: tuple[PredictionCandidate, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_columns(names: Iterable[str]) -> dict[str, str | None]:
    available = set(names)
    resolved: dict[str, str | None] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        resolved[canonical] = next((alias for alias in aliases if alias in available), None)
    missing = sorted(name for name in _REQUIRED if resolved[name] is None)
    if missing:
        raise ValueError(f"predictions.parquet is missing required columns: {', '.join(missing)}")
    if resolved["execution_up_ask"] is None and resolved["decision_up_ask"] is None:
        raise ValueError("predictions.parquet has no UP execution or decision ask column")
    if resolved["execution_down_ask"] is None and resolved["decision_down_ask"] is None:
        raise ValueError("predictions.parquet has no DOWN execution or decision ask column")
    return resolved


def _decimal(value: Any, *, field: str, row_id: str, nullable: bool = False) -> Decimal | None:
    if value is None:
        if nullable:
            return None
        raise ValueError(f"row {row_id}: {field} is null")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"row {row_id}: {field} is not decimal-compatible") from exc
    if not result.is_finite():
        if nullable:
            return None
        raise ValueError(f"row {row_id}: {field} must be finite")
    return result


def _timestamp(value: Any, *, field: str, row_id: str) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError(f"row {row_id}: {field} must be a timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"row {row_id}: {field} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _boolean(value: Any, *, field: str, row_id: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"row {row_id}: {field} must be boolean or 0/1")


def _manifest_prediction_hash(value: Any) -> str | None:
    """Find a declared predictions hash in common experiment manifest layouts."""
    if isinstance(value, dict):
        direct = value.get("predictions_sha256")
        if isinstance(direct, str):
            return direct
        path_value = value.get("path") or value.get("name")
        digest = value.get("sha256") or value.get("hash")
        if isinstance(path_value, str) and Path(path_value).name == "predictions.parquet":
            if isinstance(digest, str):
                return digest
        for key, child in value.items():
            if key == "predictions.parquet" and isinstance(child, str):
                return child
            found = _manifest_prediction_hash(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _manifest_prediction_hash(child)
            if found is not None:
                return found
    return None


def _manifest_path(experiment_dir: Path) -> Path:
    candidates = (
        experiment_dir / "manifest.json",
        experiment_dir / "experiment_manifest.json",
    )
    found = [path for path in candidates if path.is_file()]
    if not found:
        raise ValueError(
            f"experiment directory {experiment_dir} has no manifest.json or "
            "experiment_manifest.json"
        )
    return found[0]


def load_experiment_predictions(
    experiment_dir: Path, *, selected_split: str = "test"
) -> ExperimentPredictions:
    experiment_dir = Path(experiment_dir)
    predictions_path = experiment_dir / "predictions.parquet"
    if not predictions_path.is_file():
        raise ValueError(f"missing training predictions artifact: {predictions_path}")
    manifest_path = _manifest_path(experiment_dir)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"invalid experiment manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("experiment manifest root must be a JSON object")

    predictions_hash = sha256_file(predictions_path)
    declared_hash = _manifest_prediction_hash(manifest)
    if declared_hash is not None and declared_hash.lower() != predictions_hash:
        raise ValueError("predictions.parquet sha256 does not match the experiment manifest")

    table = pq.read_table(predictions_path)
    columns = _resolve_columns(table.column_names)
    candidates: list[PredictionCandidate] = []
    seen: set[str] = set()
    for raw in table.to_pylist():
        split_column = columns["split"]
        split = str(raw[split_column]) if split_column and raw[split_column] is not None else None
        if split is not None and split != selected_split:
            continue
        row_id = str(raw[columns["row_id"]])  # type: ignore[index]
        if not row_id:
            raise ValueError("prediction row_id must not be empty")
        if row_id in seen:
            raise ValueError(f"duplicate row_id in selected split: {row_id}")
        seen.add(row_id)
        decision_at = _timestamp(
            raw[columns["decision_at"]], field="decision_at", row_id=row_id  # type: ignore[index]
        )
        window_end = _timestamp(
            raw[columns["window_end"]], field="window_end", row_id=row_id  # type: ignore[index]
        )
        if window_end <= decision_at:
            raise ValueError(f"row {row_id}: window_end must be after decision_at")
        probability = _decimal(
            raw[columns["calibrated_probability"]],  # type: ignore[index]
            field="calibrated_probability",
            row_id=row_id,
        )
        assert probability is not None
        if probability < 0 or probability > 1:
            raise ValueError(f"row {row_id}: calibrated_probability must be in [0, 1]")
        quote_valid = _boolean(
            raw[columns["quote_valid"]],  # type: ignore[index]
            field="quote_valid",
            row_id=row_id,
        )
        midprice = _decimal(
            raw[columns["market_midprice"]],  # type: ignore[index]
            field="market_midprice",
            row_id=row_id,
            nullable=not quote_valid,
        )
        if midprice is None:
            # Invalid-quote rows are retained in the coverage denominator but
            # can never enter. A neutral mark avoids exposing labels or
            # failing replay on an intentionally preserved data hole.
            midprice = Decimal("0.5")
        if midprice < 0 or midprice > 1:
            raise ValueError(f"row {row_id}: market_midprice must be in [0, 1]")

        def optional_price(name: str) -> Decimal | None:
            column = columns[name]
            if column is None:
                return None
            price = _decimal(raw[column], field=name, row_id=row_id, nullable=True)
            if price is not None and (price <= 0 or price >= 1):
                raise ValueError(f"row {row_id}: {name} must be in (0, 1)")
            return price

        candidates.append(
            PredictionCandidate(
                row_id=row_id,
                decision_at=decision_at,
                window_end=window_end,
                label_binary=_boolean(
                    raw[columns["label_binary"]],  # type: ignore[index]
                    field="label_binary",
                    row_id=row_id,
                ),
                calibrated_probability=probability,
                quote_valid=quote_valid,
                execution_up_ask=optional_price("execution_up_ask"),
                execution_down_ask=optional_price("execution_down_ask"),
                decision_up_ask=optional_price("decision_up_ask"),
                decision_down_ask=optional_price("decision_down_ask"),
                market_midprice=midprice,
                split=split,
            )
        )
    if not candidates:
        split_note = " (predictions have no split column)" if columns["split"] is None else ""
        raise ValueError(f"no prediction rows selected for split {selected_split!r}{split_note}")
    candidates.sort(key=lambda row: (row.decision_at, row.row_id))
    return ExperimentPredictions(
        experiment_dir=experiment_dir,
        predictions_path=predictions_path,
        manifest_path=manifest_path,
        manifest=manifest,
        predictions_sha256=predictions_hash,
        manifest_sha256=sha256_file(manifest_path),
        candidates=tuple(candidates),
    )
