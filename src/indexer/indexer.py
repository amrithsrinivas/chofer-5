"""Indexer runner: plan units, skip committed ones, fetch/parse/commit
sequentially (one unit at a time, rate-limit friendly), and write one
manifest per unit (always last).

Crash semantics: data files land before their manifest, so an interrupted run
can leave files without a manifest — those units are simply re-ingested on the
next run (files are replaced atomically as a whole unit).
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .connectors.base import Connector
from .http import MalformedError, PermanentError, TransientError, UnavailableError
from .lake import Lake
from .manifest import (
    FileEntry,
    Manifest,
    manifest_for,
    schema_fingerprint,
    write_manifest,
)
from .units import DataUnit


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    run_id: str
    planned: int = 0
    skipped_complete: int = 0
    skipped_unavailable: int = 0
    complete: int = 0
    unavailable: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (unit id, error class)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Indexer:
    def __init__(self, lake: Lake, *, config_hash: str, code_version: str) -> None:
        self._lake = lake
        self._config_hash = config_hash
        self._code_version = code_version

    def filter_pending(
        self,
        units: list[DataUnit],
        *,
        force: bool = False,
        include_unavailable: bool = False,
    ) -> tuple[list[DataUnit], RunSummary]:
        """Drop units whose manifests are already terminal for this run mode."""
        summary = RunSummary(run_id="")
        pending: list[DataUnit] = []
        for unit in units:
            manifest = manifest_for(self._lake, unit)
            status = manifest.status if manifest else None
            if status == "complete" and not force:
                summary.skipped_complete += 1
                continue
            if status == "unavailable" and not (force or include_unavailable):
                summary.skipped_unavailable += 1
                continue
            pending.append(unit)
        return pending, summary

    def run(
        self,
        connector: Connector,
        units: list[DataUnit],
        *,
        force: bool = False,
        include_unavailable: bool = False,
        dry_run: bool = False,
    ) -> RunSummary:
        # Never delete other processes' staging: only sweep run dirs that
        # have been untouched for 24h (crashed-run residue).
        self._lake.clean_tmp(older_than_hours=24)
        run_id = uuid.uuid4().hex
        pending, summary = self.filter_pending(
            units, force=force, include_unavailable=include_unavailable
        )
        summary.run_id = run_id
        summary.planned = len(units)
        if dry_run:
            for unit in pending:
                log.info("dry-run: would ingest", extra={"unit": unit.id})
            return summary

        for unit in pending:
            unit_id, status, error_class = self._ingest_one(connector, unit, run_id)
            if status == "complete":
                summary.complete += 1
            elif status == "unavailable":
                summary.unavailable += 1
            else:
                summary.failed += 1
                summary.failures.append((unit_id, error_class or "transient"))
        self._lake.clean_run(run_id)
        return summary

    def _ingest_one(
        self, connector: Connector, unit: DataUnit, run_id: str
    ) -> tuple[str, str, str | None]:
        started = _utcnow()
        status: str
        files: list[FileEntry] = []
        row_count = 0
        min_ts = max_ts = None
        fingerprints: dict[str, str] = {}
        warnings: list[str] = []
        error_class: str | None = None
        error_message: str | None = None
        try:
            fetched = connector.fetch(unit)
            parsed = connector.parse(unit, fetched)
            committed = self._lake.commit_tables(
                unit, parsed.tables, namespace=run_id
            )
            files = [
                FileEntry(
                    path=f.rel_path,
                    sha256=f.sha256,
                    bytes=f.size_bytes,
                    rows=f.rows,
                )
                for f in committed
            ]
            row_count = sum(f.rows for f in committed)
            fingerprints = {
                name: schema_fingerprint(table)
                for name, table in parsed.tables.items()
            }
            min_ts, max_ts = parsed.min_ts, parsed.max_ts
            warnings = parsed.warnings
            status = "complete"
        except UnavailableError as exc:
            status = "unavailable"
            error_message = str(exc)
        except MalformedError as exc:
            status = "failed"
            error_class = "malformed"
            error_message = str(exc)
        except (TransientError, PermanentError) as exc:
            status = "failed"
            error_class = (
                "transient" if isinstance(exc, TransientError) else "permanent"
            )
            error_message = str(exc)
        except Exception as exc:  # unexpected: retryable by default
            status = "failed"
            error_class = "transient"
            error_message = f"{type(exc).__name__}: {exc}"
        manifest = Manifest(
            unit_id=unit.id,
            source=unit.source,
            dataset=unit.dataset,
            partitions=unit.partitions,
            date=unit.date,
            status=status,  # type: ignore[arg-type]
            files=files,
            row_count=row_count,
            min_ts=min_ts,
            max_ts=max_ts,
            schema_fingerprints=fingerprints,
            config_hash=self._config_hash,
            code_version=self._code_version,
            run_id=run_id,
            started_at=started,
            finished_at=_utcnow(),
            warnings=warnings,
            error_class=error_class,  # type: ignore[arg-type]
            error_message=error_message,
        )
        # The manifest is always written last: it is the commit marker.
        write_manifest(self._lake, manifest, namespace=run_id)
        log.info(
            "unit ingested",
            extra={
                "unit": unit.id,
                "status": status,
                "rows": row_count,
                "error_class": error_class,
            },
        )
        return unit.id, status, error_class
