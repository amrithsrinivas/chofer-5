"""Typed records shared by prediction loading, replay, and analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    UP = "UP"
    DOWN = "DOWN"


class RejectionReason(StrEnum):
    QUOTE_INVALID = "quote_invalid"
    MISSING_EXECUTION_QUOTE = "missing_execution_quote"
    PRICE_OUTSIDE_ENTRY_BAND = "price_outside_entry_band"
    COST_ADJUSTED_PRICE_INVALID = "cost_adjusted_price_invalid"
    MAX_OPEN_POSITIONS = "max_open_positions"
    INSUFFICIENT_CAPITAL = "insufficient_capital"


class TradeStatus(StrEnum):
    FILLED = "filled"
    REJECTED = "rejected"


class EquityEventType(StrEnum):
    INITIAL = "initial"
    ENTRY = "entry"
    SETTLEMENT = "settlement"


@dataclass(frozen=True, slots=True)
class PredictionCandidate:
    row_id: str
    decision_at: datetime
    window_end: datetime
    label_binary: bool
    calibrated_probability: Decimal
    quote_valid: bool
    execution_up_ask: Decimal | None
    execution_down_ask: Decimal | None
    decision_up_ask: Decimal | None
    decision_down_ask: Decimal | None
    market_midprice: Decimal
    split: str | None = None


@dataclass(slots=True)
class TradeRecord:
    threshold: Decimal
    row_id: str
    side: Side
    signal_at: datetime
    window_end: datetime
    status: TradeStatus
    rejection_reason: RejectionReason | None = None
    quote_source: str | None = None
    reference_entry_price: Decimal | None = None
    effective_entry_price: Decimal | None = None
    stake_micros: int | None = None
    token_quantity: Decimal | None = None
    capital_after_entry_micros: int | None = None
    settled_at: datetime | None = None
    won: bool | None = None
    payout_micros: int | None = None
    pnl_micros: int | None = None
    capital_after_settlement_micros: int | None = None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    threshold: Decimal
    event_at: datetime
    event_type: EquityEventType
    row_id: str | None
    cash_micros: int
    open_position_count: int
    reserved_stake_micros: int
    marked_position_value_micros: int
    equity_micros: int
    peak_equity_micros: int
    drawdown_micros: int
    drawdown_fraction: Decimal
