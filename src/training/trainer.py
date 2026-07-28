"""Deterministic, content-addressed XGBoost experiment runner."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from xgboost import XGBClassifier

from .artifact import (
    DatasetArtifact,
    canonical_json_hash,
    load_dataset_artifact,
    sha256_file,
    validate_selected_features,
)
from .calibration import (
    ProbabilityCalibrator,
    apply_calibrator,
    fit_platt_calibrator,
    identity_calibrator,
)
from .metrics import binary_metrics
from .spec import ExperimentSpec, load_experiment_spec, spec_as_jsonable
from .split import SplitResult, chronological_split

ARTIFACT_SCHEMA_VERSION = 1


class TrainingError(ValueError):
    """Actionable experiment/data validation failure."""


def _utc_iso(value: datetime | pd.Timestamp) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    payload = json.dumps(
        value,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _source_hash() -> str:
    package_dir = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_dir.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_metadata() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--short"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        commit = None
        dirty = None
    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "training_source_sha256": _source_hash(),
    }


def _package_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for distribution in ("numpy", "pandas", "pyarrow", "scikit-learn", "xgboost"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "unknown"
    return versions


def _select_features(
    artifact: DatasetArtifact, spec: ExperimentSpec
) -> tuple[tuple[str, ...], pd.DataFrame]:
    requested: str | list[str]
    if spec.features == "all":
        requested = "all"
    else:
        requested = list(spec.features)
    try:
        selected, numeric = validate_selected_features(
            artifact.rows, artifact.feature_columns, requested
        )
    except ValueError as exc:
        raise TrainingError(str(exc)) from exc
    if spec.asset_encoding == "none":
        return selected, numeric

    asset_values = artifact.rows["asset"].astype(str)
    categories = sorted(asset_values.unique())
    if not categories or any(not category for category in categories):
        raise TrainingError("one-hot asset encoding requires non-empty asset values")
    encoded_names: list[str] = []
    for category in categories:
        feature_name = f"asset_identity_{category}"
        if feature_name in numeric.columns:
            raise TrainingError(f"derived asset feature collides with {feature_name!r}")
        numeric[feature_name] = (asset_values == category).astype(float)
        encoded_names.append(feature_name)
    return (*selected, *encoded_names), numeric


def _fit_eligibility(
    rows: pd.DataFrame, split: SplitResult
) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    eligible = pd.Series(False, index=rows.index, dtype=bool)
    reasons = split.exclusion_reasons.copy().astype("string")
    reasons.loc[split.assignments == "test"] = "evaluation_only"
    labeled = rows["label_binary"].notna()
    report: dict[str, Any] = {
        "resolution_availability_enforced": "resolution_available_at" in rows.columns,
        "model_fit_at": _utc_iso(split.model_fit_at),
        "by_split": {},
    }

    for split_name in ("train", "validation"):
        in_split = split.assignments == split_name
        candidate = in_split & labeled
        local_reason = pd.Series("", index=rows.index, dtype="string")
        local_reason.loc[in_split & ~labeled] = "unlabeled"
        local_reason.loc[candidate & (rows["decision_at"] >= split.model_fit_at)] = (
            "decision_not_before_fit"
        )
        local_reason.loc[
            candidate
            & (local_reason == "")
            & (rows["window_end"] >= split.model_fit_at)
        ] = "window_not_ended_by_fit"
        if "resolution_available_at" in rows.columns:
            resolution = rows["resolution_available_at"]
            local_reason.loc[candidate & (local_reason == "") & resolution.isna()] = (
                "resolution_availability_missing"
            )
            local_reason.loc[
                candidate
                & (local_reason == "")
                & (resolution > split.model_fit_at)
            ] = "resolution_unavailable_at_fit"
        local_eligible = in_split & labeled & (local_reason == "")
        eligible.loc[local_eligible] = True
        reasons.loc[in_split] = local_reason.loc[in_split]
        counts = Counter(local_reason.loc[in_split & ~local_eligible].tolist())
        counts.pop("", None)
        report["by_split"][split_name] = {
            "assigned_rows": int(in_split.sum()),
            "fit_rows": int(local_eligible.sum()),
            "excluded_rows": int((in_split & ~local_eligible).sum()),
            "exclusion_reasons": dict(sorted(counts.items())),
        }
    report["by_split"]["test"] = {
        "assigned_rows": int((split.assignments == "test").sum()),
        "fit_rows": 0,
        "excluded_rows": int((split.assignments == "test").sum()),
        "exclusion_reasons": {"evaluation_only": int((split.assignments == "test").sum())},
    }
    report["outside_or_purged_rows"] = int((split.assignments == "excluded").sum())
    return eligible, reasons, report


def _fit_model(
    features: pd.DataFrame,
    labels: pd.Series,
    train_mask: pd.Series,
    validation_mask: pd.Series,
    spec: ExperimentSpec,
) -> tuple[XGBClassifier, dict[str, Any]]:
    y_train = labels.loc[train_mask].astype(int).to_numpy()
    y_validation = labels.loc[validation_mask].astype(int).to_numpy()
    if len(y_train) == 0 or len(y_validation) == 0:
        raise TrainingError("training and validation must each have fit-eligible rows")
    if np.unique(y_train).size != 2:
        raise TrainingError("fit-eligible training labels must contain both classes")

    params: dict[str, Any] = {
        "n_estimators": 100,
        "max_depth": 3,
        "learning_rate": 0.05,
        "eval_metric": "logloss",
        **spec.xgboost.params,
        "objective": "binary:logistic",
        "n_jobs": 1,
        "tree_method": "hist",
        "device": "cpu",
        "random_state": spec.xgboost.seed,
    }
    early_stopping_used = spec.xgboost.early_stopping_rounds is not None
    if early_stopping_used:
        params["early_stopping_rounds"] = spec.xgboost.early_stopping_rounds

    model = XGBClassifier(**params)
    x_train = features.loc[train_mask]
    x_validation = features.loc[validation_mask]
    try:
        model.fit(
            x_train,
            y_train,
            eval_set=[(x_validation, y_validation)],
            verbose=False,
        )
    except TypeError as exc:
        if not early_stopping_used or "early_stopping" not in str(exc):
            raise
        params.pop("early_stopping_rounds", None)
        early_stopping_used = False
        model = XGBClassifier(**params)
        model.fit(
            x_train,
            y_train,
            eval_set=[(x_validation, y_validation)],
            verbose=False,
        )

    training_info = {
        "effective_params": params,
        "early_stopping_requested": spec.xgboost.early_stopping_rounds is not None,
        "early_stopping_used": early_stopping_used,
        "best_iteration": getattr(model, "best_iteration", None)
        if early_stopping_used
        else None,
        "train_rows": int(len(y_train)),
        "validation_rows": int(len(y_validation)),
    }
    return model, training_info


def _fit_calibration(
    method: str,
    raw_probabilities: np.ndarray,
    labels: pd.Series,
    validation_mask: pd.Series,
    seed: int,
) -> ProbabilityCalibrator:
    if method == "none":
        return identity_calibrator()
    try:
        return fit_platt_calibrator(
            raw_probabilities[validation_mask.to_numpy()],
            labels.loc[validation_mask].astype(int).to_numpy(),
            seed=seed,
        )
    except ValueError as exc:
        raise TrainingError(str(exc)) from exc


def _metric_for_rows(
    labels: pd.Series, probabilities: pd.Series | np.ndarray
) -> dict[str, Any]:
    p = np.asarray(probabilities, dtype=float)
    y = labels.astype(int).to_numpy()
    finite = np.isfinite(p)
    if not finite.any():
        return {"rows": 0, "missing_probability_rows": int(len(p))}
    result = binary_metrics(y[finite], p[finite])
    result["missing_probability_rows"] = int((~finite).sum())
    return result


def _all_metrics(predictions: pd.DataFrame) -> dict[str, Any]:
    models = {
        "xgboost_raw": "raw_probability",
        "xgboost_calibrated": "calibrated_probability",
        "constant_0_5": "constant_0_5_probability",
        "market_midprice": "market_midprice_probability",
    }
    result: dict[str, Any] = {}
    for model_name, probability_column in models.items():
        segments: dict[str, Any] = {}
        for split_name in ("all", "train", "validation", "test"):
            segment = (
                predictions
                if split_name == "all"
                else predictions.loc[predictions["split"] == split_name]
            )
            if segment.empty:
                segments[split_name] = {"rows": 0, "missing_probability_rows": 0}
            else:
                segments[split_name] = _metric_for_rows(
                    cast(pd.Series, segment["label_binary"]),
                    cast(pd.Series, segment[probability_column]),
                )
        result[model_name] = segments
    return result


def _importance_value(value: float | list[float] | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, list):
        return float(sum(value))
    return float(value)


def _feature_importance(
    model: XGBClassifier, selected_features: tuple[str, ...]
) -> dict[str, Any]:
    booster = model.get_booster()
    gain = booster.get_score(importance_type="gain")
    weight = booster.get_score(importance_type="weight")
    return {
        feature: {
            "gain": _importance_value(gain.get(feature)),
            "weight": _importance_value(weight.get(feature)),
        }
        for feature in selected_features
    }


def _predictions_frame(
    artifact: DatasetArtifact,
    split: SplitResult,
    fit_eligible: pd.Series,
    fit_reasons: pd.Series,
    raw_probabilities: np.ndarray,
    calibrated_probabilities: np.ndarray,
) -> pd.DataFrame:
    labeled = artifact.rows["label_binary"].notna()
    columns = list(artifact.identity_quote_columns)
    predictions = artifact.rows.loc[labeled, columns].copy()
    predictions["split"] = split.assignments.loc[labeled].astype(str)
    predictions["fit_eligible"] = fit_eligible.loc[labeled].astype(bool)
    predictions["fit_exclusion_reason"] = fit_reasons.loc[labeled].fillna("").astype(str)
    predictions["raw_probability"] = raw_probabilities[labeled.to_numpy()]
    predictions["calibrated_probability"] = calibrated_probabilities[labeled.to_numpy()]
    predictions["constant_0_5_probability"] = 0.5
    predictions["market_midprice_probability"] = predictions["market_midprice"].astype(float)
    predictions = predictions.sort_values(
        ["decision_at", "row_id"], kind="mergesort"
    ).reset_index(drop=True)
    return predictions


def _artifact_checksums(directory: Path, names: list[str]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "sha256": sha256_file(directory / name),
            "bytes": (directory / name).stat().st_size,
        }
        for name in names
    }


def _commit_atomic(temp_dir: Path, final_dir: Path) -> Path:
    if final_dir.exists():
        manifest_path = final_dir / "manifest.json"
        if not manifest_path.is_file():
            raise TrainingError(f"experiment destination exists but is incomplete: {final_dir}")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("experiment_id") != final_dir.name:
            raise TrainingError(f"experiment destination has a mismatched manifest: {final_dir}")
        shutil.rmtree(temp_dir)
        return final_dir
    try:
        os.replace(temp_dir, final_dir)
    except OSError:
        if final_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            return final_dir
        raise
    return final_dir


def train_experiment(spec_path: Path, output_root: Path) -> Path:
    """Run one experiment and atomically return its immutable artifact directory."""

    spec = load_experiment_spec(spec_path)
    try:
        artifact = load_dataset_artifact(spec.dataset.path, spec.dataset.dataset_id)
    except (FileNotFoundError, ValueError) as exc:
        raise TrainingError(str(exc)) from exc
    selected_features, numeric_features = _select_features(artifact, spec)
    try:
        split = chronological_split(artifact.rows, spec.split)
    except ValueError as exc:
        raise TrainingError(str(exc)) from exc
    fit_eligible, fit_reasons, exclusion_report = _fit_eligibility(artifact.rows, split)
    train_mask = fit_eligible & (split.assignments == "train")
    validation_mask = fit_eligible & (split.assignments == "validation")

    labels = cast(pd.Series, artifact.rows["label_binary"])
    model, training_info = _fit_model(
        numeric_features,
        labels,
        train_mask,
        validation_mask,
        spec,
    )
    raw_probabilities = model.predict_proba(numeric_features)[:, 1].astype(float)
    calibrator = _fit_calibration(
        spec.calibration.method,
        raw_probabilities,
        labels,
        validation_mask,
        spec.xgboost.seed,
    )
    calibrated_probabilities = apply_calibrator(raw_probabilities, calibrator)
    predictions = _predictions_frame(
        artifact,
        split,
        fit_eligible,
        fit_reasons,
        raw_probabilities,
        calibrated_probabilities,
    )

    code = _git_metadata()
    package_versions = _package_versions()
    config = spec_as_jsonable(spec)
    config_hash = canonical_json_hash(config)
    identity_payload = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "dataset_hash": artifact.dataset_hash,
        "config_hash": config_hash,
        "training_source_sha256": code["training_source_sha256"],
        "package_versions": package_versions,
        "seed": spec.xgboost.seed,
    }
    experiment_id = canonical_json_hash(identity_payload)
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / experiment_id
    if final_dir.exists():
        manifest_path = final_dir / "manifest.json"
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("experiment_id") == experiment_id:
                return final_dir
        raise TrainingError(f"experiment destination already exists and is invalid: {final_dir}")

    temp_dir = output_root / f".{experiment_id}.tmp-{uuid.uuid4().hex}"
    temp_dir.mkdir()
    try:
        model_path = temp_dir / "model.ubj"
        model.save_model(model_path)
        _fsync_file(model_path)
        _write_json(temp_dir / "calibrator.json", calibrator.as_dict())

        prediction_table = pa.Table.from_pandas(predictions, preserve_index=False)
        pq.write_table(prediction_table, temp_dir / "predictions.parquet", compression="zstd")
        _fsync_file(temp_dir / "predictions.parquet")

        metrics = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "model_fit_at": _utc_iso(split.model_fit_at),
            "exclusions": exclusion_report,
            "market_counts": dict(split.market_counts),
            "models": _all_metrics(predictions),
        }
        _write_json(temp_dir / "metrics.json", metrics)
        _write_json(
            temp_dir / "feature_importance.json",
            {
                "importance_type": ["gain", "weight"],
                "features": _feature_importance(model, selected_features),
            },
        )

        artifact_names = [
            "model.ubj",
            "calibrator.json",
            "predictions.parquet",
            "metrics.json",
            "feature_importance.json",
        ]
        checksums = _artifact_checksums(temp_dir, artifact_names)
        manifest = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dataset": {
                "dataset_id": artifact.dataset_id,
                "path": str(artifact.path),
                "dataset_hash": artifact.dataset_hash,
                "rows_sha256": artifact.rows_sha256,
                "manifest_sha256": artifact.manifest_sha256,
            },
            "config": config,
            "config_hash": config_hash,
            "selected_features": list(selected_features),
            "threshold_policy_reference": spec.threshold_policy.reference,
            "model_fit_at": _utc_iso(split.model_fit_at),
            "seeds": {"xgboost": spec.xgboost.seed, "calibration": spec.xgboost.seed},
            "training": training_info,
            "code": code,
            "packages": package_versions,
            "runtime": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python_executable": sys.executable,
            },
            "checksums": checksums,
        }
        _write_json(temp_dir / "manifest.json", manifest)
        return _commit_atomic(temp_dir, final_dir)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
