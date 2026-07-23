"""Binance Vision daily archives: spot klines, aggTrades, UM futures metrics
and premium-index klines. Verifies the sibling .CHECKSUM SHA-256 before parsing.

Raw zone keeps source-native columns and timestamp values untouched; timestamp
magnitude (ms vs us) is validated and recorded as a manifest warning.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.csv as pacsv

from ..catalog import BinanceDataset
from ..http import HttpClient, MalformedError, TransientError
from ..units import DataUnit, day_range
from .base import Connector, FetchedUnit, ParsedUnit, with_provenance

log = logging.getLogger(__name__)

BASE_URL = "https://data.binance.vision"

KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
KLINE_TYPES: dict[str, pa.DataType] = {
    "open_time": pa.int64(),
    "open": pa.float64(),
    "high": pa.float64(),
    "low": pa.float64(),
    "close": pa.float64(),
    "volume": pa.float64(),
    "close_time": pa.int64(),
    "quote_volume": pa.float64(),
    "count": pa.int64(),
    "taker_buy_volume": pa.float64(),
    "taker_buy_quote_volume": pa.float64(),
    "ignore": pa.string(),
}
KLINE_TS_COLUMNS = ["open_time", "close_time"]

# Live-verified layout (2026-07, spot archives probed back to 2022): headerless
# 8 columns — the documented 7 plus the trailing "M" flag from the aggTrade
# payload, is_best_match (vestigial; observed constant True, kept source-native).
AGG_TRADE_COLUMNS = [
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
    "is_best_match",
]
AGG_TRADE_TYPES: dict[str, pa.DataType] = {
    "agg_trade_id": pa.int64(),
    "price": pa.float64(),
    "quantity": pa.float64(),
    "first_trade_id": pa.int64(),
    "last_trade_id": pa.int64(),
    "transact_time": pa.int64(),
    "is_buyer_maker": pa.bool_(),
    "is_best_match": pa.bool_(),
}
AGG_TRADE_TS_COLUMNS = ["transact_time"]

# Live-verified layout (2026-07): metrics CSVs carry a header row and exactly
# these 8 columns. create_time is a datetime *string* ("2026-07-20 00:00:00"),
# not an epoch — it is kept source-native and excluded from magnitude checks.
# Because create_time is non-numeric, a headerless metrics file would be
# misdetected as having a header; real archives always carry the header.
METRICS_COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]
METRICS_TYPES: dict[str, pa.DataType] = {
    "create_time": pa.string(),
    "symbol": pa.string(),
    "sum_open_interest": pa.float64(),
    "sum_open_interest_value": pa.float64(),
    "count_toptrader_long_short_ratio": pa.float64(),
    "sum_toptrader_long_short_ratio": pa.float64(),
    "count_long_short_ratio": pa.float64(),
    "sum_taker_long_short_vol_ratio": pa.float64(),
}
METRICS_TS_COLUMNS: list[str] = []  # create_time is a string, not an epoch


class DatasetSpec:
    def __init__(
        self,
        key_template: str,
        columns: list[str],
        column_types: dict[str, pa.DataType],
        ts_columns: list[str],
    ) -> None:
        self.key_template = key_template
        self.columns = columns
        self.column_types = column_types
        self.ts_columns = ts_columns

    def object_key(self, symbol: str, day: date) -> str:
        return self.key_template.format(symbol=symbol, date=day.isoformat())


DATASET_SPECS: dict[str, DatasetSpec] = {
    "spot_klines": DatasetSpec(
        "data/spot/daily/klines/{symbol}/1m/{symbol}-1m-{date}.zip",
        KLINE_COLUMNS,
        KLINE_TYPES,
        KLINE_TS_COLUMNS,
    ),
    "agg_trades": DatasetSpec(
        "data/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{date}.zip",
        AGG_TRADE_COLUMNS,
        AGG_TRADE_TYPES,
        AGG_TRADE_TS_COLUMNS,
    ),
    "futures_metrics": DatasetSpec(
        "data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip",
        METRICS_COLUMNS,
        METRICS_TYPES,
        METRICS_TS_COLUMNS,
    ),
    "premium_index_klines": DatasetSpec(
        "data/futures/um/daily/premiumIndexKlines/{symbol}/1m/{symbol}-1m-{date}.zip",
        KLINE_COLUMNS,
        KLINE_TYPES,
        KLINE_TS_COLUMNS,
    ),
}


def detect_timestamp_unit(values: list[int], column: str) -> str:
    """Validate epoch-magnitude and return 'ms' or 'us'; raise otherwise."""
    if not values:
        return "unknown"
    magnitude = max(abs(v) for v in values)
    if 10**11 <= magnitude < 10**14:
        return "ms"
    if 10**14 <= magnitude < 10**17:
        return "us"
    raise MalformedError(
        f"timestamp column {column!r} has unexpected magnitude {magnitude} "
        "(expected epoch milliseconds or microseconds)"
    )


def parse_daily_csv(
    spec: DatasetSpec, csv_bytes: bytes, *, dataset: str
) -> tuple[pa.Table, list[str]]:
    """Parse one daily CSV into a typed table. Returns (table, warnings)."""
    warnings: list[str] = []
    text = csv_bytes.decode("utf-8")
    if not text.strip():
        raise MalformedError(f"{dataset}: empty CSV payload")
    first_line = text.splitlines()[0]
    first_fields = next(csv.reader([first_line]))
    try:
        int(first_fields[0])
        has_header = False
    except ValueError:
        has_header = True

    expected = spec.columns
    if has_header:
        names = [field.strip() for field in first_fields]
        if len(names) != len(expected):
            raise MalformedError(
                f"{dataset}: header has {len(names)} columns, expected {len(expected)}"
            )
        if names != expected:
            warnings.append(
                f"{dataset}: header names {names} differ from expected {expected}; "
                "keeping source header names"
            )
    else:
        names = expected

    column_types = {name: spec.column_types[exp] for name, exp in zip(names, expected)}
    try:
        table = pacsv.read_csv(
            io.BytesIO(csv_bytes),
            read_options=pacsv.ReadOptions(
                column_names=names, skip_rows=1 if has_header else 0
            ),
            convert_options=pacsv.ConvertOptions(
                column_types=column_types,
                true_values=["True", "true", "TRUE"],
                false_values=["False", "false", "FALSE"],
            ),
        )
    except pa.ArrowInvalid as exc:
        raise MalformedError(f"{dataset}: CSV parse failed: {exc}") from exc
    if table.num_columns != len(expected):
        raise MalformedError(
            f"{dataset}: parsed {table.num_columns} columns, expected {len(expected)}"
        )

    for ts_col in spec.ts_columns:
        if ts_col not in table.column_names:
            raise MalformedError(f"{dataset}: missing timestamp column {ts_col!r}")
        values = table.column(ts_col).drop_null().to_pylist()
        unit = detect_timestamp_unit(values, ts_col)
        if unit != "unknown":
            warnings.append(f"{dataset}: {ts_col} detected as epoch {unit}")
    return table, warnings


def parse_daily_zip(
    spec: DatasetSpec, zip_bytes: bytes, *, dataset: str
) -> tuple[pa.Table, list[str]]:
    """Unzip a daily archive in memory; expect exactly one CSV member."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise MalformedError(f"{dataset}: not a valid ZIP archive") from exc
    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) != 1:
        raise MalformedError(
            f"{dataset}: expected exactly 1 file in ZIP, found "
            f"{[m.filename for m in members]}"
        )
    member = members[0]
    if not member.filename.lower().endswith(".csv"):
        raise MalformedError(
            f"{dataset}: unexpected ZIP member type {member.filename!r} (expected CSV)"
        )
    return parse_daily_csv(spec, archive.read(member), dataset=dataset)


class BinanceVisionConnector(Connector):
    source = "binance"

    def __init__(
        self, datasets: dict[str, BinanceDataset], client: HttpClient | None = None
    ) -> None:
        self._datasets = datasets
        self._client = client or HttpClient(
            BASE_URL, headers={"User-Agent": "chofer/0.1"}
        )

    def plan(
        self,
        *,
        dataset: str | None = None,
        entity: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DataUnit]:
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        start = start or yesterday
        end = end or yesterday
        units: list[DataUnit] = []
        for name, cfg in self._datasets.items():
            if name not in DATASET_SPECS:
                log.warning("no dataset spec for configured dataset %r; skipping", name)
                continue
            if dataset and name != dataset:
                continue
            for asset, symbol in cfg.tickers.items():
                if entity and entity not in (asset, symbol):
                    continue
                partitions: dict[str, str] = {"symbol": symbol}
                if cfg.interval:
                    partitions["interval"] = cfg.interval
                for day in day_range(start, end):
                    units.append(
                        DataUnit(
                            source=self.source,
                            dataset=name,
                            partitions=partitions,
                            date=day.isoformat(),
                        )
                    )
        return units

    def fetch(self, unit: DataUnit) -> FetchedUnit:
        spec = DATASET_SPECS[unit.dataset]
        day = date.fromisoformat(unit.date)
        key = spec.object_key(unit.partitions["symbol"], day)
        payload = self._client.get_bytes(key)
        try:
            checksum_body = self._client.get_bytes(f"{key}.CHECKSUM")
        except TransientError as exc:
            raise TransientError(f"checksum unavailable for {key}: {exc}") from exc
        expected_sha = checksum_body.decode("utf-8").split()[0].lower()
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            raise MalformedError(
                f"checksum mismatch for {key}: expected {expected_sha}, got {actual_sha}"
            )
        return FetchedUnit(payload=payload, source_key=key, source_sha256=actual_sha)

    def parse(self, unit: DataUnit, fetched: FetchedUnit) -> ParsedUnit:
        spec = DATASET_SPECS[unit.dataset]
        assert isinstance(fetched.payload, bytes)
        table, warnings = parse_daily_zip(
            spec, fetched.payload, dataset=unit.dataset
        )
        table = with_provenance(
            table,
            source=self.source,
            source_key=fetched.source_key,
            source_sha256=fetched.source_sha256,
        )
        min_ts = max_ts = None
        minmax_col = spec.ts_columns[0] if spec.ts_columns else (
            "create_time" if "create_time" in table.column_names else None
        )
        if minmax_col and table.num_rows:
            col = table.column(minmax_col).drop_null().to_pylist()
            if col:
                min_ts, max_ts = str(min(col)), str(max(col))
        return ParsedUnit(
            tables={"data.parquet": table},
            min_ts=min_ts,
            max_ts=max_ts,
            warnings=warnings,
        )
