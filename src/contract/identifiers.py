"""Validated, path-independent identifiers for canonical records."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic_core import core_schema


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_FEATURE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ValidatedIdentifier(str):
    """A non-empty stable identifier with a conservative portable alphabet."""

    pattern: ClassVar[re.Pattern[str]] = _IDENTIFIER_RE
    description: ClassVar[str] = "canonical identifier"

    def __new__(cls, value: str) -> ValidatedIdentifier:
        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__} must be a string")
        if not cls.pattern.fullmatch(value):
            raise ValueError(f"invalid {cls.description}: {value!r}")
        return str.__new__(cls, value)

    @classmethod
    def _validate(cls, value: str) -> ValidatedIdentifier:
        return cls(value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ) -> core_schema.CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )


class SeriesId(ValidatedIdentifier):
    description = "series ID"


class MarketId(ValidatedIdentifier):
    description = "market ID"


class ConditionId(ValidatedIdentifier):
    description = "condition ID"


class TokenId(ValidatedIdentifier):
    description = "token ID"


class WindowId(ValidatedIdentifier):
    description = "window ID"


class DecisionScheduleId(ValidatedIdentifier):
    description = "decision schedule ID"


class EventId(ValidatedIdentifier):
    description = "event ID"


class AvailabilityPolicyId(ValidatedIdentifier):
    description = "availability policy ID"


class FeatureName(ValidatedIdentifier):
    pattern = _FEATURE_NAME_RE
    description = "feature name"


class VersionId(ValidatedIdentifier):
    pattern = _VERSION_RE
    description = "version"


class Sha256(str):
    """Lowercase SHA-256 hex digest."""

    def __new__(cls, value: str) -> Sha256:
        if not isinstance(value, str):
            raise TypeError("Sha256 must be a string")
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("SHA-256 must be 64 lowercase hexadecimal characters")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ) -> core_schema.CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )
