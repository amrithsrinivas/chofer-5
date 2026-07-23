#!/usr/bin/env python
"""Completeness report: what is indexed and what is missing, per dataset.

Read-only and offline: reuses each connector's plan() for the expected unit
set and scans <data-dir>/manifests/. Run as:

    uv run python scripts/check_completeness.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                                [--source binance|pmdata|polymarket]
                                                [--json] [--data-dir DIR] [--config-dir DIR]

Date-range defaults match the indexer CLI: binance/pmdata plan over their
usual default ranges (pmdata clamps to each series' recording_starts_at);
polymarket reports the latest snapshot per series.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from indexer.catalog import load_catalog  # noqa: E402
from indexer.cli import build_connector  # noqa: E402
from indexer.lake import Lake  # noqa: E402
from indexer.manifest import Manifest, iter_manifests  # noqa: E402
from indexer.units import DataUnit, parse_day  # noqa: E402

LEGEND = (
    "unavailable = source 404 (not an indexer failure); "
    "missing = never attempted"
)


def compact_date_ranges(dates: list[str]) -> list[str]:
    """Compact sorted YYYY-MM-DD dates into 'a..b (N days)' ranges."""
    days = sorted({date.fromisoformat(d) for d in dates})
    if not days:
        return []
    ranges: list[str] = []
    start = prev = days[0]
    for day in days[1:]:
        if day == prev + timedelta(days=1):
            prev = day
            continue
        ranges.append(_fmt_range(start, prev))
        start = prev = day
    ranges.append(_fmt_range(start, prev))
    return ranges


def _fmt_range(start: date, end: date) -> str:
    n = (end - start).days + 1
    if n == 1:
        return f"{start.isoformat()} (1 day)"
    return f"{start.isoformat()}..{end.isoformat()} ({n} days)"


def _entity_of(unit: DataUnit) -> str:
    for key in ("symbol", "series", "series_id"):
        if key in unit.partitions:
            return f"{key}={unit.partitions[key]}"
    return "-"


def _short_error(manifest: Manifest, limit: int = 100) -> str:
    msg = manifest.error_message or ""
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def _planner(source: str, catalog):
    """Build a connector for offline planning (no fetch ever happens)."""
    try:
        return build_connector(source, catalog)
    except ValueError:
        if source == "pmdata":
            # Planning does not need credentials; only fetch() does.
            from indexer.connectors.pmdata import PmdataConnector

            return PmdataConnector(catalog.pmdata, api_key="unused-for-planning")
        raise


def build_report(
    config_dir: Path,
    data_dir: Path,
    *,
    source: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    catalog = load_catalog(config_dir)
    lake = Lake(data_dir)
    manifests = {m.unit_id: m for m in iter_manifests(lake)}
    # Latest manifest per (source, dataset, partition key) for snapshot sources.
    latest: dict[tuple, Manifest] = {}
    for m in manifests.values():
        key = (m.source, m.dataset, tuple(sorted(m.partitions.items())))
        if key not in latest or m.date > latest[key].date:
            latest[key] = m

    sources = [source] if source else ["binance", "pmdata", "polymarket"]
    groups: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {
            "expected": 0,
            "complete": 0,
            "unavailable": 0,
            "failed": 0,
            "missing": 0,
            "missing_dates": [],
            "unavailable_dates": [],
            "failures": [],
        }
    )
    for src in sources:
        connector = _planner(src, catalog)
        units = connector.plan(start=start, end=end)
        for unit in units:
            g = groups[(src, unit.dataset, _entity_of(unit))]
            g["expected"] += 1
            if src == "polymarket":
                # Metadata is a mutable daily snapshot: count the latest
                # snapshot for this series whatever its date.
                key = (
                    unit.source,
                    unit.dataset,
                    tuple(sorted(unit.partitions.items())),
                )
                manifest = latest.get(key)
            else:
                manifest = manifests.get(unit.id)
            status = manifest.status if manifest else "missing"
            g[status] += 1
            if status == "missing":
                g["missing_dates"].append(unit.date)
            elif status == "unavailable":
                g["unavailable_dates"].append(unit.date)
            elif status == "failed":
                g["failures"].append(
                    {
                        "unit": unit.id,
                        "error_class": manifest.error_class,
                        "error": _short_error(manifest),
                    }
                )

    overall = {"expected": 0, "complete": 0, "unavailable": 0, "failed": 0, "missing": 0}
    group_list = []
    for (src, dataset, entity), g in sorted(groups.items()):
        for k in overall:
            overall[k] += g[k]
        entry = {
            "source": src,
            "dataset": dataset,
            "entity": entity,
            **{k: g[k] for k in overall},
            "pct_complete": _pct(g["complete"], g["expected"]),
            "missing_ranges": compact_date_ranges(g["missing_dates"]),
            "unavailable_ranges": compact_date_ranges(g["unavailable_dates"]),
            "failures": g["failures"],
        }
        group_list.append(entry)
    overall["pct_complete"] = _pct(overall["complete"], overall["expected"])
    return {"overall": overall, "groups": group_list, "legend": LEGEND}


def _pct(complete: int, expected: int) -> float:
    return round(complete / expected * 100, 1) if expected else 0.0


def format_text(report: dict) -> str:
    o = report["overall"]
    lines = [
        f"OVERALL: expected={o['expected']} complete={o['complete']} "
        f"({o['pct_complete']}%) unavailable={o['unavailable']} "
        f"failed={o['failed']} missing={o['missing']}",
        f"Legend: {report['legend']}",
        "",
    ]
    for g in report["groups"]:
        lines.append(
            f"{g['source']}/{g['dataset']} {g['entity']}: "
            f"expected={g['expected']} complete={g['complete']} "
            f"({g['pct_complete']}%) unavailable={g['unavailable']} "
            f"failed={g['failed']} missing={g['missing']}"
        )
        if g["missing_ranges"]:
            lines.append(f"  missing: {', '.join(g['missing_ranges'])}")
        if g["unavailable_ranges"]:
            lines.append(f"  unavailable: {', '.join(g['unavailable_ranges'])}")
        for f in g["failures"]:
            lines.append(f"  failed: {f['unit']} ({f['error_class']}): {f['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_completeness")
    parser.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--source", choices=["binance", "pmdata", "polymarket"])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--config-dir", type=Path, default=REPO_ROOT / "configs" / "indexer")
    args = parser.parse_args(argv)

    report = build_report(
        args.config_dir,
        args.data_dir,
        source=args.source,
        start=parse_day(args.start) if args.start else None,
        end=parse_day(args.end) if args.end else None,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
