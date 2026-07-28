from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from backtest.cli import main
from backtest.predictions import load_experiment_predictions
from backtest.runner import run_backtest
from backtest.spec import BacktestSpec, load_spec
from .helpers import spec, write_experiment


def test_toml_threshold_range_defaults_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "backtest.toml"
    path.write_text(
        """
version = 1
threshold_range = { start = 0.50, stop = 0.60, step = 0.05 }
fixed_stake_usd = 10
initial_capital_usd = 100
fee_bps = 5
slippage_bps = 10
max_open_positions = 3
execution_quote_preference = "prefer_execution"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    loaded = load_spec(path)
    assert loaded.thresholds == (Decimal("0.50"), Decimal("0.55"), Decimal("0.60"))
    assert loaded.selected_split == "test"
    assert loaded.entry_price_min == Decimal("0.49")
    assert loaded.entry_price_max == Decimal("0.52")
    assert loaded.execution_quote_preference.value == "execution_then_decision"

    with pytest.raises(ValidationError, match=r"\[0.5, 1\]"):
        BacktestSpec(
            probability_thresholds=(Decimal("0.49"),),
            fixed_stake_usd=Decimal("10"),
            initial_capital_usd=Decimal("100"),
            max_open_positions=1,
        )


def test_prediction_loader_selects_test_and_checks_manifest_hash(tmp_path: Path) -> None:
    experiment = write_experiment(tmp_path / "experiment")
    loaded = load_experiment_predictions(experiment)
    assert [row.row_id for row in loaded.candidates] == ["test-up", "test-down"]

    manifest_path = experiment / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        load_experiment_predictions(experiment)


def test_content_addressed_artifacts_are_complete_and_deterministic(tmp_path: Path) -> None:
    experiment = write_experiment(tmp_path / "experiment")
    output = tmp_path / "backtests"
    policy = spec(thresholds=("0.6", "0.7"))

    first = run_backtest(experiment_dir=experiment, spec=policy, output_root=output)
    second = run_backtest(experiment_dir=experiment, spec=policy, output_root=output)

    assert first.backtest_id == second.backtest_id
    assert first.path == second.path
    assert len(first.backtest_id) == 64
    expected_files = {
        "threshold_metrics.parquet",
        "threshold_metrics.json",
        "trades.parquet",
        "equity_curve.parquet",
        "analytics.json",
        "manifest.json",
    }
    assert {path.name for path in first.path.iterdir()} == expected_files

    manifest = json.loads((first.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_mode"] == "replay"
    assert manifest["policies"]["outcome_visibility"] == "settlement_only"
    assert manifest["input"]["candidate_rows"] == 2
    for artifact in manifest["artifacts"]:
        data = (first.path / artifact["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]

    threshold_table = pq.read_table(first.path / "threshold_metrics.parquet")
    trades_table = pq.read_table(first.path / "trades.parquet")
    equity_table = pq.read_table(first.path / "equity_curve.parquet")
    assert threshold_table.num_rows == 2
    assert trades_table.num_rows == 4
    assert equity_table.num_rows == 10

    analytics = json.loads((first.path / "analytics.json").read_text(encoding="utf-8"))
    assert analytics["execution_mode"] == "replay"
    assert analytics["coverage_definitions"]["candidate_coverage"] == (
        "signal_count / candidate_count"
    )
    first_threshold = analytics["thresholds"][0]
    assert {row["key"] for row in first_threshold["side_breakdown"]} == {"UP", "DOWN"}
    assert {row["key"] for row in first_threshold["entry_price_buckets"]} == {"0.50-0.51"}


def test_cli_runs_backtest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    experiment = write_experiment(tmp_path / "experiment")
    spec_path = tmp_path / "backtest.toml"
    spec_path.write_text(
        """
thresholds = [0.60]
fixed_stake_usd = 10
initial_capital_usd = 100
fee_bps = 0
slippage_bps = 0
max_open_positions = 2
execution_quote_preference = "execution_then_decision"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "backtests"
    assert main(
        [
            "run",
            "--experiment-dir",
            str(experiment),
            "--spec",
            str(spec_path),
            "--output-dir",
            str(output),
        ]
    ) == 0
    stdout = capsys.readouterr().out
    assert stdout.startswith("backtest ")
    artifact_dirs = list(output.iterdir())
    assert len(artifact_dirs) == 1
    assert (artifact_dirs[0] / "manifest.json").is_file()
