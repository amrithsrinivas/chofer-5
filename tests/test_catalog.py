from pathlib import Path

import pytest
from pydantic import ValidationError

from chofer.catalog import Catalog, load_catalog

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "indexer"


def test_loads_real_configs():
    catalog = load_catalog(CONFIG_DIR)
    assert set(catalog.binance) == {
        "spot_klines",
        "futures_metrics",
        "agg_trades",
        "premium_index_klines",
    }
    assert catalog.binance["spot_klines"].tickers["BTC"] == "BTCUSDT"
    assert catalog.binance["spot_klines"].interval == "1m"

    assert catalog.pmdata.market_data_types == ["poly_l2", "poly_trade", "onchain_fills"]
    btc_5m = catalog.pmdata.series["btc_5m"]
    assert btc_5m.provider_series == "btc-5m"
    assert btc_5m.recording_starts_at.year == 2026
    assert len(catalog.pmdata.series) == 12

    assert catalog.polymarket["5m"].series_ids["BTC"] == 10684
    assert set(catalog.polymarket) == {"5m", "15m", "1h", "1d"}


def test_rejects_bad_toml(tmp_path: Path):
    (tmp_path / "binance.toml").write_text("[binance\nbroken", encoding="utf-8")
    (tmp_path / "pmdata.toml").write_text("", encoding="utf-8")
    (tmp_path / "polymarket.toml").write_text("", encoding="utf-8")
    with pytest.raises(Exception):
        load_catalog(tmp_path)


def test_rejects_wrong_schema(tmp_path: Path):
    (tmp_path / "binance.toml").write_text(
        '[binance."spot_klines"]\ntickers = "not-a-table"\n', encoding="utf-8"
    )
    (tmp_path / "pmdata.toml").write_text(
        'market_data_types = []\n[pmdata]\nmarket_data_types = []\n[pmdata.series]\n',
        encoding="utf-8",
    )
    (tmp_path / "polymarket.toml").write_text("[polymarket]\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_catalog(tmp_path)


def test_catalog_is_pydantic_model():
    assert isinstance(load_catalog(CONFIG_DIR), Catalog)
