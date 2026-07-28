"""Command-line entry point for content-addressed training experiments."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .trainer import TrainingError, train_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trainer")
    parser.add_argument("experiment_spec", type=Path, help="validated TOML experiment spec")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="parent directory for immutable content-addressed experiments",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        experiment_dir = train_experiment(args.experiment_spec, args.output_dir)
    except (FileNotFoundError, OSError, TrainingError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(experiment_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
