"""indexer CLI: index, status, audit.

Secrets: PMDATA_KEY is read from the environment (optionally seeded from a
local .env file). Its value is never logged or printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

from .catalog import Catalog, load_catalog
from .connectors.base import Connector
from .connectors.binance_vision import BinanceVisionConnector
from .connectors.pmdata import PmdataConnector
from .connectors.polymarket_gamma import PolymarketGammaConnector
from .indexer import Indexer, configure_logging
from .lake import Lake
from .manifest import (
    audit_unit,
    get_code_version,
    get_config_hash,
    iter_manifests,
    manifest_for,
)
from .units import parse_day

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    """Seed os.environ from a simple key=value .env file (no overrides, no logging)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_connector(source: str, catalog: Catalog) -> Connector:
    if source == "binance":
        return BinanceVisionConnector(catalog.binance)
    if source == "pmdata":
        return PmdataConnector(catalog.pmdata, api_key=os.environ.get("PMDATA_KEY", ""))
    if source == "polymarket":
        series_by_interval = {
            interval: cfg.series_ids for interval, cfg in catalog.polymarket.items()
        }
        return PolymarketGammaConnector(series_by_interval)
    raise ValueError(f"unknown source {source!r}")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-dir", type=Path, default=REPO_ROOT / "configs" / "indexer")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", choices=["binance", "pmdata", "polymarket"])
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--symbol", default=None, help="asset (BTC) or symbol (BTCUSDT)")
    parser.add_argument("--series", default=None, help="series key/id/provider name")
    parser.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD (UTC)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="indexer")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="ingest data units")
    _add_common_args(index)
    _add_selection_args(index)
    index.set_defaults(source_required=True)
    index.add_argument("--dry-run", action="store_true")
    index.add_argument("--force", action="store_true")
    index.add_argument("--include-unavailable", action="store_true")

    status = sub.add_parser("status", help="coverage vs expected range")
    _add_common_args(status)
    _add_selection_args(status)

    audit = sub.add_parser("audit", help="re-verify committed file checksums")
    _add_common_args(audit)
    audit.add_argument("--source", choices=["binance", "pmdata", "polymarket"])
    audit.add_argument("--dataset", default=None)
    return parser


def _entity(args: argparse.Namespace) -> str | None:
    return args.symbol or args.series


def cmd_index(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.config_dir)
    lake = Lake(args.data_dir)
    connector = build_connector(args.source, catalog)
    units = connector.plan(
        dataset=args.dataset,
        entity=_entity(args),
        start=parse_day(args.start) if args.start else None,
        end=parse_day(args.end) if args.end else None,
    )
    indexer = Indexer(
        lake,
        config_hash=get_config_hash(args.config_dir),
        code_version=get_code_version(REPO_ROOT),
    )
    summary = indexer.run(
        connector,
        units,
        force=args.force,
        include_unavailable=args.include_unavailable,
        dry_run=args.dry_run,
    )
    print(
        f"run {summary.run_id}: planned={summary.planned} "
        f"skipped_complete={summary.skipped_complete} "
        f"skipped_unavailable={summary.skipped_unavailable} "
        f"complete={summary.complete} unavailable={summary.unavailable} "
        f"failed={summary.failed}"
    )
    for unit_id, error_class in summary.failures:
        print(f"  FAILED {unit_id} ({error_class})")
    return 0 if summary.ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.config_dir)
    lake = Lake(args.data_dir)
    sources = [args.source] if args.source else ["binance", "pmdata", "polymarket"]
    try:
        connectors = {source: build_connector(source, catalog) for source in sources}
    except ValueError:
        # status should work without credentials; pmdata needs a key to build
        connectors = {}
        for source in sources:
            try:
                connectors[source] = build_connector(source, catalog)
            except ValueError as exc:
                print(f"{source}: cannot plan ({exc})")
    for source, connector in connectors.items():
        units = connector.plan(
            dataset=args.dataset,
            entity=_entity(args),
            start=parse_day(args.start) if args.start else None,
            end=parse_day(args.end) if args.end else None,
        )
        counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for unit in units:
            manifest = manifest_for(lake, unit)
            key = (source, unit.dataset)
            counts[key]["expected"] += 1
            counts[key][manifest.status if manifest else "missing"] += 1
        for (src, dataset), c in sorted(counts.items()):
            print(
                f"{src}/{dataset}: expected={c['expected']} complete={c['complete']} "
                f"unavailable={c['unavailable']} failed={c['failed']} "
                f"missing={c['missing']}"
            )
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    lake = Lake(args.data_dir)
    problems = 0
    checked = 0
    for manifest in iter_manifests(lake, source=args.source, dataset=args.dataset):
        if manifest.status != "complete":
            continue
        checked += 1
        for problem in audit_unit(lake, manifest):
            problems += 1
            print(f"MISMATCH {manifest.unit_id}: {problem}")
    print(f"audited {checked} complete units, {problems} problems")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    _load_env_file(REPO_ROOT / ".env")
    args = build_parser().parse_args(argv)
    if getattr(args, "source_required", False) and not args.source:
        print("error: --source is required for index", file=sys.stderr)
        return 2
    if args.command == "index":
        return cmd_index(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "audit":
        return cmd_audit(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
