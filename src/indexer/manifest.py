"""Unit manifests: the commit marker + lineage record for each data unit.

A unit is complete iff its manifest exists with status="complete". The
manifest is always written last, after all data files are in place.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

import pyarrow as pa
from pydantic import BaseModel, Field

from .lake import Lake, _fsync_dir, _fsync_file
from .units import DataUnit

log = logging.getLogger(__name__)

UnitStatus = Literal["complete", "unavailable", "failed"]
ErrorClass = Literal["transient", "permanent", "malformed"]


class FileEntry(BaseModel):
    path: str  # relative to the raw root
    sha256: str
    bytes: int
    rows: int


class Manifest(BaseModel):
    unit_id: str
    source: str
    dataset: str
    partitions: dict[str, str]
    date: str
    status: UnitStatus
    files: list[FileEntry] = Field(default_factory=list)
    row_count: int = 0
    min_ts: str | None = None
    max_ts: str | None = None
    schema_fingerprints: dict[str, str] = Field(default_factory=dict)
    config_hash: str
    code_version: str
    run_id: str
    started_at: datetime
    finished_at: datetime
    warnings: list[str] = Field(default_factory=list)
    error_class: ErrorClass | None = None
    error_message: str | None = None


def schema_fingerprint(table: pa.Table) -> str:
    return hashlib.sha256(str(table.schema).encode("utf-8")).hexdigest()


def write_manifest(
    lake: Lake, manifest: Manifest, *, namespace: str | None = None
) -> Path:
    """Atomically write the manifest (staging + rename), fsyncing on the way."""
    path = lake.manifest_path(
        DataUnit(
            source=manifest.source,
            dataset=manifest.dataset,
            partitions=manifest.partitions,
            date=manifest.date,
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging_root = lake.tmp_root / namespace if namespace else lake.tmp_root
    staging = staging_root / uuid.uuid4().hex / f"{uuid.uuid4().hex}.json"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _fsync_file(staging)
    os.rename(staging, path)
    _fsync_dir(path.parent)
    try:
        staging.parent.rmdir()
    except OSError:
        pass
    return path


def load_manifest(path: Path) -> Manifest | None:
    if not path.exists():
        return None
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def manifest_for(lake: Lake, unit: DataUnit) -> Manifest | None:
    return load_manifest(lake.manifest_path(unit))


def unit_status(lake: Lake, unit: DataUnit) -> UnitStatus | None:
    manifest = manifest_for(lake, unit)
    return manifest.status if manifest else None


def iter_manifests(
    lake: Lake, source: str | None = None, dataset: str | None = None
) -> Iterator[Manifest]:
    root = lake.manifests_root
    if not root.exists():
        return
    for path in sorted(root.rglob("*.json")):
        try:
            manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # corrupt manifest: report, keep scanning
            log.warning("skipping unreadable manifest %s: %s", path, exc)
            continue
        if source and manifest.source != source:
            continue
        if dataset and manifest.dataset != dataset:
            continue
        yield manifest


def audit_unit(lake: Lake, manifest: Manifest) -> list[str]:
    """Read-only re-verification of committed files. Returns a list of problems."""
    problems: list[str] = []
    if manifest.status != "complete":
        return problems
    for entry in manifest.files:
        path = lake.raw_root / entry.path
        if not path.exists():
            problems.append(f"{entry.path}: missing")
            continue
        data = path.read_bytes()
        if len(data) != entry.bytes:
            problems.append(
                f"{entry.path}: size mismatch (manifest {entry.bytes}, disk {len(data)})"
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.sha256:
            problems.append(f"{entry.path}: sha256 mismatch")
    return problems


def get_code_version(repo_root: Path) -> str:
    """git sha (+ '-dirty' if the tree is dirty), or 'nogit' outside a repo."""
    repo_root = Path(repo_root)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        sha = git("rev-parse", "HEAD")
    except Exception:
        return "nogit"
    try:
        dirty = bool(git("status", "--porcelain"))
    except Exception:
        dirty = False
    return sha + ("-dirty" if dirty else "")


def get_config_hash(config_dir: Path) -> str:
    """sha256 over the three indexer config files' bytes (sorted by name)."""
    config_dir = Path(config_dir)
    digest = hashlib.sha256()
    for name in sorted(["binance.toml", "pmdata.toml", "polymarket.toml"]):
        digest.update(name.encode("utf-8"))
        digest.update((config_dir / name).read_bytes())
    return digest.hexdigest()
