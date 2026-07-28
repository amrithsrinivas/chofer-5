from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from xgboost import XGBClassifier

from training import cli
from training.artifact import validate_selected_features
from training.trainer import train_experiment


def test_real_xgboost_cli_writes_complete_content_addressed_artifact(
    experiment_spec: Path, tmp_path: Path, capsys
) -> None:
    output_root = tmp_path / "experiments"
    rc = cli.main([str(experiment_spec), "--output-dir", str(output_root)])
    assert rc == 0
    artifact_dir = Path(capsys.readouterr().out.strip())
    assert artifact_dir.parent == output_root
    assert len(artifact_dir.name) == 64

    expected_files = {
        "model.ubj",
        "calibrator.json",
        "predictions.parquet",
        "metrics.json",
        "feature_importance.json",
        "manifest.json",
    }
    assert {path.name for path in artifact_dir.iterdir()} == expected_files
    assert not list(artifact_dir.glob("*.pkl"))

    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["experiment_id"] == artifact_dir.name
    assert manifest["dataset"]["dataset_id"] == "synthetic-btc-v1"
    assert manifest["selected_features"] == ["signal", "with_nan"]
    assert manifest["training"]["early_stopping_used"] is True
    assert manifest["packages"]["xgboost"]
    assert manifest["seeds"] == {"calibration": 17, "xgboost": 17}
    for name, details in manifest["checksums"].items():
        actual = hashlib.sha256((artifact_dir / name).read_bytes()).hexdigest()
        assert actual == details["sha256"]

    calibrator = json.loads((artifact_dir / "calibrator.json").read_text(encoding="utf-8"))
    assert calibrator["method"] == "platt"
    assert set(calibrator) == {"method", "coefficient", "intercept", "fitted_rows"}

    predictions = pq.read_table(artifact_dir / "predictions.parquet").to_pandas()
    assert len(predictions) == 24
    assert predictions["row_id"].is_unique
    assert predictions.groupby("slug")["split"].nunique().max() == 1
    assert predictions["raw_probability"].between(0, 1).all()
    assert predictions["calibrated_probability"].between(0, 1).all()
    assert set(
        [
            "up_decision_ask",
            "down_decision_ask",
            "up_execution_ask",
            "down_execution_ask",
            "quote_valid",
        ]
    ).issubset(predictions.columns)

    metrics = json.loads((artifact_dir / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics["models"]) == {
        "xgboost_raw",
        "xgboost_calibrated",
        "constant_0_5",
        "market_midprice",
    }
    validation_exclusions = metrics["exclusions"]["by_split"]["validation"]
    assert validation_exclusions["exclusion_reasons"] == {
        "resolution_unavailable_at_fit": 2
    }
    assert metrics["models"]["xgboost_raw"]["test"]["rows"] == 6

    loaded = XGBClassifier()
    loaded.load_model(artifact_dir / "model.ubj")
    assert loaded.get_booster().num_boosted_rounds() >= 1

    # Exact reruns address the same immutable directory and leave its manifest intact.
    original_manifest = (artifact_dir / "manifest.json").read_bytes()
    assert train_experiment(experiment_spec, output_root) == artifact_dir
    assert (artifact_dir / "manifest.json").read_bytes() == original_manifest


def test_cli_rejects_dataset_id_mismatch(
    experiment_spec: Path, tmp_path: Path, capsys
) -> None:
    text = experiment_spec.read_text(encoding="utf-8")
    experiment_spec.write_text(
        text.replace('dataset_id = "synthetic-btc-v1"', 'dataset_id = "wrong-id"'),
        encoding="utf-8",
    )
    rc = cli.main(
        [str(experiment_spec), "--output-dir", str(tmp_path / "experiments")]
    )
    assert rc == 2
    assert "dataset ID guard failed" in capsys.readouterr().err


def test_all_null_selected_feature_is_rejected() -> None:
    rows = pd.DataFrame({"valid": [1.0, 2.0], "empty": [None, None]})
    try:
        validate_selected_features(rows, ("valid", "empty"), "all")
    except ValueError as exc:
        assert "selected features are all-null: empty" in str(exc)
    else:
        raise AssertionError("all-null feature was accepted")


def test_one_hot_asset_encoding_is_recorded(
    experiment_spec: Path, tmp_path: Path
) -> None:
    text = experiment_spec.read_text(encoding="utf-8")
    experiment_spec.write_text(
        text.replace(
            "\n[dataset]",
            '\nasset_encoding = "one_hot"\n\n[dataset]',
            1,
        ),
        encoding="utf-8",
    )
    artifact_dir = train_experiment(experiment_spec, tmp_path / "experiments")
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config"]["asset_encoding"] == "one_hot"
    assert manifest["selected_features"] == [
        "signal",
        "with_nan",
        "asset_identity_BTC",
    ]
