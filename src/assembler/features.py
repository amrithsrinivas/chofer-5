"""Extensible built-in point-in-time feature registry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

import numpy as np

from .book import BookSnapshot, derive_book_metrics
from .source import DataSelection, PointInTimeContext

ProductionStatus = Literal["production_eligible", "parity_pending", "research_only"]


@dataclass(frozen=True)
class FeatureValue:
    value: float
    event_time: datetime | None
    available_at: datetime | None
    null_reason: str | None
    policy: str


FeatureFunction = Callable[[PointInTimeContext, dict[str, Any]], FeatureValue]


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    version: str
    production_status: ProductionStatus
    compute: FeatureFunction


class FeatureRegistry:
    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}

    def register(self, definition: FeatureDefinition) -> None:
        if definition.name in self._features:
            raise ValueError(f"feature already registered: {definition.name}")
        self._features[definition.name] = definition

    def get(self, name: str) -> FeatureDefinition:
        try:
            return self._features[name]
        except KeyError as exc:
            raise ValueError(f"unknown built-in feature {name!r}") from exc

    def definitions(self, names: list[str]) -> list[FeatureDefinition]:
        return [self.get(name) for name in names]

    def register_function(
        self,
        name: str,
        function: FeatureFunction,
        *,
        version: str = "1",
        production_status: ProductionStatus = "research_only",
    ) -> None:
        self.register(FeatureDefinition(name, version, production_status, function))


def _missing(selection: DataSelection, reason: str | None = None) -> FeatureValue:
    return FeatureValue(math.nan, None, None, reason or selection.null_reason or "missing_input", selection.policy)


def _book_feature(key: str) -> FeatureFunction:
    def compute(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
        snapshot: BookSnapshot | None = context.memo.get("decision_book")
        if snapshot is None or not snapshot.valid:
            return FeatureValue(math.nan, snapshot.event_time if snapshot else None, snapshot.available_at if snapshot else None, snapshot.reason if snapshot else "missing_book", "pmdata_l2_reconstruction_v1")
        metrics = derive_book_metrics(snapshot, int(candidate["depth_levels"]))
        value = metrics.get(key, math.nan)
        reason = "undefined_book_metric" if math.isnan(value) else None
        return FeatureValue(value, snapshot.event_time, snapshot.available_at, reason, "pmdata_l2_reconstruction_v1")
    return compute


def _staleness(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    snapshot: BookSnapshot | None = context.memo.get("decision_book")
    if snapshot is None or snapshot.event_time is None:
        return FeatureValue(math.nan, None, None, "missing_book", "pmdata_l2_reconstruction_v1")
    return FeatureValue(max(0.0, (context.decision_at - snapshot.event_time).total_seconds()), snapshot.event_time, snapshot.available_at, None, "pmdata_l2_reconstruction_v1")


def _snapshot_feature(
    function: Callable[[BookSnapshot], float],
    *,
    null_reason: str,
) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        snapshot: BookSnapshot | None = context.memo.get("decision_book")
        if snapshot is None or not snapshot.valid:
            return FeatureValue(
                math.nan,
                snapshot.event_time if snapshot else None,
                snapshot.available_at if snapshot else None,
                snapshot.reason if snapshot else "missing_book",
                "pmdata_l2_reconstruction_v2",
            )
        value = function(snapshot)
        return FeatureValue(
            value,
            snapshot.event_time,
            snapshot.available_at,
            null_reason if math.isnan(value) else None,
            "pmdata_l2_reconstruction_v2",
        )

    return compute


def _depth_imbalance(snapshot: BookSnapshot, levels: int) -> float:
    bid_depth = sum(size for _, size in snapshot.bids[:levels])
    ask_depth = sum(size for _, size in snapshot.asks[:levels])
    total = bid_depth + ask_depth
    return (bid_depth - ask_depth) / total if total else math.nan


def _microprice_minus_midpoint(snapshot: BookSnapshot) -> float:
    metrics = derive_book_metrics(snapshot, 5)
    midpoint = metrics.get("up_midprice")
    microprice = metrics.get("up_microprice")
    if midpoint is None or microprice is None:
        return math.nan
    return microprice - midpoint


def _relative_spread(snapshot: BookSnapshot) -> float:
    metrics = derive_book_metrics(snapshot, 5)
    midpoint = metrics.get("up_midprice")
    spread = metrics.get("up_spread")
    if midpoint in (None, 0) or spread is None:
        return math.nan
    return spread / midpoint


def _vwap_for_budget(
    levels: tuple[tuple[int, float], ...],
    budget_usd: float,
) -> float:
    remaining = budget_usd
    tokens = 0.0
    spent = 0.0
    for price_micros, size in levels:
        price = price_micros / 1_000_000
        if price <= 0 or size <= 0:
            continue
        available_cost = price * size
        used_cost = min(remaining, available_cost)
        spent += used_cost
        tokens += used_cost / price
        remaining -= used_cost
        if remaining <= 1e-9:
            break
    return spent / tokens if remaining <= 1e-9 and tokens > 0 else math.nan


def _up_ask_vwap_10usd(snapshot: BookSnapshot) -> float:
    return _vwap_for_budget(snapshot.asks, 10.0)


def _down_ask_vwap_10usd(snapshot: BookSnapshot) -> float:
    down_asks = tuple(
        (1_000_000 - price_micros, size)
        for price_micros, size in snapshot.bids
    )
    return _vwap_for_budget(down_asks, 10.0)


def _book_row_snapshot(row: dict[str, Any]) -> BookSnapshot:
    return BookSnapshot(
        valid=bool(row.get("valid")),
        reason=row.get("reason"),
        bids=tuple(
            zip(
                row.get("bid_prices_micros") or [],
                row.get("bid_sizes") or [],
                strict=True,
            )
        ),
        asks=tuple(
            zip(
                row.get("ask_prices_micros") or [],
                row.get("ask_sizes") or [],
                strict=True,
            )
        ),
        event_time=row.get("last_event_time"),
        available_at=row.get("max_available_at"),
        row_ordinal=row.get("source_row_ordinal"),
    )


def _book_history_metrics(
    context: PointInTimeContext,
    seconds: int,
) -> tuple[DataSelection, list[tuple[dict[str, Any], dict[str, float]]]]:
    memo_key = f"book_history_metrics:{seconds}"
    cached = context.memo.get(memo_key)
    if cached is not None:
        return cached
    selection = context.book_history(
        lookback_seconds=seconds,
        required_depth_levels=20,
    )
    metrics: list[tuple[dict[str, Any], dict[str, float]]] = []
    if not selection.missing:
        for row in selection.table.to_pylist():
            snapshot = _book_row_snapshot(row)
            if snapshot.valid:
                metrics.append((row, derive_book_metrics(snapshot, 5)))
    result = (selection, metrics)
    context.memo[memo_key] = result
    return result


def _history_evidence(
    rows: list[dict[str, Any]],
) -> tuple[datetime | None, datetime | None]:
    event_times = [
        row.get("_event_time")
        for row in rows
        if row.get("_event_time") is not None
    ]
    available_times = [
        row.get("_available_at")
        for row in rows
        if row.get("_available_at") is not None
    ]
    return (
        max(event_times) if event_times else None,
        max(available_times) if available_times else None,
    )


def _book_metric_change(
    key: str,
    seconds: int,
    *,
    relative: bool = False,
) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        selection, metrics = _book_history_metrics(context, seconds)
        usable = [
            (row, values[key])
            for row, values in metrics
            if key in values and math.isfinite(values[key])
        ]
        if len(usable) < 2:
            return _missing(selection, "insufficient_valid_book_history")
        reference = usable[0][1]
        latest = usable[-1][1]
        if relative:
            if reference == 0:
                return _missing(selection, "zero_book_history_reference")
            value = latest / reference - 1.0
        else:
            value = latest - reference
        event_time, available_at = _history_evidence(
            [usable[0][0], usable[-1][0]]
        )
        return FeatureValue(
            value,
            event_time,
            available_at,
            None,
            "canonical_book_bars_1s_v1",
        )

    return compute


def _book_metric_stat(
    key: str,
    seconds: int,
    statistic: Literal["mean", "max"],
) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        selection, metrics = _book_history_metrics(context, seconds)
        usable = [
            (row, values[key])
            for row, values in metrics
            if key in values and math.isfinite(values[key])
        ]
        if not usable:
            return _missing(selection, "no_valid_book_history")
        values = [value for _, value in usable]
        value = (
            float(np.mean(values))
            if statistic == "mean"
            else float(np.max(values))
        )
        event_time, available_at = _history_evidence(
            [row for row, _ in usable]
        )
        return FeatureValue(
            value,
            event_time,
            available_at,
            None,
            "canonical_book_bars_1s_v1",
        )

    return compute


def _l2_stat(seconds: int, statistic: str) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        selection = context.select("poly_l2", lookback_seconds=seconds)
        if selection.missing:
            return _missing(selection)
        rows = selection.table.to_pylist()
        changes = [
            row
            for row in rows
            if str(row.get("event_type") or "") == "price_change"
        ]
        if statistic == "count":
            value = float(len(changes))
        elif not changes:
            return _missing(selection, "no_l2_price_changes")
        elif statistic == "delete_fraction":
            value = sum(
                float(row.get("pc_size") or 0.0) <= 0 for row in changes
            ) / len(changes)
        elif statistic == "side_imbalance":
            buy = sum(
                str(row.get("pc_side") or "").upper() == "BUY"
                for row in changes
            )
            sell = sum(
                str(row.get("pc_side") or "").upper() == "SELL"
                for row in changes
            )
            value = (buy - sell) / len(changes)
        elif statistic == "acceleration":
            recent_cutoff = context.decision_at - timedelta(
                seconds=seconds / 2
            )
            recent = sum(
                row["_event_time"] > recent_cutoff for row in changes
            )
            prior = len(changes) - recent
            value = (recent - prior) / max(1, len(changes))
        else:
            raise AssertionError(f"unknown L2 statistic {statistic}")
        event_time, available_at = _history_evidence(rows)
        return FeatureValue(
            value,
            event_time,
            available_at,
            None,
            selection.policy,
        )

    return compute


def _trade_pressure(seconds: int) -> FeatureFunction:
    signs = {
        ("yes", "BUY"): 1.0,
        ("yes", "SELL"): -1.0,
        ("no", "BUY"): -1.0,
        ("no", "SELL"): 1.0,
        ("up", "BUY"): 1.0,
        ("up", "SELL"): -1.0,
    }

    def compute(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
        selection = context.select("poly_trade", lookback_seconds=seconds)
        if selection.missing:
            return _missing(selection)
        pressure = 0.0
        latest_event = latest_available = None
        for row in selection.table.to_pylist():
            if row.get("signed_up_pressure") is not None:
                pressure += float(row["signed_up_pressure"])
                latest_event = (
                    row["_event_time"]
                    if latest_event is None
                    else max(latest_event, row["_event_time"])
                )
                latest_available = (
                    row["_available_at"]
                    if latest_available is None
                    else max(latest_available, row["_available_at"])
                )
                continue
            outcome = str(row.get("outcome") or "").lower()
            side = str(row.get("side") or "").upper()
            sign = signs.get((outcome, side))
            size = row.get("size")
            if sign is None or size is None:
                continue
            pressure += sign * float(size)
            latest_event = (
                row["_event_time"]
                if latest_event is None
                else max(latest_event, row["_event_time"])
            )
            latest_available = (
                row["_available_at"]
                if latest_available is None
                else max(latest_available, row["_available_at"])
            )
        return FeatureValue(pressure, latest_event, latest_available, None, selection.policy)

    return compute


def _trade_summary(seconds: int, statistic: str) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        selection = context.select("poly_trade", lookback_seconds=seconds)
        if selection.missing:
            return _missing(selection)
        rows = selection.table.to_pylist()
        pressure = sum(
            float(
                row.get("signed_up_pressure")
                if row.get("signed_up_pressure") is not None
                else 0.0
            )
            for row in rows
        )
        total_size = sum(
            float(
                row.get("total_size")
                if row.get("total_size") is not None
                else row.get("size") or 0.0
            )
            for row in rows
        )
        trade_count = sum(
            int(
                row.get("trade_count")
                if row.get("trade_count") is not None
                else 1
            )
            for row in rows
        )
        if statistic == "imbalance":
            if total_size <= 0:
                return _missing(selection, "zero_polymarket_trade_size")
            value = pressure / total_size
        elif statistic == "count":
            value = float(trade_count)
        elif statistic == "size":
            value = total_size
        elif statistic == "acceleration":
            cutoff = context.decision_at - timedelta(seconds=seconds / 2)
            recent = sum(
                int(
                    row.get("trade_count")
                    if row.get("trade_count") is not None
                    else 1
                )
                for row in rows
                if row["_event_time"] > cutoff
            )
            prior = trade_count - recent
            value = (recent - prior) / max(1, trade_count)
        else:
            raise AssertionError(f"unknown trade statistic {statistic}")
        event_time, available_at = _history_evidence(rows)
        return FeatureValue(
            value,
            event_time,
            available_at,
            None,
            selection.policy,
        )

    return compute


def _latest_and_reference(selection: DataSelection, seconds: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    rows = selection.table.to_pylist()
    if not rows:
        return None
    latest = rows[-1]
    cutoff = latest["_event_time"] - timedelta(seconds=seconds)
    references = [row for row in rows if row["_event_time"] <= cutoff]
    if not references:
        return None
    return latest, references[-1]


def _return_feature(asset: str | None, seconds: int) -> FeatureFunction:
    def compute(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
        selected_asset = context.primary_asset if asset is None else asset
        selection = context.select("spot_klines", asset=selected_asset, lookback_seconds=seconds + 180)
        if selection.missing:
            return _missing(selection)
        pair = _latest_and_reference(selection, seconds)
        if pair is None:
            return _missing(selection, "insufficient_closed_klines")
        latest, reference = pair
        base = float(reference["close"])
        if base == 0:
            return _missing(selection, "zero_reference_close")
        return FeatureValue(float(latest["close"]) / base - 1.0, latest["_event_time"], latest["_available_at"], None, selection.policy)
    return compute


def _realized_vol_15m_for(asset: str | None) -> FeatureFunction:
    def compute(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
        selected_asset = context.primary_asset if asset is None else asset
        selection = context.select("spot_klines", asset=selected_asset, lookback_seconds=16 * 60)
        if selection.missing:
            return _missing(selection)
        rows = selection.table.to_pylist()
        closes = [float(row["close"]) for row in rows if row.get("close") not in (None, 0)]
        if len(closes) < 2:
            return _missing(selection, "insufficient_closed_klines")
        log_returns = np.diff(np.log(np.asarray(closes, dtype=float)))
        latest = rows[-1]
        return FeatureValue(float(np.sqrt(np.sum(np.square(log_returns)))), latest["_event_time"], latest["_available_at"], None, selection.policy)

    return compute


def _premium_change_for(asset: str | None) -> FeatureFunction:
    def compute(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
        selected_asset = context.primary_asset if asset is None else asset
        selection = context.select("premium_index_klines", asset=selected_asset, lookback_seconds=8 * 60)
        if selection.missing:
            return _missing(selection)
        pair = _latest_and_reference(selection, 5 * 60)
        if pair is None:
            return _missing(selection, "insufficient_closed_klines")
        latest, reference = pair
        return FeatureValue(float(latest["close"]) - float(reference["close"]), latest["_event_time"], latest["_available_at"], None, selection.policy)

    return compute


def _realized_vol_15m(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    return _realized_vol_15m_for("BTC")(context, candidate)


def _agg_trade_pressure(seconds: int, asset: str | None = "BTC") -> FeatureFunction:
    def compute(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
        selected_asset = context.primary_asset if asset is None else asset
        selection = context.select(
            "agg_trades", asset=selected_asset, lookback_seconds=seconds
        )
        if selection.missing:
            return _missing(selection)
        pressure = 0.0
        latest = None
        max_available = None
        for row in selection.table.to_pylist():
            if row.get("signed_quote_flow") is not None:
                pressure += float(row["signed_quote_flow"])
                latest = row
                max_available = (
                    row["_available_at"]
                    if max_available is None
                    else max(max_available, row["_available_at"])
                )
                continue
            price = row.get("price")
            quantity = row.get("quantity")
            maker = row.get("is_buyer_maker")
            if price is None or quantity is None or maker is None:
                continue
            # Buyer-maker means the aggressive side sold into the bid.
            pressure += (-1.0 if bool(maker) else 1.0) * float(price) * float(quantity)
            latest = row
            max_available = (
                row["_available_at"]
                if max_available is None
                else max(max_available, row["_available_at"])
            )
        if latest is None:
            return _missing(selection, "no_valid_aggregate_trades")
        return FeatureValue(
            pressure,
            latest["_event_time"],
            max_available,
            None,
            selection.policy,
        )

    return compute


def _aggregate_trade_summary(
    asset: str,
    seconds: int,
    statistic: str,
) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        lookback = seconds + 60 if statistic == "return" else seconds
        selection = context.select(
            "agg_trades",
            asset=asset,
            lookback_seconds=lookback,
        )
        if selection.missing:
            return _missing(selection)
        rows = selection.table.to_pylist()
        in_window = [
            row
            for row in rows
            if row["_event_time"]
            >= context.decision_at - timedelta(seconds=seconds)
        ]
        if statistic == "return":
            if not rows:
                return _missing(selection, "no_aggregate_trade_prices")
            latest = rows[-1]
            references = [
                row
                for row in rows
                if row["_event_time"]
                <= context.decision_at - timedelta(seconds=seconds)
            ]
            if not references:
                return _missing(
                    selection, "insufficient_aggregate_trade_history"
                )
            reference = references[-1]
            latest_price = latest.get("last_price", latest.get("price"))
            reference_price = reference.get(
                "last_price", reference.get("price")
            )
            if latest_price in (None, 0) or reference_price in (None, 0):
                return _missing(selection, "missing_aggregate_trade_price")
            value = float(latest_price) / float(reference_price) - 1.0
            evidence_rows = [reference, latest]
        elif not in_window:
            return _missing(selection, "no_aggregate_trades_in_window")
        elif statistic == "flow_imbalance":
            signed = sum(
                float(row.get("signed_quote_flow") or 0.0)
                for row in in_window
            )
            total = sum(
                float(
                    row.get("total_quote_volume")
                    if row.get("total_quote_volume") is not None
                    else (
                        float(row.get("price") or 0.0)
                        * float(row.get("quantity") or 0.0)
                    )
                )
                for row in in_window
            )
            if total <= 0:
                return _missing(selection, "zero_aggregate_quote_volume")
            value = signed / total
            evidence_rows = in_window
        elif statistic == "count":
            value = float(
                sum(
                    int(row.get("total_trade_count") or 1)
                    for row in in_window
                )
            )
            evidence_rows = in_window
        elif statistic in {"realized_vol", "range"}:
            prices = [
                float(row.get("last_price", row.get("price")))
                for row in in_window
                if row.get("last_price", row.get("price")) not in (None, 0)
            ]
            if len(prices) < 2:
                return _missing(
                    selection, "insufficient_aggregate_trade_prices"
                )
            if statistic == "realized_vol":
                value = float(
                    np.sqrt(
                        np.sum(
                            np.square(
                                np.diff(np.log(np.asarray(prices, dtype=float)))
                            )
                        )
                    )
                )
            else:
                highs = [
                    float(row.get("high_price", row.get("price")))
                    for row in in_window
                    if row.get("high_price", row.get("price")) is not None
                ]
                lows = [
                    float(row.get("low_price", row.get("price")))
                    for row in in_window
                    if row.get("low_price", row.get("price")) is not None
                ]
                low = min(lows)
                value = max(highs) / low - 1.0 if low else math.nan
            evidence_rows = in_window
        else:
            raise AssertionError(
                f"unknown aggregate-trade statistic {statistic}"
            )
        event_time, available_at = _history_evidence(evidence_rows)
        return FeatureValue(
            value,
            event_time,
            available_at,
            "zero_aggregate_price_reference"
            if math.isnan(value)
            else None,
            selection.policy,
        )

    return compute


def _relative_crypto_aggregate_return(
    context: PointInTimeContext, candidate: dict[str, Any]
) -> FeatureValue:
    values = [
        _aggregate_trade_summary(asset, 30, "return")(context, candidate)
        for asset in ("BTC", "ETH", "SOL", "XRP")
    ]
    if any(math.isnan(item.value) for item in values):
        return FeatureValue(
            math.nan,
            None,
            None,
            "missing_cross_asset_aggregate_return",
            "modeled_live_research_v1",
        )
    event_times = [
        item.event_time for item in values if item.event_time is not None
    ]
    available_times = [
        item.available_at for item in values if item.available_at is not None
    ]
    return FeatureValue(
        values[0].value
        - float(np.mean([item.value for item in values[1:]])),
        max(event_times) if event_times else None,
        max(available_times) if available_times else None,
        None,
        "modeled_live_research_v1",
    )


def _futures_change(column: str, seconds: int) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        selection = context.select(
            "futures_metrics",
            asset="BTC",
            lookback_seconds=seconds + 10 * 60,
        )
        if selection.missing:
            return _missing(selection)
        rows = [
            row
            for row in selection.table.to_pylist()
            if row.get(column) is not None
        ]
        if len(rows) < 2:
            return _missing(selection, "insufficient_futures_metrics")
        latest = rows[-1]
        references = [
            row
            for row in rows
            if row["_event_time"]
            <= latest["_event_time"] - timedelta(seconds=seconds)
        ]
        if not references:
            return _missing(selection, "insufficient_futures_history")
        reference = float(references[-1][column])
        value = (
            float(latest[column]) / reference - 1.0
            if reference
            else math.nan
        )
        event_time, available_at = _history_evidence(
            [references[-1], latest]
        )
        return FeatureValue(
            value,
            event_time,
            available_at,
            "zero_futures_reference" if math.isnan(value) else None,
            selection.policy,
        )

    return compute


def _latest_futures_value(column: str) -> FeatureFunction:
    def compute(
        context: PointInTimeContext, candidate: dict[str, Any]
    ) -> FeatureValue:
        selection = context.select(
            "futures_metrics",
            asset="BTC",
            lookback_seconds=15 * 60,
        )
        if selection.missing:
            return _missing(selection)
        rows = [
            row
            for row in selection.table.to_pylist()
            if row.get(column) is not None
        ]
        if not rows:
            return _missing(selection, "missing_futures_metric")
        latest = rows[-1]
        return FeatureValue(
            float(latest[column]),
            latest["_event_time"],
            latest["_available_at"],
            None,
            selection.policy,
        )

    return compute


def _premium_level(
    context: PointInTimeContext, candidate: dict[str, Any]
) -> FeatureValue:
    selection = context.select(
        "premium_index_klines",
        asset="BTC",
        lookback_seconds=3 * 60,
    )
    if selection.missing:
        return _missing(selection)
    rows = [
        row
        for row in selection.table.to_pylist()
        if row.get("close") is not None
    ]
    if not rows:
        return _missing(selection, "missing_premium_level")
    latest = rows[-1]
    return FeatureValue(
        float(latest["close"]),
        latest["_event_time"],
        latest["_available_at"],
        None,
        selection.policy,
    )


def _open_interest_change(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    selection = context.select("futures_metrics", asset="BTC", lookback_seconds=20 * 60)
    if selection.missing:
        return _missing(selection)
    rows = [row for row in selection.table.to_pylist() if row.get("sum_open_interest") is not None]
    if len(rows) < 2:
        return _missing(selection, "insufficient_futures_metrics")
    first = float(rows[0]["sum_open_interest"])
    latest = rows[-1]
    if first == 0:
        return _missing(selection, "zero_open_interest_reference")
    return FeatureValue(
        float(latest["sum_open_interest"]) / first - 1.0,
        latest["_event_time"],
        latest["_available_at"],
        None,
        selection.policy,
    )


def _long_short_ratio(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    selection = context.select("futures_metrics", asset="BTC", lookback_seconds=10 * 60)
    if selection.missing:
        return _missing(selection)
    rows = [row for row in selection.table.to_pylist() if row.get("count_long_short_ratio") is not None]
    if not rows:
        return _missing(selection, "missing_long_short_ratio")
    latest = rows[-1]
    return FeatureValue(
        float(latest["count_long_short_ratio"]),
        latest["_event_time"],
        latest["_available_at"],
        None,
        selection.policy,
    )


def _premium_change(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    return _premium_change_for("BTC")(context, candidate)


def _time_sin(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    seconds = context.decision_at.hour * 3600 + context.decision_at.minute * 60 + context.decision_at.second + context.decision_at.microsecond / 1_000_000
    return FeatureValue(math.sin(2 * math.pi * seconds / 86400), context.decision_at, context.decision_at, None, "deterministic_utc_calendar_v1")


def _time_cos(context: PointInTimeContext, candidate: dict[str, Any]) -> FeatureValue:
    seconds = context.decision_at.hour * 3600 + context.decision_at.minute * 60 + context.decision_at.second + context.decision_at.microsecond / 1_000_000
    return FeatureValue(math.cos(2 * math.pi * seconds / 86400), context.decision_at, context.decision_at, None, "deterministic_utc_calendar_v1")


def _day_of_week_sin(
    context: PointInTimeContext, candidate: dict[str, Any]
) -> FeatureValue:
    value = math.sin(2 * math.pi * context.decision_at.weekday() / 7)
    return FeatureValue(
        value,
        context.decision_at,
        context.decision_at,
        None,
        "deterministic_utc_calendar_v1",
    )


def _day_of_week_cos(
    context: PointInTimeContext, candidate: dict[str, Any]
) -> FeatureValue:
    value = math.cos(2 * math.pi * context.decision_at.weekday() / 7)
    return FeatureValue(
        value,
        context.decision_at,
        context.decision_at,
        None,
        "deterministic_utc_calendar_v1",
    )


def builtin_registry() -> FeatureRegistry:
    registry = FeatureRegistry()
    book = {
        "up_best_bid": "up_best_bid",
        "up_best_ask": "up_best_ask",
        "up_midprice": "up_midprice",
        "book_midprice": "up_midprice",
        "up_spread": "up_spread",
        "book_spread": "up_spread",
        "up_bid_depth_top5": "up_bid_depth_top5",
        "up_ask_depth_top5": "up_ask_depth_top5",
        "up_depth_top5": "up_depth_top5",
        "book_depth_top5": "up_depth_top5",
        "up_book_imbalance": "up_book_imbalance",
        "book_imbalance": "up_book_imbalance",
        "up_microprice": "up_microprice",
        "book_microprice": "up_microprice",
    }
    for name, key in book.items():
        registry.register_function(name, _book_feature(key), production_status="parity_pending")
    registry.register_function(
        "book_microprice_minus_midpoint",
        _snapshot_feature(
            _microprice_minus_midpoint,
            null_reason="undefined_microprice_midpoint_difference",
        ),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "book_relative_spread",
        _snapshot_feature(
            _relative_spread,
            null_reason="undefined_relative_spread",
        ),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "book_imbalance_top1",
        _snapshot_feature(
            lambda snapshot: _depth_imbalance(snapshot, 1),
            null_reason="undefined_top1_imbalance",
        ),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "book_imbalance_top10",
        _snapshot_feature(
            lambda snapshot: _depth_imbalance(snapshot, 10),
            null_reason="undefined_top10_imbalance",
        ),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "book_depth_top10",
        _snapshot_feature(
            lambda snapshot: sum(
                size for _, size in snapshot.bids[:10]
            )
            + sum(size for _, size in snapshot.asks[:10]),
            null_reason="undefined_top10_depth",
        ),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "up_ask_vwap_10usd",
        _snapshot_feature(
            _up_ask_vwap_10usd,
            null_reason="insufficient_up_ask_depth_10usd",
        ),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "down_ask_vwap_10usd",
        _snapshot_feature(
            _down_ask_vwap_10usd,
            null_reason="insufficient_down_ask_depth_10usd",
        ),
        version="2",
        production_status="parity_pending",
    )
    for seconds in (5, 15, 30, 60):
        registry.register_function(
            f"book_midprice_change_{seconds}s",
            _book_metric_change("up_midprice", seconds),
            version="2",
            production_status="parity_pending",
        )
    for seconds in (15, 60):
        registry.register_function(
            f"book_microprice_change_{seconds}s",
            _book_metric_change("up_microprice", seconds),
            version="2",
            production_status="parity_pending",
        )
        registry.register_function(
            f"book_imbalance_change_{seconds}s",
            _book_metric_change("up_book_imbalance", seconds),
            version="2",
            production_status="parity_pending",
        )
    registry.register_function(
        "book_depth_change_30s",
        _book_metric_change("up_depth_top5", 30, relative=True),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "book_spread_mean_30s",
        _book_metric_stat("up_spread", 30, "mean"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "book_spread_max_30s",
        _book_metric_stat("up_spread", 30, "max"),
        version="2",
        production_status="parity_pending",
    )
    for name, statistic in (
        ("l2_update_count_30s", "count"),
        ("l2_delete_fraction_30s", "delete_fraction"),
        ("l2_side_imbalance_30s", "side_imbalance"),
        ("l2_update_acceleration_30s", "acceleration"),
    ):
        registry.register_function(
            name,
            _l2_stat(30, statistic),
            version="2",
            production_status="parity_pending",
        )
    registry.register_function("quote_staleness_seconds", _staleness, production_status="parity_pending")
    registry.register_function("book_staleness", _staleness, production_status="parity_pending")
    for seconds in (5, 15, 30):
        registry.register_function(
            f"trade_pressure_{seconds}s",
            _trade_pressure(seconds),
            version="2",
            production_status="parity_pending",
        )
    for seconds in (15, 30):
        registry.register_function(
            f"trade_imbalance_{seconds}s",
            _trade_summary(seconds, "imbalance"),
            version="2",
            production_status="parity_pending",
        )
    registry.register_function(
        "polymarket_trade_count_30s",
        _trade_summary(30, "count"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "polymarket_trade_size_30s",
        _trade_summary(30, "size"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "polymarket_trade_acceleration_30s",
        _trade_summary(30, "acceleration"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function("trade_pressure_60s", _trade_pressure(60), production_status="parity_pending")
    registry.register_function("trade_pressure_300s", _trade_pressure(300), production_status="parity_pending")
    registry.register_function("net_trade_pressure", _trade_pressure(300), production_status="parity_pending")
    registry.register_function("btc_agg_trade_pressure_60s", _agg_trade_pressure(60), production_status="parity_pending")
    registry.register_function("btc_agg_trade_pressure_300s", _agg_trade_pressure(300), production_status="parity_pending")
    registry.register_function("primary_agg_trade_pressure_60s", _agg_trade_pressure(60, None), production_status="parity_pending")
    registry.register_function("primary_agg_trade_pressure_300s", _agg_trade_pressure(300, None), production_status="parity_pending")
    registry.register_function("primary_spot_return_1m", _return_feature(None, 60), production_status="parity_pending")
    registry.register_function("primary_spot_return_5m", _return_feature(None, 300), production_status="parity_pending")
    registry.register_function("primary_spot_return_15m", _return_feature(None, 900), production_status="parity_pending")
    registry.register_function("primary_spot_realized_vol_15m", _realized_vol_15m_for(None), production_status="parity_pending")
    registry.register_function("primary_premium_change_5m", _premium_change_for(None), production_status="parity_pending")
    registry.register_function("btc_spot_return_1m", _return_feature("BTC", 60), production_status="parity_pending")
    registry.register_function("btc_spot_return_5m", _return_feature("BTC", 300), production_status="parity_pending")
    registry.register_function("btc_spot_return_15m", _return_feature("BTC", 900), production_status="parity_pending")
    registry.register_function("btc_spot_realized_vol_15m", _realized_vol_15m, production_status="parity_pending")
    registry.register_function("btc_premium_change", _premium_change, production_status="parity_pending")
    registry.register_function("btc_premium_change_5m", _premium_change, production_status="parity_pending")
    registry.register_function("btc_open_interest_change_20m", _open_interest_change, production_status="parity_pending")
    registry.register_function("btc_long_short_ratio", _long_short_ratio, production_status="parity_pending")
    for seconds in (5, 15, 30, 60):
        registry.register_function(
            f"btc_agg_trade_return_{seconds}s",
            _aggregate_trade_summary("BTC", seconds, "return"),
            version="2",
            production_status="parity_pending",
        )
        registry.register_function(
            f"btc_agg_trade_flow_imbalance_{seconds}s",
            _aggregate_trade_summary(
                "BTC", seconds, "flow_imbalance"
            ),
            version="2",
            production_status="parity_pending",
        )
    registry.register_function(
        "btc_agg_trade_count_30s",
        _aggregate_trade_summary("BTC", 30, "count"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_agg_trade_realized_vol_60s",
        _aggregate_trade_summary("BTC", 60, "realized_vol"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_agg_trade_range_60s",
        _aggregate_trade_summary("BTC", 60, "range"),
        version="2",
        production_status="parity_pending",
    )
    for asset in ("ETH", "SOL", "XRP"):
        registry.register_function(
            f"{asset.lower()}_agg_trade_return_30s",
            _aggregate_trade_summary(asset, 30, "return"),
            version="2",
            production_status="parity_pending",
        )
    registry.register_function(
        "btc_relative_crypto_agg_trade_return_30s",
        _relative_crypto_aggregate_return,
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_open_interest_change_5m",
        _futures_change("sum_open_interest", 5 * 60),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_open_interest_change_60m",
        _futures_change("sum_open_interest", 60 * 60),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_long_short_ratio_change_20m",
        _futures_change("count_long_short_ratio", 20 * 60),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_taker_long_short_ratio",
        _latest_futures_value("sum_taker_long_short_vol_ratio"),
        version="2",
        production_status="parity_pending",
    )
    registry.register_function(
        "btc_premium_level",
        _premium_level,
        version="2",
        production_status="parity_pending",
    )
    for asset in ("ETH", "SOL", "XRP"):
        registry.register_function(f"{asset.lower()}_spot_return_5m", _return_feature(asset, 300), production_status="parity_pending")
    registry.register_function("time_of_day_sin", _time_sin, production_status="production_eligible")
    registry.register_function("time_of_day_cos", _time_cos, production_status="production_eligible")
    registry.register_function(
        "day_of_week_sin",
        _day_of_week_sin,
        version="2",
        production_status="production_eligible",
    )
    registry.register_function(
        "day_of_week_cos",
        _day_of_week_cos,
        version="2",
        production_status="production_eligible",
    )
    return registry
