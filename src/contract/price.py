"""Exact binary contract prices and long-position PnL helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .enums import Outcome

PRICE_SCALE = 1_000_000


@dataclass(frozen=True, slots=True, order=True)
class BinaryPrice:
    """Binary contract price as integer millionths in the inclusive [0, 1] range."""

    micros: int

    def __post_init__(self) -> None:
        if isinstance(self.micros, bool) or not isinstance(self.micros, int):
            raise TypeError("binary price micros must be an integer")
        if not 0 <= self.micros <= PRICE_SCALE:
            raise ValueError(f"binary price micros must be between 0 and {PRICE_SCALE}")

    @classmethod
    def from_decimal(cls, value: Decimal | str) -> BinaryPrice:
        if isinstance(value, float):
            raise TypeError("use BinaryPrice.from_float for explicit float conversion")
        try:
            decimal = value if isinstance(value, Decimal) else Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"invalid binary price: {value!r}") from exc
        scaled = decimal * PRICE_SCALE
        if not decimal.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError("binary price must be exactly representable to six decimals")
        return cls(int(scaled))

    @classmethod
    def from_float(cls, value: float) -> BinaryPrice:
        """Convert through the float's shortest decimal representation, explicitly."""
        if isinstance(value, bool) or not isinstance(value, float):
            raise TypeError("value must be a float")
        return cls.from_decimal(Decimal(str(value)))

    def to_decimal(self) -> Decimal:
        return Decimal(self.micros) / PRICE_SCALE

    def to_float(self) -> float:
        """Lossy model-boundary conversion; do not use for accounting."""
        return float(self.to_decimal())

    def complement(self) -> BinaryPrice:
        return BinaryPrice(PRICE_SCALE - self.micros)

    def payout_micros(self, held_outcome: Outcome, resolution: Outcome) -> int:
        return PRICE_SCALE if held_outcome is resolution else 0

    def pnl_micros(
        self,
        held_outcome: Outcome,
        resolution: Outcome,
        *,
        contracts: int = 1,
        fee_micros: int = 0,
    ) -> int:
        """Gross payout less entry cost and exact total fee for integer contracts."""
        if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 0:
            raise ValueError("contracts must be a non-negative integer")
        if isinstance(fee_micros, bool) or not isinstance(fee_micros, int) or fee_micros < 0:
            raise ValueError("fee_micros must be a non-negative integer")
        payout = self.payout_micros(held_outcome, resolution)
        return contracts * (payout - self.micros) - fee_micros

    def pnl_decimal(
        self,
        held_outcome: Outcome,
        resolution: Outcome,
        *,
        contracts: int = 1,
        fee_micros: int = 0,
    ) -> Decimal:
        return Decimal(
            self.pnl_micros(
                held_outcome,
                resolution,
                contracts=contracts,
                fee_micros=fee_micros,
            )
        ) / PRICE_SCALE


def binary_pnl_micros(
    entry_price: BinaryPrice,
    held_outcome: Outcome,
    resolution: Outcome,
    *,
    contracts: int = 1,
    fee_micros: int = 0,
) -> int:
    return entry_price.pnl_micros(
        held_outcome,
        resolution,
        contracts=contracts,
        fee_micros=fee_micros,
    )
