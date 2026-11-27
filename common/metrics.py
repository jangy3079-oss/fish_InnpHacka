"""MetricRecord contract from SDD 4.1; provisional choices are in docs/metric-record.md."""

from datetime import datetime, timezone
from enum import Enum
from math import isclose
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class MetricSource(str, Enum):
    SONAR = "sonar"
    CAMERA = "camera"
    ENV = "env"


class MetricName(str, Enum):
    VERTICAL_DIST = "vertical_dist"
    ACTIVITY = "activity"
    COHESION = "cohesion"
    FEEDING = "feeding"
    STILL_RATIO = "still_ratio"
    RUBBING_RATE = "rubbing_rate"
    QUALITY = "quality"
    TEMP_SURFACE = "temp_surface"
    TEMP_MID = "temp_mid"
    DO = "do"


FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Ratio = Annotated[FiniteNumber, Field(ge=0, le=1)]
Distribution = Annotated[list[Ratio], Field(min_length=1)]


class MetricRecord(BaseModel):
    """One observed metric. Missing observations must not be encoded as zero."""

    model_config = ConfigDict(extra="forbid")

    cage_id: Annotated[str, Field(strict=True, min_length=1, pattern=r"^\S(?:.*\S)?$")]
    ts: AwareDatetime
    source: MetricSource
    metric: MetricName
    value: FiniteNumber | Distribution
    confidence: Ratio

    @field_validator("ts", mode="before")
    @classmethod
    def require_timestamp(cls, value: object) -> object:
        # Pydantic also accepts Unix numbers by default; the wire contract uses ISO 8601.
        if not isinstance(value, (str, datetime)):
            raise ValueError("ts must be an ISO 8601 timestamp with timezone or an aware datetime")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("ts must be an ISO 8601 timestamp with timezone") from exc
        return value

    @field_validator("ts")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_metric_value(self) -> Self:
        if self.metric == MetricName.VERTICAL_DIST:
            if not isinstance(self.value, list):
                raise ValueError("vertical_dist requires an array of depth-bin ratios")
            # A tiny floating-point margin keeps rounded thirds at the tolerance boundary valid.
            if not isclose(sum(self.value), 1.0, rel_tol=0, abs_tol=1e-6 + 1e-12):
                raise ValueError("vertical_dist ratios must sum to 1 (tolerance 1e-6)")
        else:
            if isinstance(self.value, list):
                raise ValueError("only vertical_dist accepts an array")
            if self.metric in (MetricName.QUALITY, MetricName.STILL_RATIO):
                if not 0 <= self.value <= 1:
                    raise ValueError("quality and still_ratio must be between 0 and 1")
        return self
