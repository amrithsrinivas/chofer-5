from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract import (
    AvailabilityBasis,
    AvailabilityPolicyIdentity,
    EvidenceSummary,
    FeatureDType,
    FeatureDefinition,
    FeatureNullReason,
    FeatureProvenance,
    FeatureValue,
    ProductionStatus,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def definition() -> FeatureDefinition:
    return FeatureDefinition(
        name="book.midpoint",
        version="1",
        dtype=FeatureDType.DECIMAL,
        description="Exact midpoint at the decision cutoff.",
        lookback="0s",
        availability_policy=AvailabilityPolicyIdentity(
            policy_id="pmdata-observed",
            version="1",
            basis=AvailabilityBasis.OBSERVED_AT,
        ),
        compute_budget_us=250,
        nullable=True,
        production_status=ProductionStatus.PARITY_PENDING,
        implementation_sha256=SHA_A,
    )


def provenance(max_available_at: str | None) -> FeatureProvenance:
    return FeatureProvenance(
        definition_sha256=definition().behavior_hash,
        implementation_sha256=SHA_A,
        evidence=EvidenceSummary(
            event_ids=("book:1",),
            policy_keys=("pmdata-observed@1:observed_at",),
            latest_event_time="2026-07-17T23:59:29Z",
            max_available_at=max_available_at,
        ),
        input_file_sha256s=(SHA_B,),
        computed_at="2026-07-20T00:00:00Z",
    )


def test_feature_definition_has_stable_behavior_identity() -> None:
    first = definition()
    second = definition()
    assert first.ref.key == "book.midpoint@1"
    assert first.behavior_hash == second.behavior_hash


def test_feature_value_enforces_point_in_time_evidence() -> None:
    value = FeatureValue(
        window_id="window-1",
        schedule_id="premarket-30s",
        feature=definition().ref,
        decision_at="2026-07-17T23:59:30Z",
        value="0.50",
        provenance=provenance("2026-07-17T23:59:29.500000Z"),
    )
    assert value.null_reason is None

    with pytest.raises(ValidationError, match="not available by decision_at"):
        value.model_copy(
            update={"provenance": provenance("2026-07-17T23:59:30.000001Z")}
        ).model_validate(
            value.model_copy(
                update={"provenance": provenance("2026-07-17T23:59:30.000001Z")}
            ).model_dump()
        )


def test_feature_null_reason_matches_value_presence() -> None:
    null_value = FeatureValue(
        window_id="window-1",
        schedule_id="premarket-30s",
        feature=definition().ref,
        decision_at="2026-07-17T23:59:30Z",
        value=None,
        null_reason=FeatureNullReason.MISSING_INPUT,
        provenance=provenance(None),
    )
    assert null_value.value is None
    with pytest.raises(ValidationError, match="requires null_reason"):
        FeatureValue(
            window_id="window-1",
            schedule_id="premarket-30s",
            feature=definition().ref,
            decision_at="2026-07-17T23:59:30Z",
            value=None,
            provenance=provenance(None),
        )
