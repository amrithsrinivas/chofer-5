"""Load and validate configs/indexer/*.toml into a typed source catalog.

Note: the config files use multi-line inline tables with trailing commas
(TOML 1.1 style), which stdlib tomllib rejects — hence tomlkit.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import tomlkit
from pydantic import BaseModel, Field


class BinanceDataset(BaseModel):
    interval: str | None = None
    tickers: dict[str, str]  # asset -> symbol, e.g. BTC -> BTCUSDT


class BinanceFile(BaseModel):
    binance: dict[str, BinanceDataset]


class PmdataSeries(BaseModel):
    asset: str
    interval: str
    provider_series: str  # e.g. "btc-5m"
    recording_starts_at: datetime  # UTC


class PmdataChainlinkFeed(BaseModel):
    asset: str
    symbol: str  # e.g. BTCUSD
    recording_starts_at: datetime


class PmdataConfig(BaseModel):
    market_data_types: list[str]
    series: dict[str, PmdataSeries]
    chainlink_feeds: dict[str, PmdataChainlinkFeed] = Field(default_factory=dict)


class PmdataFile(BaseModel):
    pmdata: PmdataConfig


class PolymarketInterval(BaseModel):
    series_ids: dict[str, int]  # asset -> gamma series id


class PolymarketFile(BaseModel):
    polymarket: dict[str, PolymarketInterval]  # interval label -> ids


class Catalog(BaseModel):
    """Validated view over the three indexer config files."""

    binance: dict[str, BinanceDataset]
    pmdata: PmdataConfig
    polymarket: dict[str, PolymarketInterval]


def _load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomlkit.load(fh)


def load_catalog(config_dir: Path) -> Catalog:
    """Load and validate the three indexer config files. Raises on bad TOML/schema."""
    config_dir = Path(config_dir)
    binance = BinanceFile.model_validate(_load_toml(config_dir / "binance.toml"))
    pmdata = PmdataFile.model_validate(_load_toml(config_dir / "pmdata.toml"))
    polymarket = PolymarketFile.model_validate(
        _load_toml(config_dir / "polymarket.toml")
    )
    return Catalog(
        binance=binance.binance,
        pmdata=pmdata.pmdata,
        polymarket=polymarket.polymarket,
    )
