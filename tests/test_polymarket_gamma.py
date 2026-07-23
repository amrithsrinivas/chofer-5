"""Polymarket gamma connector tests: fixture JSON only, no network."""

from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from indexer.connectors.polymarket_gamma import (
    PolymarketGammaConnector,
    events_to_table,
)
from indexer.http import HttpClient, MalformedError

SERIES_ID = 10684
TODAY = datetime.now(timezone.utc).date()
RECENT = (TODAY - timedelta(days=3)).isoformat() + "T16:00:00Z"
OLD = (TODAY - timedelta(days=60)).isoformat() + "T16:00:00Z"
CUTOFF = TODAY - timedelta(days=14)


def make_market(slug: str, start: str, closed: bool = True) -> dict:
    return {
        "id": "512345",
        "question": "Bitcoin Up or Down?",
        "conditionId": "0xabc123",
        "slug": slug,
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["712345678901234567890", "712345678901234567891"]',
        "startDate": start,
        "endDate": start,
        "closed": closed,
        "active": not closed,
        "archived": False,
        "umaResolutionStatus": "resolved" if closed else None,
        "resolutionSource": "https://data.binance.com/api/v3/klines?symbol=BTCUSDT",
    }


def make_event(event_id: str, start: str, closed: bool = True) -> dict:
    slug = f"btc-updown-5m-{event_id}"
    return {
        "id": event_id,
        "slug": slug,
        "title": f"Bitcoin Up or Down {event_id}",
        "startDate": start,
        "endDate": start,
        "markets": [make_market(slug, start, closed=closed)],
    }


def test_events_to_table_row_extraction():
    table = events_to_table(SERIES_ID, [make_event("99001", RECENT)], date(2020, 1, 1))
    assert table.num_rows == 1
    row = table.to_pylist()[0]
    assert row["series_id"] == "10684"
    assert row["condition_id"] == "0xabc123"
    assert row["outcomes_raw"] == '["Up", "Down"]'
    assert row["outcomes_count"] == 2
    assert row["clob_token_ids_count"] == 2
    assert row["closed"] is True
    assert row["active"] is False
    assert row["uma_resolution_status"] == "resolved"
    assert "binance" in row["resolution_source"]
    assert row["start_date"] == RECENT


def test_events_to_table_handles_native_arrays():
    event = make_event("99001", RECENT)
    event["markets"][0]["outcomes"] = ["Up", "Down"]
    event["markets"][0]["clobTokenIds"] = ["1", "2"]
    row = events_to_table(SERIES_ID, [event], date(2020, 1, 1)).to_pylist()[0]
    assert row["outcomes_count"] == 2
    assert row["clob_token_ids_raw"] == '["1","2"]'


def test_events_to_table_rejects_malformed_array_field():
    event = make_event("99001", RECENT)
    event["markets"][0]["outcomes"] = "not json"
    with pytest.raises(MalformedError):
        events_to_table(SERIES_ID, [event], date(2020, 1, 1))


def test_cutoff_drops_old_closed_events_keeps_open_ones():
    events = [
        make_event("new", RECENT, closed=True),  # recent closed: kept
        make_event("old-closed", OLD, closed=True),  # old closed: dropped
        make_event("old-open", OLD, closed=False),  # old but open: kept
    ]
    table = events_to_table(SERIES_ID, events, CUTOFF)
    slugs = {row["event_id"] for row in table.to_pylist()}
    assert slugs == {"new", "old-open"}


def _gamma_client(events: list[dict], series_events: list[dict] | None = None):
    """Mock gamma API that honors the window/limit/offset query params."""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        params = parse_qs(parsed.query)
        requests.append({"path": parsed.path, "params": params})
        if parsed.path == f"/series/{SERIES_ID}":
            return httpx.Response(
                200, json={"id": SERIES_ID, "events": series_events or []}
            )
        if parsed.path == "/events":
            assert params.get("order") == ["startDate"]
            assert params.get("ascending") == ["true"]
            assert "start_date_min" in params and "start_date_max" in params
            lo = params["start_date_min"][0]
            hi = params["start_date_max"][0]
            windowed = sorted(
                (e for e in events if lo <= e["startDate"] < hi),
                key=lambda e: e["startDate"],
            )
            offset = int(params["offset"][0])
            limit = int(params["limit"][0])
            return httpx.Response(200, json=windowed[offset : offset + limit])
        return httpx.Response(404)

    client = HttpClient(
        "https://gamma-api.polymarket.com", transport=httpx.MockTransport(handler)
    )
    return client, requests


def _connector(client) -> PolymarketGammaConnector:
    return PolymarketGammaConnector({"5m": {"BTC": SERIES_ID}}, client=client)


def _event_requests(requests: list[dict]) -> list[dict]:
    return [r["params"] for r in requests if r["path"] == "/events"]


def test_windows_requested_per_day_from_cutoff_through_tomorrow():
    cutoff = TODAY - timedelta(days=1)
    events = [
        make_event("d1", cutoff.isoformat() + "T05:00:00Z"),
        make_event("d2", TODAY.isoformat() + "T05:00:00Z"),
        make_event("d3", (TODAY + timedelta(days=1)).isoformat() + "T05:00:00Z"),
    ]
    client, requests = _gamma_client(events)
    connector = _connector(client)
    unit = connector.plan(start=cutoff)[0]
    table = connector.parse(unit, connector.fetch(unit)).tables["data.parquet"]
    assert table.num_rows == 3
    windows = {
        (p["start_date_min"][0], p["start_date_max"][0])
        for p in _event_requests(requests)
    }
    expected = set()
    day = cutoff
    while day <= TODAY + timedelta(days=1):
        nxt = day + timedelta(days=1)
        expected.add((f"{day.isoformat()}T00:00:00Z", f"{nxt.isoformat()}T00:00:00Z"))
        day = nxt
    assert windows == expected  # cutoff day .. tomorrow, one window each


def test_multi_page_within_a_window():
    cutoff = TODAY
    events = [
        make_event(f"e{i:03d}", TODAY.isoformat() + "T05:00:00Z")
        for i in range(250)
    ]
    client, requests = _gamma_client(events)
    connector = _connector(client)
    unit = connector.plan(start=cutoff)[0]
    table = connector.parse(unit, connector.fetch(unit)).tables["data.parquet"]
    assert table.num_rows == 250
    today_window = [
        p
        for p in _event_requests(requests)
        if p["start_date_min"][0] == f"{TODAY.isoformat()}T00:00:00Z"
    ]
    offsets = sorted(int(p["offset"][0]) for p in today_window)
    assert offsets == [0, 100, 200]  # short page (50) stops the window


def test_boundary_dedupe_and_series_embed():
    cutoff = TODAY
    dup = make_event("dup", TODAY.isoformat() + "T00:00:00Z")  # sits on boundary
    client, _ = _gamma_client([dup, dup], series_events=[dup])
    connector = _connector(client)
    unit = connector.plan(start=cutoff)[0]
    table = connector.parse(unit, connector.fetch(unit)).tables["data.parquet"]
    ids = [row["event_id"] for row in table.to_pylist()]
    assert ids == ["dup"]


def test_plan_default_cutoff_is_14_days_and_start_overrides():
    connector = _connector(_gamma_client([])[0])
    connector.plan()
    assert connector._cutoff == TODAY - timedelta(days=14)
    connector.plan(start=date(2025, 11, 21))
    assert connector._cutoff == date(2025, 11, 21)
    units = connector.plan(entity="BTC")
    assert len(units) == 1
    assert units[0].partitions == {"series_id": str(SERIES_ID)}
    assert units[0].date == TODAY.isoformat()
    assert connector.plan(dataset="nope") == []
