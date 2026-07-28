"""Filesystem data lake: path scheme, atomic Parquet commit, tmp cleanup.

Layout:
  <root>/raw/<unit.id>/<file>.parquet
  <root>/manifests/<unit.id>.json
  <root>/_tmp/<uuid>/...          staging only; never survives a commit
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pyarrow as pa

from .units import DataUnit


@dataclass
class CommittedFile:
    name: str  # file name within the unit dir, e.g. "data.parquet"
    rel_path: str  # relative to the raw root
    sha256: str
    size_bytes: int
    rows: int


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Lake:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def raw_root(self) -> Path:
        return self.root / "raw"

    @property
    def manifests_root(self) -> Path:
        return self.root / "manifests"

    @property
    def tmp_root(self) -> Path:
        return self.root / "_tmp"

    def unit_dir(self, unit: DataUnit) -> Path:
        return self.raw_root / unit.id

    def manifest_path(self, unit: DataUnit) -> Path:
        return self.manifests_root / f"{unit.id}.json"

    def clean_tmp(self, *, older_than_hours: float | None = None) -> None:
        """Remove staging residue.

        With `older_than_hours=None` the whole _tmp tree is removed (test and
        single-process use). With a value, only per-run staging dirs older
        than that many hours are removed — safe while other indexer processes
        are running, since a live run's staging is always fresh.
        """
        if not self.tmp_root.exists():
            return
        if older_than_hours is None:
            shutil.rmtree(self.tmp_root)
            return
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_hours * 3600
        for entry in self.tmp_root.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except FileNotFoundError:
                continue
        try:
            self.tmp_root.rmdir()  # remove _tmp itself if now empty
        except OSError:
            pass

    def clean_run(self, namespace: str) -> None:
        """Remove one run's staging dir; never touches other runs' dirs."""
        shutil.rmtree(self.tmp_root / namespace, ignore_errors=True)
        try:
            self.tmp_root.rmdir()
        except OSError:
            pass

    def commit_tables(
        self,
        unit: DataUnit,
        tables: dict[str, pa.Table],
        *,
        namespace: str | None = None,
        row_group_size: int | None = None,
    ) -> list[CommittedFile]:
        """Atomically commit all tables of a unit.

        Writes parquet into _tmp (per-run namespace when given), fsyncs, then
        os.rename into place. Returns per-file metadata for the manifest.
        Raises on any failure; caller must treat the unit as not committed.
        """
        if not tables:
            raise ValueError("commit_tables requires at least one table")
        staging = self.tmp_root / namespace / uuid.uuid4().hex if namespace else (
            self.tmp_root / uuid.uuid4().hex
        )
        staging.mkdir(parents=True, exist_ok=False)
        final_dir = self.unit_dir(unit)
        try:
            staged: list[tuple[Path, CommittedFile]] = []
            for name, table in tables.items():
                staged_path = staging / name
                pq.write_table(
                    table,
                    staged_path,
                    compression="zstd" if row_group_size is not None else "snappy",
                    row_group_size=row_group_size,
                )
                _fsync_file(staged_path)
                data = staged_path.read_bytes()
                staged.append(
                    (
                        staged_path,
                        CommittedFile(
                            name=name,
                            rel_path=f"{unit.id}/{name}",
                            sha256=hashlib.sha256(data).hexdigest(),
                            size_bytes=len(data),
                            rows=table.num_rows,
                        ),
                    )
                )
            final_dir.mkdir(parents=True, exist_ok=True)
            committed: list[CommittedFile] = []
            for staged_path, info in staged:
                os.rename(staged_path, final_dir / info.name)
                committed.append(info)
            _fsync_dir(final_dir)
            return committed
        finally:
            shutil.rmtree(staging, ignore_errors=True)
