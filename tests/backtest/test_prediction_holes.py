from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from backtest.predictions import load_experiment_predictions


def test_invalid_quote_hole_with_null_prices_is_retained(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    decision = datetime(2026, 7, 18, tzinfo=timezone.utc)
    table = pa.table(
        {
            "row_id": ["hole"],
            "decision_at": pa.array([decision], pa.timestamp("us", tz="UTC")),
            "window_end": pa.array(
                [decision + timedelta(minutes=5)], pa.timestamp("us", tz="UTC")
            ),
            "label_binary": [True],
            "calibrated_probability": [0.8],
            "quote_valid": [False],
            "execution_up_ask": pa.array([None], pa.float64()),
            "execution_down_ask": pa.array([None], pa.float64()),
            "decision_up_ask": pa.array([None], pa.float64()),
            "decision_down_ask": pa.array([None], pa.float64()),
            "market_midprice": pa.array([None], pa.float64()),
            "split": ["test"],
        }
    )
    predictions = experiment / "predictions.parquet"
    pq.write_table(table, predictions)
    digest = hashlib.sha256(predictions.read_bytes()).hexdigest()
    (experiment / "manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": "holes",
                "checksums": {"predictions.parquet": {"sha256": digest}},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_experiment_predictions(experiment)
    assert len(loaded.candidates) == 1
    candidate = loaded.candidates[0]
    assert candidate.quote_valid is False
    assert candidate.execution_up_ask is None
    assert float(candidate.market_midprice) == 0.5
