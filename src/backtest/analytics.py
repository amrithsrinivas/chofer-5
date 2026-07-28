"""Threshold-level performance and segmented replay analytics."""

from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import Decimal
from typing import Any, Callable

from .models import TradeRecord, TradeStatus
from .replay import ReplayResult, micros_to_usd
from .spec import BacktestSpec


def _ratio(numerator: int | Decimal, denominator: int | Decimal) -> float | None:
    if denominator == 0:
        return None
    return float(Decimal(numerator) / Decimal(denominator))


def _wilson(wins: int, count: int, z: Decimal = Decimal("1.959963984540054")) -> tuple[float | None, float | None]:
    if count == 0:
        return None, None
    n = Decimal(count)
    p = Decimal(wins) / n
    z2 = z * z
    denominator = Decimal(1) + z2 / n
    centre = (p + z2 / (Decimal(2) * n)) / denominator
    margin = z * ((p * (Decimal(1) - p) / n + z2 / (Decimal(4) * n * n)).sqrt()) / denominator
    return float(centre - margin), float(centre + margin)


def _filled(result: ReplayResult) -> list[TradeRecord]:
    return [trade for trade in result.trades if trade.status is TradeStatus.FILLED]


def threshold_metrics(result: ReplayResult) -> dict[str, Any]:
    trades = _filled(result)
    pnl = [trade.pnl_micros for trade in trades]
    if any(value is None for value in pnl):
        raise RuntimeError("filled trade was not settled before analytics")
    pnl_micros = [int(value) for value in pnl if value is not None]
    entries = [trade.reference_entry_price for trade in trades]
    if any(value is None for value in entries):
        raise RuntimeError("filled trade has no reference entry price")
    entry_values = [value for value in entries if value is not None]
    wins = sum(trade.won is True for trade in trades)
    total_pnl = sum(pnl_micros)
    gross_profit = sum(value for value in pnl_micros if value > 0)
    gross_loss = -sum(value for value in pnl_micros if value < 0)
    stake_total = sum(trade.stake_micros or 0 for trade in trades)
    max_drawdown = max((point.drawdown_micros for point in result.equity_curve), default=0)
    max_drawdown_fraction = max(
        (point.drawdown_fraction for point in result.equity_curve), default=Decimal(0)
    )
    lower, upper = _wilson(wins, len(trades))
    winrate = _ratio(wins, len(trades))
    return {
        "threshold": float(result.threshold),
        "candidate_count": result.candidate_count,
        "signal_count": result.signal_count,
        "quote_count": result.quote_count,
        "entry_band_count": result.band_eligible_count,
        "entry_count": result.entry_count,
        "candidate_coverage": _ratio(result.signal_count, result.candidate_count),
        "quote_coverage": _ratio(result.quote_count, result.signal_count),
        "signal_fill_coverage": _ratio(result.entry_count, result.signal_count),
        "win_count": wins,
        "loss_count": len(trades) - wins,
        "winrate": winrate,
        "winrate_edge_over_0_5": winrate - 0.5 if winrate is not None else None,
        "winrate_wilson_lower_95": lower,
        "winrate_wilson_upper_95": upper,
        "average_entry_price": (
            float(sum(entry_values, Decimal(0)) / Decimal(len(entry_values)))
            if entry_values
            else None
        ),
        "median_entry_price": (
            float(statistics.median(entry_values)) if entry_values else None
        ),
        "total_pnl_usd": float(micros_to_usd(total_pnl)),
        "average_pnl_usd": (
            float(micros_to_usd(total_pnl) / Decimal(len(trades))) if trades else None
        ),
        "roi": _ratio(total_pnl, stake_total),
        "profit_factor": _ratio(gross_profit, gross_loss),
        "max_drawdown_usd": float(micros_to_usd(max_drawdown)),
        "max_drawdown_fraction": float(max_drawdown_fraction),
        "final_capital_usd": float(micros_to_usd(result.final_cash_micros)),
        "rejection_counts": {
            reason.value: count for reason, count in result.rejection_counts.items()
        },
    }


def _group_summary(trades: list[TradeRecord]) -> dict[str, Any]:
    wins = sum(trade.won is True for trade in trades)
    pnl = sum(trade.pnl_micros or 0 for trade in trades)
    stake = sum(trade.stake_micros or 0 for trade in trades)
    return {
        "entry_count": len(trades),
        "wins": wins,
        "winrate": _ratio(wins, len(trades)),
        "total_pnl_usd": float(micros_to_usd(pnl)),
        "roi": _ratio(pnl, stake),
    }


def _breakdown(
    trades: list[TradeRecord], key: Callable[[TradeRecord], str]
) -> list[dict[str, Any]]:
    groups: dict[str, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade)
    return [
        {"key": name, **_group_summary(groups[name])}
        for name in sorted(groups)
    ]


def _bucket_edges(spec: BacktestSpec) -> list[tuple[Decimal, Decimal]]:
    edges: list[tuple[Decimal, Decimal]] = []
    low = spec.entry_price_min
    cent = Decimal("0.01")
    while low < spec.entry_price_max:
        high = min(low + cent, spec.entry_price_max)
        edges.append((low, high))
        low = high
    if not edges:
        edges.append((spec.entry_price_min, spec.entry_price_max))
    return edges


def _price_bucket(price: Decimal, spec: BacktestSpec) -> str:
    edges = _bucket_edges(spec)
    for index, (low, high) in enumerate(edges):
        if low <= price < high or (index == len(edges) - 1 and price == high):
            return f"{low:.2f}-{high:.2f}"
    return "outside-configured-band"


def build_analytics(results: list[ReplayResult], spec: BacktestSpec) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metrics = [threshold_metrics(result) for result in results]
    threshold_details: list[dict[str, Any]] = []
    for result in results:
        trades = _filled(result)
        threshold_details.append(
            {
                "threshold": float(result.threshold),
                "entry_price_buckets": _breakdown(
                    trades,
                    lambda trade: _price_bucket(trade.reference_entry_price, spec),  # type: ignore[arg-type]
                ),
                "side_breakdown": _breakdown(trades, lambda trade: trade.side.value),
                "day_breakdown": _breakdown(
                    trades, lambda trade: trade.signal_at.date().isoformat()
                ),
            }
        )
    analytics = {
        "execution_mode": "replay",
        "coverage_definitions": {
            "candidate_coverage": "signal_count / candidate_count",
            "quote_coverage": "selected-side valid quote count / signal_count",
            "signal_fill_coverage": "entry_count / signal_count",
            "roi": "net pnl / filled fixed stakes",
        },
        "thresholds": threshold_details,
    }
    return metrics, analytics
