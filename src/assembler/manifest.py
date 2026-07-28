"""Read-only catalog of raw unit manifests and their exact committed files."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    bytes: int
    rows: int


@dataclass(frozen=True)
class ManifestRecord:
    path: Path
    relative_path: str
    sha256: str
    source: str
    dataset: str
    partitions: tuple[tuple[str, str], ...]
    date: str
    status: str
    files: tuple[FileRecord, ...]
    finished_at: str
    raw: dict[str, Any]

    def partition(self, name: str) -> str | None:
        return dict(self.partitions).get(name)

    def lineage(self, files: Iterable[FileRecord] | None = None) -> dict[str, Any]:
        selected = self.files if files is None else tuple(files)
        return {
            "manifest_path": self.relative_path,
            "manifest_sha256": self.sha256,
            "source": self.source,
            "dataset": self.dataset,
            "partitions": dict(self.partitions),
            "date": self.date,
            "status": self.status,
            "files": [record.__dict__ for record in selected],
        }


class ManifestCatalog:
    """Manifest index. Raw Parquet is reachable only through a complete entry."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.manifests_root = self.data_dir / "manifests"
        self.raw_root = self.data_dir / "raw"
        self._records = self._load_records()

    def _load_records(self) -> tuple[ManifestRecord, ...]:
        records: list[ManifestRecord] = []
        if not self.manifests_root.exists():
            return ()
        for directory, subdirs, names in os.walk(self.manifests_root):
            subdirs.sort()
            for name in sorted(names):
                if not name.endswith(".json"):
                    continue
                path = Path(directory) / name
                payload = path.read_bytes()
                raw = json.loads(payload)
                files = tuple(
                    FileRecord(
                        path=str(item["path"]),
                        sha256=str(item["sha256"]),
                        bytes=int(item["bytes"]),
                        rows=int(item["rows"]),
                    )
                    for item in raw.get("files", [])
                )
                records.append(
                    ManifestRecord(
                        path=path,
                        relative_path=path.relative_to(self.data_dir).as_posix(),
                        sha256=hashlib.sha256(payload).hexdigest(),
                        source=str(raw.get("source", "")),
                        dataset=str(raw.get("dataset", "")),
                        partitions=tuple(sorted((str(k), str(v)) for k, v in raw.get("partitions", {}).items())),
                        date=str(raw.get("date", "")),
                        status=str(raw.get("status", "")),
                        files=files,
                        finished_at=str(raw.get("finished_at", "")),
                        raw=raw,
                    )
                )
        return tuple(records)

    def records(
        self,
        *,
        source: str,
        dataset: str,
        partitions: dict[str, str] | None = None,
        date: str | None = None,
        complete_only: bool = False,
    ) -> list[ManifestRecord]:
        expected = {str(k): str(v) for k, v in (partitions or {}).items()}
        result = []
        for record in self._records:
            if record.source != source or record.dataset != dataset:
                continue
            if date is not None and record.date != date:
                continue
            actual = dict(record.partitions)
            if any(actual.get(key) != value for key, value in expected.items()):
                continue
            if complete_only and record.status != "complete":
                continue
            result.append(record)
        return sorted(result, key=lambda item: (item.date, item.finished_at, item.relative_path))

    def latest_complete(
        self, *, source: str, dataset: str, partitions: dict[str, str]
    ) -> ManifestRecord:
        records = self.records(
            source=source,
            dataset=dataset,
            partitions=partitions,
            complete_only=True,
        )
        if not records:
            raise FileNotFoundError(
                f"no complete manifest for {source}/{dataset} {partitions}"
            )
        return records[-1]

    def exact_unit(
        self,
        *,
        source: str,
        dataset: str,
        partitions: dict[str, str],
        date: str,
    ) -> ManifestRecord | None:
        records = self.records(
            source=source,
            dataset=dataset,
            partitions=partitions,
            date=date,
            complete_only=True,
        )
        if len(records) > 1:
            raise ValueError(f"multiple complete manifests for one raw unit: {records}")
        return records[0] if records else None

    def raw_path(self, file: FileRecord) -> Path:
        path = (self.raw_root / file.path).resolve()
        root = self.raw_root.resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"manifest file escapes raw root: {file.path}")
        if not path.is_file():
            raise FileNotFoundError(f"complete manifest file is absent: {file.path}")
        return path


class InputLedger:
    def __init__(self) -> None:
        self._records: dict[str, tuple[ManifestRecord, dict[str, FileRecord]]] = {}

    def add(
        self, record: ManifestRecord, files: Iterable[FileRecord] | None = None
    ) -> None:
        selected = record.files if files is None else tuple(files)
        existing = self._records.get(record.relative_path)
        merged = {} if existing is None else dict(existing[1])
        merged.update((file.path, file) for file in selected)
        self._records[record.relative_path] = (record, merged)

    def extend(self, records: Iterable[ManifestRecord]) -> None:
        for record in records:
            self.add(record)

    def snapshot(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for key in sorted(self._records):
            record, files = self._records[key]
            result.append(record.lineage(files[key] for key in sorted(files)))
        return result
