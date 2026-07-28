"""Synthetic assembler artifacts for training tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture()
def assembler_artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "dataset"
    artifact.mkdir()
    rows: dict[str, list] = {
        "row_id": [],
        "asset": [],
        "slug": [],
        "decision_at": [],
        "window_start": [],
        "window_end": [],
        "resolution_available_at": [],
        "label_binary": [],
        "market_midprice": [],
        "up_decision_ask": [],
        "down_decision_ask": [],
        "up_execution_ask": [],
        "down_execution_ask": [],
        "quote_valid": [],
        "signal": [],
        "with_nan": [],
    }
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for market in range(12):
        window_start = origin + timedelta(minutes=5 * market)
        window_end = window_start + timedelta(minutes=5)
        label = market % 2
        for decision_number in range(2):
            decision_at = window_start + timedelta(minutes=4, seconds=decision_number * 10)
            rows["row_id"].append(f"row-{market:02d}-{decision_number}")
            rows["asset"].append("BTC")
            rows["slug"].append(f"btc-market-{market:02d}")
            rows["decision_at"].append(decision_at)
            rows["window_start"].append(window_start)
            rows["window_end"].append(window_end)
            resolution_delay = timedelta(hours=1) if market == 8 else timedelta(seconds=30)
            rows["resolution_available_at"].append(window_end + resolution_delay)
            rows["label_binary"].append(label)
            midpoint = 0.65 if label else 0.35
            rows["market_midprice"].append(midpoint)
            rows["up_decision_ask"].append(midpoint + 0.01)
            rows["down_decision_ask"].append(1.01 - midpoint)
            rows["up_execution_ask"].append(midpoint + 0.02)
            rows["down_execution_ask"].append(1.02 - midpoint)
            rows["quote_valid"].append(True)
            rows["signal"].append(float(label) + decision_number * 0.01)
            rows["with_nan"].append(
                None if (market + decision_number) % 5 == 0 else float(market)
            )

    table = pa.table(rows)
    rows_path = artifact / "rows.parquet"
    pq.write_table(table, rows_path)
    rows_sha256 = hashlib.sha256(rows_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "dataset_id": "synthetic-btc-v1",
        "feature_columns": ["signal", "with_nan"],
        "files": {"rows.parquet": {"sha256": rows_sha256}},
    }
    (artifact / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return artifact


def write_spec(
    path: Path,
    artifact: Path,
    *,
    dataset_id: str = "synthetic-btc-v1",
    features: str = '["signal", "with_nan"]',
    calibration: str = "platt",
) -> Path:
    path.write_text(
        f"""
features = {features}

[dataset]
path = {json.dumps(str(artifact))}
dataset_id = {json.dumps(dataset_id)}

[split]
train_fraction = 0.5
validation_fraction = 0.25
test_fraction = 0.25
purge_seconds = 0

[xgboost]
seed = 17
early_stopping_rounds = 4

[xgboost.params]
n_estimators = 20
max_depth = 2
learning_rate = 0.2
subsample = 1.0
colsample_bytree = 1.0

[calibration]
method = {json.dumps(calibration)}

[threshold_policy]
reference = "policy://edge-v1"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def experiment_spec(tmp_path: Path, assembler_artifact: Path) -> Path:
    return write_spec(tmp_path / "experiment.toml", assembler_artifact)
