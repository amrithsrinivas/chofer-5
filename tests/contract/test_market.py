from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from contract import (
    Asset,
    DecisionAnchor,
    DecisionSchedule,
    Interval,
    MarketWindow,
    decision_at,
    parse_utc,
)


def market_window(**changes: object) -> MarketWindow:
    values: dict[str, object] = {
        "window_id": "btc-updown-5m-1784332800",
        "asset": Asset.BTC,
        "interval": Interval.FIVE_MINUTES,
        "series_id": "10684",
        "market_id": "market-1",
        "condition_id": "0xcondition",
        "up_token_id": "token-up",
        "down_token_id": "token-down",
        "window_start": "2026-07-18T00:00:00Z",
        "window_end": "2026-07-18T00:05:00Z",
    }
    values.update(changes)
    return MarketWindow.model_validate(values)


def test_market_window_and_explicit_decision_anchors() -> None:
    window = market_window()
    before_start = DecisionSchedule(
        schedule_id="premarket-30s",
        anchor=DecisionAnchor.WINDOW_START,
        offset="-30s",
    )
    before_end = DecisionSchedule(
        schedule_id="before-end-30s",
        anchor=DecisionAnchor.WINDOW_END,
        offset="-30s",
    )

    assert before_start.decision_at(window) == parse_utc("2026-07-17T23:59:30Z")
    assert decision_at(window, before_end) == parse_utc("2026-07-18T00:04:30Z")
    assert before_start.offset == timedelta(seconds=-30)
    assert '"offset":-30000000' in before_start.model_dump_json()


def test_market_window_requires_exact_interval_and_distinct_tokens() -> None:
    with pytest.raises(ValidationError, match="interval duration"):
        market_window(window_end="2026-07-18T00:04:59Z")
    with pytest.raises(ValidationError, match="must be distinct"):
        market_window(down_token_id="token-up")


def test_identifiers_reject_whitespace_and_empty_values() -> None:
    with pytest.raises(ValidationError, match="window ID"):
        market_window(window_id="bad window")
    with pytest.raises(ValidationError, match="market ID"):
        market_window(market_id="")
