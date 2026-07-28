from __future__ import annotations

import pyarrow as pa

from contract import (
    DECISION_ROW_SCHEMA,
    EVENT_EVIDENCE_SCHEMA,
    MARKET_RESOLUTION_SCHEMA,
    MARKET_WINDOW_SCHEMA,
)


def test_arrow_schemas_use_utc_microseconds_and_integer_price_micros() -> None:
    assert MARKET_WINDOW_SCHEMA.field("window_start").type == pa.timestamp("us", tz="UTC")
    assert MARKET_RESOLUTION_SCHEMA.field("resolution_available_at").nullable
    assert EVENT_EVIDENCE_SCHEMA.field("available_at").nullable
    assert DECISION_ROW_SCHEMA.field("up_ask_micros").type == pa.int32()
    assert DECISION_ROW_SCHEMA.metadata[b"contract"] == b"decision_row"
