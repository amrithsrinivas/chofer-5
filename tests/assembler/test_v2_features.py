from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

from assembler.book import BOOK_ASSUMPTION_VERSION, BookSnapshot
from assembler.features import builtin_registry
from assembler.source import LakeDataSource, PointInTimeContext

from .conftest import write_unit

UTC = timezone.utc


def _context(
    data_dir: Path,
    decision: datetime,
    *,
    slug: str = "btc-updown-5m-1767225600",
) -> PointInTimeContext:
    return PointInTimeContext(
        LakeDataSource(data_dir, binance_delay_seconds=0.25),
        decision,
        "BTC",
        slug,
        decision.date().isoformat(),
    )


def test_partition_indexes_release_prior_dates(tmp_path: Path) -> None:
    source = LakeDataSource(tmp_path)
    source._book_bar_index[("BTC", "2026-01-01")] = {"old": []}
    source._book_bar_index[("BTC", "2026-01-02")] = {"btc": []}
    source._book_bar_index[("ETH", "2026-01-02")] = {"eth": []}

    source._retain_partition_date(source._book_bar_index, "2026-01-02")

    assert set(source._book_bar_index) == {
        ("BTC", "2026-01-02"),
        ("ETH", "2026-01-02"),
    }


def test_static_book_features_include_depth_and_executable_vwap(
    tmp_path: Path,
) -> None:
    decision = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    context = _context(tmp_path, decision)
    context.memo["decision_book"] = BookSnapshot(
        valid=True,
        reason=None,
        bids=((490_000, 10.0), (480_000, 20.0)),
        asks=((510_000, 5.0), (520_000, 20.0)),
        event_time=decision - timedelta(milliseconds=10),
        available_at=decision - timedelta(milliseconds=5),
        row_ordinal=4,
    )
    registry = builtin_registry()

    top1 = registry.get("book_imbalance_top1").compute(context, {})
    top10 = registry.get("book_imbalance_top10").compute(context, {})
    micro_delta = registry.get("book_microprice_minus_midpoint").compute(
        context, {}
    )
    up_vwap = registry.get("up_ask_vwap_10usd").compute(context, {})
    down_vwap = registry.get("down_ask_vwap_10usd").compute(context, {})

    assert math.isclose(top1.value, 1 / 3)
    assert math.isclose(top10.value, 5 / 55)
    assert micro_delta.value > 0
    assert 0.51 < up_vwap.value < 0.52
    assert 0.51 < down_vwap.value < 0.52
    assert up_vwap.available_at is not None
    assert up_vwap.available_at <= decision


def test_canonical_book_history_excludes_future_states(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "lake"
    canonical = data_dir / "derived" / "canonical"
    decision = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    slug = "btc-updown-5m-1767225600"
    times = [
        decision - timedelta(seconds=5),
        decision,
        decision + timedelta(seconds=1),
    ]
    table = pa.table(
        {
            "slug": [slug] * 3,
            "checkpoint_at": pa.array(times, pa.timestamp("us", tz="UTC")),
            "valid": [True] * 3,
            "reason": pa.array([None] * 3, pa.string()),
            "bid_prices_micros": [[490_000], [500_000], [900_000]],
            "bid_sizes": [[10.0], [10.0], [10.0]],
            "ask_prices_micros": [[510_000], [520_000], [910_000]],
            "ask_sizes": [[10.0], [10.0], [10.0]],
            "last_event_time": pa.array(times, pa.timestamp("us", tz="UTC")),
            "max_available_at": pa.array(times, pa.timestamp("us", tz="UTC")),
            "source_row_ordinal": [1, 2, 3],
        }
    )
    manifest_path = write_unit(
        canonical,
        source="canonical",
        dataset="market_inputs",
        partitions={"asset": "BTC", "interval": "5m"},
        day="2026-01-01",
        tables={"book_bars_1s.parquet": table},
    )
    manifest = json.loads(manifest_path.read_text())
    manifest["parameters"] = {
        "depth_levels": 20,
        "book_assumption_version": BOOK_ASSUMPTION_VERSION,
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))

    context = _context(data_dir, decision, slug=slug)
    history = context.book_history(
        lookback_seconds=5,
        required_depth_levels=20,
    )
    assert history.table.num_rows == 2
    assert max(history.table["_available_at"].to_pylist()) <= decision

    result = builtin_registry().get("book_midprice_change_5s").compute(
        context, {}
    )
    assert math.isclose(result.value, 0.01)
    assert result.available_at is not None and result.available_at <= decision


def test_aggregate_trade_features_use_only_available_rows(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "lake"
    canonical = data_dir / "derived" / "canonical"
    decision = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    events = [
        decision - timedelta(seconds=31),
        decision - timedelta(seconds=1),
        decision - timedelta(milliseconds=500),
    ]
    table = pa.table(
        {
            "last_event_time": pa.array(
                events, pa.timestamp("us", tz="UTC")
            ),
            "base_available_at": pa.array(
                [
                    events[0],
                    events[1],
                    decision + timedelta(seconds=1),
                ],
                pa.timestamp("us", tz="UTC"),
            ),
            "last_price": [100.0, 101.0, 500.0],
            "signed_quote_flow": [0.0, 50.0, 1_000.0],
            "total_quote_volume": [10.0, 100.0, 1_000.0],
            "total_trade_count": [1, 4, 100],
            "high_price": [100.0, 101.0, 500.0],
            "low_price": [100.0, 101.0, 500.0],
        }
    )
    write_unit(
        canonical,
        source="canonical",
        dataset="underlying_inputs",
        partitions={"asset": "BTC"},
        day="2026-01-01",
        tables={"agg_trade_bars_1s.parquet": table},
    )
    context = _context(data_dir, decision)
    registry = builtin_registry()
    return_30s = registry.get("btc_agg_trade_return_30s").compute(
        context, {}
    )
    imbalance_30s = registry.get(
        "btc_agg_trade_flow_imbalance_30s"
    ).compute(context, {})

    assert math.isclose(return_30s.value, 0.01)
    assert math.isclose(imbalance_30s.value, 0.5)
    assert return_30s.available_at is not None
    assert return_30s.available_at <= decision
