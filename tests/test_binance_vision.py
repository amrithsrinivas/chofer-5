"""Binance Vision connector tests: synthetic ZIPs + CHECKSUMs, no network."""

import httpx
import pytest

from chofer.connectors.binance_vision import (
    DATASET_SPECS,
    BinanceVisionConnector,
    parse_daily_zip,
)
from chofer.catalog import BinanceDataset
from chofer.http import HttpClient, MalformedError, UnavailableError
from chofer.units import DataUnit

from conftest import make_csv_zip

MS = 1_784_275_200_000  # 2026-07-17T00:00:00Z in epoch milliseconds
US = MS * 1000
SECONDS = MS // 1000

KLINE_HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)
KLINE_ROW = (
    "{ts},113000.0,113100.0,112900.0,113050.0,12.5,{ts2},1413125.0,150,"
    "6.2,700600.0,0"
)

AGG_HEADER = (
    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,"
    "is_buyer_maker"
)
AGG_ROW = "123456,113000.5,0.01,1000,1001,{ts},True"

# Real layout verified from BTCUSDT-metrics-2026-07-20.zip: 8 columns, header
# row always present, create_time is a datetime string (not an epoch).
METRICS_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio"
)
METRICS_ROW = "2026-07-20 00:00:00,BTCUSDT,100.0,11300000.0,1.5,2.1,1.2,1.9"

DATASET_CSV = {
    "spot_klines": (KLINE_HEADER, KLINE_ROW.format(ts=MS, ts2=MS + 59_999)),
    "agg_trades": (AGG_HEADER, AGG_ROW.format(ts=MS)),
    "futures_metrics": (METRICS_HEADER, METRICS_ROW),
    "premium_index_klines": (
        KLINE_HEADER,
        KLINE_ROW.format(ts=MS, ts2=MS + 59_999),
    ),
}

# Datasets whose first column is an epoch (headerless detection is possible).
EPOCH_DATASETS = ["agg_trades", "premium_index_klines", "spot_klines"]


def _unit(dataset: str) -> DataUnit:
    partitions = {"symbol": "BTCUSDT"}
    if dataset in ("spot_klines", "premium_index_klines"):
        partitions["interval"] = "1m"
    return DataUnit(
        source="binance", dataset=dataset, partitions=partitions, date="2026-07-17"
    )


@pytest.mark.parametrize("dataset", EPOCH_DATASETS)
def test_parse_epoch_layouts_without_header(dataset):
    header, row = DATASET_CSV[dataset]
    payload, _ = make_csv_zip(row + "\n")
    table, warnings = parse_daily_zip(DATASET_SPECS[dataset], payload, dataset=dataset)
    spec = DATASET_SPECS[dataset]
    assert table.column_names == spec.columns
    assert table.num_rows == 1
    assert any("ms" in w for w in warnings)


def test_metrics_layout_matches_live_file():
    # create_time is a datetime string; it must stay source-native and must
    # not be epoch-validated.
    header, row = DATASET_CSV["futures_metrics"]
    payload, _ = make_csv_zip(header + "\n" + row + "\n")
    table, warnings = parse_daily_zip(
        DATASET_SPECS["futures_metrics"], payload, dataset="futures_metrics"
    )
    assert table.column_names == DATASET_SPECS["futures_metrics"].columns
    assert table.num_rows == 1
    row0 = table.to_pylist()[0]
    assert row0["create_time"] == "2026-07-20 00:00:00"
    assert row0["sum_taker_long_short_vol_ratio"] == 1.9
    assert not any("epoch" in w for w in warnings)


@pytest.mark.parametrize("dataset", sorted(DATASET_SPECS))
def test_header_row_detected(dataset):
    header, row = DATASET_CSV[dataset]
    payload, _ = make_csv_zip(header + "\n" + row + "\n")
    table, _ = parse_daily_zip(DATASET_SPECS[dataset], payload, dataset=dataset)
    assert table.num_rows == 1
    assert table.column_names == DATASET_SPECS[dataset].columns


def test_types_and_values_preserved():
    header, row = DATASET_CSV["spot_klines"]
    payload, _ = make_csv_zip(header + "\n" + row + "\n")
    table, _ = parse_daily_zip(
        DATASET_SPECS["spot_klines"], payload, dataset="spot_klines"
    )
    assert table.schema.field("open_time").type.bit_width == 64
    row0 = table.to_pylist()[0]
    assert row0["open_time"] == MS
    assert row0["close"] == 113050.0
    assert row0["count"] == 150


def test_microsecond_timestamps_accepted():
    header, row = DATASET_CSV["agg_trades"]
    payload, _ = make_csv_zip(header + "\n" + AGG_ROW.format(ts=US) + "\n")
    _, warnings = parse_daily_zip(
        DATASET_SPECS["agg_trades"], payload, dataset="agg_trades"
    )
    assert any("us" in w for w in warnings)


def test_second_magnitude_timestamps_rejected():
    header, _ = DATASET_CSV["agg_trades"]
    payload, _ = make_csv_zip(header + "\n" + AGG_ROW.format(ts=SECONDS) + "\n")
    with pytest.raises(MalformedError, match="magnitude"):
        parse_daily_zip(DATASET_SPECS["agg_trades"], payload, dataset="agg_trades")


def test_unexpected_column_count_rejected():
    payload, _ = make_csv_zip(KLINE_HEADER + ",extra\n" + KLINE_ROW.format(ts=MS, ts2=MS) + ",x\n")
    with pytest.raises(MalformedError, match="columns"):
        parse_daily_zip(DATASET_SPECS["spot_klines"], payload, dataset="spot_klines")


def test_ragged_rows_rejected():
    payload, _ = make_csv_zip(f"{MS},1,2,3\n")  # 4 fields instead of 12
    with pytest.raises(MalformedError):
        parse_daily_zip(DATASET_SPECS["spot_klines"], payload, dataset="spot_klines")


def test_multiple_members_rejected():
    from conftest import make_zip

    payload = make_zip({"a.csv": b"1,2\n", "b.csv": b"3,4\n"})
    with pytest.raises(MalformedError, match="exactly 1"):
        parse_daily_zip(DATASET_SPECS["spot_klines"], payload, dataset="spot_klines")


def _connector(handler) -> BinanceVisionConnector:
    client = HttpClient(
        "https://data.binance.vision",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    datasets = {
        "spot_klines": BinanceDataset(interval="1m", tickers={"BTC": "BTCUSDT"})
    }
    return BinanceVisionConnector(datasets, client=client)


def test_fetch_verifies_checksum():
    header, row = DATASET_CSV["spot_klines"]
    payload, sha = make_csv_zip(header + "\n" + row + "\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{sha}  BTCUSDT-1m-2026-07-17.zip\n")
        return httpx.Response(200, content=payload)

    connector = _connector(handler)
    unit = _unit("spot_klines")
    fetched = connector.fetch(unit)
    assert fetched.source_sha256 == sha
    parsed = connector.parse(unit, fetched)
    table = parsed.tables["data.parquet"]
    assert table.num_rows == 1
    for col in ("_source", "_source_key", "_source_sha256", "_ingested_at"):
        assert col in table.column_names
    assert table.column("_source")[0].as_py() == "binance"
    assert "data/spot/daily/klines/BTCUSDT/1m/" in fetched.source_key
    assert parsed.min_ts == str(MS)


def test_checksum_mismatch_rejected():
    header, row = DATASET_CSV["spot_klines"]
    payload, _ = make_csv_zip(header + "\n" + row + "\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text="0" * 64 + "  file.zip\n")
        return httpx.Response(200, content=payload)

    with pytest.raises(MalformedError, match="checksum mismatch"):
        _connector(handler).fetch(_unit("spot_klines"))


def test_404_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(UnavailableError):
        _connector(handler).fetch(_unit("spot_klines"))


def test_plan_respects_entity_and_dates():
    datasets = {
        "spot_klines": BinanceDataset(
            interval="1m", tickers={"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
        )
    }
    connector = BinanceVisionConnector(datasets, client=_connector(lambda r: None)._client)
    from datetime import date

    units = connector.plan(start=date(2026, 7, 1), end=date(2026, 7, 3))
    assert len(units) == 2 * 3
    units = connector.plan(entity="ETHUSDT", start=date(2026, 7, 1), end=date(2026, 7, 1))
    assert [u.partitions["symbol"] for u in units] == ["ETHUSDT"]
