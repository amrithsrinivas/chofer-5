from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from assembler.book import derive_book_metrics, quote_fields, reconstruct_book

UTC = timezone.utc


def test_book_rows_replace_and_price_changes_mutate_correct_sides() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {"event_type": "book", "bid_prices": [0.49, 0.48], "bid_sizes": [10.0, 20.0], "ask_prices": [0.51, 0.52], "ask_sizes": [11.0, 21.0], "pc_price": None, "pc_size": None, "pc_side": None},
        {"event_type": "price_change", "bid_prices": None, "bid_sizes": None, "ask_prices": None, "ask_sizes": None, "pc_price": 0.49, "pc_size": 0.0, "pc_side": "BUY"},
        {"event_type": "price_change", "bid_prices": None, "bid_sizes": None, "ask_prices": None, "ask_sizes": None, "pc_price": 0.50, "pc_size": 7.0, "pc_side": "BUY"},
        {"event_type": "price_change", "bid_prices": None, "bid_sizes": None, "ask_prices": None, "ask_sizes": None, "pc_price": 0.51, "pc_size": 0.0, "pc_side": "SELL"},
        {"event_type": "price_change", "bid_prices": None, "bid_sizes": None, "ask_prices": None, "ask_sizes": None, "pc_price": 0.53, "pc_size": 9.0, "pc_side": "SELL"},
        {"event_type": "book", "bid_prices": [0.47], "bid_sizes": [5.0], "ask_prices": [0.54], "ask_sizes": [6.0], "pc_price": None, "pc_size": None, "pc_side": None},
    ]
    for ordinal, row in enumerate(rows):
        row["_event_time"] = base + timedelta(seconds=ordinal)
        row["_available_at"] = base + timedelta(seconds=ordinal, milliseconds=1)
        row["_row_ordinal"] = ordinal
    snapshot = reconstruct_book(pa.Table.from_pylist(rows))
    assert snapshot.valid
    assert snapshot.bids == ((470000, 5.0),)
    assert snapshot.asks == ((540000, 6.0),)
    metrics = derive_book_metrics(snapshot)
    assert metrics["up_midprice"] == pytest.approx(0.505)
    quote = quote_fields(snapshot, at=base + timedelta(seconds=6), max_staleness=5, band_min=0.49, band_max=0.51)
    assert quote["down_bid"] == pytest.approx(0.46)
    assert quote["down_ask"] == pytest.approx(0.53)
