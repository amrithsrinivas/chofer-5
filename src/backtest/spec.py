"""Validated configuration for deterministic replay backtests."""

from __future__ import annotations

import tomllib
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionQuotePreference(StrEnum):
    """Which point-in-time quote may be used for entry."""

    EXECUTION_THEN_DECISION = "execution_then_decision"
    EXECUTION_ONLY = "execution_only"
    DECISION_ONLY = "decision_only"


_QUOTE_PREFERENCE_ALIASES = {
    "execution": ExecutionQuotePreference.EXECUTION_ONLY.value,
    "prefer_execution": ExecutionQuotePreference.EXECUTION_THEN_DECISION.value,
    "decision": ExecutionQuotePreference.DECISION_ONLY.value,
}


class ThresholdRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: Decimal
    stop: Decimal
    step: Decimal

    @model_validator(mode="after")
    def validate_range(self) -> "ThresholdRange":
        if not self.start.is_finite() or not self.stop.is_finite() or not self.step.is_finite():
            raise ValueError("threshold range values must be finite")
        if self.step <= 0:
            raise ValueError("threshold range step must be positive")
        if self.start > self.stop:
            raise ValueError("threshold range start must not exceed stop")
        return self

    def expand(self) -> tuple[Decimal, ...]:
        values: list[Decimal] = []
        value = self.start
        while value <= self.stop:
            values.append(value)
            value += self.step
        return tuple(values)


class BacktestSpec(BaseModel):
    """Behaviorally complete backtest policy.

    Monetary inputs are converted to integer USD micros by the replay engine.
    Values with more than six decimal places are rejected rather than rounded
    silently.
    """

    model_config = ConfigDict(frozen=True)

    version: int = 1
    probability_thresholds: tuple[Decimal, ...] | None = None
    probability_threshold_range: ThresholdRange | None = None
    selected_split: str = "test"
    entry_price_min: Decimal = Decimal("0.49")
    entry_price_max: Decimal = Decimal("0.52")
    fixed_stake_usd: Decimal
    initial_capital_usd: Decimal
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    max_open_positions: int = Field(gt=0)
    execution_quote_preference: ExecutionQuotePreference = (
        ExecutionQuotePreference.EXECUTION_THEN_DECISION
    )

    @model_validator(mode="after")
    def validate_spec(self) -> "BacktestSpec":
        if self.version != 1:
            raise ValueError(f"unsupported backtest spec version {self.version}")
        if bool(self.probability_thresholds) == bool(self.probability_threshold_range):
            raise ValueError(
                "spec must define exactly one of probability_thresholds or "
                "probability_threshold_range"
            )
        thresholds = self.thresholds
        if len(set(thresholds)) != len(thresholds):
            raise ValueError("probability thresholds must be unique")
        for threshold in thresholds:
            if not threshold.is_finite() or threshold < Decimal("0.5") or threshold > 1:
                raise ValueError("probability thresholds must be finite and in [0.5, 1]")
        if not self.selected_split.strip():
            raise ValueError("selected_split must not be empty")
        for name, value in (
            ("entry_price_min", self.entry_price_min),
            ("entry_price_max", self.entry_price_max),
        ):
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        if self.entry_price_min > self.entry_price_max:
            raise ValueError("entry_price_min must not exceed entry_price_max")
        for name, value in (
            ("fixed_stake_usd", self.fixed_stake_usd),
            ("initial_capital_usd", self.initial_capital_usd),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            if value.quantize(Decimal("0.000001")) != value:
                raise ValueError(f"{name} may have at most six decimal places")
        if self.fixed_stake_usd > self.initial_capital_usd:
            raise ValueError("fixed_stake_usd must not exceed initial_capital_usd")
        for name, value in (("fee_bps", self.fee_bps), ("slippage_bps", self.slippage_bps)):
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        return self

    @property
    def thresholds(self) -> tuple[Decimal, ...]:
        raw = (
            self.probability_thresholds
            if self.probability_thresholds is not None
            else self.probability_threshold_range.expand()  # type: ignore[union-attr]
        )
        return tuple(sorted(raw))

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "probability_thresholds": [str(value) for value in self.thresholds],
            "selected_split": self.selected_split,
            "entry_price_min": str(self.entry_price_min),
            "entry_price_max": str(self.entry_price_max),
            "fixed_stake_usd": str(self.fixed_stake_usd),
            "initial_capital_usd": str(self.initial_capital_usd),
            "fee_bps": str(self.fee_bps),
            "slippage_bps": str(self.slippage_bps),
            "max_open_positions": self.max_open_positions,
            "execution_quote_preference": self.execution_quote_preference.value,
        }


def _normalize_raw_spec(raw: dict[str, Any]) -> dict[str, Any]:
    if "backtest" in raw:
        nested = raw["backtest"]
        if not isinstance(nested, dict):
            raise ValueError("[backtest] must be a TOML table")
        raw = dict(nested)
    else:
        raw = dict(raw)

    if "thresholds" in raw:
        if "probability_thresholds" in raw or "probability_threshold_range" in raw:
            raise ValueError("thresholds alias cannot be combined with probability threshold fields")
        value = raw.pop("thresholds")
        if isinstance(value, list):
            raw["probability_thresholds"] = value
        elif isinstance(value, dict):
            raw["probability_threshold_range"] = value
        else:
            raise ValueError("thresholds must be a list or range table")
    if "threshold_range" in raw:
        if "probability_threshold_range" in raw:
            raise ValueError("threshold_range aliases are duplicated")
        raw["probability_threshold_range"] = raw.pop("threshold_range")

    preference = raw.get("execution_quote_preference")
    if isinstance(preference, str):
        raw["execution_quote_preference"] = _QUOTE_PREFERENCE_ALIASES.get(
            preference, preference
        )
    return raw


def load_spec(path: Path) -> BacktestSpec:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid backtest TOML {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("backtest spec root must be a TOML table")
    return BacktestSpec.model_validate(_normalize_raw_spec(raw))
