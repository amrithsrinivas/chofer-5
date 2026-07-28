"""Point-in-time availability policy identity and event evidence."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from .base import CanonicalHashModel, ContractModel
from .enums import AvailabilityBasis
from .identifiers import AvailabilityPolicyId, EventId, Sha256, VersionId
from .time import UtcDatetime


class AvailabilityPolicyIdentity(CanonicalHashModel):
    """Versioned identity of the policy that produced `available_at`."""

    policy_id: AvailabilityPolicyId
    version: VersionId
    basis: AvailabilityBasis

    @property
    def key(self) -> str:
        return f"{self.policy_id}@{self.version}:{self.basis.value}"


class EventEvidence(ContractModel):
    """Causal timestamps and lineage for one canonical source event."""

    event_id: EventId
    event_time: UtcDatetime
    source_publish_time: UtcDatetime | None = None
    observed_at: UtcDatetime | None = None
    available_at: UtcDatetime | None = None
    ingested_at: UtcDatetime
    availability_policy: AvailabilityPolicyIdentity
    source_record_id: str = Field(min_length=1, max_length=1024)
    lineage_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> EventEvidence:
        if self.source_publish_time is not None and self.source_publish_time < self.event_time:
            raise ValueError("source_publish_time cannot precede event_time")
        if self.observed_at is not None and self.observed_at < self.event_time:
            raise ValueError("observed_at cannot precede event_time")
        if self.ingested_at < self.event_time:
            raise ValueError("ingested_at cannot precede event_time")

        basis = self.availability_policy.basis
        if basis is AvailabilityBasis.UNAVAILABLE:
            if self.available_at is not None:
                raise ValueError("unavailable policy must not choose available_at")
            return self
        if self.available_at is None:
            raise ValueError("availability policy must choose available_at")
        if self.available_at < self.event_time:
            raise ValueError("available_at cannot precede event_time")

        selected_time: datetime
        if basis is AvailabilityBasis.SOURCE_PUBLISH_TIME:
            if self.source_publish_time is None:
                raise ValueError("source_publish_time basis requires source_publish_time")
            selected_time = self.source_publish_time
        elif basis is AvailabilityBasis.OBSERVED_AT:
            if self.observed_at is None:
                raise ValueError("observed_at basis requires observed_at")
            selected_time = self.observed_at
        elif basis is AvailabilityBasis.INGESTED_AT:
            selected_time = self.ingested_at
        else:
            selected_time = self.event_time
        if self.available_at < selected_time:
            raise ValueError(f"available_at cannot precede selected {basis.value} basis")
        return self

    def is_available_by(self, decision_at: datetime) -> bool:
        from .time import parse_utc

        cutoff = parse_utc(decision_at)
        return self.available_at is not None and self.available_at <= cutoff


class EvidenceSummary(ContractModel):
    """Compact, auditable evidence attached to a derived value."""

    event_ids: tuple[EventId, ...] = ()
    unavailable_event_ids: tuple[EventId, ...] = ()
    policy_keys: tuple[str, ...] = ()
    latest_event_time: UtcDatetime | None = None
    max_available_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_summary(self) -> EvidenceSummary:
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("evidence event IDs must be unique")
        if len(self.unavailable_event_ids) != len(set(self.unavailable_event_ids)):
            raise ValueError("unavailable event IDs must be unique")
        if not set(self.unavailable_event_ids).issubset(self.event_ids):
            raise ValueError("unavailable event IDs must be included in event_ids")
        return self

    @classmethod
    def from_events(cls, events: tuple[EventEvidence, ...]) -> EvidenceSummary:
        available = [event.available_at for event in events if event.available_at is not None]
        return cls(
            event_ids=tuple(sorted({event.event_id for event in events})),
            unavailable_event_ids=tuple(
                sorted(event.event_id for event in events if event.available_at is None)
            ),
            policy_keys=tuple(sorted({event.availability_policy.key for event in events})),
            latest_event_time=max((event.event_time for event in events), default=None),
            max_available_at=max(available, default=None),
        )

    def is_available_by(self, decision_at: datetime) -> bool:
        from .time import parse_utc

        if self.unavailable_event_ids:
            return False
        return self.max_available_at is None or self.max_available_at <= parse_utc(decision_at)
