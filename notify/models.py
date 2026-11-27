"""Notify-only contracts. Existing API and decision schemas are not changed."""

from datetime import datetime, timezone
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Rule = Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
Level = Literal["정상", "주의", "경고", "위험"]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Evidence(Contract):
    temp_surface: Number | None = None
    activity_change: Number | None = None
    lower_layers_ratio_x: Annotated[Number, Field(ge=0)] | None = None
    feeding_change: Number | None = None


class NotificationContext(Contract):
    cage_id: Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]
    ts: AwareDatetime
    baseline_status: Literal["ready", "warming_up"]
    level: Level | None
    stress_score: Annotated[Number, Field(ge=0, le=100)] | None
    rule_id: Rule | None = None
    evidence: Evidence = Field(default_factory=Evidence)

    @field_validator("ts", mode="before")
    @classmethod
    def timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("ISO timestamp or aware datetime required")
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("ts")
    @classmethod
    def utc(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.baseline_status == "ready" and (self.level is None or self.stress_score is None):
            raise ValueError("ready requires score and level")
        if self.baseline_status == "warming_up" and (self.stress_score is not None or self.level is not None or self.rule_id is not None):
            raise ValueError("warming_up cannot claim a score, level or cause")
        return self


class Citation(Contract):
    id: str
    source: str
    source_url: str | None = None
    source_sha256: str
    scope: str
    quote: str


class NotificationResult(Contract):
    cage_id: int
    ts: AwareDatetime
    message: str
    mode: Literal["template", "llm"]
    fallback_reason: str | None
    citations: tuple[Citation, ...]
    config_revision: str
    generation_config_sha256: str
    dispatch_status: Literal["not_dispatched"] = "not_dispatched"
