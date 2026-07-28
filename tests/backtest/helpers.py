from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from backtest.models import PredictionCandidate
from backtest.spec import BacktestSpec, ExecutionQuotePreference

UTC = timezone.utc
BASE_TIME = datetime(2026, 7, 18, tzinfo=UTC)


def candidate(
    row_id: str,
    *,
    minute: int = 0,
    duration_minutes: int = 5,
    probability: str = "0.8",
    label: bool = True,
    quote_valid: bool = True,
    execution_up: str | None = "0.50",
    execution_down: str | None = "0.50",
    decision_up: str | None = "0.505",
    decision_down: str | None = "0.505",
    midprice: str = "0.50",
) -> PredictionCandidate:
    decision_at = BASE_TIME + timedelta(minutes=minute)
    return PredictionCandidate(
        row_id=row_id,
        decision_at=decision_at,
        window_end=decision_at + timedelta(minutes=duration_minutes),
        label_binary=label,
        calibrated_probability=Decimal(probability),
        quote_valid=quote_valid,
        execution_up_ask=Decimal(execution_up) if execution_up is not None else None,
        execution_down_ask=Decimal(execution_down) if execution_down is not None else None,
        decision_up_ask=Decimal(decision_up) if decision_up is not None else None,
        decision_down_ask=Decimal(decision_down) if decision_down is not None else None,
        market_midprice=Decimal(midprice),
        split="test",
    )


def spec(
    *,
    thresholds: tuple[str, ...] = ("0.6",),
    stake: str = "10",
    capital: str = "100",
    max_open: int = 10,
    fee_bps: str = "0",
    slippage_bps: str = "0",
    price_min: str = "0.49",
    price_max: str = "0.52",
    preference: ExecutionQuotePreference = ExecutionQuotePreference.EXECUTION_THEN_DECISION,
) -> BacktestSpec:
    return BacktestSpec(
        probability_thresholds=tuple(Decimal(value) for value in thresholds),
        fixed_stake_usd=Decimal(stake),
        initial_capital_usd=Decimal(capital),
        fee_bps=Decimal(fee_bps),
        slippage_bps=Decimal(slippage_bps),
        max_open_positions=max_open,
        entry_price_min=Decimal(price_min),
        entry_price_max=Decimal(price_max),
        execution_quote_preference=preference,
    )


def write_experiment(path: Path) -> Path:
    path.mkdir(parents=True)
    timestamps = pa.array(
        [BASE_TIME, BASE_TIME + timedelta(minutes=1), BASE_TIME + timedelta(minutes=2)],
        type=pa.timestamp("us", tz="UTC"),
    )
    window_ends = pa.array(
        [BASE_TIME + timedelta(minutes=5), BASE_TIME + timedelta(minutes=6), BASE_TIME + timedelta(minutes=7)],
        type=pa.timestamp("us", tz="UTC"),
    )
    table = pa.table(
        {
            "row_id": ["test-up", "train-only", "test-down"],
            "decision_at": timestamps,
            "window_end": window_ends,
            "label_binary": [True, False, False],
            "calibrated_probability": [0.8, 0.9, 0.2],
            "quote_valid": [True, True, True],
            "up_execution_ask": [0.50, 0.50, 0.50],
            "down_execution_ask": [0.50, 0.50, 0.50],
            "decision_up_ask": [0.51, 0.51, 0.51],
            "decision_down_ask": [0.51, 0.51, 0.51],
            "market_midprice": [0.50, 0.50, 0.50],
            "split": ["test", "train", "test"],
        }
    )
    predictions = path / "predictions.parquet"
    pq.write_table(table, predictions)
    digest = hashlib.sha256(predictions.read_bytes()).hexdigest()
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": "synthetic-experiment",
                "artifacts": [
                    {
                        "path": "predictions.parquet",
                        "sha256": digest,
                        "rows": table.num_rows,
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
