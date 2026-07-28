"""Canonical binary market windows and explicit decision schedules."""

from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from .base import CanonicalHashModel, ContractModel
from .enums import (
    Asset,
    DecisionAnchor,
    EligibilityReason,
    EligibilityStatus,
    Interval,
)
from .identifiers import (
    ConditionId,
    DecisionScheduleId,
    MarketId,
    SeriesId,
    TokenId,
    WindowId,
)
from .price import BinaryPrice
from .time import SignedDuration, UtcDatetime


class MarketWindow(ContractModel):
    """One canonical two-token UP/DOWN market with exact UTC window bounds."""

    window_id: WindowId
    asset: Asset
    interval: Interval
    series_id: SeriesId
    market_id: MarketId
    condition_id: ConditionId
    up_token_id: TokenId
    down_token_id: TokenId
    window_start: UtcDatetime
    window_end: UtcDatetime

    @model_validator(mode="after")
    def validate_window(self) -> MarketWindow:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.window_end - self.window_start != self.interval.duration:
            raise ValueError("window bounds must exactly match interval duration")
        if self.up_token_id == self.down_token_id:
            raise ValueError("UP and DOWN token IDs must be distinct")
        return self


class DecisionSchedule(CanonicalHashModel):
    """A named signed offset from an explicit market-window anchor."""

    schedule_id: DecisionScheduleId
    anchor: DecisionAnchor
    offset: SignedDuration

    def decision_at(self, window: MarketWindow) -> datetime:
        anchor_time = (
            window.window_start
            if self.anchor is DecisionAnchor.WINDOW_START
            else window.window_end
        )
        return anchor_time + self.offset


def decision_at(window: MarketWindow, schedule: DecisionSchedule) -> datetime:
    return schedule.decision_at(window)


class DecisionRow(ContractModel):
    """Candidate decision state; labels are intentionally absent."""

    window_id: WindowId
    schedule_id: DecisionScheduleId
    decision_at: UtcDatetime
    eligibility: EligibilityStatus
    eligibility_reasons: tuple[EligibilityReason, ...] = ()
    up_bid: BinaryPrice | None = None
    up_ask: BinaryPrice | None = None
    down_bid: BinaryPrice | None = None
    down_ask: BinaryPrice | None = None

    @model_validator(mode="after")
    def validate_eligibility_and_quotes(self) -> DecisionRow:
        reasons = tuple(dict.fromkeys(self.eligibility_reasons))
        if reasons != self.eligibility_reasons:
            raise ValueError("eligibility reasons must be unique and ordered")
        if self.eligibility is EligibilityStatus.ELIGIBLE and reasons:
            raise ValueError("eligible rows cannot have ineligibility reasons")
        if self.eligibility is EligibilityStatus.INELIGIBLE and not reasons:
            raise ValueError("ineligible rows require at least one reason")
        for bid, ask, side in (
            (self.up_bid, self.up_ask, "UP"),
            (self.down_bid, self.down_ask, "DOWN"),
        ):
            if bid is not None and ask is not None and bid > ask:
                raise ValueError(f"{side} bid cannot exceed ask")
        return self
