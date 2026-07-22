"""PMData connector tests: synthetic ZIPs of parquet members, no network."""

import hashlib

import httpx
import pyarrow as pa
import pytest

from chofer.catalog import PmdataConfig, PmdataSeries
from chofer.connectors.pmdata import (
    PmdataConnector,
    parse_day_archive,
    parse_slug_parquet,
)
from chofer.http import HttpClient, MalformedError, UnavailableError
from chofer.units import DataUnit

from conftest import make_zip, sample_table, table_to_parquet_bytes
from datetime import date, datetime, timezone


def _config() -> PmdataConfig:
    return PmdataConfig(
        market_data_types=["poly_l2", "poly_trade"],
        series={
            "btc_5m": PmdataSeries(
                asset="BTC",
                interval="5m",
                provider_series="btc-5m",
                recording_starts_at=datetime(2026, 2, 13, 17, tzinfo=timezone.utc),
            )
        },
    )


def _unit(dataset: str = "poly_l2") -> DataUnit:
    return DataUnit(
        source="pmdata",
        dataset=dataset,
        partitions={"series": "btc-5m"},
        date="2026-07-18",
    )


def _archive(slugs: list[str]) -> bytes:
    return make_zip(
        {f"{slug}.parquet": table_to_parquet_bytes(sample_table(slug)) for slug in slugs}
    )


def test_day_archive_splits_one_table_per_slug():
    slugs = ["btc-updown-5m-1784332800", "btc-updown-5m-1784333100"]
    payload = _archive(slugs)
    parsed = parse_day_archive(
        payload, source="pmdata", source_key="/x.zip", source_sha256="a" * 64
    )
    assert sorted(parsed.tables) == sorted(f"{s}.parquet" for s in slugs)
    for slug in slugs:
        table = parsed.tables[f"{slug}.parquet"]
        assert table.num_rows == 3
        assert set(table.column("market_slug").to_pylist()) == {slug}
        for col in ("_source", "_source_key", "_source_sha256", "_ingested_at"):
            assert col in table.column_names


def test_day_archive_multi_slug_member_is_split():
    combined = pa.concat_tables(
        [sample_table("slug-a", 2), sample_table("slug-b", 1)]
    )
    payload = make_zip({"chunk_0.parquet": table_to_parquet_bytes(combined)})
    parsed = parse_day_archive(
        payload, source="pmdata", source_key="/x.zip", source_sha256="a" * 64
    )
    assert parsed.tables["slug-a.parquet"].num_rows == 2
    assert parsed.tables["slug-b.parquet"].num_rows == 1


def test_slug_derived_from_member_name_without_column():
    table = sample_table("ignored").drop_columns(["market_slug"])
    payload = make_zip({"btc-updown-5m-1784332800.parquet": table_to_parquet_bytes(table)})
    parsed = parse_day_archive(
        payload, source="pmdata", source_key="/x.zip", source_sha256="a" * 64
    )
    assert "btc-updown-5m-1784332800.parquet" in parsed.tables


def test_unexpected_member_type_fails_loudly():
    payload = make_zip({"readme.txt": b"hello", "m.parquet": table_to_parquet_bytes(sample_table("s"))})
    with pytest.raises(MalformedError, match="unexpected ZIP member"):
        parse_day_archive(
            payload, source="pmdata", source_key="/x.zip", source_sha256="a" * 64
        )


def test_corrupt_parquet_member_fails_loudly():
    payload = make_zip({"broken.parquet": b"not parquet at all"})
    with pytest.raises(MalformedError):
        parse_day_archive(
            payload, source="pmdata", source_key="/x.zip", source_sha256="a" * 64
        )


def test_slug_endpoint_passthrough_adds_provenance():
    raw = table_to_parquet_bytes(sample_table("btc-updown-5m-1784332800"))
    table = parse_slug_parquet(
        raw, source="pmdata", source_key="/download/poly_l2/x.parquet",
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert table.num_rows == 3
    assert table.column("_source")[0].as_py() == "pmdata"
    assert table.column("_source_key")[0].as_py() == "/download/poly_l2/x.parquet"


def test_fetch_builds_documented_path_and_parses():
    payload = _archive(["btc-updown-5m-1784332800"])
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert "api_key" in request.headers  # value must not leak into logs/paths
        return httpx.Response(200, content=payload)

    client = HttpClient(
        "https://api.pmdata.dev",
        headers={"api_key": "test-key", "User-Agent": "chofer-test"},
        transport=httpx.MockTransport(handler),
    )
    connector = PmdataConnector(_config(), api_key="test-key", client=client)
    unit = _unit()
    fetched = connector.fetch(unit)
    assert seen_paths == ["/polymarket/btc-5m/poly_l2/btc-5m_poly_l2_2026-07-18.zip"]
    parsed = connector.parse(unit, fetched)
    assert "btc-updown-5m-1784332800.parquet" in parsed.tables
    assert fetched.source_sha256 == hashlib.sha256(payload).hexdigest()


def test_fetch_404_maps_to_unavailable():
    client = HttpClient(
        "https://api.pmdata.dev",
        headers={"api_key": "test-key"},
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    connector = PmdataConnector(_config(), api_key="test-key", client=client)
    with pytest.raises(UnavailableError):
        connector.fetch(_unit())


def test_plan_starts_at_recording_start():
    connector = PmdataConnector(
        _config(), api_key="k",
        client=HttpClient("https://api.pmdata.dev",
                          transport=httpx.MockTransport(lambda r: httpx.Response(404))),
    )
    units = connector.plan(start=date(2026, 2, 1), end=date(2026, 2, 14))
    dates = sorted({u.date for u in units})
    assert dates == ["2026-02-13", "2026-02-14"]  # clamped to recording_starts_at
    assert {u.dataset for u in units} == {"poly_l2", "poly_trade"}
    assert all(u.partitions == {"series": "btc-5m"} for u in units)


def test_missing_api_key_rejected():
    with pytest.raises(ValueError, match="PMDATA_KEY"):
        PmdataConnector(_config(), api_key="")
