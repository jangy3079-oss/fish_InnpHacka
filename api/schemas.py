"""Provisional API contracts. See docs/api-contract.md for team decisions."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator, field_validator
from decision.actions import canonical_action

Metric = Literal[
    "vertical_dist", "activity", "cohesion", "feeding", "still_ratio",
    "rubbing_rate", "quality", "temp_surface", "temp_mid", "do",
]
Scenario = Literal["heatwave_typhoon", "turbidity_event", "disease_suspect", "parasite_suspect", "feeding_drop"]
ConfigFile = Literal["thresholds", "cause_rules", "release_rules", "costs", "first_aid"]
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Cause(Contract):
    name: str
    probability: Ratio
    rule_id: str
    display: bool = True
    cause_source: Literal['cause_engine'] | None = None


class ObservationProgress(Contract):
    done: Annotated[float, Field(ge=0)]
    needed: Annotated[float, Field(gt=0)]
    unit: Literal['hour']


class CausePending(Contract):
    rule_id: Literal['R5','R8']
    reason: Literal['trend_waiting','observing','observation_gap','advisory_unknown']
    progress: ObservationProgress | None = None


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
    cause_source: Literal['cause_engine'] | None = None
    cause_pending: list[CausePending] = Field(default_factory=list)
    last_seen: dict[Literal['sonar','camera','env'], AwareDatetime | None] = Field(default_factory=dict)
    feeding_reset_reason: Literal['observation_gap','conditions_changed','observed_normal','hold_expired'] | None = None
    evidence: dict[str, float | None]
    camera_weight: Annotated[float, Field(ge=0, le=0.5)]
    missing_sources: list[Literal["sonar", "camera", "env"]]
    data_status: Literal['ready','warming_up','insufficient_data'] = 'ready'
    metric_reliability: dict[Metric,MetricReliability] = Field(default_factory=dict)
    model_version: str | None = None
    baseline_windows: int = 0
    level_held_due_to_missing: bool = False
    r7_held_due_to_missing: bool = False
    feeding_last_observed_at: AwareDatetime | None = None
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


class DeadlineEntry(Contract):
    kind: Literal['next_assessment','release_application','harvest']
    label: str
    action_id: Literal['release','harvest'] | None
    applicable: bool
    at: AwareDatetime | None
    source_status: Literal['pending','assumed','confirmed']
    basis: Literal['absolute','relative','unconfirmed']
    source: str | None
    expired: bool = False

    @model_validator(mode='after')
    def coherent_time(self):
        if (self.at is None)!=(self.source_status=='pending') or (self.at is None)!=(self.basis=='unconfirmed'):
            raise ValueError('Deadline time and source state must agree')
        if self.source_status=='confirmed' and (not self.source or not self.source.strip()):
            raise ValueError('Confirmed deadlines require a source')
        if (self.kind=='next_assessment')!=(self.action_id is None):
            raise ValueError('Deadline action scope mismatch')
        if self.kind=='next_assessment' and not self.applicable:
            raise ValueError('Next assessment must apply to this recommendation')
        if self.action_id is not None and self.action_id!={'release_application':'release','harvest':'harvest'}[self.kind]:
            raise ValueError('Deadline action scope mismatch')
        return self


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
    deadline: AwareDatetime | None = None
    deadlines: list[DeadlineEntry] = Field(default_factory=list)
    deadline_expired: bool = False

    @model_validator(mode='after')
    def coherent_mode(self):
        if self.deadlines:
            times = [e.at for e in self.deadlines if e.applicable and e.at is not None]
            if len({e.kind for e in self.deadlines})!=len(self.deadlines) or self.deadline!=(min(times) if times else None):
                raise ValueError('Primary deadline must be the earliest applicable time')
            if not any(e.kind=='next_assessment' for e in self.deadlines):
                raise ValueError('Deadline metadata requires a next assessment')
            for entry in self.deadlines:
                if entry.action_id is not None and not any(o.id==entry.action_id and o.available==entry.applicable for o in self.options):
                    raise ValueError('Deadline option availability mismatch')
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


SafeId = Annotated[int, Field(strict=True, gt=0, le=9_007_199_254_740_991)]


class AccessibleCage(Contract):
    id: SafeId
    name: str
    farm_id: SafeId
    sea_area_id: SafeId
    can_write_stock: Annotated[bool, Field(strict=True)]
    can_read_financial: Annotated[bool, Field(strict=True)]


class UserAccess(Contract):
    user_id: UUID
    role: Literal['farmer','official','edge','analysis','admin','unassigned']
    area_ids: list[SafeId]
    cages: list[AccessibleCage]


class StockViewItem(SavedStockItem):
    count: Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
    insured: Annotated[bool, Field(strict=True)]


class StockView(Contract):
    cage_id: SafeId
    insured: Annotated[bool, Field(strict=True)] | None
    insured_mixed: Annotated[bool, Field(strict=True)]
    stock: list[StockViewItem]


class AreaGeometry(Contract):
    type: Literal['Polygon']
    coordinates: list[list[list[float]]]

    @model_validator(mode='after')
    def valid_wgs84(self):
        if not self.coordinates or any(len(r)<4 or r[0]!=r[-1] or any(
            len(p)!=2 or not -180<=p[0]<=180 or not -90<=p[1]<=90 for p in r) for r in self.coordinates):
            raise ValueError('Invalid WGS84 polygon')
        return self


class AreaProperties(Contract):
    area_id: SafeId
    name: str
    source: Annotated[str, Field(min_length=1)]
    station_id: str | None


class AreaFeature(Contract):
    type: Literal['Feature']
    geometry: AreaGeometry
    properties: AreaProperties


class AreaShapes(Contract):
    type: Literal['FeatureCollection']
    features: list[AreaFeature]
    unverified_area_ids: list[SafeId]


class CauseCount(Contract):
    rule_id: Annotated[str, Field(pattern=r'^R[1-8]$')]
    name: str
    count: Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]


class AreaCauseDistribution(Contract):
    ts: AwareDatetime
    config_revision: str
    cause_distribution: list[CauseCount]


class DiagnosisRequest(Contract):
    request_key: UUID


WorkKind = Literal["net_change", "sorting"]


class WorkLogRequest(Contract):
    """망갈이·선별 작업 기록 (#45, 원인 규칙 R6 입력). 시각은 실제 작업한 때."""
    kind: WorkKind
    performed_at: AwareDatetime
    request_key: UUID


class WorkLogReceipt(Contract):
    persisted: Literal[True]
    work_log_id: Annotated[int, Field(gt=0)]
    cage_id: Annotated[int, Field(gt=0)]
    kind: WorkKind
    performed_at: AwareDatetime
    created_at: AwareDatetime


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


class DiagnosisSummary(Contract):
    persisted: Literal[True]
    id: Annotated[int, Field(strict=True, gt=0)]
    cage_id: Annotated[int, Field(strict=True, gt=0)]
    request_key: UUID
    primary_cause_id: Annotated[int, Field(strict=True, gt=0)]
    status: Literal['pending']
    dispatch_status: Literal['pending']
    created_at: AwareDatetime
    observed_at: AwareDatetime


class ConfigRequest(Contract):
    values: Annotated[dict[str, Any], Field(min_length=1)]
    expected_revision: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    request_key: UUID
    reason: Annotated[str, Field(min_length=1,max_length=500)]

    @field_validator('reason')
    @classmethod
    def nonblank_reason(cls,value):
        if not value.strip(): raise ValueError('A change reason is required')
        return value


class ConfigView(Contract):
    file: ConfigFile
    revision: str
    values: dict[str,Any]
    loaded_at: AwareDatetime


class ConfigReceipt(Contract):
    persisted: Literal[True]
    changed: bool
    file: ConfigFile
    request_key: UUID
    previous_revision: str
    revision: str
    validated_at: AwareDatetime
    reload_status: Literal['validated']
    application: Literal['next_snapshot','restart_required']
    restart_required: bool


class MockReceipt(Contract):
    mock: Literal[True] = True
    persisted: Literal[False] = False
    action: str
    resource_id: int | str | None = None
    received_count: int = 1


class IngestReceipt(Contract):
    persisted: Literal[True] = True
    received_count: Annotated[int, Field(strict=True, ge=1, le=1000)]
    inserted_count: Annotated[int, Field(strict=True, ge=0, le=1000)]
    duplicate_count: Annotated[int, Field(strict=True, ge=0, le=1000)]

    @model_validator(mode='after')
    def counts_match(self):
        if self.received_count != self.inserted_count + self.duplicate_count:
            raise ValueError('Inconsistent ingestion counts')
        return self


AreaCount = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
AreaId = Annotated[int, Field(strict=True, gt=0, le=9_007_199_254_740_991)]
AreaDataStatus = Literal["complete", "partial", "unavailable"]


class AreaSummary(Contract):
    area_id: AreaId
    name: str
    level: Literal["정상", "주의", "경고", "위험"] | None
    stress_score: Annotated[float, Field(ge=0, le=100)] | None
    cage_count: AreaCount
    assessed_cage_count: AreaCount
    held_cage_count: AreaCount
    unavailable_cage_count: AreaCount
    data_status: AreaDataStatus
    latest_analysis_at: AwareDatetime | None

    @model_validator(mode="after")
    def counts_agree(self):
        known = self.assessed_cage_count + self.held_cage_count
        expected = "complete" if self.cage_count > 0 and self.assessed_cage_count == self.cage_count else "partial" if known else "unavailable"
        if known + self.unavailable_cage_count != self.cage_count or self.data_status != expected:
            raise ValueError("Invalid area coverage")
        if (self.level is None) != (known == 0) or (self.stress_score is None) != (self.assessed_cage_count == 0):
            raise ValueError("Invalid area assessment")
        return self


class StressMap(Contract):
    ts: AwareDatetime
    config_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    aggregation_method: Literal["max_current_level_and_score"]
    areas: list[AreaSummary]


class ReleaseDemand(Contract):
    area_id: AreaId
    ts: AwareDatetime
    config_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    basis: Literal["recommended_not_chosen"]
    estimated_fish_count: AreaCount | None
    cage_count: AreaCount
    recommended_cage_count: AreaCount
    assessed_cage_count: AreaCount
    unavailable_cage_count: AreaCount
    data_status: AreaDataStatus

    @model_validator(mode="after")
    def counts_agree(self):
        expected = "complete" if self.unavailable_cage_count == 0 else "partial" if self.assessed_cage_count else "unavailable"
        if (self.assessed_cage_count + self.unavailable_cage_count != self.cage_count
                or self.recommended_cage_count > self.assessed_cage_count or self.data_status != expected
                or (self.estimated_fish_count is None) != (expected == "unavailable")):
            raise ValueError("Invalid demand coverage")
        if self.estimated_fish_count is not None and (self.estimated_fish_count > 0) != (self.recommended_cage_count > 0):
            raise ValueError("Invalid recommended count")
        return self


class DemoResult(Contract):
    mock: Literal[False] = False
    simulated: Literal[True] = True
    persisted: Literal[False] = False
    scenario: Scenario
    t: float
    t_unit: Literal['hours']
    effective_t: float
    duration_h: float
    status: CageStatus
    recommendation: Recommendation | None
    recommendation_reason: str | None
    timeseries: dict[str,list[Point]]
    config_revision: str
    scenario_revision: str
    producer_id: str
    cause_source: Literal['cause_engine']
    stock_source: Literal['synthetic_demo_config']
