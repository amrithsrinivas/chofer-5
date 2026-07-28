"""PMData connector tests: synthetic ZIPs of parquet members, no network."""

import hashlib

import httpx
import pyarrow as pa
import pytest

from indexer.catalog import PmdataConfig, PmdataSeries
from indexer.connectors.pmdata import (
    PmdataConnector,
    parse_day_archive,
    parse_slug_parquet,
    validate_poly_trade_member,
)
from indexer.http import HttpClient, MalformedError, UnavailableError
from indexer.units import DataUnit

from .conftest import make_zip, sample_table, table_to_parquet_bytes
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


def test_empty_members_skipped_with_warning():
    # Live archives include 0-row members for markets with no activity that
    # day (their market_slug column is empty); they must not fail the day.
    payload = make_zip(
        {
            "btc-updown-5m-1784332800.parquet": table_to_parquet_bytes(
                sample_table("btc-updown-5m-1784332800")
            ),
            "btc-updown-5m-1784333100.parquet": table_to_parquet_bytes(
                sample_table("btc-updown-5m-1784333100", 0)
            ),
        }
    )
    parsed = parse_day_archive(
        payload, source="pmdata", source_key="/x.zip", source_sha256="a" * 64
    )
    assert list(parsed.tables) == ["btc-updown-5m-1784332800.parquet"]
    assert any("empty member" in w for w in parsed.warnings)


def test_all_members_empty_fails_loudly():
    payload = make_zip(
        {"a.parquet": table_to_parquet_bytes(sample_table("a", 0))}
    )
    with pytest.raises(MalformedError, match="no data at all"):
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


# ── poly_trade/v2 member validation ──────────────────────────────────────────


def _trade_table(
    slug: str = "btc-updown-5m-1784332800",
    *,
    outcomes=("yes", "no", "yes"),
    prices=(0.51, 0.49, 0.52),
    sizes=(10.0, 5.0, 2.0),
    sides=("BUY", "SELL", "BUY"),
    event_types=("last_trade_price", "last_trade_price", "last_trade_price"),
) -> pa.Table:
    n = len(outcomes)
    return pa.table(
        {
            "market_slug": [slug] * n,
            "timestamp": pa.array(
                [1784332800000000 + i * 1000 for i in range(n)], pa.timestamp("us")
            ),
            "local_timestamp": pa.array(
                [1784332800000500 + i * 1000 for i in range(n)], pa.timestamp("us")
            ),
            "event_type": list(event_types),
            "outcome": list(outcomes),
            "price": pa.array(list(prices), pa.float64()),
            "size": pa.array(list(sizes), pa.float64()),
            "side": list(sides),
        }
    )


def test_valid_poly_trade_member_passes():
    flags = validate_poly_trade_member(
        _trade_table(), member_name="m.parquet", context="/x.zip"
    )
    assert flags == {"outcome_alias": 0, "boundary_price": 0}


def test_poly_trade_null_outcome_rejected_as_corrupted_export():
    table = _trade_table(outcomes=("yes", None, "yes"))
    with pytest.raises(MalformedError, match="NULL outcome"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_unknown_outcome_rejected():
    table = _trade_table(outcomes=("yes", "maybe", "no"))
    with pytest.raises(MalformedError, match="unknown outcome"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_up_alias_accepted_and_counted():
    table = _trade_table(outcomes=("yes", "Up", "no"))
    flags = validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")
    assert flags["outcome_alias"] == 1


@pytest.mark.parametrize("boundary", [0.0, 1.0])
def test_poly_trade_boundary_price_accepted_and_counted(boundary):
    table = _trade_table(prices=(0.5, boundary, 0.4))
    flags = validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")
    assert flags["boundary_price"] == 1


@pytest.mark.parametrize("bad_price", [-0.1, 1.5])
def test_poly_trade_out_of_band_price_rejected(bad_price):
    table = _trade_table(prices=(0.5, bad_price, 0.4))
    with pytest.raises(MalformedError, match="outside the closed interval"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_nan_price_rejected():
    table = _trade_table(prices=(0.5, float("nan"), 0.4))
    with pytest.raises(MalformedError, match="outside the closed interval"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_nonpositive_size_rejected():
    table = _trade_table(sizes=(10.0, 0.0, 2.0))
    with pytest.raises(MalformedError, match="non-positive"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_unknown_event_type_rejected():
    table = _trade_table(event_types=("last_trade_price", "book", "last_trade_price"))
    with pytest.raises(MalformedError, match="unknown event_type"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_unknown_side_rejected():
    table = _trade_table(sides=("BUY", "HOLD", "SELL"))
    with pytest.raises(MalformedError, match="unknown side"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_poly_trade_missing_required_column_rejected():
    table = _trade_table().drop_columns(["outcome"])
    with pytest.raises(MalformedError, match="missing required column"):
        validate_poly_trade_member(table, member_name="m.parquet", context="/x.zip")


def test_parse_poly_trade_day_validates_members_and_warns_one_sided():
    yes_only = _trade_table(outcomes=("yes",) * 3)
    payload = make_zip(
        {"btc-updown-5m-1784332800.parquet": table_to_parquet_bytes(yes_only)}
    )
    parsed = parse_day_archive(
        payload,
        source="pmdata",
        source_key="/x.zip",
        source_sha256="a" * 64,
        dataset="poly_trade",
    )
    assert parsed.tables["btc-updown-5m-1784332800.parquet"].num_rows == 3
    assert any("one-sided" in w for w in parsed.warnings)


def test_parse_poly_trade_day_surfaces_alias_and_boundary_warnings():
    table = _trade_table(outcomes=("yes", "Up", "no"), prices=(0.5, 1.0, 0.4))
    payload = make_zip(
        {"btc-updown-5m-1784332800.parquet": table_to_parquet_bytes(table)}
    )
    parsed = parse_day_archive(
        payload,
        source="pmdata",
        source_key="/x.zip",
        source_sha256="a" * 64,
        dataset="poly_trade",
    )
    assert any("'Up' outcome alias" in w for w in parsed.warnings)
    assert any("boundary price" in w for w in parsed.warnings)


def test_parse_poly_trade_day_rejects_null_outcome_member():
    bad = _trade_table(outcomes=("yes", None, "yes"))
    payload = make_zip(
        {"btc-updown-5m-1784332800.parquet": table_to_parquet_bytes(bad)}
    )
    with pytest.raises(MalformedError, match="NULL outcome"):
        parse_day_archive(
            payload,
            source="pmdata",
            source_key="/x.zip",
            source_sha256="a" * 64,
            dataset="poly_trade",
        )


def test_parse_poly_l2_day_skips_poly_trade_validation():
    # poly_l2 members have no outcome column; validation must not apply.
    payload = _archive(["btc-updown-5m-1784332800"])
    parsed = parse_day_archive(
        payload,
        source="pmdata",
        source_key="/x.zip",
        source_sha256="a" * 64,
        dataset="poly_l2",
    )
    assert "btc-updown-5m-1784332800.parquet" in parsed.tables


def test_chainlink_daily_file_planning_is_disabled():
    connector = PmdataConnector(_config(), api_key="test", client=object())
    units = connector.plan(
        dataset="chainlink_streams",
        start=date(2026, 7, 18),
        end=date(2026, 7, 18),
    )
    assert units == []
