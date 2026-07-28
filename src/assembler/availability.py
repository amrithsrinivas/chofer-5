"""Timestamp normalization and modeled availability policies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
PMDATA_POLICY_V1 = "pmdata_local_timestamp_utc_clamped_v1"
BINANCE_POLICY_V1 = "modeled_live_research_v1"
FUTURES_METRIC_INTERVAL_SECONDS = 300


def attach_utc(value: Any) -> datetime | None:
    """Attach UTC to provider-naive timestamps; preserve aware instants."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def pmdata_times(event_value: Any, local_value: Any) -> tuple[datetime, datetime] | None:
    """Return (event_time, available_at), failing closed on either NULL.

    PMData's timezone-naive values represent UTC. Negative measured transport
    delays are conservatively clamped so availability can never precede the
    source event.
    """
    event_time = attach_utc(event_value)
    observed_at = attach_utc(local_value)
    if event_time is None or observed_at is None:
        return None
    return event_time, max(event_time, observed_at)


def epoch_to_utc(value: Any) -> datetime:
    """Decode seconds/ms/us/ns by magnitude rather than a dataset-wide guess."""
    if value is None or isinstance(value, bool):
        raise ValueError("epoch timestamp is null or invalid")
    raw = int(value)
    magnitude = abs(raw)
    if 10**8 <= magnitude < 10**11:
        divisor = 1
    elif 10**11 <= magnitude < 10**14:
        divisor = 1_000
    elif 10**14 <= magnitude < 10**17:
        divisor = 1_000_000
    elif 10**17 <= magnitude < 10**20:
        divisor = 1_000_000_000
    else:
        raise ValueError(f"unexpected epoch magnitude {magnitude}")
    return datetime.fromtimestamp(raw / divisor, tz=UTC)


def binance_times(event_time_value: Any, delay_seconds: float) -> tuple[datetime, datetime]:
    """Normalize an epoch-valued Binance event and apply modeled live delay."""
    event_time = epoch_to_utc(event_time_value)
    return event_time, event_time + timedelta(seconds=delay_seconds)


def binance_row_times(
    dataset: str, row: dict[str, Any], delay_seconds: float
) -> tuple[datetime, datetime]:
    """Return causal event/availability times for supported Binance datasets.

    Klines become usable only at ``close_time``. Aggregate trades use their
    transaction time. Futures metrics are held back for one complete five-
    minute interval after provider ``create_time``. This conservative rule
    remains safe across the observed start-stamped/end-stamped regime change.
    """
    if dataset in {"spot_klines", "premium_index_klines"}:
        return binance_times(row.get("close_time"), delay_seconds)
    if dataset == "agg_trades":
        return binance_times(row.get("transact_time"), delay_seconds)
    if dataset == "futures_metrics":
        event_time = attach_utc(row.get("create_time"))
        if event_time is None:
            raise ValueError("futures metric create_time is null")
        return event_time, event_time + timedelta(
            seconds=FUTURES_METRIC_INTERVAL_SECONDS + delay_seconds
        )
    raise ValueError(f"unsupported Binance dataset {dataset!r}")
