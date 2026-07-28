from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from backtest.analytics import threshold_metrics
from backtest.models import RejectionReason, Side, TradeStatus
from backtest.replay import ReplayEngine, signal_side
from .helpers import BASE_TIME, candidate, spec


def test_symmetric_signal_and_abstention() -> None:
    assert signal_side(Decimal("0.60"), Decimal("0.60")) is Side.UP
    assert signal_side(Decimal("0.40"), Decimal("0.60")) is Side.DOWN
    assert signal_side(Decimal("0.50"), Decimal("0.60")) is None

    result = ReplayEngine(
        [candidate("abstain", probability="0.50")],
        threshold=Decimal("0.60"),
        spec=spec(),
    ).run()
    assert result.candidate_count == 1
    assert result.signal_count == 0
    assert result.entry_count == 0
    assert result.trades == ()


@pytest.mark.parametrize(
    ("row", "probability", "label", "side", "won", "pnl_micros"),
    [
        ("up-win", "0.8", True, Side.UP, True, 10_000_000),
        ("up-loss", "0.8", False, Side.UP, False, -10_000_000),
        ("down-win", "0.2", False, Side.DOWN, True, 10_000_000),
        ("down-loss", "0.2", True, Side.DOWN, False, -10_000_000),
    ],
)
def test_up_down_pnl(
    row: str,
    probability: str,
    label: bool,
    side: Side,
    won: bool,
    pnl_micros: int,
) -> None:
    result = ReplayEngine(
        [candidate(row, probability=probability, label=label)],
        threshold=Decimal("0.6"),
        spec=spec(),
    ).run()
    trade = result.trades[0]
    assert trade.status is TradeStatus.FILLED
    assert trade.side is side
    assert trade.won is won
    assert trade.stake_micros == 10_000_000
    assert trade.token_quantity == Decimal("20.000000000000")
    assert trade.pnl_micros == pnl_micros


def test_fee_and_slippage_are_in_effective_entry_cash_cost() -> None:
    result = ReplayEngine(
        [candidate("costs", execution_up="0.50")],
        threshold=Decimal("0.6"),
        spec=spec(fee_bps="10", slippage_bps="10"),
    ).run()
    trade = result.trades[0]
    assert trade.reference_entry_price == Decimal("0.500000")
    assert trade.effective_entry_price == Decimal("0.501000")
    assert trade.stake_micros == 10_000_000
    assert trade.token_quantity == Decimal("19.960079840319")
    assert trade.pnl_micros == 9_960_080


def test_selected_side_price_band_and_decision_quote_fallback() -> None:
    outside = ReplayEngine(
        [
            candidate(
                "selected-up-outside",
                probability="0.8",
                execution_up="0.53",
                execution_down="0.50",
            )
        ],
        threshold=Decimal("0.6"),
        spec=spec(),
    ).run()
    assert outside.quote_count == 1
    assert outside.entry_count == 0
    assert outside.trades[0].rejection_reason is RejectionReason.PRICE_OUTSIDE_ENTRY_BAND

    fallback = ReplayEngine(
        [candidate("fallback", execution_up=None, decision_up="0.51")],
        threshold=Decimal("0.6"),
        spec=spec(),
    ).run()
    assert fallback.entry_count == 1
    assert fallback.trades[0].quote_source == "decision"
    assert fallback.trades[0].reference_entry_price == Decimal("0.510000")


def test_coverage_denominators_are_candidates_signals_and_fills() -> None:
    result = ReplayEngine(
        [
            candidate("abstain", minute=0, probability="0.5"),
            candidate("bad-quote", minute=1, quote_valid=False),
            candidate("bad-band", minute=2, execution_up="0.53"),
            candidate("filled", minute=3),
        ],
        threshold=Decimal("0.6"),
        spec=spec(),
    ).run()
    metrics = threshold_metrics(result)
    assert result.candidate_count == 4
    assert result.signal_count == 3
    assert result.quote_count == 2
    assert result.entry_count == 1
    assert metrics["candidate_coverage"] == pytest.approx(3 / 4)
    assert metrics["quote_coverage"] == pytest.approx(2 / 3)
    assert metrics["signal_fill_coverage"] == pytest.approx(1 / 3)


def test_outcome_is_not_visible_until_chronological_settlement() -> None:
    engine = ReplayEngine(
        [candidate("later-win", duration_minutes=5, label=True)],
        threshold=Decimal("0.6"),
        spec=spec(),
    )
    engine.advance(BASE_TIME)
    assert engine.open_positions == ("later-win",)
    assert engine.trades[0].won is None
    assert engine.trades[0].pnl_micros is None
    assert engine.trades[0].settled_at is None

    engine.advance(BASE_TIME + timedelta(minutes=4, seconds=59))
    assert engine.trades[0].won is None

    engine.advance(BASE_TIME + timedelta(minutes=5))
    assert engine.open_positions == ()
    assert engine.trades[0].won is True
    assert engine.trades[0].settled_at == BASE_TIME + timedelta(minutes=5)


def test_settlement_precedes_entry_at_same_timestamp() -> None:
    result = ReplayEngine(
        [
            candidate("first", minute=0, duration_minutes=5, label=True),
            candidate("second", minute=5, duration_minutes=5, label=True),
        ],
        threshold=Decimal("0.6"),
        spec=spec(stake="10", capital="10", max_open=1),
    ).run()
    assert [trade.status for trade in result.trades] == [TradeStatus.FILLED, TradeStatus.FILLED]
    assert result.rejection_counts == {}


def test_max_open_and_capital_rejections_are_typed() -> None:
    max_open_result = ReplayEngine(
        [
            candidate("open", minute=0, duration_minutes=10),
            candidate("blocked", minute=1, duration_minutes=10),
        ],
        threshold=Decimal("0.6"),
        spec=spec(capital="20", max_open=1),
    ).run()
    assert max_open_result.trades[1].rejection_reason is RejectionReason.MAX_OPEN_POSITIONS

    capital_result = ReplayEngine(
        [
            candidate("uses-cash", minute=0, duration_minutes=10),
            candidate("no-cash", minute=1, duration_minutes=10),
        ],
        threshold=Decimal("0.6"),
        spec=spec(capital="10", max_open=2),
    ).run()
    assert capital_result.trades[1].rejection_reason is RejectionReason.INSUFFICIENT_CAPITAL
