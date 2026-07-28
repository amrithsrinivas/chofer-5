from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

from assembler.source import LakeDataSource, PointInTimeContext, Selector
from .conftest import write_unit

UTC = timezone.utc


def test_no_future_event_or_null_observation_passes(tmp_path: Path) -> None:
    data_dir = tmp_path / "lake"
    decision = datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
    slug = "btc-updown-5m-1767225600"
    table = pa.table(
        {
            "timestamp": pa.array(
                [decision.replace(tzinfo=None) - timedelta(seconds=2), decision.replace(tzinfo=None) + timedelta(seconds=1), decision.replace(tzinfo=None) - timedelta(seconds=3), decision.replace(tzinfo=None) - timedelta(seconds=4)],
                pa.timestamp("us"),
            ),
            "local_timestamp": pa.array(
                [decision.replace(tzinfo=None) - timedelta(seconds=1), decision.replace(tzinfo=None) - timedelta(seconds=5), None, decision.replace(tzinfo=None) - timedelta(seconds=8)],
                pa.timestamp("us"),
            ),
            "event_type": ["book", "price_change", "price_change", "price_change"],
            "winning_outcome": ["Up", "Up", "Up", "Up"],
        }
    )
    write_unit(
        data_dir,
        source="pmdata",
        dataset="poly_l2",
        partitions={"series": "btc-5m"},
        day="2026-01-01",
        tables={f"{slug}.parquet": table},
    )
    selection = LakeDataSource(data_dir).select(
        Selector("BTC", "poly_l2", slug),
        decision_at=decision,
        partition_date="2026-01-01",
    )
    rows = selection.table.to_pylist()
    assert len(rows) == 2
    assert all(row["_event_time"] <= decision for row in rows)
    assert all(row["_available_at"] <= decision for row in rows)
    assert rows[-1]["_available_at"] == rows[-1]["_event_time"]  # negative latency clamped
    assert "winning_outcome" not in selection.table.column_names


def test_cross_asset_selector_uses_requested_asset(tmp_path: Path) -> None:
    data_dir = tmp_path / "lake"
    decision = datetime(2026, 1, 1, 0, 7, tzinfo=UTC)
    close_times = [int(datetime(2026, 1, 1, 0, minute, tzinfo=UTC).timestamp() * 1_000_000) for minute in (0, 5)]
    for asset, prices in (("BTC", [100.0, 101.0]), ("ETH", [10.0, 12.0])):
        table = pa.table({"close_time": close_times, "close": prices})
        write_unit(
            data_dir,
            source="binance",
            dataset="spot_klines",
            partitions={"symbol": f"{asset}USDT", "interval": "1m"},
            day="2026-01-01",
            tables={"data.parquet": table},
        )
    source = LakeDataSource(data_dir, binance_delay_seconds=1)
    context = PointInTimeContext(source, decision, "BTC", "btc-updown-5m-1767225600", "2026-01-01")
    selected = context.select("spot_klines", asset="ETH", lookback_seconds=420)
    assert selected.selector.asset == "ETH"
    assert selected.table.column("close").to_pylist() == [10.0, 12.0]
