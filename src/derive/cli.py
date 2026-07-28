"""CLI for resumable canonical market-input compaction."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from tqdm.auto import tqdm

from .pipeline import CanonicalDeriver
from .spec import load_derive_spec

REPO_ROOT = Path(__file__).resolve().parents[2]


def _days(start: str, end: str) -> list[str]:
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ValueError("--to must be on or after --from")
    result = []
    while first <= last:
        result.append(first.isoformat())
        first += timedelta(days=1)
    return result


def _one(task: tuple[str, str, str, str, tuple[int, ...], int, bool]) -> tuple[str, str, int, bool]:
    data_dir, kind, asset, day, offsets, depth, force = task
    deriver = CanonicalDeriver(data_dir, checkpoint_offsets_ms=offsets, depth_levels=depth)
    result = (
        deriver.derive_market(asset, day, force=force)
        if kind == "market"
        else deriver.derive_underlying(asset, day, force=force)
    )
    return result.unit_id, result.status, result.rows, result.skipped


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="derive")
    sub = root.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="compact raw inputs into atomic asset/date units")
    build.add_argument("--data-dir", type=Path, required=True)
    build.add_argument(
        "--spec",
        type=Path,
        default=REPO_ROOT / "configs" / "derive" / "market-inputs-v1.toml",
    )
    build.add_argument("--kind", choices=["market", "underlying", "all"], default="all")
    build.add_argument("--asset", action="append", choices=["BTC", "ETH", "SOL", "XRP"])
    build.add_argument("--from", dest="start", required=True)
    build.add_argument("--to", dest="end", required=True)
    build.add_argument("--workers", type=int, default=1)
    build.add_argument("--depth-levels", type=int)
    build.add_argument(
        "--checkpoint-offset-ms",
        action="append",
        type=int,
        help="repeatable; defaults include T-30s and T-29.75s",
    )
    build.add_argument("--force", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.workers <= 0:
        parser().error("--workers must be positive")
    assets = args.asset or ["BTC", "ETH", "SOL", "XRP"]
    kinds = ["market", "underlying"] if args.kind == "all" else [args.kind]
    spec = load_derive_spec(args.spec)
    offsets = tuple(args.checkpoint_offset_ms or spec.checkpoint_offsets_ms)
    depth_levels = args.depth_levels or spec.depth_levels
    tasks = [
        (str(args.data_dir), kind, asset, day, offsets, depth_levels, args.force)
        for day in _days(args.start, args.end)
        for asset in assets
        for kind in kinds
    ]
    completed = skipped = failed = 0
    if args.workers == 1:
        outcomes = map(_one, tasks)
        progress = tqdm(
            outcomes,
            total=len(tasks),
            desc="Deriving canonical partitions",
            unit="partition",
            dynamic_ncols=True,
            disable=None,
        )
        for unit, status, rows, was_skipped in progress:
            completed += status == "complete"
            skipped += was_skipped
            progress.set_postfix_str(
                f"{unit} {status} rows={rows} skipped={was_skipped}",
                refresh=False,
            )
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_one, task): task for task in tasks}
            progress = tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Deriving canonical partitions",
                unit="partition",
                dynamic_ncols=True,
                disable=None,
            )
            for future in progress:
                try:
                    unit, status, rows, was_skipped = future.result()
                    completed += status == "complete"
                    skipped += was_skipped
                    progress.set_postfix_str(
                        f"{unit} {status} rows={rows} skipped={was_skipped}",
                        refresh=False,
                    )
                except Exception as exc:
                    failed += 1
                    tqdm.write(
                        f"FAILED {futures[future][1:4]}: "
                        f"{type(exc).__name__}: {exc}"
                    )
    print(f"planned={len(tasks)} complete={completed} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
