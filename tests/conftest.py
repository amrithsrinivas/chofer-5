"""Shared test fixtures: synthetic data only, no network access."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from indexer.catalog import load_catalog
from indexer.lake import Lake
from indexer.units import DataUnit

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "indexer"


@pytest.fixture()
def catalog():
    return load_catalog(CONFIG_DIR)


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    return Lake(tmp_path / "data")


@pytest.fixture()
def unit() -> DataUnit:
    return DataUnit(
        source="binance",
        dataset="spot_klines",
        partitions={"symbol": "BTCUSDT", "interval": "1m"},
        date="2026-07-20",
    )


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buf.getvalue()


def make_csv_zip(csv_text: str, member_name: str = "data.csv") -> tuple[bytes, str]:
    """Return (zip bytes, sha256 hex) for a single-CSV archive."""
    payload = make_zip({member_name: csv_text.encode("utf-8")})
    return payload, hashlib.sha256(payload).hexdigest()


def table_to_parquet_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def sample_table(slug: str, n: int = 3) -> pa.Table:
    return pa.table(
        {
            "market_slug": [slug] * n,
            "timestamp": pa.array(
                [1784332800000000 + i * 1_000_000 for i in range(n)],
                pa.timestamp("us", tz="UTC"),
            ),
            "price": pa.array([0.51 + i * 0.01 for i in range(n)], pa.float64()),
        }
    )
