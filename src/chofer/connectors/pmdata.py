"""PMData connector: per-day series archives (ZIP of Parquet members) and the
single-market slug endpoint. Auth via the `api_key` header; the key is never
logged or written into manifests, provenance, or paths.

Day-archive ZIP internals are undocumented: members are expected to be Parquet,
one per market. Slugs come from the `market_slug` column when present, else
from the member file name. Anything else fails loudly with MalformedError.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ..catalog import PmdataConfig
from ..http import HttpClient, MalformedError
from ..units import DataUnit, day_range
from .base import Connector, FetchedUnit, ParsedUnit, with_provenance

log = logging.getLogger(__name__)

BASE_URL = "https://api.pmdata.dev"
USER_AGENT = "chofer/0.1"


def _read_parquet(data: bytes, *, context: str) -> pa.Table:
    try:
        return pq.read_table(io.BytesIO(data))
    except Exception as exc:
        raise MalformedError(f"{context}: not a readable parquet payload: {exc}") from exc


def split_by_slug(
    table: pa.Table, *, member_name: str, context: str
) -> dict[str, pa.Table]:
    """Split a member table into one table per market slug."""
    if "market_slug" in table.column_names:
        slugs = sorted(table.column("market_slug").drop_null().unique().to_pylist())
        if not slugs:
            raise MalformedError(
                f"{context}: market_slug column present but empty in {member_name}"
            )
        return {
            slug: table.filter(pc.equal(table["market_slug"], slug)) for slug in slugs
        }
    slug = member_name.rsplit("/", 1)[-1]
    if slug.lower().endswith(".parquet"):
        slug = slug[: -len(".parquet")]
    if not slug:
        raise MalformedError(
            f"{context}: cannot derive market slug for member {member_name!r}"
        )
    return {slug: table}


def parse_day_archive(
    zip_bytes: bytes,
    *,
    source: str,
    source_key: str,
    source_sha256: str,
) -> ParsedUnit:
    """Parse a PMData day archive into one provenance-tagged table per slug."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise MalformedError(f"{source_key}: not a valid ZIP archive") from exc
    members = [info for info in archive.infolist() if not info.is_dir()]
    if not members:
        raise MalformedError(f"{source_key}: ZIP archive contains no files")
    tables: dict[str, pa.Table] = {}
    for member in members:
        if not member.filename.lower().endswith(".parquet"):
            raise MalformedError(
                f"{source_key}: unexpected ZIP member {member.filename!r} "
                "(only Parquet members are supported)"
            )
        table = _read_parquet(archive.read(member), context=member.filename)
        for slug, sub in split_by_slug(
            table, member_name=member.filename, context=source_key
        ).items():
            if slug in tables:
                raise MalformedError(
                    f"{source_key}: duplicate market slug {slug!r} across members"
                )
            tables[slug] = with_provenance(
                sub,
                source=source,
                source_key=source_key,
                source_sha256=source_sha256,
            )
    return ParsedUnit(tables={f"{slug}.parquet": t for slug, t in tables.items()})


def parse_slug_parquet(
    payload: bytes,
    *,
    source: str,
    source_key: str,
    source_sha256: str,
) -> pa.Table:
    """Parse a slug-endpoint parquet download and append provenance columns."""
    table = _read_parquet(payload, context=source_key)
    return with_provenance(
        table, source=source, source_key=source_key, source_sha256=source_sha256
    )


class PmdataConnector(Connector):
    source = "pmdata"

    def __init__(
        self,
        config: PmdataConfig,
        api_key: str,
        client: HttpClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("PMDATA_KEY is required for the pmdata source")
        self._config = config
        self._client = client or HttpClient(
            BASE_URL, headers={"api_key": api_key, "User-Agent": USER_AGENT}
        )

    def plan(
        self,
        *,
        dataset: str | None = None,
        entity: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DataUnit]:
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        end = end or yesterday
        units: list[DataUnit] = []
        for series_key, series in self._config.series.items():
            if entity and entity not in (series_key, series.provider_series):
                continue
            first_day = series.recording_starts_at.date()
            range_start = max(start, first_day) if start else first_day
            for data_type in self._config.market_data_types:
                if dataset and data_type != dataset:
                    continue
                for day in day_range(range_start, end):
                    units.append(
                        DataUnit(
                            source=self.source,
                            dataset=data_type,
                            partitions={"series": series.provider_series},
                            date=day.isoformat(),
                        )
                    )
        return units

    def fetch(self, unit: DataUnit) -> FetchedUnit:
        series = unit.partitions["series"]
        path = (
            f"/polymarket/{series}/{unit.dataset}/"
            f"{series}_{unit.dataset}_{unit.date}.zip"
        )
        payload = self._client.get_bytes(path)
        return FetchedUnit(
            payload=payload,
            source_key=path,
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )

    def parse(self, unit: DataUnit, fetched: FetchedUnit) -> ParsedUnit:
        assert isinstance(fetched.payload, bytes)
        assert fetched.source_sha256 is not None
        return parse_day_archive(
            fetched.payload,
            source=self.source,
            source_key=fetched.source_key,
            source_sha256=fetched.source_sha256,
        )

    def fetch_slug(self, data_type: str, slug: str) -> pa.Table:
        """Single-market backfill via /download/{data_type}/{slug}.parquet."""
        path = f"/download/{data_type}/{slug}.parquet"
        payload = self._client.get_bytes(path)
        return parse_slug_parquet(
            payload,
            source=self.source,
            source_key=path,
            source_sha256=hashlib.sha256(payload).hexdigest(),
        )
