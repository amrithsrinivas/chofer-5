"""UTC parsing, canonical serialization, durations, and exact epoch helpers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DURATION_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)(us|ms|s|m|h)$")


class EpochUnit(str, Enum):
    SECONDS = "s"
    MILLISECONDS = "ms"
    MICROSECONDS = "us"
    NANOSECONDS = "ns"

    @property
    def nanoseconds(self) -> int:
        return {
            EpochUnit.SECONDS: 1_000_000_000,
            EpochUnit.MILLISECONDS: 1_000_000,
            EpochUnit.MICROSECONDS: 1_000,
            EpochUnit.NANOSECONDS: 1,
        }[self]


def parse_utc(value: datetime | str) -> datetime:
    """Parse an aware timestamp and normalize it to `timezone.utc`."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be an ISO-8601 string or datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Serialize an aware timestamp at fixed microsecond precision with `Z`."""
    normalized = parse_utc(value)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_duration(value: timedelta | str) -> timedelta:
    """Parse a signed duration such as `-30s`, `250ms`, `5m`, or `1h`."""
    if isinstance(value, timedelta):
        return value
    if not isinstance(value, str):
        raise TypeError("duration must be a timedelta or signed unit string")
    match = _DURATION_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError("duration must use us, ms, s, m, or h (for example '-30s')")
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise ValueError(f"invalid duration: {value!r}") from exc
    unit_micros = {"us": 1, "ms": 1_000, "s": 1_000_000, "m": 60_000_000, "h": 3_600_000_000}
    micros = amount * unit_micros[match.group(2)]
    if not micros.is_finite() or micros != micros.to_integral_value():
        raise ValueError("duration cannot be represented at microsecond precision")
    return timedelta(microseconds=int(micros))


def duration_microseconds(value: timedelta) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


def datetime_from_epoch(value: int, unit: EpochUnit | str) -> datetime:
    """Convert an integer epoch value without binary floating-point arithmetic."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("epoch value must be an integer")
    unit = EpochUnit(unit)
    nanoseconds = value * unit.nanoseconds
    if nanoseconds % 1_000:
        raise ValueError("epoch value has sub-microsecond precision")
    return _EPOCH + timedelta(microseconds=nanoseconds // 1_000)


def datetime_to_epoch(value: datetime, unit: EpochUnit | str) -> int:
    """Convert UTC-normalized time to an exact integer epoch unit."""
    unit = EpochUnit(unit)
    micros = duration_microseconds(parse_utc(value) - _EPOCH)
    unit_micros = unit.nanoseconds // 1_000
    if unit is EpochUnit.NANOSECONDS:
        return micros * 1_000
    if micros % unit_micros:
        raise ValueError(f"timestamp is not exactly representable in epoch {unit.value}")
    return micros // unit_micros


UtcDatetime = Annotated[
    datetime,
    BeforeValidator(parse_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]

SignedDuration = Annotated[
    timedelta,
    BeforeValidator(parse_duration),
    PlainSerializer(duration_microseconds, return_type=int, when_used="json"),
]
