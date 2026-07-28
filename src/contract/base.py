"""Strict contract model base and deterministic canonical JSON hashing."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Mapping

from pydantic import BaseModel, ConfigDict

from .time import duration_microseconds, format_utc


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite decimals are not canonical JSON values")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def canonical_value(value: Any) -> Any:
    """Convert supported Python values into deterministic JSON primitives."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", by_alias=True, exclude_none=False)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not canonical JSON values")
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, timedelta):
        return duration_microseconds(value)
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [canonical_value(item) for item in value]
        return sorted(items, key=lambda item: canonical_json(item))
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """RFC-8259 JSON with sorted keys and no insignificant whitespace."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ContractModel(BaseModel):
    """Frozen, extra-forbidden base for values crossing component boundaries."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        validate_default=True,
    )


class CanonicalHashModel(ContractModel):
    """A contract with deterministic content and behavior identities."""

    behavior_exclude: ClassVar[frozenset[str]] = frozenset()

    def canonical_json(self) -> str:
        return canonical_json(self)

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self)

    def behavior_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="python",
            by_alias=True,
            exclude=set(self.behavior_exclude),
            exclude_none=False,
        )

    @property
    def behavior_hash(self) -> str:
        return canonical_sha256(self.behavior_payload())
