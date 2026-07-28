"""Canonical closed vocabularies shared by research pipeline components."""

from __future__ import annotations

from datetime import timedelta
from enum import Enum


class Asset(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    XRP = "XRP"


class Interval(str, Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"

    @property
    def duration(self) -> timedelta:
        return {
            Interval.ONE_MINUTE: timedelta(minutes=1),
            Interval.FIVE_MINUTES: timedelta(minutes=5),
            Interval.FIFTEEN_MINUTES: timedelta(minutes=15),
            Interval.ONE_HOUR: timedelta(hours=1),
        }[self]


class Outcome(str, Enum):
    UP = "UP"
    DOWN = "DOWN"

    @property
    def label(self) -> int:
        """Canonical binary model label: UP=1 and DOWN=0."""
        return 1 if self is Outcome.UP else 0

    @classmethod
    def from_label(cls, label: int) -> Outcome:
        if isinstance(label, bool) or not isinstance(label, int) or label not in (0, 1):
            raise ValueError("binary outcome label must be integer 0 or 1")
        return cls.UP if label == 1 else cls.DOWN


class ProductionStatus(str, Enum):
    RESEARCH_ONLY = "research_only"
    PARITY_PENDING = "parity_pending"
    PRODUCTION_ELIGIBLE = "production_eligible"


class AvailabilityBasis(str, Enum):
    SOURCE_PUBLISH_TIME = "source_publish_time"
    OBSERVED_AT = "observed_at"
    INGESTED_AT = "ingested_at"
    MODELED_EVENT_TIME = "modeled_event_time"
    UNAVAILABLE = "unavailable"


class DecisionAnchor(str, Enum):
    WINDOW_START = "window_start"
    WINDOW_END = "window_end"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


class EligibilityReason(str, Enum):
    MARKET_NOT_DISCOVERED = "market_not_discovered"
    MARKET_NOT_TRADING = "market_not_trading"
    MARKET_CLOSED = "market_closed"
    INVALID_BOOK = "invalid_book"
    STALE_BOOK = "stale_book"
    MISSING_QUOTE = "missing_quote"
    MISSING_DEPTH = "missing_depth"
    PRICE_OUT_OF_RANGE = "price_out_of_range"
    MISSING_REQUIRED_FEATURE = "missing_required_feature"
    INPUT_UNAVAILABLE_AT_DECISION = "input_unavailable_at_decision"
    COVERAGE_GAP = "coverage_gap"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    OTHER = "other"


class FeatureNullReason(str, Enum):
    MISSING_INPUT = "missing_input"
    INPUT_UNAVAILABLE_AT_DECISION = "input_unavailable_at_decision"
    INSUFFICIENT_HISTORY = "insufficient_history"
    STALE_INPUT = "stale_input"
    INVALID_INPUT = "invalid_input"
    COVERAGE_GAP = "coverage_gap"
    NOT_APPLICABLE = "not_applicable"
    COMPUTATION_ERROR = "computation_error"


class FeatureDType(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DECIMAL = "decimal"
    CATEGORY = "category"


class ResolutionBasis(str, Enum):
    OFFICIAL = "official"
    RECONSTRUCTED = "reconstructed"


class ResolutionConfidence(str, Enum):
    OFFICIAL = "official"
    RECONSTRUCTED_HIGH = "reconstructed_high"
    RECONSTRUCTED_MEDIUM = "reconstructed_medium"
    RECONSTRUCTED_LOW = "reconstructed_low"
