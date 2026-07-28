"""Generic manifest-backed selectors and row-bound point-in-time context."""

from __future__ import annotations

import os
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .availability import (
    BINANCE_POLICY_V1,
    PMDATA_POLICY_V1,
    binance_row_times,
    pmdata_times,
)
from .book import BOOK_ASSUMPTION_VERSION, BookSnapshot, reconstruct_book
from .manifest import FileRecord, InputLedger, ManifestCatalog, ManifestRecord

UTC = timezone.utc
FORBIDDEN_FEATURE_COLUMNS = frozenset(
    {"winning_outcome", "outcome_prices_raw", "uma_resolution_status", "resolution_source"}
)
PMDATA_DATASETS = frozenset({"poly_l2", "poly_trade"})
BINANCE_DATASETS = frozenset(
    {"spot_klines", "premium_index_klines", "agg_trades", "futures_metrics"}
)


def is_forbidden_feature_column(name: str) -> bool:
    lowered = name.lower()
    return name in FORBIDDEN_FEATURE_COLUMNS or lowered.startswith("winning_") or "resolution" in lowered


@dataclass(frozen=True)
class Selector:
    asset: str
    dataset: str
    slug: str | None = None


@dataclass(frozen=True)
class DataSelection:
    selector: Selector
    table: pa.Table
    policy: str
    null_reason: str | None = None
    manifests: tuple[str, ...] = ()

    @property
    def missing(self) -> bool:
        return self.null_reason is not None


class LakeDataSource:
    """Load only files named by complete JSON manifest entries.

    The small daily Binance tables are cached. PMData market tables use a small
    LRU as well so decision and execution snapshots do not reread one file.
    """

    def __init__(
        self,
        data_dir: Path | str,
        *,
        pmdata_policy: str = PMDATA_POLICY_V1,
        binance_policy: str = BINANCE_POLICY_V1,
        binance_delay_seconds: float = 0.0,
        catalog: ManifestCatalog | None = None,
        ledger: InputLedger | None = None,
        cache_entries: int = 12,
    ) -> None:
        self.catalog = catalog or ManifestCatalog(data_dir)
        self.derived_catalog = ManifestCatalog(
            Path(data_dir) / "derived" / "canonical"
        )
        self.ledger = ledger or InputLedger()
        self.pmdata_policy = pmdata_policy
        self.binance_policy = binance_policy
        self.binance_delay_seconds = binance_delay_seconds
        self.cache_entries = cache_entries
        self._raw_cache: OrderedDict[str, pa.Table] = OrderedDict()
        self._normalized_pmdata_cache: OrderedDict[str, pa.Table] = OrderedDict()
        self._derived_cache: OrderedDict[str, pa.Table] = OrderedDict()
        self._derived_rows_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._book_checkpoint_index: dict[
            tuple[str, str, str], dict[tuple[str, datetime], dict[str, Any]]
        ] = {}
        self._trade_bar_index: dict[
            tuple[str, str], dict[str, list[dict[str, Any]]]
        ] = {}
        self._l2_event_index: dict[
            tuple[str, str], dict[str, list[dict[str, Any]]]
        ] = {}
        self._book_bar_index: dict[
            tuple[str, str], dict[str, list[dict[str, Any]]]
        ] = {}
        self._resolution_index: dict[
            tuple[str, str], dict[str, tuple[str, datetime] | None]
        ] = {}

    @staticmethod
    def _retain_partition_date(
        index: dict[tuple[str, str], Any]
        | dict[tuple[str, str, str], Any],
        partition_date: str,
    ) -> None:
        """Keep only indexes for the active UTC market date.

        Candidate feature rows are assembled in chronological order, and every
        PMData/canonical market selector is scoped to the candidate market's
        partition date. Retaining indexes from older dates therefore cannot
        improve a later lookup; it only keeps millions of expanded Python rows
        alive after their bounded file-cache entries have been evicted.

        All assets for the active date remain resident so multi-asset builds do
        not thrash while candidates at the same timestamp are interleaved.
        """

        stale = [key for key in index if key[1] != partition_date]
        for key in stale:
            del index[key]

    def _derived_read(
        self, manifest: ManifestRecord, file: FileRecord
    ) -> pa.Table:
        path = self.derived_catalog.raw_path(file)
        key = "derived:" + os.fspath(path)
        self.ledger.add(manifest, (file,))
        cached = self._derived_cache.get(key)
        if cached is not None:
            self._derived_cache.move_to_end(key)
            return cached
        table = pq.read_table(path)
        self._derived_cache[key] = table
        self._derived_cache.move_to_end(key)
        while len(self._derived_cache) > self.cache_entries:
            self._derived_cache.popitem(last=False)
        return table

    def _derived_rows(
        self, manifest: ManifestRecord, file: FileRecord
    ) -> list[dict[str, Any]]:
        key = file.path
        cached = self._derived_rows_cache.get(key)
        if cached is not None:
            self._derived_rows_cache.move_to_end(key)
            return cached
        rows = self._derived_read(manifest, file).to_pylist()
        self._derived_rows_cache[key] = rows
        self._derived_rows_cache.move_to_end(key)
        while len(self._derived_rows_cache) > self.cache_entries:
            self._derived_rows_cache.popitem(last=False)
        return rows

    @staticmethod
    def _time_slice(
        rows: list[dict[str, Any]],
        *,
        key: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        values = [row[key] for row in rows]
        lower = bisect_left(values, start_at)
        upper = bisect_right(values, end_at)
        return rows[lower:upper]

    @staticmethod
    def _named_file(
        manifest: ManifestRecord, name: str
    ) -> FileRecord | None:
        matches = [item for item in manifest.files if Path(item.path).name == name]
        if len(matches) > 1:
            raise ValueError(f"duplicate {name} in {manifest.relative_path}")
        return matches[0] if matches else None

    def _derived_manifest(
        self, *, dataset: str, asset: str, date: str
    ) -> ManifestRecord | None:
        partitions = {"asset": asset.upper()}
        if dataset == "market_inputs":
            partitions["interval"] = "5m"
        return self.derived_catalog.exact_unit(
            source="canonical",
            dataset=dataset,
            partitions=partitions,
            date=date,
        )

    def book_at(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        partition_date: str,
        required_depth_levels: int = 1,
    ) -> tuple[BookSnapshot, DataSelection]:
        """Use an exact canonical checkpoint, otherwise reconstruct raw data."""
        if not selector.slug:
            raise ValueError("book selector requires a slug")
        manifest = self._derived_manifest(
            dataset="market_inputs", asset=selector.asset, date=partition_date
        )
        if manifest is not None:
            parameters = manifest.raw.get("parameters", {})
            compatible = (
                int(parameters.get("depth_levels", 0)) >= required_depth_levels
                and parameters.get("book_assumption_version")
                == BOOK_ASSUMPTION_VERSION
            )
            if compatible:
                row = None
                for name in (
                    "book_checkpoints.parquet",
                    "book_bars_1s.parquet",
                ):
                    file = self._named_file(manifest, name)
                    if file is None:
                        continue
                    index_key = (
                        selector.asset.upper(),
                        partition_date,
                        name,
                    )
                    self._retain_partition_date(
                        self._book_checkpoint_index,
                        partition_date,
                    )
                    if index_key not in self._book_checkpoint_index:
                        index: dict[
                            tuple[str, datetime], dict[str, Any]
                        ] = {}
                        for item in self._derived_rows(manifest, file):
                            key = (
                                str(item["slug"]),
                                item["checkpoint_at"],
                            )
                            if key in index:
                                raise ValueError(
                                    f"duplicate checkpoint {key} in "
                                    f"{manifest.relative_path}/{name}"
                                )
                            index[key] = item
                        self._book_checkpoint_index[index_key] = index
                    row = self._book_checkpoint_index[index_key].get(
                        (selector.slug, decision_at)
                    )
                    if row is not None:
                        break
                if row is not None:
                    snapshot = BookSnapshot(
                        valid=bool(row["valid"]),
                        reason=row.get("reason"),
                        bids=tuple(
                            zip(
                                row.get("bid_prices_micros") or [],
                                row.get("bid_sizes") or [],
                                strict=True,
                            )
                        ),
                        asks=tuple(
                            zip(
                                row.get("ask_prices_micros") or [],
                                row.get("ask_sizes") or [],
                                strict=True,
                            )
                        ),
                        event_time=row.get("last_event_time"),
                        available_at=row.get("max_available_at"),
                        row_ordinal=row.get("source_row_ordinal"),
                    )
                    evidence = pa.table(
                        {
                            "_event_time": pa.array(
                                [snapshot.event_time],
                                pa.timestamp("us", tz="UTC"),
                            ),
                            "_available_at": pa.array(
                                [snapshot.available_at],
                                pa.timestamp("us", tz="UTC"),
                            ),
                            "_row_ordinal": pa.array(
                                [snapshot.row_ordinal], pa.int64()
                            ),
                        }
                    )
                    return snapshot, DataSelection(
                        selector,
                        evidence,
                        self.pmdata_policy,
                        None,
                        (manifest.relative_path,),
                    )
        selection = self.select(
            selector,
            decision_at=decision_at,
            partition_date=partition_date,
        )
        return reconstruct_book(selection.table), selection

    def book_history(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        start_at: datetime,
        partition_date: str,
        required_depth_levels: int = 1,
    ) -> DataSelection:
        """Return causal one-second reconstructed states from the canonical zone."""
        if not selector.slug:
            raise ValueError("book-history selector requires a slug")
        manifest = self._derived_manifest(
            dataset="market_inputs", asset=selector.asset, date=partition_date
        )
        if manifest is None:
            return DataSelection(
                selector,
                self._empty(),
                self.pmdata_policy,
                "missing_canonical_market_manifest",
            )
        parameters = manifest.raw.get("parameters", {})
        if (
            int(parameters.get("depth_levels", 0)) < required_depth_levels
            or parameters.get("book_assumption_version")
            != BOOK_ASSUMPTION_VERSION
        ):
            return DataSelection(
                selector,
                self._empty(),
                self.pmdata_policy,
                "incompatible_canonical_book_history",
                (manifest.relative_path,),
            )
        file = self._named_file(manifest, "book_bars_1s.parquet")
        if file is None:
            return DataSelection(
                selector,
                self._empty(),
                self.pmdata_policy,
                "missing_canonical_book_bars",
                (manifest.relative_path,),
            )
        index_key = (selector.asset.upper(), partition_date)
        self._retain_partition_date(
            self._book_bar_index,
            partition_date,
        )
        if index_key not in self._book_bar_index:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in self._derived_rows(manifest, file):
                grouped.setdefault(str(item["slug"]), []).append(item)
            for items in grouped.values():
                items.sort(key=lambda item: item["checkpoint_at"])
            self._book_bar_index[index_key] = grouped
        market_rows = self._book_bar_index[index_key].get(selector.slug, [])
        candidate_rows = self._time_slice(
            market_rows,
            key="checkpoint_at",
            start_at=start_at,
            end_at=decision_at,
        )
        rows: list[dict[str, Any]] = []
        for source_row in candidate_rows:
            row = dict(source_row)
            event_time = row.get("last_event_time")
            available_at = row.get("max_available_at")
            if (
                event_time is None
                or available_at is None
                or event_time > decision_at
                or available_at > decision_at
            ):
                continue
            row["_event_time"] = event_time
            row["_available_at"] = available_at
            row["_row_ordinal"] = row.get("source_row_ordinal")
            rows.append(row)
        return DataSelection(
            selector,
            pa.Table.from_pylist(rows) if rows else self._empty(),
            self.pmdata_policy,
            None if rows else "no_causal_book_history",
            (manifest.relative_path,),
        )

    def _select_derived_market_bars(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        start_at: datetime | None,
        partition_date: str,
    ) -> DataSelection | None:
        if selector.dataset not in {"poly_l2", "poly_trade"} or not selector.slug:
            return None
        manifest = self._derived_manifest(
            dataset="market_inputs", asset=selector.asset, date=partition_date
        )
        if manifest is None:
            return None
        file_name = (
            "l2_premarket_events.parquet"
            if selector.dataset == "poly_l2"
            else "trade_bars_1s.parquet"
        )
        file = self._named_file(manifest, file_name)
        if file is None:
            return None
        index_key = (selector.asset.upper(), partition_date)
        index = (
            self._l2_event_index
            if selector.dataset == "poly_l2"
            else self._trade_bar_index
        )
        self._retain_partition_date(index, partition_date)
        if index_key not in index:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in self._derived_rows(manifest, file):
                grouped.setdefault(str(item["slug"]), []).append(item)
            for items in grouped.values():
                items.sort(
                    key=lambda item: item[
                        "event_time"
                        if selector.dataset == "poly_l2"
                        else "last_event_time"
                    ]
                )
            index[index_key] = grouped
        market_rows = index[index_key].get(selector.slug, [])
        event_key = (
            "event_time"
            if selector.dataset == "poly_l2"
            else "last_event_time"
        )
        candidate_rows = self._time_slice(
            market_rows,
            key=event_key,
            start_at=start_at or datetime.min.replace(tzinfo=UTC),
            end_at=decision_at,
        )
        rows: list[dict[str, Any]] = []
        for source_row in candidate_rows:
            row = dict(source_row)
            if selector.dataset == "poly_l2":
                event_time = row.get("event_time")
                available_at = row.get("available_at")
                row_ordinal = row.get("source_row_ordinal")
            else:
                event_time = row.get("last_event_time")
                available_at = row.get("max_available_at")
                row_ordinal = row.get("source_row_end")
            if event_time is None or available_at is None:
                continue
            if event_time > decision_at or available_at > decision_at:
                continue
            row["_event_time"] = event_time
            row["_available_at"] = available_at
            row["_row_ordinal"] = row_ordinal
            rows.append(row)
        table = pa.Table.from_pylist(rows) if rows else self._empty()
        return DataSelection(
            selector,
            table,
            self.pmdata_policy,
            None,
            (manifest.relative_path,),
        )

    def _select_derived_binance(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        start_at: datetime | None,
    ) -> DataSelection | None:
        start_at = start_at or decision_at - timedelta(days=1)
        manifests: list[ManifestRecord] = []
        for day in self._days(start_at, decision_at):
            manifest = self._derived_manifest(
                dataset="underlying_inputs", asset=selector.asset, date=day
            )
            if manifest is None:
                return None
            manifests.append(manifest)
        file_name = {
            "spot_klines": "bars.parquet",
            "premium_index_klines": "bars.parquet",
            "agg_trades": "agg_trade_bars_1s.parquet",
            "futures_metrics": "futures_metrics.parquet",
        }[selector.dataset]
        rows: list[dict[str, Any]] = []
        used: list[str] = []
        ordinal = 0
        for manifest in manifests:
            file = self._named_file(manifest, file_name)
            if file is None:
                return None
            used.append(manifest.relative_path)
            source_rows = self._derived_rows(manifest, file)
            if selector.dataset in {"spot_klines", "premium_index_klines"}:
                source_rows = [
                    row
                    for row in source_rows
                    if row.get("dataset") == selector.dataset
                ]
                time_key = "event_time"
            elif selector.dataset == "agg_trades":
                time_key = "last_event_time"
            else:
                time_key = "event_time"
            source_rows = self._time_slice(
                source_rows,
                key=time_key,
                start_at=start_at,
                end_at=decision_at,
            )
            for source_row in source_rows:
                row = dict(source_row)
                event_time = row.get(time_key)
                base_available = row.get("base_available_at")
                if event_time is None or base_available is None:
                    continue
                available_at = base_available + timedelta(
                    seconds=self.binance_delay_seconds
                )
                if (
                    event_time <= decision_at
                    and available_at <= decision_at
                    and event_time >= start_at
                ):
                    row["_event_time"] = event_time
                    row["_available_at"] = available_at
                    row["_row_ordinal"] = ordinal
                    rows.append(row)
                ordinal += 1
        rows.sort(
            key=lambda row: (
                row["_event_time"],
                row["_available_at"],
                row["_row_ordinal"],
            )
        )
        return DataSelection(
            selector,
            pa.Table.from_pylist(rows) if rows else self._empty(),
            self.binance_policy,
            None if rows else "no_rows_available_by_decision",
            tuple(used),
        )

    def _cached_read(self, manifest: ManifestRecord, file: FileRecord) -> pa.Table:
        path = self.catalog.raw_path(file)
        key = os.fspath(path)
        self.ledger.add(manifest, (file,))
        cached = self._raw_cache.get(key)
        if cached is not None:
            self._raw_cache.move_to_end(key)
            return cached
        table = pq.read_table(path)
        self._raw_cache[key] = table
        self._raw_cache.move_to_end(key)
        while len(self._raw_cache) > self.cache_entries:
            self._raw_cache.popitem(last=False)
        return table

    def _normalized_pmdata(
        self, manifest: ManifestRecord, file: FileRecord
    ) -> pa.Table:
        key = file.path
        cached = self._normalized_pmdata_cache.get(key)
        if cached is not None:
            self._normalized_pmdata_cache.move_to_end(key)
            return cached
        raw = self._cached_read(manifest, file)
        required = {"timestamp", "local_timestamp"}
        if not required.issubset(raw.column_names):
            missing = sorted(required - set(raw.column_names))
            raise ValueError(f"{file.path} missing PMData timestamp columns {missing}")
        rows: list[dict[str, Any]] = []
        for ordinal, source_row in enumerate(raw.to_pylist()):
            times = pmdata_times(
                source_row.get("timestamp"), source_row.get("local_timestamp")
            )
            if times is None:
                continue
            event_time, available_at = times
            projected = {
                name: value
                for name, value in source_row.items()
                if not is_forbidden_feature_column(name)
            }
            projected["_event_time"] = event_time
            projected["_available_at"] = available_at
            projected["_row_ordinal"] = ordinal
            rows.append(projected)
        table = pa.Table.from_pylist(rows) if rows else self._empty()
        self._normalized_pmdata_cache[key] = table
        self._normalized_pmdata_cache.move_to_end(key)
        while len(self._normalized_pmdata_cache) > self.cache_entries:
            self._normalized_pmdata_cache.popitem(last=False)
        return table

    @staticmethod
    def _empty() -> pa.Table:
        return pa.table(
            {
                "_event_time": pa.array([], pa.timestamp("us", tz="UTC")),
                "_available_at": pa.array([], pa.timestamp("us", tz="UTC")),
                "_row_ordinal": pa.array([], pa.int64()),
            }
        )

    def _pmdata_manifest(self, selector: Selector, partition_date: str) -> tuple[ManifestRecord | None, FileRecord | None, str | None]:
        series = f"{selector.asset.lower()}-5m"
        manifest = self.catalog.exact_unit(
            source="pmdata",
            dataset=selector.dataset,
            partitions={"series": series},
            date=partition_date,
        )
        if manifest is None:
            return None, None, "missing_complete_manifest"
        # The manifest checksum proves both selected files and intentional holes.
        self.ledger.add(manifest, ())
        expected = f"{selector.slug}.parquet"
        matches = [record for record in manifest.files if Path(record.path).name == expected]
        if not matches:
            return manifest, None, "file_not_in_complete_manifest"
        if len(matches) != 1:
            raise ValueError(f"duplicate {expected} entries in {manifest.relative_path}")
        return manifest, matches[0], None

    def _select_pmdata(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        start_at: datetime | None,
        partition_date: str,
    ) -> DataSelection:
        if self.pmdata_policy != PMDATA_POLICY_V1:
            raise ValueError(f"unsupported PMData availability policy {self.pmdata_policy!r}")
        if not selector.slug:
            raise ValueError("PMData selector requires a market slug")
        derived = self._select_derived_market_bars(
            selector,
            decision_at=decision_at,
            start_at=start_at,
            partition_date=partition_date,
        )
        if derived is not None:
            return derived
        manifest, file, reason = self._pmdata_manifest(selector, partition_date)
        if reason:
            manifests = () if manifest is None else (manifest.relative_path,)
            return DataSelection(
                selector, self._empty(), self.pmdata_policy, reason, manifests
            )
        assert manifest is not None and file is not None
        normalized = self._normalized_pmdata(manifest, file)
        if normalized.num_rows:
            event_us = normalized["_event_time"].combine_chunks().cast(pa.int64()).to_numpy()
            available_us = normalized["_available_at"].combine_chunks().cast(pa.int64()).to_numpy()
            decision_us = int(decision_at.timestamp() * 1_000_000)
            keep = (available_us <= decision_us) & (event_us <= decision_us)
            if start_at is not None:
                start_us = int(start_at.timestamp() * 1_000_000)
                keep &= event_us >= start_us
            table = normalized.filter(pa.array(np.asarray(keep, dtype=bool)))
        else:
            table = normalized
        forbidden = [name for name in table.column_names if is_forbidden_feature_column(name)]
        if forbidden:
            raise AssertionError(f"feature projection contains forbidden columns: {forbidden}")
        return DataSelection(
            selector,
            table,
            self.pmdata_policy,
            None,
            (manifest.relative_path,),
        )

    def resolution_observation(
        self, *, asset: str, slug: str, partition_date: str
    ) -> tuple[str, datetime] | None:
        """Read resolution only on the label path, never through a feature context."""
        derived = self._derived_manifest(
            dataset="market_inputs", asset=asset, date=partition_date
        )
        if derived is not None:
            file = self._named_file(derived, "resolutions.parquet")
            if file is not None:
                index_key = (asset.upper(), partition_date)
                self._retain_partition_date(
                    self._resolution_index,
                    partition_date,
                )
                if index_key not in self._resolution_index:
                    index: dict[str, tuple[str, datetime] | None] = {}
                    for row in self._derived_read(derived, file).to_pylist():
                        outcome = row.get("pmdata_outcome")
                        available_at = row.get(
                            "pmdata_resolution_available_at"
                        )
                        index[str(row["slug"])] = (
                            (str(outcome), available_at)
                            if outcome is not None and available_at is not None
                            else None
                        )
                    self._resolution_index[index_key] = index
                observation = self._resolution_index[index_key].get(slug)
                if observation is not None:
                    return observation
        selector = Selector(asset=asset.upper(), dataset="poly_l2", slug=slug)
        manifest, file, reason = self._pmdata_manifest(selector, partition_date)
        if reason or manifest is None or file is None:
            return None
        raw = self._cached_read(manifest, file)
        required = {"timestamp", "local_timestamp", "event_type", "winning_outcome"}
        if not required.issubset(raw.column_names):
            return None
        observations: list[tuple[datetime, str]] = []
        for row in raw.to_pylist():
            if str(row.get("event_type") or "") != "market_resolved":
                continue
            times = pmdata_times(row.get("timestamp"), row.get("local_timestamp"))
            outcome = row.get("winning_outcome")
            if times is None or outcome is None:
                continue
            observations.append((times[1], str(outcome)))
        if not observations:
            return None
        available_at, outcome = min(observations, key=lambda item: item[0])
        return outcome, available_at

    @staticmethod
    def _days(start_at: datetime, decision_at: datetime) -> list[str]:
        day = start_at.astimezone(UTC).date()
        end = decision_at.astimezone(UTC).date()
        result: list[str] = []
        while day <= end:
            result.append(day.isoformat())
            day += timedelta(days=1)
        return result

    def _select_binance(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        start_at: datetime | None,
    ) -> DataSelection:
        if self.binance_policy != BINANCE_POLICY_V1:
            raise ValueError(f"unsupported Binance availability policy {self.binance_policy!r}")
        derived = self._select_derived_binance(
            selector, decision_at=decision_at, start_at=start_at
        )
        if derived is not None:
            return derived
        start_at = start_at or decision_at - timedelta(days=1)
        symbol = f"{selector.asset.upper()}USDT"
        partitions = {"symbol": symbol}
        if selector.dataset in {"spot_klines", "premium_index_klines"}:
            partitions["interval"] = "1m"
        tables: list[tuple[ManifestRecord, pa.Table]] = []
        missing_days: list[str] = []
        for day in self._days(start_at, decision_at):
            manifest = self.catalog.exact_unit(
                source="binance",
                dataset=selector.dataset,
                partitions=partitions,
                date=day,
            )
            if manifest is None:
                missing_days.append(day)
                continue
            if len(manifest.files) != 1:
                raise ValueError(f"expected one file in {manifest.relative_path}")
            tables.append((manifest, self._cached_read(manifest, manifest.files[0])))
        if not tables:
            return DataSelection(selector, self._empty(), self.binance_policy, "missing_complete_manifest")
        # Missing units are holes, not permission to discard rows from complete
        # units. Feature minimum-observation rules decide whether the partial
        # lookback is usable.
        rows: list[dict[str, Any]] = []
        ordinal = 0
        used: list[str] = []
        for manifest, table in tables:
            used.append(manifest.relative_path)
            for source_row in table.to_pylist():
                try:
                    event_time, available_at = binance_row_times(
                        selector.dataset, source_row, self.binance_delay_seconds
                    )
                except (TypeError, ValueError):
                    ordinal += 1
                    continue
                if event_time <= decision_at and available_at <= decision_at and event_time >= start_at:
                    source_row["_event_time"] = event_time
                    source_row["_available_at"] = available_at
                    source_row["_row_ordinal"] = ordinal
                    rows.append(source_row)
                ordinal += 1
        rows.sort(key=lambda row: (row["_event_time"], row["_available_at"], row["_row_ordinal"]))
        table = pa.Table.from_pylist(rows) if rows else self._empty()
        reason = None if rows else (
            "missing_complete_manifest:" + ",".join(missing_days)
            if missing_days
            else "no_rows_available_by_decision"
        )
        return DataSelection(selector, table, self.binance_policy, reason, tuple(used))

    def select(
        self,
        selector: Selector,
        *,
        decision_at: datetime,
        start_at: datetime | None = None,
        partition_date: str | None = None,
    ) -> DataSelection:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        decision_at = decision_at.astimezone(UTC)
        start_at = start_at.astimezone(UTC) if start_at is not None else None
        if selector.dataset in PMDATA_DATASETS:
            if partition_date is None:
                raise ValueError("PMData selector requires partition_date")
            return self._select_pmdata(
                selector,
                decision_at=decision_at,
                start_at=start_at,
                partition_date=partition_date,
            )
        if selector.dataset in BINANCE_DATASETS:
            return self._select_binance(selector, decision_at=decision_at, start_at=start_at)
        raise KeyError(f"no point-in-time selector implementation for dataset {selector.dataset!r}")


@dataclass
class PointInTimeContext:
    """Feature API bound to one decision; selectors may name any asset/dataset."""

    source: LakeDataSource
    decision_at: datetime
    primary_asset: str
    slug: str
    partition_date: str
    memo: dict[str, Any] = field(default_factory=dict)
    _selections: dict[tuple[str, str, float | None], DataSelection] = field(default_factory=dict)

    def select(
        self,
        dataset: str,
        *,
        asset: str | None = None,
        lookback_seconds: float | None = None,
    ) -> DataSelection:
        asset = (asset or self.primary_asset).upper()
        key = (asset, dataset, lookback_seconds)
        if key not in self._selections:
            start_at = None if lookback_seconds is None else self.decision_at - timedelta(seconds=lookback_seconds)
            slug = None
            if dataset in PMDATA_DATASETS:
                if asset == self.primary_asset:
                    slug = self.slug
                else:
                    parts = self.slug.split("-", 1)
                    if len(parts) != 2:
                        raise ValueError(f"cannot derive cross-asset slug from {self.slug!r}")
                    slug = f"{asset.lower()}-{parts[1]}"
            selection = self.source.select(
                Selector(asset=asset, dataset=dataset, slug=slug),
                decision_at=self.decision_at,
                start_at=start_at,
                partition_date=self.partition_date,
            )
            forbidden = [name for name in selection.table.column_names if is_forbidden_feature_column(name)]
            if forbidden:
                raise AssertionError(f"point-in-time feature table leaked columns {forbidden}")
            self._selections[key] = selection
        return self._selections[key]

    def book_history(
        self,
        *,
        lookback_seconds: float,
        required_depth_levels: int = 1,
    ) -> DataSelection:
        key = (
            self.primary_asset.upper(),
            f"poly_l2_book_bars_depth_{required_depth_levels}",
            lookback_seconds,
        )
        if key not in self._selections:
            selection = self.source.book_history(
                Selector(
                    asset=self.primary_asset,
                    dataset="poly_l2",
                    slug=self.slug,
                ),
                decision_at=self.decision_at,
                start_at=self.decision_at
                - timedelta(seconds=lookback_seconds),
                partition_date=self.partition_date,
                required_depth_levels=required_depth_levels,
            )
            self._selections[key] = selection
        return self._selections[key]

    def selected(self) -> tuple[DataSelection, ...]:
        return tuple(self._selections.values())
