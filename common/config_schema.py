"""Structural contracts for the five config files; no runtime default values."""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator, model_validator

Positive = Annotated[float, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]
Ratio = Annotated[float, Field(ge=0, le=1)]
Score = Annotated[float, Field(ge=0, le=100)]
Text = Annotated[str, Field(min_length=1)]
Texts = Annotated[list[Text], Field(min_length=1)]
RuleId = Annotated[str, Field(pattern=r"^R[1-8]$")]
Level = Literal["주의", "경고", "위험"]
Size = Literal["fry", "mid", "market"]
Clock = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Baseline(ConfigModel):
    window_hours: Positive
    min_windows: Annotated[int, Field(gt=0)]
    day_start: Clock
    day_end: Clock

    @model_validator(mode="after")
    def distinct_times(self) -> Self:
        if self.day_start == self.day_end:
            raise ValueError("day_start and day_end must differ")
        return self


class Quality(ConfigModel):
    clahe_min: Ratio
    clahe_max: Ratio
    camera_only_min: Ratio
    rubbing_min: Ratio

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.clahe_min > self.clahe_max:
            raise ValueError("clahe_min must not exceed clahe_max")
        return self


class Fusion(ConfigModel):
    gate_low: Ratio
    full_at: Ratio
    max_camera_weight: Ratio

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.gate_low >= self.full_at:
            raise ValueError("gate_low must be less than full_at")
        return self


class Thresholds(ConfigModel):
    cycle_minutes: Positive
    levels: dict[Level, Score]
    hold_hours: dict[Level, Positive]
    release_hours: Positive
    immediate_danger_rules: list[RuleId]
    alert_dedup_hours: Positive
    cause_min_stress: Score
    cause_min_stress_exceptions: list[RuleId]
    baseline: Baseline
    quality: Quality
    fusion: Fusion

    @model_validator(mode="after")
    def level_order(self) -> Self:
        expected = {"주의", "경고", "위험"}
        if set(self.levels) != expected or set(self.hold_hours) != expected:
            raise ValueError("levels and hold_hours require all three alert levels")
        if not self.levels["주의"] < self.levels["경고"] < self.levels["위험"]:
            raise ValueError("alert thresholds must be strictly increasing")
        return self


class Probability(ConfigModel):
    method: Literal["normalize_sum_to_one"]
    min_display: Ratio


class Rule(ConfigModel):
    # Rule-specific evidence names are evolving in #25/#60, so these maps and
    # additional rule fields are preserved rather than silently discarded.
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    name: Text
    phase: Text
    behavior: dict[str, JsonValue]
    env: dict[str, JsonValue]
    first_action: Texts


class CauseRules(ConfigModel):
    env_constants: dict[str, JsonValue]
    probability: Probability
    rules: dict[RuleId, Rule]

    @model_validator(mode="after")
    def all_rules(self) -> Self:
        if set(self.rules) != {f"R{i}" for i in range(1, 9)}:
            raise ValueError("rules must contain R1 through R8")
        return self


class Species(ConfigModel):
    allowed: Texts
    reason_if_not: Text


class ReleaseGroup(ConfigModel):
    label: Text
    release_allowed: bool
    reason_if_not: Text | None = None


class Sequential(ConfigModel):
    type: Literal["sequential"]
    interval_days: Positive


class Spread(ConfigModel):
    type: Literal["spread"]
    min_distance_km: Positive


class ReleaseLimit(ConfigModel):
    max_count: Annotated[int, Field(gt=0)]
    if_exceeded: list[Sequential | Spread]
    reason_if_not: Text


class Subsidy(ConfigModel):
    cap_per_farm_krw: NonNegative
    per_fish_krw: NonNegative


class ReleaseRules(ConfigModel):
    species: Species
    size_groups: dict[Size, ReleaseGroup]
    simultaneous_limit: ReleaseLimit
    subsidy: Subsidy


class CostGroup(ConfigModel):
    label: Text
    price_per_kg: NonNegative
    avg_weight_kg: Positive


class LossOption(ConfigModel):
    use: Literal["expected_mortality_by_level"] | None = None
    loss_rate: Ratio | None = None
    mortality_factor: Ratio | None = None

    @model_validator(mode="after")
    def one_definition(self) -> Self:
        if sum(value is not None for value in (self.use, self.loss_rate, self.mortality_factor)) != 1:
            raise ValueError("each option requires exactly one loss definition")
        return self


class Insurance(ConfigModel):
    payout_rate: Ratio


class Costs(ConfigModel):
    currency: Literal["KRW"]
    size_groups: dict[Size, CostGroup]
    expected_mortality_by_level: dict[Literal["normal", "caution", "warning", "danger"], Ratio]
    option_loss: dict[str, LossOption]
    action_cost_krw: dict[str, NonNegative]
    insurance: Insurance

    @model_validator(mode="after")
    def complete_options(self) -> Self:
        if set(self.expected_mortality_by_level) != {"normal", "caution", "warning", "danger"}:
            raise ValueError("mortality rates require all four levels")
        if not self.option_loss or set(self.option_loss) != set(self.action_cost_krw):
            raise ValueError("option_loss and action_cost_krw must define the same options")
        return self


class FirstAidAction(ConfigModel):
    id: Text
    name: Text
    text: Text
    source: Text
    source_status: Text | None = None


class Diagnosis(ConfigModel):
    disease: Text
    parasite: Text


class BlockedTerms(ConfigModel):
    categories: Texts
    ingredients: Texts
    dose_patterns: Texts

    @field_validator("dose_patterns")
    @classmethod
    def valid_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error:
                raise ValueError("dose_patterns contains an invalid regular expression") from None
        return patterns


class FirstAid(ConfigModel):
    common_first_aid: Annotated[list[FirstAidAction], Field(min_length=1)]
    diagnosis_request: Diagnosis
    blocked_terms: BlockedTerms

    @model_validator(mode="after")
    def unique_actions(self) -> Self:
        ids = [action.id for action in self.common_first_aid]
        if len(set(ids)) != len(ids):
            raise ValueError("common_first_aid action IDs must be unique")
        return self


CONFIG_MODELS = {"thresholds": Thresholds, "cause_rules": CauseRules,
                 "release_rules": ReleaseRules, "costs": Costs, "first_aid": FirstAid}


def validate_bundle(documents: dict) -> dict:
    result = {}
    for name, model in CONFIG_MODELS.items():
        try:
            result[name] = model.model_validate(documents[name]).model_dump(exclude_unset=True)
        except ValidationError as exc:
            paths = [".".join(map(str, error["loc"])) or "root" for error in exc.errors()]
            raise ValueError(f"{name}.yaml: schema validation failed at " + ", ".join(paths)) from None
    for name in ("release_rules", "costs"):
        if set(result[name]["size_groups"]) != {"fry", "mid", "market"}:
            raise ValueError(f"{name}.size_groups requires fry, mid, market")
    return result
