from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from contract import (
    BinaryPrice,
    MarketResolution,
    Outcome,
    ResolutionBasis,
    ResolutionConfidence,
)


def test_binary_price_exact_conversion_complement_and_pnl() -> None:
    price = BinaryPrice.from_decimal("0.490001")
    assert price.micros == 490_001
    assert price.to_decimal() == Decimal("0.490001")
    assert price.complement() == BinaryPrice(509_999)
    assert price.pnl_micros(Outcome.UP, Outcome.UP, contracts=2) == 1_019_998
    assert price.pnl_micros(Outcome.UP, Outcome.DOWN, contracts=2) == -980_002


def test_binary_price_rejects_lossy_or_out_of_range_inputs() -> None:
    with pytest.raises(ValueError, match="six decimals"):
        BinaryPrice.from_decimal("0.1234567")
    with pytest.raises(ValueError, match="between"):
        BinaryPrice.from_decimal("1.000001")
    with pytest.raises(TypeError, match="explicit float"):
        BinaryPrice.from_decimal(0.49)  # type: ignore[arg-type]
    assert BinaryPrice.from_float(0.49) == BinaryPrice(490_000)


def test_outcome_and_resolution_label_mapping_is_explicit() -> None:
    assert Outcome.UP.label == 1
    assert Outcome.DOWN.label == 0
    assert Outcome.from_label(1) is Outcome.UP
    assert Outcome.from_label(0) is Outcome.DOWN
    with pytest.raises(ValueError, match="0 or 1"):
        Outcome.from_label(2)
    with pytest.raises(ValueError, match="integer"):
        Outcome.from_label(1.0)  # type: ignore[arg-type]

    resolution = MarketResolution(
        window_id="btc-updown-5m-1784332800",
        market_id="market-1",
        outcome=Outcome.DOWN,
        basis=ResolutionBasis.OFFICIAL,
        confidence=ResolutionConfidence.OFFICIAL,
        source="Polymarket",
        resolved_at="2026-07-18T00:06:00Z",
        resolution_available_at="2026-07-18T00:06:01Z",
    )
    assert resolution.label == 0


def test_resolution_confidence_and_availability_are_consistent() -> None:
    with pytest.raises(ValidationError, match="official confidence"):
        MarketResolution(
            window_id="window-1",
            market_id="market-1",
            outcome=Outcome.UP,
            basis=ResolutionBasis.OFFICIAL,
            confidence=ResolutionConfidence.RECONSTRUCTED_HIGH,
            source="oracle",
        )
    with pytest.raises(ValidationError, match="cannot precede resolved_at"):
        MarketResolution(
            window_id="window-1",
            market_id="market-1",
            outcome=Outcome.UP,
            basis=ResolutionBasis.RECONSTRUCTED,
            confidence=ResolutionConfidence.RECONSTRUCTED_HIGH,
            source="reconstruction-v1",
            resolved_at="2026-07-18T00:06:00Z",
            resolution_available_at="2026-07-18T00:05:59Z",
        )
