"""Atomic, content-addressed backtest artifact persistence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .analytics import build_analytics

from .predictions import ExperimentPredictions, sha256_file
from .replay import ReplayResult
from .spec import BacktestSpec

IMPLEMENTATION_VERSION = "backtest-replay-v1"


@dataclass(frozen=True, slots=True)
class BacktestArtifact:
    backtest_id: str
    path: Path
    manifest: dict[str, Any]


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    else:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    return text.encode("utf-8")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request(experiment: ExperimentPredictions, spec: BacktestSpec) -> dict[str, Any]:
    return {
        "implementation_version": IMPLEMENTATION_VERSION,
        "execution_mode": "replay",
        "predictions_sha256": experiment.predictions_sha256,
        "experiment_manifest_sha256": experiment.manifest_sha256,
        "spec": spec.canonical_dict(),
    }


def _threshold_table(metrics: list[dict[str, Any]]) -> pa.Table:
    rows = []
    for metric in metrics:
        row = dict(metric)
        row["rejection_counts_json"] = json.dumps(
            row.pop("rejection_counts"), sort_keys=True, separators=(",", ":")
        )
        rows.append(row)
    schema = pa.schema(
        [
            ("threshold", pa.float64()),
            ("candidate_count", pa.int64()),
            ("signal_count", pa.int64()),
            ("quote_count", pa.int64()),
            ("entry_band_count", pa.int64()),
            ("entry_count", pa.int64()),
            ("candidate_coverage", pa.float64()),
            ("quote_coverage", pa.float64()),
            ("signal_fill_coverage", pa.float64()),
            ("win_count", pa.int64()),
            ("loss_count", pa.int64()),
            ("winrate", pa.float64()),
            ("winrate_edge_over_0_5", pa.float64()),
            ("winrate_wilson_lower_95", pa.float64()),
            ("winrate_wilson_upper_95", pa.float64()),
            ("average_entry_price", pa.float64()),
            ("median_entry_price", pa.float64()),
            ("total_pnl_usd", pa.float64()),
            ("average_pnl_usd", pa.float64()),
            ("roi", pa.float64()),
            ("profit_factor", pa.float64()),
            ("max_drawdown_usd", pa.float64()),
            ("max_drawdown_fraction", pa.float64()),
            ("final_capital_usd", pa.float64()),
            ("rejection_counts_json", pa.string()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _trade_rows(results: list[ReplayResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for trade in result.trades:
            rows.append(
                {
                    "threshold": float(trade.threshold),
                    "row_id": trade.row_id,
                    "side": trade.side.value,
                    "signal_at": trade.signal_at,
                    "window_end": trade.window_end,
                    "status": trade.status.value,
                    "rejection_reason": (
                        trade.rejection_reason.value if trade.rejection_reason else None
                    ),
                    "quote_source": trade.quote_source,
                    "reference_entry_price": (
                        float(trade.reference_entry_price)
                        if trade.reference_entry_price is not None
                        else None
                    ),
                    "effective_entry_price": (
                        float(trade.effective_entry_price)
                        if trade.effective_entry_price is not None
                        else None
                    ),
                    "stake_micros": trade.stake_micros,
                    "token_quantity": (
                        str(trade.token_quantity) if trade.token_quantity is not None else None
                    ),
                    "capital_after_entry_micros": trade.capital_after_entry_micros,
                    "settled_at": trade.settled_at,
                    "won": trade.won,
                    "payout_micros": trade.payout_micros,
                    "pnl_micros": trade.pnl_micros,
                    "pnl_usd": (
                        trade.pnl_micros / 1_000_000
                        if trade.pnl_micros is not None
                        else None
                    ),
                    "capital_after_settlement_micros": trade.capital_after_settlement_micros,
                }
            )
    rows.sort(key=lambda row: (row["threshold"], row["signal_at"], row["row_id"]))
    return rows


def _trade_table(results: list[ReplayResult]) -> pa.Table:
    schema = pa.schema(
        [
            ("threshold", pa.float64()),
            ("row_id", pa.string()),
            ("side", pa.string()),
            ("signal_at", pa.timestamp("us", tz="UTC")),
            ("window_end", pa.timestamp("us", tz="UTC")),
            ("status", pa.string()),
            ("rejection_reason", pa.string()),
            ("quote_source", pa.string()),
            ("reference_entry_price", pa.float64()),
            ("effective_entry_price", pa.float64()),
            ("stake_micros", pa.int64()),
            ("token_quantity", pa.string()),
            ("capital_after_entry_micros", pa.int64()),
            ("settled_at", pa.timestamp("us", tz="UTC")),
            ("won", pa.bool_()),
            ("payout_micros", pa.int64()),
            ("pnl_micros", pa.int64()),
            ("pnl_usd", pa.float64()),
            ("capital_after_settlement_micros", pa.int64()),
        ]
    )
    return pa.Table.from_pylist(_trade_rows(results), schema=schema)


def _equity_table(results: list[ReplayResult]) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for result in results:
        for point in result.equity_curve:
            rows.append(
                {
                    "threshold": float(point.threshold),
                    "event_at": point.event_at,
                    "event_type": point.event_type.value,
                    "row_id": point.row_id,
                    "cash_micros": point.cash_micros,
                    "open_position_count": point.open_position_count,
                    "reserved_stake_micros": point.reserved_stake_micros,
                    "marked_position_value_micros": point.marked_position_value_micros,
                    "equity_micros": point.equity_micros,
                    "peak_equity_micros": point.peak_equity_micros,
                    "drawdown_micros": point.drawdown_micros,
                    "drawdown_fraction": float(point.drawdown_fraction),
                }
            )
    rows.sort(
        key=lambda row: (
            row["threshold"],
            row["event_at"],
            {"initial": 0, "settlement": 1, "entry": 2}[row["event_type"]],
            row["row_id"] or "",
        )
    )
    schema = pa.schema(
        [
            ("threshold", pa.float64()),
            ("event_at", pa.timestamp("us", tz="UTC")),
            ("event_type", pa.string()),
            ("row_id", pa.string()),
            ("cash_micros", pa.int64()),
            ("open_position_count", pa.int64()),
            ("reserved_stake_micros", pa.int64()),
            ("marked_position_value_micros", pa.int64()),
            ("equity_micros", pa.int64()),
            ("peak_equity_micros", pa.int64()),
            ("drawdown_micros", pa.int64()),
            ("drawdown_fraction", pa.float64()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_json_bytes(value, pretty=True))
    _fsync_file(path)


def _write_parquet(path: Path, table: pa.Table) -> None:
    pq.write_table(table, path, compression="zstd")
    _fsync_file(path)


def _verify_existing(path: Path, backtest_id: str) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"existing content-addressed directory is incomplete: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("backtest_id") != backtest_id:
        raise RuntimeError(f"existing backtest manifest identity mismatch: {path}")
    for artifact in manifest.get("artifacts", []):
        artifact_path = path / artifact["path"]
        if not artifact_path.is_file() or sha256_file(artifact_path) != artifact["sha256"]:
            raise RuntimeError(f"existing backtest artifact hash mismatch: {artifact_path}")
    return manifest


def write_backtest_artifact(
    output_root: Path,
    *,
    experiment: ExperimentPredictions,
    spec: BacktestSpec,
    results: list[ReplayResult],
) -> BacktestArtifact:
    request = _request(experiment, spec)
    request_sha256 = hashlib.sha256(_json_bytes(request)).hexdigest()
    backtest_id = request_sha256
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    final_path = output_root / backtest_id
    if final_path.exists():
        manifest = _verify_existing(final_path, backtest_id)
        return BacktestArtifact(backtest_id=backtest_id, path=final_path, manifest=manifest)

    stage = output_root / f".{backtest_id}.tmp-{uuid.uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        metrics, analytics = build_analytics(results, spec)
        files: list[tuple[str, int]] = []

        threshold_parquet = _threshold_table(metrics)
        _write_parquet(stage / "threshold_metrics.parquet", threshold_parquet)
        files.append(("threshold_metrics.parquet", threshold_parquet.num_rows))
        _write_json(stage / "threshold_metrics.json", metrics)
        files.append(("threshold_metrics.json", len(metrics)))

        trades = _trade_table(results)
        _write_parquet(stage / "trades.parquet", trades)
        files.append(("trades.parquet", trades.num_rows))

        equity = _equity_table(results)
        _write_parquet(stage / "equity_curve.parquet", equity)
        files.append(("equity_curve.parquet", equity.num_rows))

        _write_json(stage / "analytics.json", analytics)
        files.append(("analytics.json", len(analytics["thresholds"])))

        artifacts = [
            {
                "path": name,
                "sha256": sha256_file(stage / name),
                "bytes": (stage / name).stat().st_size,
                "rows": rows,
            }
            for name, rows in files
        ]
        manifest = {
            "schema_version": 1,
            "backtest_id": backtest_id,
            "request_sha256": request_sha256,
            "execution_mode": "replay",
            "implementation_version": IMPLEMENTATION_VERSION,
            "input": {
                "experiment_manifest_path": experiment.manifest_path.name,
                "experiment_manifest_sha256": experiment.manifest_sha256,
                "predictions_path": experiment.predictions_path.name,
                "predictions_sha256": experiment.predictions_sha256,
                "selected_split": spec.selected_split,
                "candidate_rows": len(experiment.candidates),
            },
            "spec": spec.canonical_dict(),
            "policies": {
                "event_order": "UTC chronological; settlements before entries at equal timestamps",
                "execution_quote_semantics": "L2 selected-side ask at decision_at plus modeled latency",
                "decision_quote_fallback": (
                    spec.execution_quote_preference.value == "execution_then_decision"
                ),
                "outcome_visibility": "settlement_only",
                "money_representation": "signed integer USD micros",
                "price_representation": "Decimal quantized to 0.000001",
                "cost_model": "fee_bps and slippage_bps added to selected-side ask",
                "stake_model": "fixed cash cost; tokens = stake / effective entry",
            },
            "artifacts": artifacts,
        }
        _write_json(stage / "manifest.json", manifest)
        _fsync_dir(stage)
        try:
            os.rename(stage, final_path)
        except FileExistsError:
            shutil.rmtree(stage, ignore_errors=True)
            manifest = _verify_existing(final_path, backtest_id)
            return BacktestArtifact(backtest_id=backtest_id, path=final_path, manifest=manifest)
        _fsync_dir(output_root)
        return BacktestArtifact(backtest_id=backtest_id, path=final_path, manifest=manifest)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
