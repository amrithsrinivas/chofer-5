"""PMData connector: per-day series archives (ZIP of Parquet members) and the
single-market slug endpoint. Auth via the `api_key` header; the key is never
logged or written into manifests, provenance, or paths.

Day-archive ZIP internals are undocumented: members are expected to be Parquet,
one per market. Slugs come from the `market_slug` column when present, else
from the member file name. 0-row members (markets with no activity that day;
observed in live poly_trade archives) are skipped with a manifest warning.
Anything else fails loudly with MalformedError.

poly_trade members are validated against the corrected ``poly_trade/v2``
contract. On 2026-07-24 PMData disclosed that exports before their fix were
corrupted: 2026-03-28..2026-07-10 contained only ``yes`` trades with
``outcome`` NULL, and 2026-03-27 and earlier exported ``no`` prices as
``1 - actual_price``. This parser therefore rejects any poly_trade member with
NULL/unknown outcomes, an unexpected ``event_type``/``side`` vocabulary, or
out-of-range prices/sizes, so a reintroduced export error fails the whole data
unit instead of silently poisoning the lake. The complement-price bug is not
detectable per-row; it is prevented by re-ingesting the affected ranges from
the corrected provider export and by per-day outcome-balance sanity warnings.
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

POLY_TRADE = "poly_trade"
POLY_TRADE_CONTRACT_VERSION = "poly_trade/v2"
CHAINLINK_STREAMS = "chainlink_streams"
CHAINLINK_STREAM_CONTRACT_VERSION = "pmdata_chainlink_streams_v1"
_CHAINLINK_REQUIRED_COLUMNS = (
    "symbol",
    "observationsTimestamp",
    "receiveMicrosecondTimestamp",
    "price",
    "bid",
    "ask",
    "version",
)
_POLY_TRADE_REQUIRED_COLUMNS = (
    "market_slug",
    "timestamp",
    "local_timestamp",
    "event_type",
    "outcome",
    "price",
    "size",
    "side",
)
_POLY_TRADE_EVENT_TYPES = {"last_trade_price"}
# Corrected exports use lowercase ``yes``/``no``. The 2026-07-10 cutover-day
# archive additionally contains a title-case ``Up`` spelling (a yes alias) on
# a subset of rows; it is accepted but counted and surfaced as a warning, not
# silently treated as canonical. Any other vocabulary is rejected.
_POLY_TRADE_OUTCOMES = {"yes", "no"}
_POLY_TRADE_OUTCOME_ALIASES = {"Up": "yes"}
_POLY_TRADE_SIDES = {"BUY", "SELL"}
# Below this share of either outcome, a poly_trade day is flagged as a
# suspected one-sided export (the Mar 28 - Jul 10 bug had exactly zero `no`
# trades). This is a warning, not a failure: genuinely quiet days happen.
_ONE_SIDED_OUTCOME_SHARE = 0.02


def _read_parquet(data: bytes, *, context: str) -> pa.Table:
    try:
        return pq.read_table(io.BytesIO(data))
    except Exception as exc:
        raise MalformedError(f"{context}: not a readable parquet payload: {exc}") from exc


def validate_poly_trade_member(
    table: pa.Table, *, member_name: str, context: str
) -> dict[str, int]:
    """Fail closed on corrupted poly_trade members; report benign edge cases.

    Hard failures (raise ``MalformedError``): missing required columns, NULL
    or unknown outcomes, unknown ``event_type``/``side`` values, NULL or
    out-of-band prices (``< 0``, ``> 1``, NaN), and NULL/non-positive/NaN
    sizes. These are the signatures of the 2026-03-28..2026-07-10 NULL-outcome
    export and of genuinely impossible data.

    Benign, returned as counts (the caller aggregates them into day-level
    warnings rather than failing the unit): title-case ``Up`` outcome aliases
    seen on the 2026-07-10 cutover day, and boundary prices of exactly ``0``
    or ``1`` (markets trading at certainty). The historical complement-price
    bug (``no`` prices exported as ``1 - actual``) cannot be detected from a
    single member; guarding against it requires re-ingesting corrected
    exports, tracked via ``POLY_TRADE_CONTRACT_VERSION`` and the lake's
    per-unit manifests.

    Returns ``{"outcome_alias": n, "boundary_price": n}``.
    """
    missing = [c for c in _POLY_TRADE_REQUIRED_COLUMNS if c not in table.column_names]
    if missing:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} missing required "
            f"column(s) {missing} (expected {POLY_TRADE_CONTRACT_VERSION} schema)"
        )

    outcome = table.column("outcome")
    null_outcomes = outcome.null_count
    if null_outcomes:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has {null_outcomes} "
            "NULL outcome value(s) — matches the corrupted 2026-03-28.."
            "2026-07-10 export; re-download from the corrected provider export"
        )
    allowed = _POLY_TRADE_OUTCOMES | set(_POLY_TRADE_OUTCOME_ALIASES)
    unknown_outcomes = set(outcome.unique().to_pylist()) - allowed
    if unknown_outcomes:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has unknown outcome "
            f"value(s) {sorted(unknown_outcomes)} (expected yes/no)"
        )
    outcome_alias = sum(
        row["counts"]
        for row in outcome.value_counts().to_pylist()
        if row["values"] in _POLY_TRADE_OUTCOME_ALIASES
    )

    unknown_events = set(table.column("event_type").drop_null().unique().to_pylist()) - _POLY_TRADE_EVENT_TYPES
    if unknown_events:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has unknown "
            f"event_type value(s) {sorted(unknown_events)}"
        )
    unknown_sides = set(table.column("side").drop_null().unique().to_pylist()) - _POLY_TRADE_SIDES
    if unknown_sides:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has unknown side "
            f"value(s) {sorted(unknown_sides)}"
        )

    prices = table.column("price")
    if prices.null_count:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has NULL price(s)"
        )
    bad_prices = pc.sum(
        pc.or_kleene(
            pc.less(prices, 0.0),
            pc.or_kleene(pc.greater(prices, 1.0), pc.is_nan(prices)),
        )
    ).as_py()
    if bad_prices:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has {bad_prices} "
            "price(s) outside the closed interval [0, 1]"
        )
    boundary_price = pc.sum(
        pc.or_kleene(pc.equal(prices, 0.0), pc.equal(prices, 1.0))
    ).as_py()

    sizes = table.column("size")
    if sizes.null_count:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has NULL size(s)"
        )
    bad_sizes = pc.sum(
        pc.or_kleene(pc.less_equal(sizes, 0.0), pc.is_nan(sizes))
    ).as_py()
    if bad_sizes:
        raise MalformedError(
            f"{context}: poly_trade member {member_name} has {bad_sizes} "
            "non-positive or NaN size(s)"
        )
    return {"outcome_alias": outcome_alias, "boundary_price": boundary_price}


def _outcome_balance_warning(tables: dict[str, pa.Table], *, context: str) -> str | None:
    """Warn when a poly_trade day is nearly one-sided (export-bug signature)."""
    yes = no = 0
    for name, table in tables.items():
        if "outcome" not in table.column_names:
            continue
        counts = table.column("outcome").value_counts().to_pylist()
        for row in counts:
            if row["values"] == "yes":
                yes += row["counts"]
            elif row["values"] == "no":
                no += row["counts"]
    total = yes + no
    if not total:
        return None
    minority = min(yes, no) / total
    if minority < _ONE_SIDED_OUTCOME_SHARE:
        return (
            f"{context}: poly_trade day is one-sided (yes={yes}, no={no}; "
            f"minority share {minority:.4f}) — resembles the corrupted "
            "2026-03-28..2026-07-10 yes-only export"
        )
    return None


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
    dataset: str | None = None,
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
    warnings: list[str] = []
    empty_members: list[str] = []
    member_flag_counts = {"outcome_alias": 0, "boundary_price": 0}
    for member in members:
        if not member.filename.lower().endswith(".parquet"):
            raise MalformedError(
                f"{source_key}: unexpected ZIP member {member.filename!r} "
                "(only Parquet members are supported)"
            )
        table = _read_parquet(archive.read(member), context=member.filename)
        if table.num_rows == 0:
            # PMData includes 0-row members for markets with no activity that
            # day; their market_slug column is all-null. There is no data to
            # persist — skip and report, don't fail the whole day.
            empty_members.append(member.filename)
            continue
        if dataset == POLY_TRADE:
            flags = validate_poly_trade_member(
                table, member_name=member.filename, context=source_key
            )
            member_flag_counts["outcome_alias"] += flags["outcome_alias"]
            member_flag_counts["boundary_price"] += flags["boundary_price"]
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
    if not tables:
        raise MalformedError(
            f"{source_key}: all {len(members)} members were empty (no data at all)"
        )
    if empty_members:
        warnings.append(
            f"skipped {len(empty_members)} empty member(s): "
            + ", ".join(empty_members)
        )
    if dataset == POLY_TRADE:
        if member_flag_counts["outcome_alias"]:
            warnings.append(
                f"{source_key}: {member_flag_counts['outcome_alias']} trade(s) use "
                "the title-case 'Up' outcome alias (yes) — seen on the "
                "2026-07-10 provider cutover day"
            )
        if member_flag_counts["boundary_price"]:
            warnings.append(
                f"{source_key}: {member_flag_counts['boundary_price']} trade(s) at "
                "a boundary price of exactly 0 or 1 (market at certainty)"
            )
        balance = _outcome_balance_warning(tables, context=source_key)
        if balance:
            warnings.append(balance)
    return ParsedUnit(
        tables={f"{slug}.parquet": t for slug, t in tables.items()},
        warnings=warnings,
    )


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


def parse_chainlink_parquet(
    payload: bytes,
    *,
    source: str,
    source_key: str,
    source_sha256: str,
    expected_symbol: str,
) -> ParsedUnit:
    """Validate and preserve one PMData Chainlink Data Streams daily file."""
    table = _read_parquet(payload, context=source_key)
    missing = sorted(set(_CHAINLINK_REQUIRED_COLUMNS) - set(table.column_names))
    if missing:
        raise MalformedError(
            f"{source_key}: Chainlink stream missing columns {missing} "
            f"({CHAINLINK_STREAM_CONTRACT_VERSION})"
        )
    symbols = set(table["symbol"].drop_null().unique().to_pylist())
    if symbols != {expected_symbol}:
        raise MalformedError(
            f"{source_key}: expected Chainlink symbol {expected_symbol!r}, "
            f"found {sorted(symbols)}"
        )
    observation = table["observationsTimestamp"]
    received = table["receiveMicrosecondTimestamp"]
    if observation.null_count or received.null_count:
        raise MalformedError(f"{source_key}: Chainlink timestamps contain NULLs")
    observation_unit = detect_chainlink_timestamp_unit(
        observation.to_pylist(), "observationsTimestamp"
    )
    received_unit = detect_chainlink_timestamp_unit(
        received.to_pylist(), "receiveMicrosecondTimestamp"
    )
    if observation_unit != "s" or received_unit != "us":
        raise MalformedError(
            f"{source_key}: unexpected Chainlink timestamp units "
            f"observation={observation_unit}, receive={received_unit}"
        )
    if any(
        value is None or float(value) <= 0
        for column in ("price", "bid", "ask")
        for value in table[column].to_pylist()
    ):
        raise MalformedError(f"{source_key}: Chainlink prices must be positive")
    if any(
        float(bid) > float(ask)
        for bid, ask in zip(table["bid"].to_pylist(), table["ask"].to_pylist(), strict=True)
    ):
        raise MalformedError(f"{source_key}: Chainlink bid exceeds ask")
    normalized = with_provenance(
        table,
        source=source,
        source_key=source_key,
        source_sha256=source_sha256,
    )
    return ParsedUnit(
        tables={"data.parquet": normalized},
        warnings=[f"{CHAINLINK_STREAM_CONTRACT_VERSION}: observation=s receive=us"],
    )


def detect_chainlink_timestamp_unit(values: list[int], column: str) -> str:
    if not values:
        raise MalformedError(f"{column}: empty timestamp column")
    magnitude = max(abs(int(value)) for value in values)
    if 10**8 <= magnitude < 10**11:
        return "s"
    if 10**14 <= magnitude < 10**17:
        return "us"
    raise MalformedError(f"{column}: unexpected epoch magnitude {magnitude}")


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
        # Historical Chainlink planning is intentionally disabled. The parser
        # and fetch path remain dormant for possible future contract research.
        # Do not let an explicit dataset request fall through and scan market
        # archives under an unrelated dataset name.
        if dataset == CHAINLINK_STREAMS:
            return []
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
        if unit.dataset == CHAINLINK_STREAMS:
            symbol = unit.partitions["symbol"]
            path = (
                f"/chainlink/{symbol}/streams/"
                f"{symbol}_streams_{unit.date}.parquet"
            )
            payload = self._client.get_bytes(path)
            return FetchedUnit(
                payload=payload,
                source_key=path,
                source_sha256=hashlib.sha256(payload).hexdigest(),
            )
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
        if unit.dataset == CHAINLINK_STREAMS:
            return parse_chainlink_parquet(
                fetched.payload,
                source=self.source,
                source_key=fetched.source_key,
                source_sha256=fetched.source_sha256,
                expected_symbol=unit.partitions["symbol"],
            )
        return parse_day_archive(
            fetched.payload,
            source=self.source,
            source_key=fetched.source_key,
            source_sha256=fetched.source_sha256,
            dataset=unit.dataset,
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
