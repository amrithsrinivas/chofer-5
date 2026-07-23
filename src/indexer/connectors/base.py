"""Connector framework: shared result types and the connector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import pyarrow as pa

from ..units import DataUnit


@dataclass
class FetchedUnit:
    """Raw payload for one unit, plus provenance of the fetch itself."""

    payload: bytes | dict | list
    source_key: str  # object key / request path (no secrets)
    source_sha256: str | None = None  # hash of the payload, when meaningful


@dataclass
class ParsedUnit:
    """Typed tables ready to commit: file name -> table (provenance appended)."""

    tables: dict[str, pa.Table]
    min_ts: str | None = None  # source-native min timestamp (as string)
    max_ts: str | None = None
    warnings: list[str] = field(default_factory=list)


class Connector(ABC):
    """A source connector: plan units, fetch raw payloads, parse to tables."""

    source: str

    @abstractmethod
    def plan(
        self,
        *,
        dataset: str | None = None,
        entity: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DataUnit]:
        """Enumerate the units covering the requested (or default) range."""

    @abstractmethod
    def fetch(self, unit: DataUnit) -> FetchedUnit:
        """Download the raw payload. Raises UnavailableError on 404."""

    @abstractmethod
    def parse(self, unit: DataUnit, fetched: FetchedUnit) -> ParsedUnit:
        """Parse the payload into typed tables with provenance columns."""


def with_provenance(
    table: pa.Table, *, source: str, source_key: str, source_sha256: str | None
) -> pa.Table:
    """Append the standard provenance columns to a source-native table."""
    from datetime import datetime, timezone

    n = table.num_rows
    ingested_at = datetime.now(timezone.utc)
    table = table.append_column("_source", pa.array([source] * n, pa.string()))
    table = table.append_column(
        "_source_key", pa.array([source_key] * n, pa.string())
    )
    table = table.append_column(
        "_source_sha256", pa.array([source_sha256] * n, pa.string())
    )
    table = table.append_column(
        "_ingested_at", pa.array([ingested_at] * n, pa.timestamp("us", tz="UTC"))
    )
    return table
