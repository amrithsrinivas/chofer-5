from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from assembler import builder
from assembler.builder import DatasetAssembler
from assembler.spec import DatasetSpec


def test_holes_are_retained_and_labels_are_attached_after_features(
    two_window_lake: tuple[Path, list, list], minimal_spec: DatasetSpec, tmp_path: Path
) -> None:
    data_dir, _, slugs = two_window_lake
    result = DatasetAssembler(minimal_spec, data_dir=data_dir, output_dir=tmp_path / "out").build()
    rows = pq.read_table(result.path / "rows.parquet").to_pylist()
    assert [row["slug"] for row in rows] == slugs
    assert rows[0]["label"] == 1
    assert rows[0]["label_binary"] == 1
    assert rows[1]["label"] == 0
    assert rows[1]["label_binary"] == 0
    assert rows[0]["up_midprice"] == 0.5
    assert rows[0]["market_midprice"] == 0.5
    assert rows[0]["quote_valid"] is True
    assert "resolution_available_at" in rows[0]
    assert rows[1]["up_midprice"] != rows[1]["up_midprice"]  # typed float NaN
    assert any("file_not_in_complete_manifest" in reason for reason in rows[1]["exclusion_reasons"])
    evidence = pq.read_table(result.path / "evidence.parquet")
    assert "winning_outcome" not in evidence.column_names
    manifest = json.loads((result.path / "manifest.json").read_text())
    assert manifest["assumptions"]["labels_attached_after_features"] is True
    assert manifest["coverage"]["candidate_rows"] == 2


def test_dataset_hash_is_stable_across_output_locations(
    two_window_lake: tuple[Path, list, list], minimal_spec: DatasetSpec, tmp_path: Path
) -> None:
    data_dir, _, _ = two_window_lake
    first = DatasetAssembler(minimal_spec, data_dir=data_dir, output_dir=tmp_path / "one").build()
    second = DatasetAssembler(minimal_spec, data_dir=data_dir, output_dir=tmp_path / "two").build()
    assert first.dataset_id == second.dataset_id
    first_manifest = json.loads((first.path / "manifest.json").read_text())
    second_manifest = json.loads((second.path / "manifest.json").read_text())
    assert first_manifest["label_free_rows_hash"] == second_manifest["label_free_rows_hash"]
    assert first_manifest["inputs"] == second_manifest["inputs"]


def test_build_reports_market_progress(
    two_window_lake: tuple[Path, list, list],
    minimal_spec: DatasetSpec,
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir, _, _ = two_window_lake
    progress: list[dict] = []

    def fake_tqdm(iterable, **kwargs):
        progress.append(kwargs)
        return iterable

    monkeypatch.setattr(builder, "tqdm", fake_tqdm)
    DatasetAssembler(
        minimal_spec,
        data_dir=data_dir,
        output_dir=tmp_path / "progress",
    ).build()

    assert [(item["desc"], item["total"], item["unit"]) for item in progress] == [
        ("Assembling feature rows", 2, "market"),
        ("Reconciling labels", 2, "market"),
    ]
