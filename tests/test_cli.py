"""CLI tests: status and index --dry-run with a fake connector, tmp data dir."""

from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

from indexer import cli
from indexer.connectors.base import Connector, FetchedUnit, ParsedUnit
from indexer.lake import Lake
from indexer.units import DataUnit

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "indexer"


class CliFakeConnector(Connector):
    source = "binance"

    def __init__(self):
        self.fetched: list[str] = []

    def plan(self, *, dataset=None, entity=None, start=None, end=None):
        start = start or date(2026, 7, 20)
        end = end or date(2026, 7, 20)
        return [
            DataUnit(
                source="binance",
                dataset="spot_klines",
                partitions={"symbol": "BTCUSDT", "interval": "1m"},
                date=start.isoformat(),
            )
        ]

    def fetch(self, unit):
        self.fetched.append(unit.id)
        return FetchedUnit(payload=b"x", source_key="/fake")

    def parse(self, unit, fetched):
        return ParsedUnit(tables={"data.parquet": pa.table({"x": [1]})})


@pytest.fixture()
def fake_connector(monkeypatch):
    connector = CliFakeConnector()
    monkeypatch.setattr(cli, "build_connector", lambda source, catalog: connector)
    return connector


def _args(tmp_path: Path, *rest: str) -> list[str]:
    # --config-dir/--data-dir are per-subcommand flags, so they follow the subcommand
    return [*rest, "--config-dir", str(CONFIG_DIR), "--data-dir", str(tmp_path / "data")]


def test_index_dry_run_fetches_nothing(fake_connector, tmp_path, capsys):
    rc = cli.main(_args(tmp_path, "index", "--source", "binance", "--dry-run"))
    assert rc == 0
    assert fake_connector.fetched == []
    out = capsys.readouterr().out
    assert "planned=1" in out
    assert not (tmp_path / "data" / "manifests").exists()


def test_index_then_status(fake_connector, tmp_path, capsys):
    rc = cli.main(_args(tmp_path, "index", "--source", "binance"))
    assert rc == 0
    assert len(fake_connector.fetched) == 1

    rc = cli.main(_args(tmp_path, "status", "--source", "binance"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "binance/spot_klines" in out
    assert "expected=1 complete=1" in out


def test_status_reports_missing(fake_connector, tmp_path, capsys):
    rc = cli.main(_args(tmp_path, "status", "--source", "binance"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "missing=1" in out


def test_audit_detects_tampering(fake_connector, tmp_path, capsys):
    assert cli.main(_args(tmp_path, "index", "--source", "binance")) == 0
    capsys.readouterr()
    lake = Lake(tmp_path / "data")
    target = next(lake.raw_root.rglob("data.parquet"))
    target.write_bytes(b"tampered")
    rc = cli.main(_args(tmp_path, "audit", "--source", "binance"))
    assert rc == 1
    assert "sha256 mismatch" in capsys.readouterr().out


def test_env_file_loading(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PMDATA_KEY=abc123\n# comment\nOTHER=\"quoted\"\n", encoding="utf-8")
    monkeypatch.delenv("PMDATA_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    cli._load_env_file(tmp_path / ".env")
    import os

    assert os.environ["PMDATA_KEY"] == "abc123"
    assert os.environ["OTHER"] == "quoted"
