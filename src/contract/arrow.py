"""Arrow storage schemas for the canonical contracts used by assembly."""

from __future__ import annotations

import pyarrow as pa

UTC_US = pa.timestamp("us", tz="UTC")
PRICE_MICROS = pa.int32()


def _schema(fields: list[pa.Field], name: str, version: str = "1") -> pa.Schema:
    return pa.schema(
        fields,
        metadata={b"contract": name.encode(), b"schema_version": version.encode()},
    )


MARKET_WINDOW_SCHEMA = _schema(
    [
        pa.field("window_id", pa.string(), nullable=False),
        pa.field("asset", pa.string(), nullable=False),
        pa.field("interval", pa.string(), nullable=False),
        pa.field("series_id", pa.string(), nullable=False),
        pa.field("market_id", pa.string(), nullable=False),
        pa.field("condition_id", pa.string(), nullable=False),
        pa.field("up_token_id", pa.string(), nullable=False),
        pa.field("down_token_id", pa.string(), nullable=False),
        pa.field("window_start", UTC_US, nullable=False),
        pa.field("window_end", UTC_US, nullable=False),
    ],
    "market_window",
)

MARKET_RESOLUTION_SCHEMA = _schema(
    [
        pa.field("window_id", pa.string(), nullable=False),
        pa.field("market_id", pa.string(), nullable=False),
        pa.field("outcome", pa.string(), nullable=False),
        pa.field("label", pa.int8(), nullable=False),
        pa.field("basis", pa.string(), nullable=False),
        pa.field("confidence", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("resolved_at", UTC_US),
        pa.field("resolution_available_at", UTC_US),
        pa.field("evidence_sha256", pa.string()),
    ],
    "market_resolution",
)

DECISION_ROW_SCHEMA = _schema(
    [
        pa.field("window_id", pa.string(), nullable=False),
        pa.field("schedule_id", pa.string(), nullable=False),
        pa.field("decision_at", UTC_US, nullable=False),
        pa.field("eligibility", pa.string(), nullable=False),
        pa.field("eligibility_reasons", pa.list_(pa.string()), nullable=False),
        pa.field("up_bid_micros", PRICE_MICROS),
        pa.field("up_ask_micros", PRICE_MICROS),
        pa.field("down_bid_micros", PRICE_MICROS),
        pa.field("down_ask_micros", PRICE_MICROS),
    ],
    "decision_row",
)

EVENT_EVIDENCE_SCHEMA = _schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("event_time", UTC_US, nullable=False),
        pa.field("source_publish_time", UTC_US),
        pa.field("observed_at", UTC_US),
        pa.field("available_at", UTC_US),
        pa.field("ingested_at", UTC_US, nullable=False),
        pa.field("availability_policy_id", pa.string(), nullable=False),
        pa.field("availability_policy_version", pa.string(), nullable=False),
        pa.field("availability_basis", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("lineage_sha256", pa.string()),
    ],
    "event_evidence",
)

FEATURE_VALUE_SCHEMA = _schema(
    [
        pa.field("window_id", pa.string(), nullable=False),
        pa.field("schedule_id", pa.string(), nullable=False),
        pa.field("feature_name", pa.string(), nullable=False),
        pa.field("feature_version", pa.string(), nullable=False),
        pa.field("decision_at", UTC_US, nullable=False),
        pa.field("value", pa.float64()),
        pa.field("null_reason", pa.string()),
        pa.field("definition_sha256", pa.string(), nullable=False),
        pa.field("implementation_sha256", pa.string(), nullable=False),
        pa.field("latest_input_event_time", UTC_US),
        pa.field("max_input_available_at", UTC_US),
        pa.field("computed_at", UTC_US, nullable=False),
    ],
    "feature_value",
)
