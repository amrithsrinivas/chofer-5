from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contract import (
    EpochUnit,
    datetime_from_epoch,
    datetime_to_epoch,
    format_utc,
    parse_duration,
    parse_utc,
)


def test_utc_parse_normalizes_offsets_and_serializes_fixed_precision() -> None:
    parsed = parse_utc("2026-07-18T02:30:00.123456+02:30")
    assert parsed == datetime(2026, 7, 18, 0, 0, 0, 123456, tzinfo=timezone.utc)
    assert format_utc(parsed) == "2026-07-18T00:00:00.123456Z"


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        parse_utc(datetime(2026, 7, 18))


def test_epoch_helpers_are_exact_across_units() -> None:
    timestamp = parse_utc("2026-07-18T00:00:00.123000Z")
    millis = 1_784_332_800_123
    assert datetime_from_epoch(millis, EpochUnit.MILLISECONDS) == timestamp
    assert datetime_to_epoch(timestamp, "ms") == millis
    assert datetime_to_epoch(timestamp, "us") == millis * 1_000
    assert datetime_to_epoch(timestamp, "ns") == millis * 1_000_000

    with pytest.raises(ValueError, match="not exactly representable"):
        datetime_to_epoch(timestamp, "s")
    with pytest.raises(ValueError, match="sub-microsecond"):
        datetime_from_epoch(1, "ns")


def test_signed_duration_parser_preserves_anchor_direction() -> None:
    assert parse_duration("-30s").total_seconds() == -30
    assert parse_duration("+1.5s").total_seconds() == 1.5
    with pytest.raises(ValueError, match="microsecond precision"):
        parse_duration("0.0000001s")
