"""Official and reconstructed binary market resolution contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import ContractModel
from .enums import Outcome, ResolutionBasis, ResolutionConfidence
from .identifiers import MarketId, Sha256, WindowId
from .time import UtcDatetime


class MarketResolution(ContractModel):
    """Resolved outcome kept physically separate from label-free decision rows."""

    window_id: WindowId
    market_id: MarketId
    outcome: Outcome
    basis: ResolutionBasis
    confidence: ResolutionConfidence
    source: str = Field(min_length=1, max_length=256)
    resolved_at: UtcDatetime | None = None
    resolution_available_at: UtcDatetime | None = None
    evidence_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> MarketResolution:
        if self.basis is ResolutionBasis.OFFICIAL:
            if self.confidence is not ResolutionConfidence.OFFICIAL:
                raise ValueError("official resolution requires official confidence")
        elif self.confidence is ResolutionConfidence.OFFICIAL:
            raise ValueError("reconstructed resolution cannot have official confidence")
        if (
            self.resolved_at is not None
            and self.resolution_available_at is not None
            and self.resolution_available_at < self.resolved_at
        ):
            raise ValueError("resolution_available_at cannot precede resolved_at")
        return self

    @property
    def label(self) -> int:
        return self.outcome.label
