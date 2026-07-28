from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

from assembler.features import builtin_registry
from assembler.source import LakeDataSource, PointInTimeContext
from .conftest import write_unit

UTC = timezone.utc


def test_signed_up_pressure_uses_outcome_and_side(tmp_path: Path) -> None:
    data_dir = tmp_path / "lake"
    decision = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    slug = "btc-updown-5m-1767225600"
    count = 5
    table = pa.table(
        {
            "timestamp": pa.array([decision.replace(tzinfo=None) - timedelta(seconds=count - index) for index in range(count)], pa.timestamp("us")),
            "local_timestamp": pa.array([decision.replace(tzinfo=None) - timedelta(seconds=count - index, milliseconds=-10) for index in range(count)], pa.timestamp("us")),
            "event_type": ["last_trade_price"] * count,
            "outcome": ["yes", "yes", "no", "no", "Up"],
            "side": ["BUY", "SELL", "BUY", "SELL", "SELL"],
            "size": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    write_unit(
        data_dir,
        source="pmdata",
        dataset="poly_trade",
        partitions={"series": "btc-5m"},
        day="2026-01-01",
        tables={f"{slug}.parquet": table},
    )
    context = PointInTimeContext(
        LakeDataSource(data_dir), decision, "BTC", slug, "2026-01-01"
    )
    result = builtin_registry().get("trade_pressure_60s").compute(context, {})
    # yes BUY +1, yes SELL -2, no BUY -3, no SELL +4, Up SELL -5.
    assert result.value == -5.0
    assert result.available_at is not None and result.available_at <= decision
