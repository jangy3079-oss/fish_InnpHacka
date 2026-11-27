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


class AnalysisPolicy(ConfigModel):
    max_age_minutes: Annotated[float, Field(gt=0, le=1440)]
    confidence_good_min: Ratio
    confidence_limited_min: Ratio

    @model_validator(mode="after")
    def ordered_confidence(self) -> Self:
        if self.confidence_limited_min >= self.confidence_good_min:
            raise ValueError("confidence thresholds must be ordered")
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
    analysis: AnalysisPolicy

    @model_validator(mode="after")
    def level_order(self) -> Self:
        expected = {"주의", "경고", "위험"}
        if set(self.levels) != expected or set(self.hold_hours) != expected:
            raise ValueError("levels and hold_hours require all three alert levels")
        if not self.levels["주의"] < self.levels["경고"] < self.levels["위험"]:
            raise ValueError("alert thresholds must be strictly increasing")
        return self


IMPLEMENTED_RULES = {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"}


class Probability(ConfigModel):
    method: Literal["normalize_sum_to_one"]
    min_display: Ratio
    scoring: Literal["binary_all_required"]
    tie_break_order: list[Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]]

    @model_validator(mode="after")
    def implemented_order(self) -> Self:
        if len(self.tie_break_order) != len(IMPLEMENTED_RULES) or set(self.tie_break_order) != IMPLEMENTED_RULES:
            raise ValueError("tie_break_order must list each implemented rule once")
        return self


class R1Behavior(ConfigModel):
    mid_low_ratio_x_min: Annotated[float, Field(ge=1)]
    activity_change_max: Annotated[float, Field(ge=-1, le=0)]


class R1Environment(ConfigModel):
    temp_surface_min_c: float
    temp_diff_min_c: Positive


class R2Behavior(ConfigModel):
    activity_change_max: Annotated[float, Field(ge=-1, le=0)]
    cohesion_drop: Positive
    lookback_h: Positive


class R2Environment(ConfigModel):
    temp_surface_min_c: float
    temp_diff_max_c: Positive


class R3Behavior(ConfigModel):
    surface_ratio_x_min: Annotated[float, Field(ge=1)]
    camera_surface_breathing: bool


class R3Environment(ConfigModel):
    do_max_mg_l: Positive


class R5Behavior(ConfigModel):
    score_rising_days_min: Annotated[int, Field(ge=2)]
    min_daily_increase: Positive
    min_daily_coverage: Annotated[float, Field(gt=0, le=1)]


class NormalEnvironment(ConfigModel):
    normal_env: Literal[True]
    advisory_active: Literal[False]


class NormalEnvironmentBounds(ConfigModel):
    temp_surface_max_c: float
    do_min_mg_l: Positive


class R8Behavior(ConfigModel):
    rubbing_rate_x_min: Annotated[float, Field(ge=1)]
    sustained_hours_min: Positive
    missing_observation: Literal["pause"]


class R7Behavior(ConfigModel):
    feeding_response_only: Literal[True]
    feeding_change_max: Annotated[float, Field(ge=-1, lt=0)]
    activity_change_abs_max: Annotated[float, Field(gt=0, le=1)]
    lower_layers_ratio_x_max: Annotated[float, Field(gt=1)]
    missing_observation: Literal['pause']
    max_hold_hours: Annotated[float, Field(gt=0, le=48)]


class R7Environment(ConfigModel):
    temp_surface_min_c: float


class R4Behavior(ConfigModel):
    activity_change_abs_min: Annotated[float, Field(gt=0, le=1)]


class R4Environment(ConfigModel):
    red_tide_advisory: Literal[True]
    advisory_stages: Annotated[list[Literal["watch", "advisory", "warning"]], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_stages(self) -> Self:
        if len(set(self.advisory_stages)) != len(self.advisory_stages):
            raise ValueError("R4 advisory_stages must not repeat")
        return self


class R6Behavior(ConfigModel):
    score_rise_after_work: Literal[True]
    pre_work_hours: Positive
    rise_window_hours: Positive
    min_score_rise: Annotated[float, Field(gt=0, le=100)]
    not_recovered_hours: Positive
    max_hours_after_work: Positive

    @model_validator(mode="after")
    def windows(self) -> Self:
        if not self.rise_window_hours <= self.not_recovered_hours < self.max_hours_after_work:
            raise ValueError("R6 windows must satisfy rise_window <= not_recovered < max_hours_after_work")
        return self


class R6Environment(ConfigModel):
    work_log: Texts


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
        for rid, behavior, environment in (("R1", R1Behavior, R1Environment), ("R2", R2Behavior, R2Environment),
                                            ("R3", R3Behavior, R3Environment), ("R5", R5Behavior, NormalEnvironment),
                                            ("R7", R7Behavior, R7Environment),
                                            ("R8", R8Behavior, NormalEnvironment), ("R4", R4Behavior, R4Environment),
                                            ("R6", R6Behavior, R6Environment)):
            behavior.model_validate(self.rules[rid].behavior)
            environment.model_validate(self.rules[rid].env)
        if type(self.rules["R2"].model_extra.get("immediate_danger")) is not bool:
            raise ValueError("R2.immediate_danger must be a boolean")
        if self.rules["R1"].env["temp_diff_min_c"] < self.rules["R2"].env["temp_diff_max_c"]:
            raise ValueError("R1/R2 temperature difference ranges must not overlap")
        NormalEnvironmentBounds.model_validate(self.env_constants.get("normal_env"))
        if self.rules['R7'].env['temp_surface_min_c'] >= self.env_constants['normal_env']['temp_surface_max_c']:
            raise ValueError('R7 feeding range must be below the normal-environment upper bound')
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
    eligible_options: list[Text]


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
        if len(set(self.insurance.eligible_options)) != len(self.insurance.eligible_options) or not set(self.insurance.eligible_options) <= set(self.option_loss):
            raise ValueError("insurance options must be unique configured actions")
        return self


class FirstAidAction(ConfigModel):
    id: Text
    name: Text
    text: Text
    source: Text
    source_status: Literal["official", "partial", "secondary", "pending"] = "pending"


class DiagnosisPolicy(ConfigModel):
    lookback_hours: Annotated[float, Field(gt=0, le=168)]
    max_points: Annotated[int, Field(gt=0, le=1000)]
    max_cause_age_minutes: Annotated[float, Field(gt=0, le=1440)]


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
    diagnosis_policy: DiagnosisPolicy

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
