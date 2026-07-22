import hashlib

import pyarrow as pa
import pyarrow.parquet as pq

from chofer.manifest import manifest_for, write_manifest
from chofer.manifest import Manifest
from datetime import datetime, timezone


def test_atomic_commit_no_tmp_residue(lake, unit):
    table = pa.table({"x": pa.array([1, 2, 3], pa.int64())})
    committed = lake.commit_tables(unit, {"data.parquet": table})

    assert len(committed) == 1
    info = committed[0]
    final = lake.unit_dir(unit) / "data.parquet"
    assert final.exists()
    data = final.read_bytes()
    assert info.sha256 == hashlib.sha256(data).hexdigest()
    assert info.size_bytes == len(data)
    assert info.rows == 3
    assert info.rel_path == f"{unit.id}/data.parquet"
    # staging is gone
    assert not lake.tmp_root.exists() or not any(lake.tmp_root.iterdir())
    # parquet is readable
    assert pq.read_table(final).num_rows == 3


def test_commit_replaces_unit_atomically(lake, unit):
    lake.commit_tables(unit, {"data.parquet": pa.table({"x": [1]})})
    lake.commit_tables(unit, {"data.parquet": pa.table({"y": [2, 3]})})
    final = lake.unit_dir(unit) / "data.parquet"
    assert pq.read_table(final).column_names == ["y"]


def test_clean_tmp_removes_staging(lake):
    (lake.tmp_root / "orphan").mkdir(parents=True)
    (lake.tmp_root / "orphan" / "partial.parquet").write_bytes(b"junk")
    lake.clean_tmp()
    assert not lake.tmp_root.exists()


def test_manifest_written_after_data(lake, unit):
    table = pa.table({"x": [1]})
    lake.commit_tables(unit, {"data.parquet": table})
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        unit_id=unit.id,
        source=unit.source,
        dataset=unit.dataset,
        partitions=unit.partitions,
        date=unit.date,
        status="complete",
        config_hash="c" * 64,
        code_version="nogit",
        run_id="r" * 32,
        started_at=now,
        finished_at=now,
    )
    write_manifest(lake, manifest)
    data_mtime = (lake.unit_dir(unit) / "data.parquet").stat().st_mtime_ns
    manifest_mtime = lake.manifest_path(unit).stat().st_mtime_ns
    assert manifest_mtime >= data_mtime
    assert manifest_for(lake, unit).status == "complete"


def test_commit_requires_tables(lake, unit):
    import pytest

    with pytest.raises(ValueError):
        lake.commit_tables(unit, {})
