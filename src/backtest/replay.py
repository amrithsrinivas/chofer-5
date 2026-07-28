"""Chronological, no-lookahead replay and integer-micros account ledger."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .models import (
    EquityEventType,
    EquityPoint,
    PredictionCandidate,
    RejectionReason,
    Side,
    TradeRecord,
    TradeStatus,
)
from .spec import BacktestSpec, ExecutionQuotePreference

USD_MICROS = Decimal("1000000")
PRICE_QUANTUM = Decimal("0.000001")
BPS_DENOMINATOR = Decimal("10000")
TOKEN_QUANTUM = Decimal("0.000000000001")


def usd_to_micros(value: Decimal) -> int:
    return int((value * USD_MICROS).to_integral_value(rounding=ROUND_HALF_UP))


def micros_to_usd(value: int) -> Decimal:
    return Decimal(value) / USD_MICROS


def _price(value: Decimal) -> Decimal:
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def signal_side(probability: Decimal, threshold: Decimal) -> Side | None:
    """Apply the symmetric UP/DOWN probability threshold policy."""
    if probability >= threshold:
        return Side.UP
    if probability <= Decimal(1) - threshold:
        return Side.DOWN
    return None


@dataclass(frozen=True, slots=True)
class _EntryEvent:
    row_id: str
    decision_at: datetime
    window_end: datetime
    probability: Decimal
    quote_valid: bool
    execution_up_ask: Decimal | None
    execution_down_ask: Decimal | None
    decision_up_ask: Decimal | None
    decision_down_ask: Decimal | None
    market_midprice: Decimal


@dataclass(slots=True)
class _Position:
    row_id: str
    side: Side
    window_end: datetime
    stake_micros: int
    token_quantity: Decimal
    mark_price: Decimal
    trade: TradeRecord


@dataclass(frozen=True, slots=True)
class ReplayResult:
    threshold: Decimal
    candidate_count: int
    signal_count: int
    quote_count: int
    band_eligible_count: int
    entry_count: int
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[EquityPoint, ...]
    rejection_counts: dict[RejectionReason, int]
    final_cash_micros: int


class ReplayEngine:
    """Replay entries and settlements in realistic chronological order.

    Outcomes are held in a private map and are looked up only by settlement
    events. Open positions and pre-settlement trade records never contain the
    outcome or provisional PnL.
    """

    def __init__(
        self,
        candidates: tuple[PredictionCandidate, ...] | list[PredictionCandidate],
        *,
        threshold: Decimal,
        spec: BacktestSpec,
    ) -> None:
        if threshold not in spec.thresholds:
            raise ValueError("threshold must be declared by the backtest spec")
        ordered = sorted(candidates, key=lambda row: (row.decision_at, row.row_id))
        if not ordered:
            raise ValueError("ReplayEngine requires at least one candidate")
        self.threshold = threshold
        self.spec = spec
        self.candidate_count = len(ordered)
        self.signal_count = 0
        self.quote_count = 0
        self.band_eligible_count = 0
        self.entry_count = 0
        self.cash_micros = usd_to_micros(spec.initial_capital_usd)
        self._stake_micros = usd_to_micros(spec.fixed_stake_usd)
        self._events: list[tuple[datetime, int, str, str, Any]] = []
        self._open: dict[str, _Position] = {}
        self._outcomes = {candidate.row_id: candidate.label_binary for candidate in ordered}
        self._trades: list[TradeRecord] = []
        self._equity: list[EquityPoint] = []
        self._rejections: dict[RejectionReason, int] = {}
        self._peak_equity_micros = self.cash_micros
        self._completed = False

        for candidate in ordered:
            event = _EntryEvent(
                row_id=candidate.row_id,
                decision_at=candidate.decision_at,
                window_end=candidate.window_end,
                probability=candidate.calibrated_probability,
                quote_valid=candidate.quote_valid,
                execution_up_ask=candidate.execution_up_ask,
                execution_down_ask=candidate.execution_down_ask,
                decision_up_ask=candidate.decision_up_ask,
                decision_down_ask=candidate.decision_down_ask,
                market_midprice=candidate.market_midprice,
            )
            heapq.heappush(
                self._events,
                (candidate.decision_at, 1, candidate.row_id, "entry", event),
            )
        self._record_equity(
            event_at=ordered[0].decision_at,
            event_type=EquityEventType.INITIAL,
            row_id=None,
        )

    @property
    def open_positions(self) -> tuple[str, ...]:
        return tuple(sorted(self._open))

    @property
    def trades(self) -> tuple[TradeRecord, ...]:
        return tuple(self._trades)

    @property
    def equity_curve(self) -> tuple[EquityPoint, ...]:
        return tuple(self._equity)

    def advance(self, until: datetime | None = None) -> None:
        """Process all events through ``until``; with None, finish the replay."""
        while self._events and (until is None or self._events[0][0] <= until):
            event_at, _, row_id, kind, payload = heapq.heappop(self._events)
            if kind == "settlement":
                self._settle(row_id, event_at)
            else:
                self._enter(payload)
        if until is None:
            self._completed = True
            if self._open:
                raise RuntimeError("replay ended with unsettled positions")

    def run(self) -> ReplayResult:
        self.advance()
        return ReplayResult(
            threshold=self.threshold,
            candidate_count=self.candidate_count,
            signal_count=self.signal_count,
            quote_count=self.quote_count,
            band_eligible_count=self.band_eligible_count,
            entry_count=self.entry_count,
            trades=tuple(self._trades),
            equity_curve=tuple(self._equity),
            rejection_counts=dict(sorted(self._rejections.items(), key=lambda item: item[0].value)),
            final_cash_micros=self.cash_micros,
        )

    def _quote(self, event: _EntryEvent, side: Side) -> tuple[Decimal | None, str | None]:
        execution = event.execution_up_ask if side is Side.UP else event.execution_down_ask
        decision = event.decision_up_ask if side is Side.UP else event.decision_down_ask
        preference = self.spec.execution_quote_preference
        if preference is ExecutionQuotePreference.EXECUTION_ONLY:
            return execution, "execution" if execution is not None else None
        if preference is ExecutionQuotePreference.DECISION_ONLY:
            return decision, "decision" if decision is not None else None
        if execution is not None:
            return execution, "execution"
        return decision, "decision" if decision is not None else None

    def _reject(self, trade: TradeRecord, reason: RejectionReason) -> None:
        trade.rejection_reason = reason
        self._trades.append(trade)
        self._rejections[reason] = self._rejections.get(reason, 0) + 1

    def _enter(self, event: _EntryEvent) -> None:
        side = signal_side(event.probability, self.threshold)
        if side is None:
            return
        self.signal_count += 1
        trade = TradeRecord(
            threshold=self.threshold,
            row_id=event.row_id,
            side=side,
            signal_at=event.decision_at,
            window_end=event.window_end,
            status=TradeStatus.REJECTED,
        )
        if not event.quote_valid:
            self._reject(trade, RejectionReason.QUOTE_INVALID)
            return
        reference_price, source = self._quote(event, side)
        trade.quote_source = source
        trade.reference_entry_price = reference_price
        if reference_price is None:
            self._reject(trade, RejectionReason.MISSING_EXECUTION_QUOTE)
            return
        self.quote_count += 1
        reference_price = _price(reference_price)
        trade.reference_entry_price = reference_price
        if not (self.spec.entry_price_min <= reference_price <= self.spec.entry_price_max):
            self._reject(trade, RejectionReason.PRICE_OUTSIDE_ENTRY_BAND)
            return
        self.band_eligible_count += 1
        cost_multiplier = Decimal(1) + (
            self.spec.fee_bps + self.spec.slippage_bps
        ) / BPS_DENOMINATOR
        effective_price = _price(reference_price * cost_multiplier)
        trade.effective_entry_price = effective_price
        if effective_price <= 0 or effective_price >= 1:
            self._reject(trade, RejectionReason.COST_ADJUSTED_PRICE_INVALID)
            return
        if len(self._open) >= self.spec.max_open_positions:
            self._reject(trade, RejectionReason.MAX_OPEN_POSITIONS)
            return
        if self.cash_micros < self._stake_micros:
            self._reject(trade, RejectionReason.INSUFFICIENT_CAPITAL)
            return

        stake_usd = micros_to_usd(self._stake_micros)
        token_quantity = (stake_usd / effective_price).quantize(
            TOKEN_QUANTUM, rounding=ROUND_HALF_UP
        )
        self.cash_micros -= self._stake_micros
        trade.status = TradeStatus.FILLED
        trade.stake_micros = self._stake_micros
        trade.token_quantity = token_quantity
        trade.capital_after_entry_micros = self.cash_micros
        mark_price = event.market_midprice if side is Side.UP else Decimal(1) - event.market_midprice
        self._open[event.row_id] = _Position(
            row_id=event.row_id,
            side=side,
            window_end=event.window_end,
            stake_micros=self._stake_micros,
            token_quantity=token_quantity,
            mark_price=_price(mark_price),
            trade=trade,
        )
        self._trades.append(trade)
        self.entry_count += 1
        heapq.heappush(
            self._events,
            (event.window_end, 0, event.row_id, "settlement", None),
        )
        self._record_equity(
            event_at=event.decision_at,
            event_type=EquityEventType.ENTRY,
            row_id=event.row_id,
        )

    def _settle(self, row_id: str, event_at: datetime) -> None:
        position = self._open.pop(row_id)
        label_up = self._outcomes.pop(row_id)
        won = label_up if position.side is Side.UP else not label_up
        payout_micros = (
            usd_to_micros(position.token_quantity) if won else 0
        )
        self.cash_micros += payout_micros
        pnl_micros = payout_micros - position.stake_micros
        trade = position.trade
        trade.settled_at = event_at
        trade.won = won
        trade.payout_micros = payout_micros
        trade.pnl_micros = pnl_micros
        trade.capital_after_settlement_micros = self.cash_micros
        self._record_equity(
            event_at=event_at,
            event_type=EquityEventType.SETTLEMENT,
            row_id=row_id,
        )

    def _record_equity(
        self, *, event_at: datetime, event_type: EquityEventType, row_id: str | None
    ) -> None:
        reserved = sum(position.stake_micros for position in self._open.values())
        marked = sum(
            usd_to_micros(position.token_quantity * position.mark_price)
            for position in self._open.values()
        )
        equity = self.cash_micros + marked
        self._peak_equity_micros = max(self._peak_equity_micros, equity)
        drawdown = self._peak_equity_micros - equity
        fraction = (
            Decimal(drawdown) / Decimal(self._peak_equity_micros)
            if self._peak_equity_micros
            else Decimal(0)
        )
        self._equity.append(
            EquityPoint(
                threshold=self.threshold,
                event_at=event_at,
                event_type=event_type,
                row_id=row_id,
                cash_micros=self.cash_micros,
                open_position_count=len(self._open),
                reserved_stake_micros=reserved,
                marked_position_value_micros=marked,
                equity_micros=equity,
                peak_equity_micros=self._peak_equity_micros,
                drawdown_micros=drawdown,
                drawdown_fraction=fraction,
            )
        )
