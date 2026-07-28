"""Validated TOML contract for assembler datasets."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UTC = timezone.utc
_DURATION = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(ms|s|m|h)?$")


class Asset(StrEnum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    XRP = "XRP"


class SourceSemantics(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"


def parse_duration_seconds(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a duration, not a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be seconds or a duration string")
    match = _DURATION.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid {field} duration {value!r}")
    number = float(match.group(1))
    scale = {None: 1.0, "ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]
    return number * scale


def ensure_utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min, tzinfo=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            value = datetime.combine(date.fromisoformat(text), time.min, tzinfo=UTC)
        else:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError(f"{field} must include a UTC offset")
    if offset.total_seconds() != 0:
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def _utc(cls, value: Any, info: Any) -> datetime:
        return ensure_utc(value, field=info.field_name)

    @model_validator(mode="after")
    def _ordered(self) -> "TimeRange":
        if self.end <= self.start:
            raise ValueError("time_range must be a non-empty half-open range [start, end)")
        return self


class DecisionSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(alias="label")
    anchor: Literal["window_start", "window_end"]
    offset_seconds: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _duration_alias(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            if "offset" in value:
                if "offset_seconds" in value:
                    raise ValueError("decision schedule cannot set both offset and offset_seconds")
                value["offset_seconds"] = parse_duration_seconds(value.pop("offset"), field="offset")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("decision schedule name cannot be empty")
        return value


class QuotePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_staleness_seconds: float = Field(gt=0)
    band_min: float = Field(ge=0, le=1)
    band_max: float = Field(ge=0, le=1)
    depth_levels: int = Field(default=5, ge=1, le=100)

    @field_validator("max_staleness_seconds", mode="before")
    @classmethod
    def _staleness(cls, value: Any) -> float:
        return parse_duration_seconds(value, field="max_staleness_seconds")

    @model_validator(mode="after")
    def _band(self) -> "QuotePolicy":
        if self.band_max < self.band_min:
            raise ValueError("quote band_max must be >= band_min")
        return self


class AvailabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    delay_seconds: float = Field(default=0.0, ge=0)

    @field_validator("delay_seconds", mode="before")
    @classmethod
    def _delay(cls, value: Any) -> float:
        return parse_duration_seconds(value, field="delay_seconds")

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("availability policy version cannot be empty")
        return value.strip()


class DatasetSpec(BaseModel):
    """All behaviorally relevant dataset settings.

    Source keys are the raw dataset names: ``poly_l2``, ``poly_trade``,
    ``spot_klines``, and ``premium_index_klines``.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: str
    name: str = "dataset"
    assets: list[Asset]
    interval: Literal["5m"] = "5m"
    time_range: TimeRange
    decision_schedules: list[DecisionSchedule]
    features: list[str]
    availability_policies: dict[str, AvailabilityPolicy]
    quote: QuotePolicy
    sources: dict[str, SourceSemantics]
    execution_delay_seconds: float = Field(ge=0)
    output_path: str
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if "version" in value and "spec_version" not in value:
            value["spec_version"] = value.pop("version")
        if "decision_schedule" in value and "decision_schedules" not in value:
            schedules = value.pop("decision_schedule")
            value["decision_schedules"] = schedules if isinstance(schedules, list) else [schedules]
        if "source_semantics" in value and "sources" not in value:
            value["sources"] = value.pop("source_semantics")
        if "execution_delay" in value and "execution_delay_seconds" not in value:
            value["execution_delay_seconds"] = parse_duration_seconds(
                value.pop("execution_delay"), field="execution_delay"
            )
        if "availability_policy_versions" in value and "availability_policies" not in value:
            value["availability_policies"] = value.pop("availability_policy_versions")
        policies = value.get("availability_policies")
        if isinstance(policies, dict):
            value["availability_policies"] = {
                key: ({"version": item} if isinstance(item, str) else item)
                for key, item in policies.items()
            }
        return value

    @field_validator("execution_delay_seconds", mode="before")
    @classmethod
    def _execution_delay(cls, value: Any) -> float:
        return parse_duration_seconds(value, field="execution_delay_seconds")

    @field_validator("assets", mode="before")
    @classmethod
    def _normalize_assets(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [item.upper() if isinstance(item, str) else item for item in value]
        return value

    @field_validator("assets")
    @classmethod
    def _assets(cls, value: list[Asset]) -> list[Asset]:
        if not value:
            raise ValueError("assets cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("assets must be unique")
        return value

    @field_validator("features")
    @classmethod
    def _features(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if not cleaned or any(not item for item in cleaned):
            raise ValueError("features must contain selected built-in names")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("features must be unique")
        return cleaned

    @field_validator("decision_schedules")
    @classmethod
    def _schedules(cls, value: list[DecisionSchedule]) -> list[DecisionSchedule]:
        if not value:
            raise ValueError("at least one decision schedule is required")
        names = [schedule.name for schedule in value]
        if len(names) != len(set(names)):
            raise ValueError("decision schedule names must be unique")
        return value

    @field_validator("output_path")
    @classmethod
    def _output(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("output_path cannot be empty")
        return value

    @model_validator(mode="after")
    def _explicit_policies(self) -> "DatasetSpec":
        if "pmdata" not in self.availability_policies:
            raise ValueError("availability_policies.pmdata is required")
        if "poly_l2" not in self.sources:
            raise ValueError("sources.poly_l2 required/optional semantics must be explicit")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=False, exclude_none=True)


def load_dataset_spec(path: Path | str) -> DatasetSpec:
    path = Path(path)
    with path.open("rb") as handle:
        raw = tomlkit.load(handle)
    data = dict(raw)
    if set(data) == {"dataset"}:
        data = dict(data["dataset"])
    return DatasetSpec.model_validate(data)
