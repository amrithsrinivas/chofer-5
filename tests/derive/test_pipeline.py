from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from assembler.builder import DatasetAssembler
from assembler.spec import DatasetSpec
from derive.pipeline import CanonicalDeriver
from derive.spec import load_derive_spec
from tests.assembler.conftest import gamma_table, write_unit

UTC = timezone.utc


def test_checked_derive_spec_loads() -> None:
    spec = load_derive_spec("configs/derive/market-inputs-v1.toml")
    assert -30_000 in spec.checkpoint_offsets_ms
    assert -29_750 in spec.checkpoint_offsets_ms
    assert spec.depth_levels == 20


def test_market_partition_is_atomic_resumable_and_consumed_by_assembler(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "lake"
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    slug = f"btc-updown-5m-{int(start.timestamp())}"
    write_unit(
        data_dir,
        source="polymarket",
        dataset="market_metadata",
        partitions={"series_id": "10684"},
        day="2026-01-02",
        tables={"data.parquet": gamma_table([start], ["Up"])},
    )
    l2 = pa.table(
        {
            "timestamp": pa.array(
                [
                    (start - timedelta(seconds=31)).replace(tzinfo=None),
                    (start - timedelta(seconds=29, milliseconds=900)).replace(
                        tzinfo=None
                    ),
                ],
                pa.timestamp("us"),
            ),
            "local_timestamp": pa.array(
                [
                    (start - timedelta(seconds=30, milliseconds=900)).replace(
                        tzinfo=None
                    ),
                    (start - timedelta(seconds=29, milliseconds=800)).replace(
                        tzinfo=None
                    ),
                ],
                pa.timestamp("us"),
            ),
            "event_type": ["book", "price_change"],
            "bid_prices": [[0.49], None],
            "bid_sizes": [[10.0], None],
            "ask_prices": [[0.51], None],
            "ask_sizes": [[10.0], None],
            "pc_price": [None, 0.49],
            "pc_size": [None, 12.0],
            "pc_side": [None, "BUY"],
            "winning_outcome": [None, None],
        }
    )
    write_unit(
        data_dir,
        source="pmdata",
        dataset="poly_l2",
        partitions={"series": "btc-5m"},
        day="2026-01-01",
        tables={f"{slug}.parquet": l2},
    )
    first = CanonicalDeriver(data_dir).derive_market("BTC", "2026-01-01")
    second = CanonicalDeriver(data_dir).derive_market("BTC", "2026-01-01")
    assert first.status == "complete"
    assert second.skipped is True
    canonical = data_dir / "derived" / "canonical"
    manifests = list((canonical / "manifests").rglob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["metrics"]["markets"] == 1
    assert manifest["inputs"]

    checkpoints = pq.read_table(
        canonical
        / "raw"
        / "canonical"
        / "market_inputs"
        / "asset=BTC"
        / "interval=5m"
        / "date=2026-01-01"
        / "book_checkpoints.parquet"
    ).to_pylist()
    at_decision = [
        row for row in checkpoints if row["checkpoint_offset_ms"] == -30_000
    ][0]
    at_execution = [
        row for row in checkpoints if row["checkpoint_offset_ms"] == -29_750
    ][0]
    assert at_decision["bid_sizes"] == [10.0]
    assert at_execution["bid_sizes"] == [12.0]
    one_second = pq.read_table(
        canonical
        / "raw"
        / "canonical"
        / "market_inputs"
        / "asset=BTC"
        / "interval=5m"
        / "date=2026-01-01"
        / "book_bars_1s.parquet"
    )
    assert one_second.num_rows == 301

    spec = DatasetSpec.model_validate(
        {
            "spec_version": "1",
            "assets": ["BTC"],
            "time_range": {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:05:00Z",
            },
            "decision_schedules": [
                {
                    "name": "premarket",
                    "anchor": "window_start",
                    "offset": "-30s",
                }
            ],
            "features": ["up_midprice"],
            "availability_policies": {
                "pmdata": "pmdata_local_timestamp_utc_clamped_v1"
            },
            "quote": {
                "max_staleness_seconds": 5,
                "band_min": 0.45,
                "band_max": 0.55,
            },
            "sources": {"poly_l2": "required"},
            "execution_delay": "250ms",
            "output_path": str(tmp_path / "declared"),
        }
    )
    result = DatasetAssembler(
        spec, data_dir=data_dir, output_dir=tmp_path / "dataset"
    ).build()
    row = pq.read_table(result.path / "rows.parquet").to_pylist()[0]
    assert row["decision_up_bid"] == 0.49
    assert row["execution_up_bid"] == 0.49
    dataset_manifest = json.loads((result.path / "manifest.json").read_text())
    assert any(
        item["source"] == "canonical" for item in dataset_manifest["inputs"]
    )


def test_underlying_partition_materializes_flow_and_conservative_metrics(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "lake"
    day = "2026-01-01"
    at = datetime(2026, 1, 1, tzinfo=UTC)
    close_us = int((at + timedelta(minutes=1)).timestamp() * 1_000_000)
    write_unit(
        data_dir,
        source="binance",
        dataset="spot_klines",
        partitions={"symbol": "BTCUSDT", "interval": "1m"},
        day=day,
        tables={
            "data.parquet": pa.table(
                {
                    "open_time": [int(at.timestamp() * 1_000_000)],
                    "close_time": [close_us],
                    "open": [100.0],
                    "high": [102.0],
                    "low": [99.0],
                    "close": [101.0],
                    "volume": [2.0],
                    "quote_volume": [201.0],
                    "count": [2],
                    "taker_buy_volume": [1.0],
                    "taker_buy_quote_volume": [101.0],
                }
            )
        },
    )
    for dataset in ("premium_index_klines",):
        write_unit(
            data_dir,
            source="binance",
            dataset=dataset,
            partitions={"symbol": "BTCUSDT", "interval": "1m"},
            day=day,
            tables={
                "data.parquet": pa.table(
                    {
                        "open_time": [int(at.timestamp() * 1000)],
                        "close_time": [
                            int((at + timedelta(minutes=1)).timestamp() * 1000)
                        ],
                        "open": [0.001],
                        "high": [0.002],
                        "low": [0.0],
                        "close": [0.001],
                        "volume": [0.0],
                        "quote_volume": [0.0],
                        "count": [0],
                        "taker_buy_volume": [0.0],
                        "taker_buy_quote_volume": [0.0],
                    }
                )
            },
        )
    write_unit(
        data_dir,
        source="binance",
        dataset="agg_trades",
        partitions={"symbol": "BTCUSDT"},
        day=day,
        tables={
            "data.parquet": pa.table(
                {
                    "agg_trade_id": [1, 2],
                    "price": [100.0, 101.0],
                    "quantity": [2.0, 1.0],
                    "transact_time": [
                        int((at + timedelta(seconds=1)).timestamp() * 1_000_000),
                        int(
                            (
                                at + timedelta(seconds=1, milliseconds=500)
                            ).timestamp()
                            * 1_000_000
                        ),
                    ],
                    "is_buyer_maker": [False, True],
                }
            )
        },
    )
    write_unit(
        data_dir,
        source="binance",
        dataset="futures_metrics",
        partitions={"symbol": "BTCUSDT"},
        day=day,
        tables={
            "data.parquet": pa.table(
                {
                    "create_time": ["2026-01-01 00:00:00"],
                    "sum_open_interest": [10.0],
                    "sum_open_interest_value": [1000.0],
                    "count_toptrader_long_short_ratio": [1.0],
                    "sum_toptrader_long_short_ratio": [1.0],
                    "count_long_short_ratio": [1.0],
                    "sum_taker_long_short_vol_ratio": [1.0],
                }
            )
        },
    )
    result = CanonicalDeriver(data_dir).derive_underlying("BTC", day)
    assert result.status == "complete"
    unit = (
        data_dir
        / "derived"
        / "canonical"
        / "raw"
        / "canonical"
        / "underlying_inputs"
        / "asset=BTC"
        / "date=2026-01-01"
    )
    agg = pq.read_table(unit / "agg_trade_bars_1s.parquet").to_pylist()[0]
    assert agg["buy_quote_volume"] == 200.0
    assert agg["sell_quote_volume"] == 101.0
    assert agg["signed_quote_flow"] == 99.0
    metric = pq.read_table(unit / "futures_metrics.parquet").to_pylist()[0]
    assert metric["base_available_at"] == at + timedelta(minutes=5)
