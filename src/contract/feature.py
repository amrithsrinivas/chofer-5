"""Versioned feature definitions, values, nullability, and causal provenance."""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from pydantic import Field, model_validator

from .base import CanonicalHashModel, ContractModel
from .enums import FeatureDType, FeatureNullReason, ProductionStatus
from .evidence import AvailabilityPolicyIdentity, EvidenceSummary
from .identifiers import DecisionScheduleId, FeatureName, Sha256, VersionId, WindowId
from .time import SignedDuration, UtcDatetime

FeatureScalar = bool | int | float | Decimal | str


class FeatureRef(ContractModel):
    name: FeatureName
    version: VersionId

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


class FeatureDefinition(CanonicalHashModel):
    """Everything needed to identify and assess one feature's behavior."""

    name: FeatureName
    version: VersionId
    dtype: FeatureDType
    description: str = Field(min_length=1, max_length=2048)
    inputs: tuple[FeatureRef, ...] = ()
    lookback: SignedDuration
    availability_policy: AvailabilityPolicyIdentity
    compute_budget_us: int = Field(gt=0)
    nullable: bool
    production_status: ProductionStatus
    implementation_sha256: Sha256

    @model_validator(mode="after")
    def validate_definition(self) -> FeatureDefinition:
        if self.lookback < timedelta(0):
            raise ValueError("feature lookback cannot be negative")
        keys = [item.key for item in self.inputs]
        if len(keys) != len(set(keys)):
            raise ValueError("feature inputs must be unique")
        return self

    @property
    def ref(self) -> FeatureRef:
        return FeatureRef(name=self.name, version=self.version)


class FeatureProvenance(ContractModel):
    definition_sha256: Sha256
    implementation_sha256: Sha256
    evidence: EvidenceSummary
    input_file_sha256s: tuple[Sha256, ...] = ()
    computed_at: UtcDatetime

    @model_validator(mode="after")
    def validate_provenance(self) -> FeatureProvenance:
        if len(self.input_file_sha256s) != len(set(self.input_file_sha256s)):
            raise ValueError("input file hashes must be unique")
        return self


class FeatureValue(ContractModel):
    """One feature cell with enough evidence to prove point-in-time eligibility."""

    window_id: WindowId
    schedule_id: DecisionScheduleId
    feature: FeatureRef
    decision_at: UtcDatetime
    value: FeatureScalar | None
    null_reason: FeatureNullReason | None = None
    provenance: FeatureProvenance

    @model_validator(mode="after")
    def validate_value(self) -> FeatureValue:
        if self.value is None and self.null_reason is None:
            raise ValueError("null feature value requires null_reason")
        if self.value is not None and self.null_reason is not None:
            raise ValueError("non-null feature value cannot have null_reason")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("feature value cannot be NaN or infinite")
        evidence = self.provenance.evidence
        if self.value is not None and evidence.unavailable_event_ids:
            raise ValueError("non-null feature cannot depend on unavailable evidence")
        maximum = evidence.max_available_at
        if maximum is not None and maximum > self.decision_at:
            raise ValueError("feature evidence was not available by decision_at")
        return self
