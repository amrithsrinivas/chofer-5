from datetime import date

import pyarrow as pa

from indexer.connectors.base import Connector, FetchedUnit, ParsedUnit
from indexer.http import TransientError
from indexer.indexer import Indexer
from indexer.manifest import manifest_for
from indexer.units import DataUnit


class FakeConnector(Connector):
    """In-memory connector; fetch() raises for units listed in fail_on."""

    source = "fake"

    def __init__(self, units: list[DataUnit], fail_on: set[str] | None = None):
        self._units = units
        self.fail_on = fail_on or set()
        self.fetch_calls: list[str] = []

    def plan(self, **kwargs) -> list[DataUnit]:
        return list(self._units)

    def fetch(self, unit: DataUnit) -> FetchedUnit:
        self.fetch_calls.append(unit.id)
        if unit.id in self.fail_on:
            raise TransientError(f"boom on {unit.id}")
        return FetchedUnit(payload=b"raw", source_key=f"/fake/{unit.id}")

    def parse(self, unit: DataUnit, fetched: FetchedUnit) -> ParsedUnit:
        table = pa.table({"x": pa.array([1, 2], pa.int64())})
        return ParsedUnit(tables={"data.parquet": table})


def make_units(n: int) -> list[DataUnit]:
    return [
        DataUnit(
            source="fake",
            dataset="ds",
            partitions={"symbol": f"S{i}"},
            date="2026-07-20",
        )
        for i in range(n)
    ]


def make_indexer(lake) -> Indexer:
    return Indexer(lake, config_hash="c" * 64, code_version="nogit")


def test_run_completes_all_units(lake):
    units = make_units(4)
    connector = FakeConnector(units)
    summary = make_indexer(lake).run(connector, units)
    assert summary.ok
    assert summary.complete == 4
    assert len(connector.fetch_calls) == 4
    for unit in units:
        manifest = manifest_for(lake, unit)
        assert manifest.status == "complete"
        assert manifest.row_count == 2
        assert len(manifest.files) == 1
        assert manifest.code_version == "nogit"
        assert (lake.unit_dir(unit) / "data.parquet").exists()


def test_idempotent_second_run_fetches_nothing(lake):
    units = make_units(3)
    connector = FakeConnector(units)
    indexer = make_indexer(lake)
    indexer.run(connector, units)
    first_calls = len(connector.fetch_calls)
    summary = indexer.run(connector, units)
    assert summary.skipped_complete == 3
    assert summary.complete == 0
    assert len(connector.fetch_calls) == first_calls


def test_resume_after_crash(lake):
    units = make_units(4)
    crashing = FakeConnector(units, fail_on={units[2].id})
    indexer = make_indexer(lake)
    summary = indexer.run(crashing, units)
    assert summary.complete == 3
    assert summary.failed == 1
    failed_manifest = manifest_for(lake, units[2])
    assert failed_manifest.status == "failed"
    assert failed_manifest.error_class == "transient"
    assert failed_manifest.files == []

    # Re-run with the failure fixed: only the failed unit is re-fetched.
    healed = FakeConnector(units)
    summary2 = indexer.run(healed, units)
    assert summary2.ok
    assert summary2.skipped_complete == 3
    assert healed.fetch_calls == [units[2].id]
    assert manifest_for(lake, units[2]).status == "complete"


def test_force_reingests_complete_units(lake):
    units = make_units(2)
    connector = FakeConnector(units)
    indexer = make_indexer(lake)
    indexer.run(connector, units)
    connector2 = FakeConnector(units)
    summary = indexer.run(connector2, units, force=True)
    assert summary.complete == 2
    assert len(connector2.fetch_calls) == 2


def test_dry_run_fetches_nothing(lake):
    units = make_units(2)
    connector = FakeConnector(units)
    summary = make_indexer(lake).run(connector, units, dry_run=True)
    assert summary.planned == 2
    assert connector.fetch_calls == []
    assert manifest_for(lake, units[0]) is None


def test_run_leaves_no_tmp_residue(lake):
    units = make_units(2)
    make_indexer(lake).run(FakeConnector(units), units)
    assert not lake.tmp_root.exists() or not any(lake.tmp_root.iterdir())


def test_startup_cleanup_preserves_other_runs_staging(lake):
    # A fresh staging dir from a concurrent process must survive our startup.
    foreign = lake.tmp_root / "another-run-id"
    foreign.mkdir(parents=True)
    (foreign / "partial.parquet").write_bytes(b"in flight")
    units = make_units(1)
    make_indexer(lake).run(FakeConnector(units), units)
    assert (foreign / "partial.parquet").exists()


def test_planning_defaults(lake):
    connector = FakeConnector(make_units(1))
    planned = connector.plan(dataset=None, entity=None, start=date(2026, 7, 1),
                             end=date(2026, 7, 2))
    assert len(planned) == 1
