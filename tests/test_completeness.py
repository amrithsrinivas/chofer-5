"""Tests for scripts/check_completeness.py (fabricated manifests, tmp dirs)."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from indexer.lake import Lake
from indexer.manifest import Manifest, write_manifest
from indexer.units import DataUnit

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "indexer"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_completeness.py"


@pytest.fixture()
def cc():
    spec = importlib.util.spec_from_file_location("check_completeness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(unit: DataUnit, status: str, **kwargs) -> Manifest:
    now = datetime.now(timezone.utc)
    return Manifest(
        unit_id=unit.id,
        source=unit.source,
        dataset=unit.dataset,
        partitions=unit.partitions,
        date=unit.date,
        status=status,
        config_hash="c" * 64,
        code_version="nogit",
        run_id="r" * 32,
        started_at=now,
        finished_at=now,
        **kwargs,
    )


def _kline_unit(day: str, symbol: str = "BTCUSDT") -> DataUnit:
    return DataUnit(
        source="binance",
        dataset="spot_klines",
        partitions={"symbol": symbol, "interval": "1m"},
        date=day,
    )


def test_compact_date_ranges(cc):
    assert cc.compact_date_ranges([]) == []
    assert cc.compact_date_ranges(["2026-03-01"]) == ["2026-03-01 (1 day)"]
    assert cc.compact_date_ranges(
        ["2026-03-03", "2026-03-01", "2026-03-02", "2026-03-14"]
    ) == ["2026-03-01..2026-03-03 (3 days)", "2026-03-14 (1 day)"]


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    lake = Lake(tmp_path / "data")
    write_manifest(lake, _manifest(_kline_unit("2026-07-01"), "complete"))
    write_manifest(
        lake,
        _manifest(
            _kline_unit("2026-07-02"),
            "failed",
            error_class="malformed",
            error_message="header has 8 columns, expected 9",
        ),
    )
    write_manifest(lake, _manifest(_kline_unit("2026-07-03"), "unavailable"))
    # 2026-07-04 and 2026-07-05: never attempted.
    return lake


def _run(cc, lake, capsys, *extra: str):
    rc = cc.main(
        [
            "--config-dir",
            str(CONFIG_DIR),
            "--data-dir",
            str(lake.root),
            "--source",
            "binance",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-05",
            *extra,
        ]
    )
    assert rc == 0
    return capsys.readouterr().out


def test_json_report_counts_and_ranges(cc, lake, capsys):
    report = json.loads(_run(cc, lake, capsys, "--json"))
    group = next(
        g
        for g in report["groups"]
        if g["dataset"] == "spot_klines" and g["entity"] == "symbol=BTCUSDT"
    )
    assert group["expected"] == 5
    assert group["complete"] == 1
    assert group["failed"] == 1
    assert group["unavailable"] == 1
    assert group["missing"] == 2
    assert group["pct_complete"] == 20.0
    assert group["missing_ranges"] == ["2026-07-04..2026-07-05 (2 days)"]
    assert group["unavailable_ranges"] == ["2026-07-03 (1 day)"]
    assert group["failures"][0]["error_class"] == "malformed"
    assert "header has 8 columns" in group["failures"][0]["error"]
    # Other symbols/datasets are fully missing but still counted.
    total_expected = report["overall"]["expected"]
    assert total_expected == 4 * 4 * 5  # 4 datasets x 4 tickers x 5 days
    assert report["overall"]["complete"] == 1


def test_text_report_has_legend_and_summary(cc, lake, capsys):
    out = _run(cc, lake, capsys)
    assert "OVERALL: expected=80 complete=1" in out
    assert "unavailable = source 404" in out
    assert "missing = never attempted" in out
    assert "2026-07-04..2026-07-05 (2 days)" in out
    assert "binance/spot_klines symbol=BTCUSDT" in out


def test_pmdata_planning_works_without_api_key(cc, lake, capsys, monkeypatch):
    monkeypatch.delenv("PMDATA_KEY", raising=False)
    rc = cc.main(
        [
            "--config-dir",
            str(CONFIG_DIR),
            "--data-dir",
            str(lake.root),
            "--source",
            "pmdata",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-02",
            "--json",
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    # 12 series x 3 data types x 2 days
    assert report["overall"]["expected"] == 12 * 3 * 2
    assert report["overall"]["missing"] == 12 * 3 * 2
