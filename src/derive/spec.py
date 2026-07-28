"""Versioned canonical-derivation configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeriveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: str
    checkpoint_offsets_ms: list[int]
    depth_levels: int = Field(gt=0)
    book_bar_seconds: int = Field(default=1, gt=0)
    polymarket_trade_bar_seconds: int = Field(default=1, gt=0)
    binance_agg_trade_bar_seconds: int = Field(default=1, gt=0)

    @field_validator("checkpoint_offsets_ms")
    @classmethod
    def _offsets(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("checkpoint_offsets_ms cannot be empty")
        return sorted(set(value))

    @field_validator(
        "book_bar_seconds",
        "polymarket_trade_bar_seconds",
        "binance_agg_trade_bar_seconds",
    )
    @classmethod
    def _implemented_cadence(cls, value: int) -> int:
        if value != 1:
            raise ValueError("the v1 canonical implementation supports 1-second bars")
        return value


def load_derive_spec(path: Path | str) -> DeriveSpec:
    with Path(path).open("rb") as handle:
        payload = tomllib.load(handle)
    return DeriveSpec.model_validate(payload["derive"])
