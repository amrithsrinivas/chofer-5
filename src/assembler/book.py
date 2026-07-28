"""Deterministic reconstruction of the provider's yes/UP L2 book."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

import pyarrow as pa

PRICE_SCALE = 1_000_000
BOOK_ASSUMPTION_VERSION = "pmdata_yes_up_l2_book_replace_price_change_v1"
DOWN_COMPLEMENT_VERSION = "down_complement_from_up_quotes_v1"


def price_to_micros(value: float | None) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    return int((Decimal(str(value)) * PRICE_SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def micros_to_price(value: int) -> float:
    return value / PRICE_SCALE


@dataclass(frozen=True)
class BookSnapshot:
    valid: bool
    reason: str | None
    bids: tuple[tuple[int, float], ...]
    asks: tuple[tuple[int, float], ...]
    event_time: datetime | None
    available_at: datetime | None
    row_ordinal: int | None

    @property
    def best_bid_micros(self) -> int | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask_micros(self) -> int | None:
        return self.asks[0][0] if self.asks else None


class OrderBookReconstructor:
    """Process eligible rows in preserved source ordinal order."""

    def __init__(self) -> None:
        self.bids: dict[int, float] = {}
        self.asks: dict[int, float] = {}
        self.has_snapshot = False
        self.valid = False
        self.reason: str | None = "no_book_snapshot"
        self.event_time: datetime | None = None
        self.available_at: datetime | None = None
        self.row_ordinal: int | None = None

    @staticmethod
    def _levels(prices: Any, sizes: Any) -> dict[int, float]:
        if prices is None or sizes is None or len(prices) != len(sizes):
            raise ValueError("book arrays are null or have unequal lengths")
        levels: dict[int, float] = {}
        for price, size in zip(prices, sizes, strict=True):
            micros = price_to_micros(float(price))
            size = float(size)
            if micros is None or not 0 <= micros <= PRICE_SCALE:
                raise ValueError("book price outside [0, 1]")
            if not math.isfinite(size) or size < 0:
                raise ValueError("book size is negative or non-finite")
            if size > 0:
                levels[micros] = size
        return levels

    def _mark_update(self, row: dict[str, Any]) -> None:
        self.event_time = row["_event_time"]
        available_at = row["_available_at"]
        self.available_at = (
            available_at
            if self.available_at is None
            else max(self.available_at, available_at)
        )
        self.row_ordinal = int(row["_row_ordinal"])

    def apply(self, row: dict[str, Any]) -> None:
        event_type = str(row.get("event_type") or "")
        if event_type == "book":
            try:
                bids = self._levels(row.get("bid_prices"), row.get("bid_sizes"))
                asks = self._levels(row.get("ask_prices"), row.get("ask_sizes"))
            except (TypeError, ValueError) as exc:
                self.valid = False
                self.reason = f"invalid_book_snapshot:{exc}"
                return
            self.bids, self.asks = bids, asks
            self.has_snapshot = True
            self.valid = True
            self.reason = None
            self._mark_update(row)
            return
        if event_type != "price_change":
            return
        if not self.has_snapshot:
            self.valid = False
            self.reason = "delta_before_snapshot"
            return
        try:
            price = price_to_micros(float(row["pc_price"]))
            size = float(row["pc_size"])
            side = str(row["pc_side"]).upper()
            if price is None or not 0 <= price <= PRICE_SCALE:
                raise ValueError("delta price outside [0, 1]")
            if not math.isfinite(size):
                raise ValueError("delta size is non-finite")
            levels = self.bids if side == "BUY" else self.asks if side == "SELL" else None
            if levels is None:
                raise ValueError(f"unknown price_change side {side!r}")
            if size <= 0:
                levels.pop(price, None)
            else:
                levels[price] = size
        except (KeyError, TypeError, ValueError) as exc:
            self.valid = False
            self.reason = f"invalid_price_change:{exc}"
            return
        self.valid = True
        self.reason = None
        self._mark_update(row)

    def finish(self) -> BookSnapshot:
        bids = tuple(sorted(self.bids.items(), key=lambda item: item[0], reverse=True))
        asks = tuple(sorted(self.asks.items(), key=lambda item: item[0]))
        valid = self.valid and bool(bids) and bool(asks)
        reason = self.reason
        if self.valid and not bids:
            reason = "empty_bid_book"
        elif self.valid and not asks:
            reason = "empty_ask_book"
        elif valid and bids[0][0] >= asks[0][0]:
            valid = False
            reason = "crossed_book"
        return BookSnapshot(
            valid=valid,
            reason=reason,
            bids=bids,
            asks=asks,
            event_time=self.event_time,
            available_at=self.available_at,
            row_ordinal=self.row_ordinal,
        )


def reconstruct_book(table: pa.Table) -> BookSnapshot:
    reconstructor = OrderBookReconstructor()
    for row in table.to_pylist():
        reconstructor.apply(row)
    return reconstructor.finish()


def derive_book_metrics(snapshot: BookSnapshot, depth_levels: int = 5) -> dict[str, float]:
    if not snapshot.valid:
        return {}
    best_bid, bid_size = snapshot.bids[0]
    best_ask, ask_size = snapshot.asks[0]
    bid = micros_to_price(best_bid)
    ask = micros_to_price(best_ask)
    bid_depth = sum(size for _, size in snapshot.bids[:depth_levels])
    ask_depth = sum(size for _, size in snapshot.asks[:depth_levels])
    total_depth = bid_depth + ask_depth
    top_size = bid_size + ask_size
    return {
        "up_best_bid": bid,
        "up_best_ask": ask,
        "up_midprice": (bid + ask) / 2,
        "up_spread": ask - bid,
        "up_bid_depth_top5": bid_depth,
        "up_ask_depth_top5": ask_depth,
        "up_depth_top5": total_depth,
        "up_book_imbalance": (bid_depth - ask_depth) / total_depth if total_depth else math.nan,
        "up_microprice": (bid_size * ask + ask_size * bid) / top_size if top_size else math.nan,
    }


def quote_fields(snapshot: BookSnapshot, *, at: datetime, max_staleness: float, band_min: float, band_max: float) -> dict[str, Any]:
    metrics = derive_book_metrics(snapshot)
    reasons: list[str] = []
    staleness = None
    if not snapshot.valid:
        reasons.append(snapshot.reason or "invalid_book")
    if snapshot.event_time is not None:
        staleness = max(0.0, (at - snapshot.event_time).total_seconds())
        if staleness > max_staleness:
            reasons.append("stale_quote")
    else:
        reasons.append("missing_quote_time")
    midpoint = metrics.get("up_midprice")
    if midpoint is None or not band_min <= midpoint <= band_max:
        reasons.append("midpoint_outside_band")
    up_bid = metrics.get("up_best_bid")
    up_ask = metrics.get("up_best_ask")
    down_bid = None if up_ask is None else 1.0 - up_ask
    down_ask = None if up_bid is None else 1.0 - up_bid
    return {
        "up_bid": up_bid,
        "up_ask": up_ask,
        "down_bid": down_bid,
        "down_ask": down_ask,
        "up_bid_micros": price_to_micros(up_bid),
        "up_ask_micros": price_to_micros(up_ask),
        "down_bid_micros": price_to_micros(down_bid),
        "down_ask_micros": price_to_micros(down_ask),
        "quote_staleness_seconds": staleness,
        "quote_valid": not reasons,
        "quote_reasons": sorted(set(reasons)),
    }
