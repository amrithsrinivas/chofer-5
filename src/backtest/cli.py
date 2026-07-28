"""CLI entry point for deterministic training-artifact replay backtests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .runner import run_backtest_from_toml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backtest")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="replay a training experiment's predictions")
    run.add_argument(
        "--experiment-dir",
        "--experiment",
        dest="experiment_dir",
        type=Path,
        required=True,
        help="directory containing predictions.parquet and manifest.json",
    )
    run.add_argument("--spec", type=Path, required=True, help="validated TOML backtest spec")
    run.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="parent directory for content-addressed backtest artifacts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = run_backtest_from_toml(
            experiment_dir=args.experiment_dir,
            spec_path=args.spec,
            output_root=args.output_dir,
        )
    except (OSError, ValueError, ValidationError) as exc:
        print(f"backtest error: {exc}", file=sys.stderr)
        return 2
    print(f"backtest {artifact.backtest_id}: {artifact.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
