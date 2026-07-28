"""Command-line entry point for manifest-driven dataset assembly."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .builder import build_dataset


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="assembler")
    subcommands = root.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="assemble an immutable point-in-time dataset")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--data-dir", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--limit", type=int)
    build.add_argument("--force", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser().error("--limit must be positive")
    result = build_dataset(
        spec_path=args.spec,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        limit=args.limit,
        force=args.force,
    )
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
