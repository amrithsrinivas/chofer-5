"""End-to-end manifest-driven point-in-time dataset builder."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm.auto import tqdm

from .artifact import commit_dataset, stable_hash
from .availability import BINANCE_POLICY_V1, PMDATA_POLICY_V1
from .book import (
    BOOK_ASSUMPTION_VERSION,
    DOWN_COMPLEMENT_VERSION,
    quote_fields,
)
from .features import FeatureRegistry, builtin_registry
from .manifest import InputLedger, ManifestCatalog, ManifestRecord
from .source import LakeDataSource, PointInTimeContext, Selector
from .spec import Asset, DatasetSpec, SourceSemantics, load_dataset_spec

UTC = timezone.utc
ASSEMBLER_IMPLEMENTATION_VERSION = "manifest_pit_assembler_v4"
SERIES_IDS = {Asset.BTC: "10684", Asset.ETH: "10683", Asset.SOL: "10686", Asset.XRP: "10685"}
_SLUG = re.compile(r"^(btc|eth|sol|xrp)-updown-5m-(\d+)$")
_LABELS = {"up": ("Up", 1), "yes": ("Up", 1), "down": ("Down", 0), "no": ("Down", 0)}


@dataclass(frozen=True)
class Candidate:
    asset: str
    series_id: str
    market_id: str | None
    condition_id: str | None
    up_token_id: str | None
    down_token_id: str | None
    slug: str
    window_start: datetime
    window_end: datetime
    schedule: str
    decision_at: datetime


@dataclass(frozen=True)
class BuildResult:
    dataset_id: str
    path: Path
    row_count: int


@dataclass(frozen=True)
class LabelInfo:
    outcome: str | None
    binary: int | None
    null_reason: str | None
    available_at: datetime | None
    confidence: str


def _source_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"metadata timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(UTC)


def _gamma_file(catalog: ManifestCatalog, ledger: InputLedger, asset: Asset) -> tuple[ManifestRecord, Path]:
    manifest = catalog.latest_complete(
        source="polymarket",
        dataset="market_metadata",
        partitions={"series_id": SERIES_IDS[asset]},
    )
    if len(manifest.files) != 1:
        raise ValueError(f"expected one metadata file in {manifest.relative_path}")
    ledger.add(manifest)
    return manifest, catalog.raw_path(manifest.files[0])


def _tokens(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, str):
        return None, None
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(values, list) or len(values) != 2:
        return None, None
    return str(values[0]), str(values[1])


class DatasetAssembler:
    def __init__(
        self,
        spec: DatasetSpec,
        *,
        data_dir: Path | str,
        output_dir: Path | str | None = None,
        registry: FeatureRegistry | None = None,
    ) -> None:
        self.spec = spec
        self.output_dir = Path(output_dir or spec.output_path)
        self.registry = registry or builtin_registry()
        self.feature_definitions = self.registry.definitions(spec.features)
        self.ledger = InputLedger()
        self.catalog = ManifestCatalog(data_dir)
        pmdata = spec.availability_policies["pmdata"]
        binance = spec.availability_policies.get("binance")
        if pmdata.version != PMDATA_POLICY_V1:
            raise ValueError(f"unsupported PMData policy {pmdata.version!r}")
        if self._uses_binance() and binance is None:
            raise ValueError("availability_policies.binance is required by selected features")
        if binance is not None and binance.version != BINANCE_POLICY_V1:
            raise ValueError(f"unsupported Binance policy {binance.version!r}")
        self.source = LakeDataSource(
            data_dir,
            pmdata_policy=pmdata.version,
            binance_policy=binance.version if binance else BINANCE_POLICY_V1,
            binance_delay_seconds=binance.delay_seconds if binance else 0.0,
            catalog=self.catalog,
            ledger=self.ledger,
        )
        self._validate_source_semantics()

    def _uses_binance(self) -> bool:
        return any(
            token in name
            for name in self.spec.features
            for token in (
                "spot_return",
                "realized_vol",
                "premium_",
                "agg_trade",
                "open_interest",
                "long_short",
            )
        )

    def _used_sources(self) -> set[str]:
        used = {"poly_l2"}
        if any(
            "trade_pressure" in name
            or "trade_imbalance" in name
            or name.startswith("polymarket_trade_")
            or name == "net_trade_pressure"
            for name in self.spec.features
        ):
            used.add("poly_trade")
        if any("spot_return" in name or "realized_vol" in name for name in self.spec.features):
            used.add("spot_klines")
        if any("premium_" in name for name in self.spec.features):
            used.add("premium_index_klines")
        if any("agg_trade" in name for name in self.spec.features):
            used.add("agg_trades")
        if any("open_interest" in name or "long_short" in name for name in self.spec.features):
            used.add("futures_metrics")
        return used

    def _validate_source_semantics(self) -> None:
        missing = sorted(self._used_sources() - set(self.spec.sources))
        if missing:
            raise ValueError(f"required/optional semantics missing for selected sources: {missing}")

    def enumerate_candidates(self, *, limit: int | None = None) -> tuple[list[Candidate], dict[str, ManifestRecord]]:
        candidates: list[Candidate] = []
        manifests: dict[str, ManifestRecord] = {}
        seen: set[tuple[str, str, str]] = set()
        for asset in self.spec.assets:
            manifest, path = _gamma_file(self.catalog, self.ledger, asset)
            manifests[asset.value] = manifest
            required = [
                "series_id",
                "market_id",
                "condition_id",
                "slug",
                "outcomes_raw",
                "outcomes_count",
                "clob_token_ids_raw",
                "clob_token_ids_count",
                "end_date",
            ]
            schema = pq.read_schema(path)
            missing_columns = sorted(set(required) - set(schema.names))
            if missing_columns:
                raise ValueError(f"{manifest.relative_path} missing metadata columns {missing_columns}")
            table = pq.read_table(path, columns=required)
            for row in table.to_pylist():
                slug = row.get("slug")
                match = _SLUG.fullmatch(str(slug or ""))
                if not match or match.group(1).upper() != asset.value:
                    continue
                epoch = int(match.group(2))
                if not 10**9 <= epoch < 10**11 or epoch % 300:
                    continue
                window_start = datetime.fromtimestamp(epoch, tz=UTC)
                if not self.spec.time_range.start <= window_start < self.spec.time_range.end:
                    continue
                try:
                    window_end = _utc(str(row["end_date"]))
                    outcomes = json.loads(row["outcomes_raw"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if window_end != window_start + timedelta(minutes=5):
                    continue
                if row.get("outcomes_count") != 2 or row.get("clob_token_ids_count") != 2:
                    continue
                if not isinstance(outcomes, list) or [str(item).lower() for item in outcomes] != ["up", "down"]:
                    continue
                up_token, down_token = _tokens(row.get("clob_token_ids_raw"))
                if up_token is None or down_token is None:
                    continue
                for schedule in self.spec.decision_schedules:
                    anchor = window_start if schedule.anchor == "window_start" else window_end
                    decision_at = anchor + timedelta(seconds=schedule.offset_seconds)
                    identity = (asset.value, slug, schedule.name)
                    if identity in seen:
                        raise ValueError(f"duplicate candidate identity {identity}")
                    seen.add(identity)
                    candidates.append(
                        Candidate(
                            asset=asset.value,
                            series_id=SERIES_IDS[asset],
                            market_id=None if row.get("market_id") is None else str(row["market_id"]),
                            condition_id=None if row.get("condition_id") is None else str(row["condition_id"]),
                            up_token_id=up_token,
                            down_token_id=down_token,
                            slug=str(slug),
                            window_start=window_start,
                            window_end=window_end,
                            schedule=schedule.name,
                            decision_at=decision_at,
                        )
                    )
        candidates.sort(key=lambda row: (row.window_start, row.asset, row.schedule, row.slug))
        if limit is not None:
            candidates = candidates[:limit]
        return candidates, manifests

    def _source_required(self, dataset: str) -> bool:
        return self.spec.sources.get(dataset) == SourceSemantics.REQUIRED

    def _assemble_feature_rows(self, candidates: list[Candidate]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        for candidate in tqdm(
            candidates,
            total=len(candidates),
            desc="Assembling feature rows",
            unit="market",
            dynamic_ncols=True,
            disable=None,
        ):
            execution_at = candidate.decision_at + timedelta(seconds=self.spec.execution_delay_seconds)
            partition_date = candidate.window_start.date().isoformat()
            decision = PointInTimeContext(
                source=self.source,
                decision_at=candidate.decision_at,
                primary_asset=candidate.asset,
                slug=candidate.slug,
                partition_date=partition_date,
            )
            execution = PointInTimeContext(
                source=self.source,
                decision_at=execution_at,
                primary_asset=candidate.asset,
                slug=candidate.slug,
                partition_date=partition_date,
            )
            decision_book, decision_l2 = self.source.book_at(
                Selector(
                    asset=candidate.asset,
                    dataset="poly_l2",
                    slug=candidate.slug,
                ),
                decision_at=candidate.decision_at,
                partition_date=partition_date,
                required_depth_levels=self.spec.quote.depth_levels,
            )
            execution_book, execution_l2 = self.source.book_at(
                Selector(
                    asset=candidate.asset,
                    dataset="poly_l2",
                    slug=candidate.slug,
                ),
                decision_at=execution_at,
                partition_date=partition_date,
                required_depth_levels=self.spec.quote.depth_levels,
            )
            decision.memo["decision_book"] = decision_book
            execution.memo["decision_book"] = execution_book
            decision_quote = quote_fields(
                decision_book,
                at=candidate.decision_at,
                max_staleness=self.spec.quote.max_staleness_seconds,
                band_min=self.spec.quote.band_min,
                band_max=self.spec.quote.band_max,
            )
            execution_quote = quote_fields(
                execution_book,
                at=execution_at,
                max_staleness=self.spec.quote.max_staleness_seconds,
                band_min=self.spec.quote.band_min,
                band_max=self.spec.quote.band_max,
            )
            row_id = stable_hash(
                {
                    "asset": candidate.asset,
                    "slug": candidate.slug,
                    "schedule": candidate.schedule,
                    "decision_at": candidate.decision_at,
                }
            )[:32]
            row: dict[str, Any] = {
                "row_id": row_id,
                "asset": candidate.asset,
                "series_id": candidate.series_id,
                "market_id": candidate.market_id,
                "condition_id": candidate.condition_id,
                "up_token_id": candidate.up_token_id,
                "down_token_id": candidate.down_token_id,
                "slug": candidate.slug,
                "window_start": candidate.window_start,
                "window_end": candidate.window_end,
                "decision_schedule": candidate.schedule,
                "decision_at": candidate.decision_at,
                "execution_at": execution_at,
            }
            for prefix, quote in (("decision", decision_quote), ("execution", execution_quote)):
                for name in ("up_bid", "up_ask", "down_bid", "down_ask", "up_bid_micros", "up_ask_micros", "down_bid_micros", "down_ask_micros", "quote_staleness_seconds", "quote_valid", "quote_reasons"):
                    row[f"{prefix}_{name}"] = quote[name]
            exclusion_reasons = [f"decision_quote:{reason}" for reason in decision_quote["quote_reasons"]]
            if decision_l2.missing and self._source_required("poly_l2"):
                exclusion_reasons.append(f"required_source:poly_l2:{decision_l2.null_reason}")
            candidate_args = {"depth_levels": self.spec.quote.depth_levels}
            for definition in self.feature_definitions:
                result = definition.compute(decision, candidate_args)
                if result.available_at is not None and result.available_at > candidate.decision_at:
                    raise AssertionError(
                        f"feature {definition.name} used future availability {result.available_at} > {candidate.decision_at}"
                    )
                row[definition.name] = result.value
                evidence.append(
                    {
                        "row_id": row_id,
                        "feature": definition.name,
                        "event_time": result.event_time,
                        "available_at": result.available_at,
                        "age_seconds": None if result.event_time is None else max(0.0, (candidate.decision_at - result.event_time).total_seconds()),
                        "null_reason": result.null_reason,
                        "policy": result.policy,
                    }
                )
            for selection in decision.selected():
                if selection.missing and self._source_required(selection.selector.dataset):
                    exclusion_reasons.append(
                        f"required_source:{selection.selector.dataset}:{selection.null_reason}"
                    )
            row["market_midprice"] = (
                None
                if decision_quote["up_bid"] is None or decision_quote["up_ask"] is None
                else (decision_quote["up_bid"] + decision_quote["up_ask"]) / 2.0
            )
            row["quote_valid"] = bool(decision_quote["quote_valid"])
            row["eligible"] = not exclusion_reasons
            row["exclusion_reasons"] = sorted(set(exclusion_reasons))
            rows.append(row)
        return rows, evidence

    def _labels(
        self,
        manifests: dict[str, ManifestRecord],
        candidates: list[Candidate],
    ) -> dict[tuple[str, str], LabelInfo]:
        labels: dict[tuple[str, str], LabelInfo] = {}
        for asset, manifest in manifests.items():
            path = self.catalog.raw_path(manifest.files[0])
            schema = pq.read_schema(path)
            columns = [
                name
                for name in (
                    "slug",
                    "winning_outcome",
                    "uma_resolution_status",
                    "resolution_source",
                    "_ingested_at",
                )
                if name in schema.names
            ]
            if "slug" not in columns or "winning_outcome" not in columns:
                raise ValueError(f"{manifest.relative_path} lacks label columns")
            for row in pq.read_table(path, columns=columns).to_pylist():
                slug = str(row.get("slug") or "")
                raw = row.get("winning_outcome")
                ingested_at = row.get("_ingested_at")
                if isinstance(ingested_at, datetime):
                    if ingested_at.tzinfo is None:
                        ingested_at = ingested_at.replace(tzinfo=UTC)
                    else:
                        ingested_at = ingested_at.astimezone(UTC)
                else:
                    ingested_at = None
                if raw is None:
                    labels[(asset, slug)] = LabelInfo(
                        None, None, "unresolved", ingested_at, "snapshot_unresolved"
                    )
                    continue
                normalized = _LABELS.get(str(raw).lower())
                if normalized is None:
                    labels[(asset, slug)] = LabelInfo(
                        None, None, "unknown_outcome", ingested_at, "invalid"
                    )
                    continue
                status = row.get("uma_resolution_status")
                if status is not None and str(status).lower() not in {"resolved", ""}:
                    labels[(asset, slug)] = LabelInfo(
                        None,
                        None,
                        "resolution_status_not_resolved",
                        ingested_at,
                        "invalid",
                    )
                    continue
                source = str(row.get("resolution_source") or "")
                confidence = (
                    "gamma_official_resolution_snapshot"
                    if "chain.link" in source
                    else "gamma_snapshot_unverified_source"
                )
                labels[(asset, slug)] = LabelInfo(
                    normalized[0], normalized[1], None, ingested_at, confidence
                )

        # PMData's market_resolved row supplies causal availability and an
        # independent outcome observation. This happens only on the label path.
        unique = {(item.asset, item.slug, item.window_start.date().isoformat()) for item in candidates}
        for asset, slug, partition_date in tqdm(
            sorted(unique),
            total=len(unique),
            desc="Reconciling labels",
            unit="market",
            dynamic_ncols=True,
            disable=None,
        ):
            current = labels.get((asset, slug))
            if current is None or current.binary is None:
                continue
            observation = self.source.resolution_observation(
                asset=asset, slug=slug, partition_date=partition_date
            )
            if observation is None:
                continue
            raw_outcome, available_at = observation
            observed = _LABELS.get(raw_outcome.lower())
            if observed is None or observed[1] != current.binary:
                labels[(asset, slug)] = LabelInfo(
                    None,
                    None,
                    "gamma_pmdata_resolution_disagreement",
                    available_at,
                    "disagreement",
                )
                continue
            labels[(asset, slug)] = LabelInfo(
                current.outcome,
                current.binary,
                None,
                available_at,
                "gamma_official_snapshot_pmdata_corroborated",
            )
        return labels

    @staticmethod
    def _rows_table(rows: list[dict[str, Any]], features: list[str]) -> pa.Table:
        fields = [
            ("row_id", pa.string()), ("asset", pa.string()), ("series_id", pa.string()),
            ("market_id", pa.string()), ("condition_id", pa.string()), ("up_token_id", pa.string()),
            ("down_token_id", pa.string()), ("slug", pa.string()),
            ("window_start", pa.timestamp("us", tz="UTC")), ("window_end", pa.timestamp("us", tz="UTC")),
            ("decision_schedule", pa.string()), ("decision_at", pa.timestamp("us", tz="UTC")),
            ("execution_at", pa.timestamp("us", tz="UTC")), ("label", pa.int8()),
            ("label_binary", pa.int8()), ("label_outcome", pa.string()),
            ("label_null_reason", pa.string()), ("label_confidence", pa.string()),
            ("resolution_available_at", pa.timestamp("us", tz="UTC")),
            ("market_midprice", pa.float64()), ("quote_valid", pa.bool_()),
            ("eligible", pa.bool_()), ("exclusion_reasons", pa.list_(pa.string())), 
        ]
        for prefix in ("decision", "execution"):
            fields.extend((f"{prefix}_{name}", pa.float64()) for name in ("up_bid", "up_ask", "down_bid", "down_ask"))
            fields.extend((f"{prefix}_{name}", pa.int64()) for name in ("up_bid_micros", "up_ask_micros", "down_bid_micros", "down_ask_micros"))
            fields.extend([
                (f"{prefix}_quote_staleness_seconds", pa.float64()),
                (f"{prefix}_quote_valid", pa.bool_()),
                (f"{prefix}_quote_reasons", pa.list_(pa.string())),
            ])
        fields.extend((name, pa.float64()) for name in features)
        schema = pa.schema(fields)
        columns = {field.name: pa.array([row.get(field.name) for row in rows], type=field.type) for field in schema}
        return pa.table(columns, schema=schema)

    @staticmethod
    def _evidence_table(rows: list[dict[str, Any]]) -> pa.Table:
        schema = pa.schema([
            ("row_id", pa.string()), ("feature", pa.string()),
            ("event_time", pa.timestamp("us", tz="UTC")),
            ("available_at", pa.timestamp("us", tz="UTC")),
            ("age_seconds", pa.float64()), ("null_reason", pa.string()), ("policy", pa.string()),
        ])
        return pa.table({field.name: pa.array([row.get(field.name) for row in rows], type=field.type) for field in schema}, schema=schema)

    def build(self, *, limit: int | None = None, force: bool = False) -> BuildResult:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        candidates, gamma_manifests = self.enumerate_candidates(limit=limit)
        feature_rows, evidence_rows = self._assemble_feature_rows(candidates)
        label_free_hash = stable_hash(feature_rows)
        labels = self._labels(gamma_manifests, candidates)
        for row in feature_rows:
            info = labels.get(
                (row["asset"], row["slug"]),
                LabelInfo(None, None, "label_not_in_snapshot", None, "missing"),
            )
            row["label_outcome"] = info.outcome
            row["label"] = info.binary
            row["label_binary"] = info.binary
            row["label_null_reason"] = info.null_reason
            row["label_confidence"] = info.confidence
            row["resolution_available_at"] = info.available_at
        inputs = self.ledger.snapshot()
        behavior = {
            "implementation": ASSEMBLER_IMPLEMENTATION_VERSION,
            "assembler_source_sha256": _source_hash(),
            "spec": self.spec.canonical_dict(),
            "effective_limit": limit,
            "features": [
                {"name": item.name, "version": item.version, "production_status": item.production_status}
                for item in self.feature_definitions
            ],
            "inputs": inputs,
            "assumptions": {
                "book": BOOK_ASSUMPTION_VERSION,
                "down_quote": DOWN_COMPLEMENT_VERSION,
                "pmdata_available_at": PMDATA_POLICY_V1,
                "labels_attached_after_features": True,
                "label_availability": "pmdata_market_resolved_then_gamma_snapshot_ingestion_v1",
            },
        }
        dataset_id = stable_hash(behavior)
        reason_counts = Counter(reason for row in feature_rows for reason in row["exclusion_reasons"])
        null_counts = Counter(item["feature"] for item in evidence_rows if item["null_reason"] is not None)
        rows_table = self._rows_table(feature_rows, self.spec.features)
        evidence_table = self._evidence_table(evidence_rows)
        manifest = {
            "dataset_id": dataset_id,
            "spec": self.spec.canonical_dict(),
            "feature_columns": self.spec.features,
            "production_statuses": {item.name: item.production_status for item in self.feature_definitions},
            "feature_versions": {item.name: item.version for item in self.feature_definitions},
            "inputs": inputs,
            "coverage": {
                "candidate_rows": len(feature_rows),
                "eligible_rows": sum(bool(row["eligible"]) for row in feature_rows),
                "labeled_rows": sum(row["label"] is not None for row in feature_rows),
                "rows_by_asset": dict(sorted(Counter(row["asset"] for row in feature_rows).items())),
                "exclusion_reasons": dict(sorted(reason_counts.items())),
                "feature_null_counts": dict(sorted(null_counts.items())),
            },
            "assumptions": behavior["assumptions"],
            "implementation_version": ASSEMBLER_IMPLEMENTATION_VERSION,
            "assembler_source_sha256": behavior["assembler_source_sha256"],
            "label_free_rows_hash": label_free_hash,
        }
        path = commit_dataset(
            output_dir=self.output_dir,
            dataset_id=dataset_id,
            rows=rows_table,
            evidence=evidence_table,
            manifest=manifest,
            force=force,
        )
        return BuildResult(dataset_id=dataset_id, path=path, row_count=len(feature_rows))


def build_dataset(
    *,
    spec_path: Path | str,
    data_dir: Path | str,
    output_dir: Path | str,
    limit: int | None = None,
    force: bool = False,
) -> BuildResult:
    spec = load_dataset_spec(spec_path)
    return DatasetAssembler(spec, data_dir=data_dir, output_dir=output_dir).build(limit=limit, force=force)
