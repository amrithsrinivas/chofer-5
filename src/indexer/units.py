"""DataUnit model and date-range planning helpers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

from pydantic import BaseModel


class DataUnit(BaseModel):
    """One immutable unit of work: source x dataset x partition keys x day."""

    source: str
    dataset: str
    partitions: dict[str, str]  # ordered: insertion order is the path order
    date: str  # YYYY-MM-DD (UTC)

    @property
    def id(self) -> str:
        """Path-safe unit id: source/dataset/key=value.../date=YYYY-MM-DD."""
        parts = [f"{key}={value}" for key, value in self.partitions.items()]
        return "/".join([self.source, self.dataset, *parts, f"date={self.date}"])


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def day_range(start: date, end: date) -> Iterator[date]:
    """Inclusive UTC day range [start, end]."""
    if end < start:
        return
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
