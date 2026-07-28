"""Backtest orchestration from an immutable training experiment artifact."""

from __future__ import annotations

from pathlib import Path

from .artifacts import BacktestArtifact, write_backtest_artifact
from .predictions import load_experiment_predictions
from .replay import ReplayEngine
from .spec import BacktestSpec, load_spec


def run_backtest(
    *, experiment_dir: Path, spec: BacktestSpec, output_root: Path
) -> BacktestArtifact:
    experiment = load_experiment_predictions(
        experiment_dir, selected_split=spec.selected_split
    )
    results = [
        ReplayEngine(
            experiment.candidates,
            threshold=threshold,
            spec=spec,
        ).run()
        for threshold in spec.thresholds
    ]
    return write_backtest_artifact(
        output_root,
        experiment=experiment,
        spec=spec,
        results=results,
    )


def run_backtest_from_toml(
    *, experiment_dir: Path, spec_path: Path, output_root: Path
) -> BacktestArtifact:
    return run_backtest(
        experiment_dir=experiment_dir,
        spec=load_spec(spec_path),
        output_root=output_root,
    )
