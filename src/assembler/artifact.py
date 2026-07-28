"""Deterministic hashing and atomic immutable dataset artifact commit."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def commit_dataset(
    *,
    output_dir: Path,
    dataset_id: str,
    rows: pa.Table,
    evidence: pa.Table,
    manifest: dict[str, Any],
    force: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final = output_dir / dataset_id
    staging_root = output_dir / "_tmp"
    staging = staging_root / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=False)
    try:
        rows_path = staging / "rows.parquet"
        evidence_path = staging / "evidence.parquet"
        pq.write_table(rows, rows_path, compression="zstd")
        pq.write_table(evidence, evidence_path, compression="zstd")
        _fsync(rows_path)
        _fsync(evidence_path)
        manifest = dict(manifest)
        manifest["artifacts"] = {
            "rows.parquet": {"sha256": file_sha256(rows_path), "rows": rows.num_rows},
            "evidence.parquet": {"sha256": file_sha256(evidence_path), "rows": evidence.num_rows},
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(json.dumps(_json_safe(manifest), indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        _fsync(manifest_path)
        _fsync(staging)
        if final.exists():
            if not force:
                raise FileExistsError(f"immutable dataset already exists: {final}")
            existing_manifest = final / "manifest.json"
            if not existing_manifest.is_file():
                raise FileExistsError(f"refusing to replace incomplete dataset directory: {final}")
            existing = json.loads(existing_manifest.read_text())
            if existing.get("dataset_id") != dataset_id or existing.get("artifacts") != manifest["artifacts"]:
                raise FileExistsError(f"immutable dataset hash collision or non-deterministic rebuild: {final}")
            return final
        os.rename(staging, final)
        _fsync(output_dir)
        return final
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass
