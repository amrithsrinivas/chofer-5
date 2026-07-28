from __future__ import annotations

from contract import DatasetManifest, ExperimentManifest, InputFileSnapshot

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def snapshot(path: str, digest: str) -> InputFileSnapshot:
    return InputFileSnapshot(
        path=path,
        sha256=digest,
        bytes=100,
        rows=10,
        schema_id="pmdata.poly_l2",
        schema_version="1",
        schema_sha256=SHA_C,
    )


def dataset(*, reverse: bool = False, threshold: int = 51) -> DatasetManifest:
    files = (
        snapshot("raw/a.parquet", SHA_A),
        snapshot("raw/b.parquet", SHA_B),
    )
    if reverse:
        files = tuple(reversed(files))
    return DatasetManifest(
        manifest_version="1",
        dataset_name="btc-5m-human-name",
        specification={
            "eligibility": {"max_price_micros": threshold, "min_price_micros": 49},
            "assets": ["BTC"],
        },
        input_files=files,
        schema_versions={"decision_row": "1", "market_window": "1"},
        code_version="abc123-dirty",
        feature_definition_hashes={"book.midpoint": SHA_A},
        seeds={"sampling": 7},
    )


def test_dataset_behavior_hash_is_canonical_and_file_order_independent() -> None:
    first = dataset()
    reordered = dataset(reverse=True)
    assert first.dataset_id == reordered.dataset_id
    assert first.dataset_id == first.behavior_hash
    assert len(first.dataset_id) == 64


def test_behavioral_mutation_changes_dataset_identity_but_outputs_do_not() -> None:
    first = dataset()
    changed = dataset(threshold=52)
    materialized = first.model_copy(update={"artifact_sha256": SHA_C, "row_count": 288})
    renamed = first.model_copy(update={"dataset_name": "friendly-name-v2"})

    assert first.dataset_id != changed.dataset_id
    assert first.dataset_id == materialized.dataset_id
    assert first.dataset_id == renamed.dataset_id
    assert first.content_hash != materialized.content_hash


def test_experiment_hash_uses_canonical_configs_and_ignores_results() -> None:
    common = {
        "manifest_version": "1",
        "experiment_name": "baseline",
        "dataset_id": dataset().dataset_id,
        "feature_definition_hashes": {"book.midpoint": SHA_A},
        "model_config": {"max_depth": 3, "objective": "binary:logistic"},
        "split_config": {"test": "2026-07", "train": "2026-03/2026-06"},
        "decision_policy": {"edge_micros": 20_000},
        "seeds": {"model": 11},
        "code_version": "abc123-dirty",
        "package_versions": {"python": "3.12", "xgboost": "2"},
    }
    first = ExperimentManifest.model_validate(common)
    reordered = ExperimentManifest.model_validate(
        {**common, "model_config": {"objective": "binary:logistic", "max_depth": 3}}
    )
    result = first.model_copy(
        update={"model_artifact_sha256": SHA_B, "metrics": {"log_loss": 0.69}}
    )

    assert first.experiment_id == reordered.experiment_id
    assert first.experiment_id == result.experiment_id
    changed = ExperimentManifest.model_validate(
        {**common, "model_config": {"max_depth": 4, "objective": "binary:logistic"}}
    )
    assert first.experiment_id != changed.experiment_id
