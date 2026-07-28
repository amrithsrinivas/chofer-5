from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from assembler.spec import DatasetSpec

UTC = timezone.utc


def write_unit(
    data_dir: Path,
    *,
    source: str,
    dataset: str,
    partitions: dict[str, str],
    day: str,
    tables: dict[str, pa.Table],
    status: str = "complete",
) -> Path:
    parts = [source, dataset, *(f"{key}={value}" for key, value in partitions.items()), f"date={day}"]
    unit = "/".join(parts)
    raw_dir = data_dir / "raw" / unit
    raw_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for name, table in tables.items():
        path = raw_dir / name
        pq.write_table(table, path)
        payload = path.read_bytes()
        entries.append(
            {
                "path": f"{unit}/{name}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "rows": table.num_rows,
            }
        )
    manifest = {
        "unit_id": unit,
        "source": source,
        "dataset": dataset,
        "partitions": partitions,
        "date": day,
        "status": status,
        "files": entries,
        "row_count": sum(item["rows"] for item in entries),
        "config_hash": "fixture",
        "code_version": "fixture",
        "run_id": "fixture",
        "started_at": f"{day}T00:00:00Z",
        "finished_at": f"{day}T00:00:01Z",
    }
    path = data_dir / "manifests" / f"{unit}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True))
    return path


def l2_table(slugs: list[str], starts: list[datetime]) -> dict[str, pa.Table]:
    result: dict[str, pa.Table] = {}
    for slug, start in zip(slugs, starts, strict=True):
        result[f"{slug}.parquet"] = pa.table(
            {
                "market_slug": [slug],
                "timestamp": pa.array([start - timedelta(seconds=2)], pa.timestamp("us")),
                "local_timestamp": pa.array([start - timedelta(seconds=1)], pa.timestamp("us")),
                "event_type": ["book"],
                "ask_prices": [[0.51, 0.52]],
                "ask_sizes": [[10.0, 20.0]],
                "bid_prices": [[0.49, 0.48]],
                "bid_sizes": [[12.0, 18.0]],
                "pc_price": pa.array([None], pa.float64()),
                "pc_size": pa.array([None], pa.float64()),
                "pc_side": pa.array([None], pa.string()),
                "winning_outcome": ["SHOULD_NEVER_REACH_FEATURES"],
            }
        )
    return result


def gamma_table(starts: list[datetime], winners: list[str | None]) -> pa.Table:
    slugs = [f"btc-updown-5m-{int(start.timestamp())}" for start in starts]
    return pa.table(
        {
            "series_id": ["10684"] * len(starts),
            "market_id": [f"market-{index}" for index in range(len(starts))],
            "condition_id": [f"condition-{index}" for index in range(len(starts))],
            "slug": slugs,
            "outcomes_raw": ['["Up","Down"]'] * len(starts),
            "outcomes_count": pa.array([2] * len(starts), pa.int64()),
            "clob_token_ids_raw": [f'["up-{index}","down-{index}"]' for index in range(len(starts))],
            "clob_token_ids_count": pa.array([2] * len(starts), pa.int64()),
            "end_date": [(start + timedelta(minutes=5)).isoformat().replace("+00:00", "Z") for start in starts],
            "winning_outcome": winners,
            "uma_resolution_status": ["resolved" if winner else None for winner in winners],
            "resolution_source": ["fixture-secret-label-source"] * len(starts),
        }
    )


@pytest.fixture
def two_window_lake(tmp_path: Path) -> tuple[Path, list[datetime], list[str]]:
    data_dir = tmp_path / "lake"
    starts = [datetime(2026, 1, 1, 0, 0, tzinfo=UTC), datetime(2026, 1, 1, 0, 5, tzinfo=UTC)]
    slugs = [f"btc-updown-5m-{int(start.timestamp())}" for start in starts]
    write_unit(
        data_dir,
        source="polymarket",
        dataset="market_metadata",
        partitions={"series_id": "10684"},
        day="2026-01-02",
        tables={"data.parquet": gamma_table(starts, ["Up", "Down"])},
    )
    # The second candidate is intentionally absent from a complete PMData manifest.
    write_unit(
        data_dir,
        source="pmdata",
        dataset="poly_l2",
        partitions={"series": "btc-5m"},
        day="2026-01-01",
        tables=l2_table(slugs[:1], starts[:1]),
    )
    return data_dir, starts, slugs


@pytest.fixture
def minimal_spec(tmp_path: Path) -> DatasetSpec:
    return DatasetSpec.model_validate(
        {
            "spec_version": "1",
            "name": "fixture",
            "assets": ["BTC"],
            "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:10:00Z"},
            "decision_schedules": [{"name": "at_start", "anchor": "window_start", "offset": "0s"}],
            "features": ["up_midprice", "time_of_day_sin", "time_of_day_cos"],
            "availability_policies": {"pmdata": {"version": "pmdata_local_timestamp_utc_clamped_v1"}},
            "quote": {"max_staleness_seconds": 5, "band_min": 0.49, "band_max": 0.51, "depth_levels": 5},
            "sources": {"poly_l2": "required"},
            "execution_delay": "250ms",
            "output_path": str(tmp_path / "declared-output"),
        }
    )
