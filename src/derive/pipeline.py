"""Date-partitioned compaction of immutable raw market inputs.

The canonical zone is itself a manifest-backed lake rooted at
``<data-dir>/derived/canonical``. A partition is complete only after every
Parquet output has landed and its manifest has been atomically committed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from assembler.availability import attach_utc, binance_row_times, epoch_to_utc, pmdata_times
from assembler.book import BOOK_ASSUMPTION_VERSION, OrderBookReconstructor
from assembler.manifest import InputLedger, ManifestCatalog, ManifestRecord
from indexer.lake import Lake
from indexer.manifest import FileEntry, Manifest, get_code_version, manifest_for, schema_fingerprint, write_manifest
from indexer.units import DataUnit

UTC = timezone.utc
CANONICAL_IMPLEMENTATION_VERSION = "canonical_market_inputs_v3"
FUTURES_AVAILABILITY_VERSION = "futures_metrics_interval_close_conservative_v1"
SERIES_IDS = {"BTC": "10684", "ETH": "10683", "SOL": "10686", "XRP": "10685"}
DEFAULT_CHECKPOINT_OFFSETS_MS = (-300_000, -120_000, -60_000, -30_000, -29_750, -15_000, -5_000, 0)
_SLUG = re.compile(r"^(btc|eth|sol|xrp)-updown-5m-(\d+)$")
_TRADE_SIGNS = {
    ("yes", "BUY"): 1.0,
    ("yes", "SELL"): -1.0,
    ("no", "BUY"): -1.0,
    ("no", "SELL"): 1.0,
    ("up", "BUY"): 1.0,
    ("up", "SELL"): -1.0,
}


@dataclass(frozen=True)
class DeriveResult:
    unit_id: str
    status: str
    path: Path | None
    rows: int
    skipped: bool = False


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _micros(value: Any) -> int | None:
    if value is None:
        return None
    numeric = Decimal(str(value))
    if not numeric.is_finite():
        return None
    return int((numeric * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _empty(schema: pa.Schema) -> pa.Table:
    return pa.table({field.name: pa.array([], type=field.type) for field in schema}, schema=schema)


def _rows(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    if not rows:
        return _empty(schema)
    return pa.table(
        {field.name: pa.array([row.get(field.name) for row in rows], type=field.type) for field in schema},
        schema=schema,
    )


BOOK_SCHEMA = pa.schema(
    [
        ("asset", pa.string()),
        ("slug", pa.string()),
        ("window_start", pa.timestamp("us", tz="UTC")),
        ("checkpoint_offset_ms", pa.int64()),
        ("checkpoint_at", pa.timestamp("us", tz="UTC")),
        ("valid", pa.bool_()),
        ("reason", pa.string()),
        ("bid_prices_micros", pa.list_(pa.int64())),
        ("bid_sizes", pa.list_(pa.float64())),
        ("ask_prices_micros", pa.list_(pa.int64())),
        ("ask_sizes", pa.list_(pa.float64())),
        ("last_event_time", pa.timestamp("us", tz="UTC")),
        ("max_available_at", pa.timestamp("us", tz="UTC")),
        ("source_row_ordinal", pa.int64()),
        ("source_file_sha256", pa.string()),
        ("book_assumption_version", pa.string()),
    ]
)
L2_EVENT_SCHEMA = pa.schema(
    [
        ("asset", pa.string()),
        ("slug", pa.string()),
        ("window_start", pa.timestamp("us", tz="UTC")),
        ("event_time", pa.timestamp("us", tz="UTC")),
        ("available_at", pa.timestamp("us", tz="UTC")),
        ("source_row_ordinal", pa.int64()),
        ("event_type", pa.string()),
        ("bid_prices", pa.list_(pa.float64())),
        ("bid_sizes", pa.list_(pa.float64())),
        ("ask_prices", pa.list_(pa.float64())),
        ("ask_sizes", pa.list_(pa.float64())),
        ("pc_price_micros", pa.int64()),
        ("pc_size", pa.float64()),
        ("pc_side", pa.string()),
        ("source_file_sha256", pa.string()),
    ]
)
TRADE_BAR_SCHEMA = pa.schema(
    [
        ("asset", pa.string()),
        ("slug", pa.string()),
        ("bucket_start", pa.timestamp("us", tz="UTC")),
        ("bucket_end", pa.timestamp("us", tz="UTC")),
        ("yes_buy_count", pa.int64()),
        ("yes_sell_count", pa.int64()),
        ("no_buy_count", pa.int64()),
        ("no_sell_count", pa.int64()),
        ("yes_buy_size", pa.float64()),
        ("yes_sell_size", pa.float64()),
        ("no_buy_size", pa.float64()),
        ("no_sell_size", pa.float64()),
        ("signed_up_pressure", pa.float64()),
        ("trade_count", pa.int64()),
        ("total_size", pa.float64()),
        ("notional", pa.float64()),
        ("last_event_time", pa.timestamp("us", tz="UTC")),
        ("max_available_at", pa.timestamp("us", tz="UTC")),
        ("source_row_start", pa.int64()),
        ("source_row_end", pa.int64()),
        ("source_file_sha256", pa.string()),
    ]
)
MARKET_SCHEMA = pa.schema(
    [
        ("asset", pa.string()),
        ("series_id", pa.string()),
        ("market_id", pa.string()),
        ("condition_id", pa.string()),
        ("slug", pa.string()),
        ("up_token_id", pa.string()),
        ("down_token_id", pa.string()),
        ("window_start", pa.timestamp("us", tz="UTC")),
        ("window_end", pa.timestamp("us", tz="UTC")),
        ("resolution_rule", pa.string()),
        ("resolution_source", pa.string()),
        ("source_manifest_sha256", pa.string()),
    ]
)
RESOLUTION_SCHEMA = pa.schema(
    [
        ("asset", pa.string()),
        ("slug", pa.string()),
        ("outcome", pa.string()),
        ("gamma_snapshot_ingested_at", pa.timestamp("us", tz="UTC")),
        ("pmdata_resolution_available_at", pa.timestamp("us", tz="UTC")),
        ("pmdata_outcome", pa.string()),
        ("confidence", pa.string()),
        ("disagreement_reason", pa.string()),
    ]
)
BAR_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("dataset", pa.string()),
        ("open_time", pa.timestamp("us", tz="UTC")),
        ("event_time", pa.timestamp("us", tz="UTC")),
        ("base_available_at", pa.timestamp("us", tz="UTC")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("open_micros", pa.int64()),
        ("high_micros", pa.int64()),
        ("low_micros", pa.int64()),
        ("close_micros", pa.int64()),
        ("volume", pa.float64()),
        ("quote_volume", pa.float64()),
        ("count", pa.int64()),
        ("taker_buy_volume", pa.float64()),
        ("taker_buy_quote_volume", pa.float64()),
        ("source_manifest_sha256", pa.string()),
    ]
)
AGG_BAR_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("bucket_start", pa.timestamp("us", tz="UTC")),
        ("bucket_end", pa.timestamp("us", tz="UTC")),
        ("buy_count", pa.int64()),
        ("sell_count", pa.int64()),
        ("buy_base_volume", pa.float64()),
        ("sell_base_volume", pa.float64()),
        ("buy_quote_volume", pa.float64()),
        ("sell_quote_volume", pa.float64()),
        ("signed_base_flow", pa.float64()),
        ("signed_quote_flow", pa.float64()),
        ("total_trade_count", pa.int64()),
        ("total_base_volume", pa.float64()),
        ("total_quote_volume", pa.float64()),
        ("vwap", pa.float64()),
        ("first_price", pa.float64()),
        ("last_price", pa.float64()),
        ("high_price", pa.float64()),
        ("low_price", pa.float64()),
        ("first_trade_id", pa.int64()),
        ("last_trade_id", pa.int64()),
        ("last_event_time", pa.timestamp("us", tz="UTC")),
        ("base_available_at", pa.timestamp("us", tz="UTC")),
        ("source_manifest_sha256", pa.string()),
    ]
)
FUTURES_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("event_time", pa.timestamp("us", tz="UTC")),
        ("base_available_at", pa.timestamp("us", tz="UTC")),
        ("sum_open_interest", pa.float64()),
        ("sum_open_interest_value", pa.float64()),
        ("count_toptrader_long_short_ratio", pa.float64()),
        ("sum_toptrader_long_short_ratio", pa.float64()),
        ("count_long_short_ratio", pa.float64()),
        ("sum_taker_long_short_vol_ratio", pa.float64()),
        ("availability_policy", pa.string()),
        ("source_manifest_sha256", pa.string()),
    ]
)
class CanonicalDeriver:
    def __init__(
        self,
        data_dir: Path | str,
        *,
        checkpoint_offsets_ms: Iterable[int] = DEFAULT_CHECKPOINT_OFFSETS_MS,
        depth_levels: int = 20,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.raw = ManifestCatalog(self.data_dir)
        self.root = self.data_dir / "derived" / "canonical"
        self.lake = Lake(self.root)
        self.checkpoint_offsets_ms = tuple(sorted(set(int(value) for value in checkpoint_offsets_ms)))
        if not self.checkpoint_offsets_ms:
            raise ValueError("at least one checkpoint offset is required")
        if depth_levels <= 0:
            raise ValueError("depth_levels must be positive")
        self.depth_levels = depth_levels

    def _unit(self, dataset: str, asset: str, day: str) -> DataUnit:
        partitions = {"asset": asset.upper()}
        if dataset == "market_inputs":
            partitions["interval"] = "5m"
        return DataUnit(source="canonical", dataset=dataset, partitions=partitions, date=day)

    def _parameters(self) -> dict[str, Any]:
        return {
            "implementation_version": CANONICAL_IMPLEMENTATION_VERSION,
            "implementation_source_sha256": _source_hash(),
            "checkpoint_offsets_ms": list(self.checkpoint_offsets_ms),
            "depth_levels": self.depth_levels,
            "book_assumption_version": BOOK_ASSUMPTION_VERSION,
        }

    def _preflight(
        self,
        unit: DataUnit,
        records: Iterable[ManifestRecord | None],
        *,
        force: bool,
    ) -> DeriveResult | None:
        existing = manifest_for(self.lake, unit)
        if existing is None or existing.status != "complete" or force:
            return None
        expected = {
            (record.relative_path, record.sha256)
            for record in records
            if record is not None
        }
        actual = {
            (
                str(item.get("manifest_path")),
                str(item.get("manifest_sha256")),
            )
            for item in existing.inputs
        }
        if existing.parameters != self._parameters() or actual != expected:
            raise FileExistsError(
                f"canonical unit is stale; rebuild with --force: {unit.id}"
            )
        return DeriveResult(
            unit.id,
            "complete",
            self.lake.unit_dir(unit),
            existing.row_count,
            True,
        )

    def _commit(
        self,
        unit: DataUnit,
        tables: dict[str, pa.Table],
        ledger: InputLedger,
        metrics: dict[str, Any],
        *,
        force: bool,
        started_at: datetime,
        elapsed_seconds: float,
    ) -> DeriveResult:
        existing = manifest_for(self.lake, unit)
        parameters = self._parameters()
        inputs = ledger.snapshot()
        if existing is not None and existing.status == "complete" and not force:
            if existing.parameters != parameters or existing.inputs != inputs:
                raise FileExistsError(
                    f"canonical unit is stale; rebuild with --force: {unit.id}"
                )
            return DeriveResult(
                unit.id,
                "complete",
                self.lake.unit_dir(unit),
                existing.row_count,
                True,
            )
        run_id = uuid.uuid4().hex
        previous_manifest: Path | None = None
        if existing is not None and force:
            # Remove the completion marker before replacing any files. A
            # crash can leave uncommitted data, but never a stale "complete"
            # manifest pointing at a mixed old/new multi-file unit.
            manifest_path = self.lake.manifest_path(unit)
            previous_manifest = (
                self.lake.tmp_root / run_id / "previous-manifest.json"
            )
            previous_manifest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(manifest_path, previous_manifest)
        committed = self.lake.commit_tables(
            unit, tables, namespace=run_id, row_group_size=65_536
        )
        manifest = Manifest(
            unit_id=unit.id,
            source=unit.source,
            dataset=unit.dataset,
            partitions=unit.partitions,
            date=unit.date,
            status="complete",
            files=[
                FileEntry(path=item.rel_path, sha256=item.sha256, bytes=item.size_bytes, rows=item.rows)
                for item in committed
            ],
            row_count=sum(item.rows for item in committed),
            schema_fingerprints={name: schema_fingerprint(table) for name, table in tables.items()},
            config_hash=_stable_hash(parameters),
            code_version=get_code_version(Path(__file__).resolve().parents[2]),
            run_id=run_id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            inputs=inputs,
            parameters=parameters,
            metrics={**metrics, "processing_seconds": elapsed_seconds},
        )
        path = write_manifest(self.lake, manifest, namespace=run_id)
        if previous_manifest is not None:
            previous_manifest.unlink(missing_ok=True)
        self.lake.clean_run(run_id)
        return DeriveResult(unit.id, "complete", path, manifest.row_count)

    @staticmethod
    def _manifest_file(
        catalog: ManifestCatalog,
        ledger: InputLedger,
        *,
        source: str,
        dataset: str,
        partitions: dict[str, str],
        day: str,
    ) -> tuple[ManifestRecord | None, Path | None]:
        manifest = catalog.exact_unit(
            source=source, dataset=dataset, partitions=partitions, date=day
        )
        if manifest is None:
            return None, None
        ledger.add(manifest)
        if len(manifest.files) != 1:
            raise ValueError(f"expected one file in {manifest.relative_path}")
        return manifest, catalog.raw_path(manifest.files[0])

    def _gamma_rows(
        self,
        asset: str,
        day: str,
        ledger: InputLedger,
        manifest: ManifestRecord | None = None,
    ) -> tuple[list[dict[str, Any]], ManifestRecord]:
        manifest = manifest or self.raw.latest_complete(
            source="polymarket",
            dataset="market_metadata",
            partitions={"series_id": SERIES_IDS[asset]},
        )
        ledger.add(manifest)
        table = pq.read_table(self.raw.raw_path(manifest.files[0]))
        result: list[dict[str, Any]] = []
        for row in table.to_pylist():
            match = _SLUG.fullmatch(str(row.get("slug") or ""))
            if not match or match.group(1).upper() != asset:
                continue
            start = datetime.fromtimestamp(int(match.group(2)), tz=UTC)
            if start.date().isoformat() == day:
                row["_window_start"] = start
                result.append(row)
        return result, manifest

    @staticmethod
    def _pmdata_file(
        manifest: ManifestRecord | None, catalog: ManifestCatalog, ledger: InputLedger, slug: str
    ) -> tuple[Path | None, str | None]:
        if manifest is None:
            return None, None
        ledger.add(manifest, ())
        name = f"{slug}.parquet"
        matches = [item for item in manifest.files if Path(item.path).name == name]
        if not matches:
            return None, None
        if len(matches) != 1:
            raise ValueError(f"duplicate {name} in {manifest.relative_path}")
        ledger.add(manifest, matches)
        return catalog.raw_path(matches[0]), matches[0].sha256

    def _l2_for_market(
        self, path: Path, source_sha: str, asset: str, slug: str, start: datetime
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        tuple[str, datetime] | None,
    ]:
        available_columns = set(pq.read_schema(path).names)
        wanted = [
            name
            for name in (
                "timestamp", "local_timestamp", "event_type", "bid_prices", "bid_sizes",
                "ask_prices", "ask_sizes", "pc_price", "pc_size", "pc_side",
                "winning_outcome",
            )
            if name in available_columns
        ]
        raw_rows = pq.read_table(path, columns=wanted).to_pylist()
        normalized: list[dict[str, Any]] = []
        resolution: tuple[str, datetime] | None = None
        max_checkpoint = start + timedelta(milliseconds=max(self.checkpoint_offsets_ms))
        for ordinal, row in enumerate(raw_rows):
            times = pmdata_times(row.get("timestamp"), row.get("local_timestamp"))
            if times is None:
                continue
            event_time, available_at = times
            if row.get("event_type") == "market_resolved" and row.get("winning_outcome") is not None:
                candidate = (str(row["winning_outcome"]), available_at)
                if resolution is None or candidate[1] < resolution[1]:
                    resolution = candidate
            if row.get("event_type") not in {"book", "price_change"}:
                continue
            if event_time > max_checkpoint or available_at > max_checkpoint:
                continue
            normalized.append(
                {
                    "asset": asset,
                    "slug": slug,
                    "window_start": start,
                    "event_time": event_time,
                    "available_at": available_at,
                    "source_row_ordinal": ordinal,
                    "event_type": row.get("event_type"),
                    "bid_prices": row.get("bid_prices"),
                    "bid_sizes": row.get("bid_sizes"),
                    "ask_prices": row.get("ask_prices"),
                    "ask_sizes": row.get("ask_sizes"),
                    "pc_price_micros": _micros(row.get("pc_price")),
                    "pc_size": row.get("pc_size"),
                    "pc_side": row.get("pc_side"),
                    "source_file_sha256": source_sha,
                    # Reconstructor-compatible aliases.
                    "_event_time": event_time,
                    "_available_at": available_at,
                    "_row_ordinal": ordinal,
                    "pc_price": row.get("pc_price"),
                }
            )
        def checkpoint_row(
            offset: int, reconstructor: OrderBookReconstructor
        ) -> dict[str, Any]:
            checkpoint_at = start + timedelta(milliseconds=offset)
            snapshot = reconstructor.finish()
            return {
                "asset": asset,
                "slug": slug,
                "window_start": start,
                "checkpoint_offset_ms": offset,
                "checkpoint_at": checkpoint_at,
                "valid": snapshot.valid,
                "reason": snapshot.reason,
                "bid_prices_micros": [
                    price for price, _ in snapshot.bids[: self.depth_levels]
                ],
                "bid_sizes": [
                    size for _, size in snapshot.bids[: self.depth_levels]
                ],
                "ask_prices_micros": [
                    price for price, _ in snapshot.asks[: self.depth_levels]
                ],
                "ask_sizes": [
                    size for _, size in snapshot.asks[: self.depth_levels]
                ],
                "last_event_time": snapshot.event_time,
                "max_available_at": snapshot.available_at,
                "source_row_ordinal": snapshot.row_ordinal,
                "source_file_sha256": source_sha,
                "book_assumption_version": BOOK_ASSUMPTION_VERSION,
            }

        checkpoints: list[dict[str, Any]] = []
        for offset in self.checkpoint_offsets_ms:
            checkpoint_at = start + timedelta(milliseconds=offset)
            reconstructor = OrderBookReconstructor()
            for row in normalized:
                if (
                    row["event_time"] <= checkpoint_at
                    and row["available_at"] <= checkpoint_at
                ):
                    reconstructor.apply(row)
            checkpoints.append(checkpoint_row(offset, reconstructor))

        first_second = math.ceil(min(self.checkpoint_offsets_ms) / 1000) * 1000
        last_second = math.floor(max(self.checkpoint_offsets_ms) / 1000) * 1000
        second_offsets = range(first_second, last_second + 1, 1000)
        eligibility = [
            max(row["event_time"], row["available_at"]) for row in normalized
        ]
        monotonic = all(
            earlier <= later
            for earlier, later in zip(eligibility, eligibility[1:])
        )
        book_bars: list[dict[str, Any]] = []
        if monotonic:
            reconstructor = OrderBookReconstructor()
            cursor = 0
            for offset in second_offsets:
                checkpoint_at = start + timedelta(milliseconds=offset)
                while (
                    cursor < len(normalized)
                    and eligibility[cursor] <= checkpoint_at
                ):
                    reconstructor.apply(normalized[cursor])
                    cursor += 1
                book_bars.append(checkpoint_row(offset, reconstructor))
        else:
            # Preserve the assembler's source-order-after-causal-filter
            # semantics even when provider receive times move backwards.
            for offset in second_offsets:
                checkpoint_at = start + timedelta(milliseconds=offset)
                reconstructor = OrderBookReconstructor()
                for row in normalized:
                    if (
                        row["event_time"] <= checkpoint_at
                        and row["available_at"] <= checkpoint_at
                    ):
                        reconstructor.apply(row)
                book_bars.append(checkpoint_row(offset, reconstructor))
        for row in normalized:
            for key in ("_event_time", "_available_at", "_row_ordinal", "pc_price"):
                row.pop(key, None)
        return normalized, checkpoints, book_bars, resolution

    @staticmethod
    def _trade_bars(path: Path, source_sha: str, asset: str, slug: str) -> list[dict[str, Any]]:
        schema = set(pq.read_schema(path).names)
        wanted = [name for name in ("timestamp", "local_timestamp", "outcome", "side", "size", "price") if name in schema]
        buckets: dict[datetime, dict[str, Any]] = {}
        for ordinal, row in enumerate(pq.read_table(path, columns=wanted).to_pylist()):
            times = pmdata_times(row.get("timestamp"), row.get("local_timestamp"))
            if times is None:
                continue
            event_time, available_at = times
            bucket = event_time.replace(microsecond=0)
            target = buckets.setdefault(
                bucket,
                {
                    "asset": asset, "slug": slug, "bucket_start": bucket,
                    "bucket_end": bucket + timedelta(seconds=1),
                    "yes_buy_count": 0, "yes_sell_count": 0,
                    "no_buy_count": 0, "no_sell_count": 0,
                    "yes_buy_size": 0.0, "yes_sell_size": 0.0,
                    "no_buy_size": 0.0, "no_sell_size": 0.0,
                    "signed_up_pressure": 0.0, "trade_count": 0,
                    "total_size": 0.0, "notional": 0.0,
                    "last_event_time": event_time, "max_available_at": available_at,
                    "source_row_start": ordinal, "source_row_end": ordinal,
                    "source_file_sha256": source_sha,
                },
            )
            outcome = str(row.get("outcome") or "").lower()
            if outcome == "up":
                outcome = "yes"
            side = str(row.get("side") or "").upper()
            size = float(row.get("size") or 0.0)
            price = float(row.get("price") or 0.0)
            key = f"{outcome}_{side.lower()}"
            if key in {"yes_buy", "yes_sell", "no_buy", "no_sell"}:
                target[f"{key}_count"] += 1
                target[f"{key}_size"] += size
            target["signed_up_pressure"] += _TRADE_SIGNS.get((outcome, side), 0.0) * size
            target["trade_count"] += 1
            target["total_size"] += size
            target["notional"] += price * size
            target["last_event_time"] = max(target["last_event_time"], event_time)
            target["max_available_at"] = max(target["max_available_at"], available_at)
            target["source_row_end"] = ordinal
        return [buckets[key] for key in sorted(buckets)]

    def derive_market(self, asset: str, day: str, *, force: bool = False) -> DeriveResult:
        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        asset = asset.upper()
        if asset not in SERIES_IDS:
            raise ValueError(f"unsupported asset {asset}")
        date.fromisoformat(day)
        unit = self._unit("market_inputs", asset, day)
        gamma_manifest = self.raw.latest_complete(
            source="polymarket",
            dataset="market_metadata",
            partitions={"series_id": SERIES_IDS[asset]},
        )
        l2_manifest = self.raw.exact_unit(
            source="pmdata", dataset="poly_l2", partitions={"series": f"{asset.lower()}-5m"}, date=day
        )
        trade_manifest = self.raw.exact_unit(
            source="pmdata", dataset="poly_trade", partitions={"series": f"{asset.lower()}-5m"}, date=day
        )
        skipped = self._preflight(
            unit,
            (gamma_manifest, l2_manifest, trade_manifest),
            force=force,
        )
        if skipped is not None:
            return skipped
        ledger = InputLedger()
        gamma_rows, gamma_manifest = self._gamma_rows(
            asset, day, ledger, gamma_manifest
        )
        if l2_manifest is not None:
            ledger.add(l2_manifest, ())
        if trade_manifest is not None:
            ledger.add(trade_manifest, ())
        l2_events: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        book_bars: list[dict[str, Any]] = []
        trade_bars: list[dict[str, Any]] = []
        definitions: list[dict[str, Any]] = []
        resolutions: list[dict[str, Any]] = []
        missing_l2 = missing_trade = 0
        for row in gamma_rows:
            slug = str(row["slug"])
            start = row["_window_start"]
            try:
                tokens = json.loads(row.get("clob_token_ids_raw") or "[]")
            except json.JSONDecodeError:
                tokens = []
            definitions.append(
                {
                    "asset": asset,
                    "series_id": SERIES_IDS[asset],
                    "market_id": None if row.get("market_id") is None else str(row["market_id"]),
                    "condition_id": None if row.get("condition_id") is None else str(row["condition_id"]),
                    "slug": slug,
                    "up_token_id": str(tokens[0]) if len(tokens) == 2 else None,
                    "down_token_id": str(tokens[1]) if len(tokens) == 2 else None,
                    "window_start": start,
                    "window_end": start + timedelta(minutes=5),
                    "resolution_rule": row.get("event_description"),
                    "resolution_source": row.get("resolution_source"),
                    "source_manifest_sha256": gamma_manifest.sha256,
                }
            )
            l2_path, l2_sha = self._pmdata_file(l2_manifest, self.raw, ledger, slug)
            pm_resolution = None
            if l2_path is None or l2_sha is None:
                missing_l2 += 1
            else:
                events, states, second_states, pm_resolution = self._l2_for_market(
                    l2_path, l2_sha, asset, slug, start
                )
                l2_events.extend(events)
                checkpoints.extend(states)
                book_bars.extend(second_states)
            trade_path, trade_sha = self._pmdata_file(trade_manifest, self.raw, ledger, slug)
            if trade_path is None or trade_sha is None:
                missing_trade += 1
            else:
                trade_bars.extend(self._trade_bars(trade_path, trade_sha, asset, slug))
            gamma_raw = row.get("winning_outcome")
            gamma_outcome = None
            if gamma_raw is not None:
                gamma_outcome = "Up" if str(gamma_raw).lower() in {"up", "yes"} else "Down" if str(gamma_raw).lower() in {"down", "no"} else None
            pm_outcome = None
            pm_available = None
            if pm_resolution is not None:
                pm_outcome = "Up" if pm_resolution[0].lower() in {"up", "yes"} else "Down" if pm_resolution[0].lower() in {"down", "no"} else None
                pm_available = pm_resolution[1]
            disagreement = None
            confidence = "missing"
            if gamma_outcome and pm_outcome and gamma_outcome == pm_outcome:
                confidence = "gamma_official_snapshot_pmdata_corroborated"
            elif gamma_outcome and pm_outcome and gamma_outcome != pm_outcome:
                confidence = "disagreement"
                disagreement = "gamma_pmdata_resolution_disagreement"
            elif gamma_outcome:
                confidence = "gamma_snapshot_only"
            ingested = attach_utc(row.get("_ingested_at"))
            resolutions.append(
                {
                    "asset": asset, "slug": slug, "outcome": gamma_outcome,
                    "gamma_snapshot_ingested_at": ingested,
                    "pmdata_resolution_available_at": pm_available,
                    "pmdata_outcome": pm_outcome, "confidence": confidence,
                    "disagreement_reason": disagreement,
                }
            )
        tables = {
            "market_definitions.parquet": _rows(definitions, MARKET_SCHEMA),
            "book_checkpoints.parquet": _rows(checkpoints, BOOK_SCHEMA),
            "book_bars_1s.parquet": _rows(book_bars, BOOK_SCHEMA),
            "l2_premarket_events.parquet": _rows(l2_events, L2_EVENT_SCHEMA),
            "trade_bars_1s.parquet": _rows(trade_bars, TRADE_BAR_SCHEMA),
            "resolutions.parquet": _rows(resolutions, RESOLUTION_SCHEMA),
        }
        return self._commit(
            unit,
            tables,
            ledger,
            {
                "markets": len(gamma_rows),
                "missing_l2_markets": missing_l2,
                "missing_trade_markets": missing_trade,
                "l2_premarket_events": len(l2_events),
                "book_checkpoints": len(checkpoints),
                "book_bars_1s": len(book_bars),
                "trade_bars_1s": len(trade_bars),
            },
            force=force,
            started_at=started_at,
            elapsed_seconds=time.perf_counter() - started_clock,
        )

    @staticmethod
    def _canonical_bars(path: Path, manifest: ManifestRecord, dataset: str, symbol: str) -> list[dict[str, Any]]:
        result = []
        for row in pq.read_table(path).to_pylist():
            event_time, _ = binance_row_times(dataset, row, 0.0)
            open_time = epoch_to_utc(row["open_time"])
            result.append(
                {
                    "symbol": symbol, "dataset": dataset, "open_time": open_time,
                    "event_time": event_time, "base_available_at": event_time,
                    "open": row.get("open"), "high": row.get("high"),
                    "low": row.get("low"), "close": row.get("close"),
                    "open_micros": _micros(row.get("open")),
                    "high_micros": _micros(row.get("high")),
                    "low_micros": _micros(row.get("low")),
                    "close_micros": _micros(row.get("close")),
                    "volume": row.get("volume"), "quote_volume": row.get("quote_volume"),
                    "count": row.get("count"), "taker_buy_volume": row.get("taker_buy_volume"),
                    "taker_buy_quote_volume": row.get("taker_buy_quote_volume"),
                    "source_manifest_sha256": manifest.sha256,
                }
            )
        return result

    @staticmethod
    def _agg_bars(path: Path, manifest: ManifestRecord, symbol: str) -> list[dict[str, Any]]:
        buckets: dict[datetime, dict[str, Any]] = {}
        for row in pq.read_table(path).to_pylist():
            event_time = epoch_to_utc(row["transact_time"])
            bucket = event_time.replace(microsecond=0)
            price = float(row["price"])
            quantity = float(row["quantity"])
            quote = price * quantity
            buyer = not bool(row["is_buyer_maker"])
            target = buckets.setdefault(
                bucket,
                {
                    "symbol": symbol, "bucket_start": bucket,
                    "bucket_end": bucket + timedelta(seconds=1),
                    "buy_count": 0, "sell_count": 0,
                    "buy_base_volume": 0.0, "sell_base_volume": 0.0,
                    "buy_quote_volume": 0.0, "sell_quote_volume": 0.0,
                    "signed_base_flow": 0.0, "signed_quote_flow": 0.0,
                    "total_trade_count": 0, "total_base_volume": 0.0,
                    "total_quote_volume": 0.0, "vwap": 0.0,
                    "first_price": price, "last_price": price,
                    "high_price": price, "low_price": price,
                    "first_trade_id": int(row["agg_trade_id"]),
                    "last_trade_id": int(row["agg_trade_id"]),
                    "last_event_time": event_time, "base_available_at": event_time,
                    "source_manifest_sha256": manifest.sha256,
                },
            )
            side = "buy" if buyer else "sell"
            target[f"{side}_count"] += 1
            target[f"{side}_base_volume"] += quantity
            target[f"{side}_quote_volume"] += quote
            target["signed_base_flow"] += quantity if buyer else -quantity
            target["signed_quote_flow"] += quote if buyer else -quote
            target["total_trade_count"] += 1
            target["total_base_volume"] += quantity
            target["total_quote_volume"] += quote
            target["last_price"] = price
            target["high_price"] = max(target["high_price"], price)
            target["low_price"] = min(target["low_price"], price)
            target["last_trade_id"] = int(row["agg_trade_id"])
            target["last_event_time"] = max(target["last_event_time"], event_time)
            target["base_available_at"] = target["last_event_time"]
        for target in buckets.values():
            target["vwap"] = (
                target["total_quote_volume"] / target["total_base_volume"]
                if target["total_base_volume"] else math.nan
            )
        return [buckets[key] for key in sorted(buckets)]

    @staticmethod
    def _futures(path: Path, manifest: ManifestRecord, symbol: str) -> list[dict[str, Any]]:
        result = []
        for row in pq.read_table(path).to_pylist():
            event_time = attach_utc(row.get("create_time"))
            if event_time is None:
                continue
            result.append(
                {
                    "symbol": symbol, "event_time": event_time,
                    # The provider changed boundary conventions. Adding a full
                    # interval is safe for either start- or end-stamped rows.
                    "base_available_at": event_time + timedelta(minutes=5),
                    **{name: row.get(name) for name in (
                        "sum_open_interest", "sum_open_interest_value",
                        "count_toptrader_long_short_ratio",
                        "sum_toptrader_long_short_ratio", "count_long_short_ratio",
                        "sum_taker_long_short_vol_ratio",
                    )},
                    "availability_policy": FUTURES_AVAILABILITY_VERSION,
                    "source_manifest_sha256": manifest.sha256,
                }
            )
        return result

    def derive_underlying(self, asset: str, day: str, *, force: bool = False) -> DeriveResult:
        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        asset = asset.upper()
        date.fromisoformat(day)
        symbol = f"{asset}USDT"
        expected_manifests: list[ManifestRecord | None] = []
        for dataset, partitions in (
            ("spot_klines", {"symbol": symbol, "interval": "1m"}),
            ("premium_index_klines", {"symbol": symbol, "interval": "1m"}),
            ("agg_trades", {"symbol": symbol}),
            ("futures_metrics", {"symbol": symbol}),
        ):
            expected_manifests.append(
                self.raw.exact_unit(
                    source="binance",
                    dataset=dataset,
                    partitions=partitions,
                    date=day,
                )
            )
        unit = self._unit("underlying_inputs", asset, day)
        skipped = self._preflight(unit, expected_manifests, force=force)
        if skipped is not None:
            return skipped
        ledger = InputLedger()
        bar_rows: list[dict[str, Any]] = []
        agg_rows: list[dict[str, Any]] = []
        futures_rows: list[dict[str, Any]] = []
        missing: list[str] = []
        for dataset in ("spot_klines", "premium_index_klines"):
            manifest, path = self._manifest_file(
                self.raw, ledger, source="binance", dataset=dataset,
                partitions={"symbol": symbol, "interval": "1m"}, day=day,
            )
            if manifest is None or path is None:
                missing.append(dataset)
            else:
                bar_rows.extend(self._canonical_bars(path, manifest, dataset, symbol))
        manifest, path = self._manifest_file(
            self.raw, ledger, source="binance", dataset="agg_trades",
            partitions={"symbol": symbol}, day=day,
        )
        if manifest is None or path is None:
            missing.append("agg_trades")
        else:
            agg_rows.extend(self._agg_bars(path, manifest, symbol))
        manifest, path = self._manifest_file(
            self.raw, ledger, source="binance", dataset="futures_metrics",
            partitions={"symbol": symbol}, day=day,
        )
        if manifest is None or path is None:
            missing.append("futures_metrics")
        else:
            futures_rows.extend(self._futures(path, manifest, symbol))
        tables = {
            "bars.parquet": _rows(bar_rows, BAR_SCHEMA),
            "agg_trade_bars_1s.parquet": _rows(agg_rows, AGG_BAR_SCHEMA),
            "futures_metrics.parquet": _rows(futures_rows, FUTURES_SCHEMA),
        }
        return self._commit(
            unit,
            tables,
            ledger,
            {
                "bars": len(bar_rows),
                "agg_trade_bars_1s": len(agg_rows),
                "futures_metrics": len(futures_rows),
                "missing_sources": missing,
            },
            force=force,
            started_at=started_at,
            elapsed_seconds=time.perf_counter() - started_clock,
        )
