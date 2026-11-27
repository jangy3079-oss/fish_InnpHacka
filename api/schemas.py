"""Provisional API contracts. See docs/api-contract.md for team decisions."""

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

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


class CageStatus(Contract):
    cage_id: int
    ts: AwareDatetime
    stress_score: Annotated[float, Field(ge=0, le=100)] | None
    level: Literal["정상", "주의", "경고", "위험"] | None
    baseline_status: Literal["ready", "warming_up"]
    cause: list[Cause]
    evidence: Evidence
    camera_weight: Annotated[float, Field(ge=0, le=0.5)]
    missing_sources: list[Literal["sonar", "camera", "env"]]


class Point(Contract):
    ts: AwareDatetime
    value: float | list[float]


class Timeseries(Contract):
    cage_id: int
    metric: Metric
    points: list[Point]


class Option(Contract):
    id: str
    label: str
    available: bool
    estimated_loss_krw: float | None
    reason: str | None


class Recommendation(Contract):
    id: int
    cage_id: int
    ts: AwareDatetime
    recommended_option: str
    options: list[Option]


class ChoiceRequest(Contract):
    option_id: Annotated[str, Field(min_length=1)]


class StockItem(Contract):
    size_group: Literal["fry", "mid", "market"]
    count: Annotated[int, Field(strict=True, ge=0)]
    avg_weight_kg: Annotated[float, Field(gt=0)]


class StockRequest(Contract):
    stock: Annotated[list[StockItem], Field(min_length=1)]
    insured: bool


class DiagnosisRequest(Contract):
    note: Annotated[str, Field(max_length=2000)] = ""


class ConfigRequest(Contract):
    values: Annotated[dict[str, Any], Field(min_length=1)]


class MockReceipt(Contract):
    mock: Literal[True] = True
    persisted: Literal[False] = False
    action: str
    resource_id: int | str | None = None
    received_count: int = 1


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
