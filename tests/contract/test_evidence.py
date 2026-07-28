from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract import (
    AvailabilityBasis,
    AvailabilityPolicyIdentity,
    EventEvidence,
    EvidenceSummary,
    parse_utc,
)


def policy(basis: AvailabilityBasis) -> AvailabilityPolicyIdentity:
    return AvailabilityPolicyIdentity(
        policy_id="pmdata-observed-fixed",
        version="1",
        basis=basis,
    )


def event(**changes: object) -> EventEvidence:
    values: dict[str, object] = {
        "event_id": "pmdata:trade:1",
        "event_time": "2026-07-17T23:59:29.000000Z",
        "observed_at": "2026-07-17T23:59:29.100000Z",
        "available_at": "2026-07-17T23:59:29.150000Z",
        "ingested_at": "2026-07-17T23:59:29.200000Z",
        "availability_policy": policy(AvailabilityBasis.OBSERVED_AT),
        "source_record_id": "archive.parquet:42",
    }
    values.update(changes)
    return EventEvidence.model_validate(values)


def test_event_evidence_proves_decision_cutoff() -> None:
    evidence = event()
    assert evidence.is_available_by(parse_utc("2026-07-17T23:59:30Z"))
    assert not evidence.is_available_by(parse_utc("2026-07-17T23:59:29.149999Z"))
    summary = EvidenceSummary.from_events((evidence,))
    assert summary.max_available_at == evidence.available_at
    assert summary.policy_keys == (evidence.availability_policy.key,)


def test_available_at_cannot_precede_event_or_selected_basis() -> None:
    with pytest.raises(ValidationError, match="available_at cannot precede event_time"):
        event(available_at="2026-07-17T23:59:28Z")
    with pytest.raises(ValidationError, match="selected observed_at"):
        event(available_at="2026-07-17T23:59:29.050000Z")


def test_policy_basis_requires_its_evidence_and_unavailable_fails_closed() -> None:
    with pytest.raises(ValidationError, match="requires source_publish_time"):
        event(
            observed_at=None,
            availability_policy=policy(AvailabilityBasis.SOURCE_PUBLISH_TIME),
        )
    unavailable = event(
        observed_at=None,
        available_at=None,
        availability_policy=policy(AvailabilityBasis.UNAVAILABLE),
    )
    cutoff = parse_utc("2100-01-01T00:00:00Z")
    assert not unavailable.is_available_by(cutoff)
    summary = EvidenceSummary.from_events((unavailable,))
    assert summary.unavailable_event_ids == (unavailable.event_id,)
    assert not summary.is_available_by(cutoff)
