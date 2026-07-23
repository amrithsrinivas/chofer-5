from datetime import date, datetime, timezone

from indexer.manifest import (
    Manifest,
    manifest_for,
    unit_status,
    write_manifest,
)
from indexer.units import DataUnit, day_range


def test_unit_id_layout():
    unit = DataUnit(
        source="binance",
        dataset="spot_klines",
        partitions={"symbol": "BTCUSDT", "interval": "1m"},
        date="2026-07-20",
    )
    assert (
        unit.id
        == "binance/spot_klines/symbol=BTCUSDT/interval=1m/date=2026-07-20"
    )


def test_day_range_inclusive():
    days = list(day_range(date(2026, 7, 18), date(2026, 7, 20)))
    assert days == [date(2026, 7, 18), date(2026, 7, 19), date(2026, 7, 20)]
    assert list(day_range(date(2026, 7, 20), date(2026, 7, 18))) == []


def _manifest(unit: DataUnit, status: str = "complete") -> Manifest:
    now = datetime.now(timezone.utc)
    return Manifest(
        unit_id=unit.id,
        source=unit.source,
        dataset=unit.dataset,
        partitions=unit.partitions,
        date=unit.date,
        status=status,  # type: ignore[arg-type]
        config_hash="c" * 64,
        code_version="nogit",
        run_id="r" * 32,
        started_at=now,
        finished_at=now,
    )


def test_manifest_round_trip(lake, unit):
    write_manifest(lake, _manifest(unit))
    path = lake.manifest_path(unit)
    assert path.exists()
    loaded = manifest_for(lake, unit)
    assert loaded is not None
    assert loaded.unit_id == unit.id
    assert loaded.status == "complete"
    assert loaded.config_hash == "c" * 64


def test_commit_marker_semantics(lake, unit):
    # No manifest -> not complete.
    assert unit_status(lake, unit) is None
    write_manifest(lake, _manifest(unit, status="failed"))
    assert unit_status(lake, unit) == "failed"
    write_manifest(lake, _manifest(unit, status="complete"))
    assert unit_status(lake, unit) == "complete"
