"""Provisional API contracts. See docs/api-contract.md for team decisions."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator, field_validator
from decision.actions import canonical_action

Metric = Literal[
    "vertical_dist", "activity", "cohesion", "feeding", "still_ratio",
    "rubbing_rate", "quality", "temp_surface", "temp_mid", "do",
]
Scenario = Literal["heatwave_typhoon", "turbidity_event", "disease_suspect", "parasite_suspect"]
ConfigFile = Literal["thresholds", "cause_rules", "release_rules", "costs", "first_aid"]
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Cause(Contract):
    name: str
    probability: Ratio
    rule_id: str


class Evidence(Contract):
    mid_layer_ratio_x: float
    activity_change: float
    temp_surface: float
    temp_mid: float


class MetricReliability(Contract):
    grade: Literal['good','limited','low','unavailable']
    confidence: Ratio | None
    sources: list[Literal['sonar','camera','env']]


class CageStatus(Contract):
    cage_id: int
    ts: AwareDatetime
    stress_score: Annotated[float, Field(ge=0, le=100)] | None
    level: Literal["정상", "주의", "경고", "위험"] | None
    baseline_status: Literal["ready", "warming_up"]
    cause: list[Cause]
    evidence: dict[str, float | None]
    camera_weight: Annotated[float, Field(ge=0, le=0.5)]
    missing_sources: list[Literal["sonar", "camera", "env"]]
    data_status: Literal['ready','warming_up','insufficient_data'] = 'ready'
    metric_reliability: dict[Metric,MetricReliability] = Field(default_factory=dict)
    model_version: str | None = None
    baseline_windows: int = 0
    level_held_due_to_missing: bool = False
    last_observation_at: AwareDatetime | None = None
    analysis_id: int | None = None
    config_revision: str | None = None
    valid_until: AwareDatetime | None = None
    stale: bool = False
    config_changed: bool = False


class Point(Contract):
    ts: AwareDatetime
    value: float | list[float]
    source: Literal["sonar", "camera", "env"] | None = None
    confidence: Ratio | None = None


class Timeseries(Contract):
    cage_id: int
    metric: Metric
    points: list[Point]
    has_more: bool = False
    next_before: AwareDatetime | None = None


class Option(Contract):
    id: str
    label: str
    available: bool
    estimated_loss_krw: int | float | None
    reason: str | None
    config_action_id: str | None = None
    reasons: list[dict[str,Any]] = Field(default_factory=list)
    calculation: dict[str,Any] | None = None
    source: str | None = None
    text: str | None = None


class Recommendation(Contract):
    id: int
    cage_id: int
    ts: AwareDatetime
    recommended_option: str | None
    options: list[Option]
    mode: Literal['loss_comparison','first_aid_required'] = 'loss_comparison'
    details: dict[str,Any] = Field(default_factory=dict)
    first_aid: dict[str,Any] | None = None
    diagnosis_request_required: bool = False
    analysis_id: int | None = None
    config_revision: str | None = None
    valid_until: AwareDatetime | None = None
    stock_changed: bool = False
    chosen_option: str | None = None
    stale: bool = False
    config_changed: bool = False
    selectable: bool = False

    @model_validator(mode='after')
    def coherent_mode(self):
        if self.mode=='first_aid_required' and (not self.first_aid or not self.first_aid.get('actions')
                or self.recommended_option is not None or any(o.estimated_loss_krw is not None for o in self.options)):
            raise ValueError('Disease-family recommendations require non-economic reviewed actions')
        if self.recommended_option is not None and not any(o.id==self.recommended_option and o.available for o in self.options):
            raise ValueError('Recommended option must be available')
        return self


class ChoiceRequest(Contract):
    option_id: Annotated[str, Field(min_length=1)]
    request_key: UUID

    @field_validator('option_id')
    @classmethod
    def supported_action(cls,value):
        return value if value in ('dead_fish_removal','tool_separation') else canonical_action(value)


class ChoiceReceipt(Contract):
    persisted: Literal[True]
    resource_id: int
    option_id: str
    request_key: UUID
    chosen_at: AwareDatetime
    execution_status: Literal['not_executed']


class StockItem(Contract):
    size_group: Literal["fry", "mid", "market"]
    count: Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
    avg_weight_kg: Annotated[float, Field(strict=True, gt=0)]


class StockRequest(Contract):
    stock: Annotated[list[StockItem], Field(min_length=1, max_length=3)]
    insured: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def unique_groups(self):
        if len({row.size_group for row in self.stock}) != len(self.stock):
            raise ValueError("Stock size groups must be unique")
        return self


class SavedStockItem(StockItem):
    updated_at: AwareDatetime


class StockReceipt(Contract):
    persisted: Literal[True]
    cage_id: int
    received_count: int
    insured: bool
    stock: list[SavedStockItem]


class DiagnosisRequest(Contract):
    request_key: UUID


class DiagnosisReceipt(Contract):
    persisted: Literal[True]
    id: Annotated[int, Field(gt=0)]
    cage_id: Annotated[int, Field(gt=0)]
    request_key: UUID
    status: Literal["pending"]
    dispatch_status: Literal["pending"]
    created_at: AwareDatetime
    creation_config_revision: str
    packet: dict[str, Any]


class ConfigRequest(Contract):
    values: Annotated[dict[str, Any], Field(min_length=1)]


class MockReceipt(Contract):
    mock: Literal[True] = True
    persisted: Literal[False] = False
    action: str
    resource_id: int | str | None = None
    received_count: int = 1


class IngestReceipt(Contract):
    persisted: Literal[True] = True
    received_count: int
    inserted_count: int


class AreaSummary(Contract):
    area_id: int
    name: str
    level: Literal["정상", "주의", "경고", "위험"]
    stress_score: float
    cage_count: int


class StressMap(Contract):
    ts: AwareDatetime
    areas: list[AreaSummary]


class ReleaseDemand(Contract):
    area_id: int
    ts: AwareDatetime
    estimated_fish_count: int
    cage_count: int


class DemoResult(Contract):
    mock: Literal[True] = True
    scenario: Scenario
    t: float
    status: CageStatus
