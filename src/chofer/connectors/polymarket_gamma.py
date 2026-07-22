"""Polymarket gamma connector: daily versioned snapshots of market metadata
per configured series id (condition id, clob token ids, outcomes, window
start/end, resolution source text, slug, status).

Gamma returns some array fields (outcomes, clobTokenIds) as stringified JSON;
we keep the raw string plus a parsed element count.

Pagination notes (verified against the live API):
- The server caps page size at 100 regardless of the requested limit.
- Offset pagination is capped at offset=2000 (larger offsets return HTTP
  422), so deep history cannot be fetched by offset alone.
- start_date_min/start_date_max window filtering works; we therefore page
  through UTC day windows ([min, max), ascending) from the cutoff date
  through tomorrow. Tomorrow is included because recurrent series pre-create
  upcoming events ~1 day ahead.
- Events are deduped by id across pages/windows (boundaries may overlap) and
  against the events embedded in /series/{id}.

Snapshots are bounded by a cutoff date (default: 14 days back from today,
UTC; override via plan(start=...) / CLI --from). Rows are kept when the
event's startDate is on/after the cutoff OR the market is not closed, so
active/upcoming markets are always included.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pyarrow as pa

from ..http import HttpClient, MalformedError
from ..units import DataUnit
from .base import Connector, FetchedUnit, ParsedUnit, with_provenance

log = logging.getLogger(__name__)

BASE_URL = "https://gamma-api.polymarket.com"
DATASET = "market_metadata"
PAGE_LIMIT = 100  # server-side cap; larger requests are truncated to 100
DEFAULT_LOOKBACK_DAYS = 14

SCHEMA = pa.schema(
    [
        ("series_id", pa.string()),
        ("event_id", pa.string()),
        ("event_slug", pa.string()),
        ("event_title", pa.string()),
        ("event_description", pa.string()),
        ("event_start_date", pa.string()),
        ("event_end_date", pa.string()),
        ("market_id", pa.string()),
        ("condition_id", pa.string()),
        ("slug", pa.string()),
        ("question", pa.string()),
        ("outcomes_raw", pa.string()),
        ("outcomes_count", pa.int64()),
        ("clob_token_ids_raw", pa.string()),
        ("clob_token_ids_count", pa.int64()),
        ("start_date", pa.string()),
        ("end_date", pa.string()),
        ("closed", pa.bool_()),
        ("active", pa.bool_()),
        ("archived", pa.bool_()),
        ("uma_resolution_status", pa.string()),
        ("resolution_source", pa.string()),
    ]
)


def _raw_and_count(value: Any, *, field_name: str, market: str) -> tuple[str | None, int | None]:
    """Gamma array fields may arrive as real arrays or stringified JSON."""
    if value is None:
        return None, None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MalformedError(
                f"market {market!r}: field {field_name} is not valid JSON: {value!r}"
            ) from exc
        if not isinstance(parsed, list):
            raise MalformedError(
                f"market {market!r}: field {field_name} parsed to "
                f"{type(parsed).__name__}, expected array"
            )
        return value, len(parsed)
    if isinstance(value, list):
        return json.dumps(value, separators=(",", ":")), len(value)
    raise MalformedError(
        f"market {market!r}: field {field_name} has unexpected type "
        f"{type(value).__name__}"
    )


def _keep_event(event: dict, cutoff_iso: str) -> bool:
    """Keep events at/after the cutoff, plus anything with an open market."""
    start = event.get("startDate")
    if start is not None and str(start) >= cutoff_iso:
        return True
    for market in event.get("markets") or []:
        if isinstance(market, dict) and market.get("closed") is False:
            return True
    return start is None


def events_to_table(series_id: int, events: list[dict], cutoff: date) -> pa.Table:
    """Flatten series events x markets into one metadata table.

    Events older than `cutoff` are dropped unless they contain a market that
    is not closed (active/upcoming markets are always kept).
    """
    cutoff_iso = cutoff.isoformat()
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            raise MalformedError("gamma events payload contains a non-object entry")
        if not _keep_event(event, cutoff_iso):
            continue
        markets = event.get("markets") or []
        for market in markets:
            slug = market.get("slug")
            outcomes_raw, outcomes_count = _raw_and_count(
                market.get("outcomes"), field_name="outcomes", market=str(slug)
            )
            tokens_raw, tokens_count = _raw_and_count(
                market.get("clobTokenIds"), field_name="clobTokenIds", market=str(slug)
            )
            rows.append(
                {
                    "series_id": str(series_id),
                    "event_id": _s(event.get("id")),
                    "event_slug": _s(event.get("slug")),
                    "event_title": _s(event.get("title")),
                    "event_description": _s(event.get("description")),
                    "event_start_date": _s(event.get("startDate")),
                    "event_end_date": _s(event.get("endDate")),
                    "market_id": _s(market.get("id")),
                    "condition_id": _s(market.get("conditionId")),
                    "slug": _s(slug),
                    "question": _s(market.get("question")),
                    "outcomes_raw": outcomes_raw,
                    "outcomes_count": outcomes_count,
                    "clob_token_ids_raw": tokens_raw,
                    "clob_token_ids_count": tokens_count,
                    "start_date": _s(market.get("startDate")),
                    "end_date": _s(market.get("endDate")),
                    "closed": _b(market.get("closed")),
                    "active": _b(market.get("active")),
                    "archived": _b(market.get("archived")),
                    "uma_resolution_status": _s(market.get("umaResolutionStatus")),
                    "resolution_source": _s(market.get("resolutionSource")),
                }
            )
    columns = {
        field.name: [row[field.name] for row in rows] for field in SCHEMA
    }
    return pa.Table.from_pydict(columns, schema=SCHEMA)


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def _b(value: Any) -> bool | None:
    return None if value is None else bool(value)


def default_cutoff() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=DEFAULT_LOOKBACK_DAYS)


class PolymarketGammaConnector(Connector):
    source = "polymarket"

    def __init__(
        self,
        series_by_interval: dict[str, dict[str, int]],
        client: HttpClient | None = None,
    ) -> None:
        # interval label -> asset -> gamma series id
        self._series_by_interval = series_by_interval
        self._client = client or HttpClient(
            BASE_URL, headers={"User-Agent": "chofer/0.1"}
        )
        self._cutoff = default_cutoff()

    def plan(
        self,
        *,
        dataset: str | None = None,
        entity: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DataUnit]:
        # Metadata is mutable: one versioned snapshot per series per run day.
        # `start` is the snapshot cutoff: events with startDate on/after it are
        # included (default 14 days back); use an early --from for backfill.
        if dataset and dataset != DATASET:
            return []
        self._cutoff = start or default_cutoff()
        today = datetime.now(timezone.utc).date()
        units: list[DataUnit] = []
        for interval, ids in self._series_by_interval.items():
            for asset, series_id in ids.items():
                if entity and entity not in (asset, str(series_id), interval):
                    continue
                units.append(
                    DataUnit(
                        source=self.source,
                        dataset=DATASET,
                        partitions={"series_id": str(series_id)},
                        date=today.isoformat(),
                    )
                )
        return units

    def fetch(self, unit: DataUnit) -> FetchedUnit:
        series_id = unit.partitions["series_id"]
        events: dict[str, dict] = {}
        series_obj = self._client.get_json(f"/series/{series_id}")
        if isinstance(series_obj, dict):
            for event in series_obj.get("events") or []:
                if isinstance(event, dict) and event.get("id") is not None:
                    events[str(event["id"])] = event
        # Day-windowed pagination: offset is capped at 2000 server-side, so
        # page each UTC day window ([min, max)) from the cutoff through
        # tomorrow (tomorrow captures pre-created upcoming events).
        today = datetime.now(timezone.utc).date()
        last_day = today + timedelta(days=1)
        day = self._cutoff
        while day <= last_day:
            next_day = day + timedelta(days=1)
            window = (
                f"&start_date_min={day.isoformat()}T00:00:00Z"
                f"&start_date_max={next_day.isoformat()}T00:00:00Z"
            )
            offset = 0
            while True:
                page = self._client.get_json(
                    f"/events?series_id={series_id}&limit={PAGE_LIMIT}"
                    f"&offset={offset}&order=startDate&ascending=true{window}"
                )
                if not isinstance(page, list):
                    raise MalformedError(
                        f"gamma /events for series {series_id}: expected a JSON array"
                    )
                for event in page:
                    if isinstance(event, dict) and event.get("id") is not None:
                        events[str(event["id"])] = event
                if len(page) < PAGE_LIMIT:
                    break
                offset += len(page)
            day = next_day
        payload = {
            "series": series_obj,
            "events": sorted(events.values(), key=lambda e: str(e.get("id"))),
        }
        return FetchedUnit(payload=payload, source_key=f"/series/{series_id}")

    def parse(self, unit: DataUnit, fetched: FetchedUnit) -> ParsedUnit:
        assert isinstance(fetched.payload, dict)
        series_id = int(unit.partitions["series_id"])
        table = events_to_table(series_id, fetched.payload["events"], self._cutoff)
        table = with_provenance(
            table,
            source=self.source,
            source_key=fetched.source_key,
            source_sha256=None,
        )
        return ParsedUnit(tables={"data.parquet": table})
