"""Inputs for the pure loss calculator; live DB/auth integration remains #104."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .actions import canonical_action

Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)]
Weight = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
Identifier = Annotated[str, Field(strict=True, min_length=1, pattern=r"^\S(?:.*\S)?$")]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def reject_numeric_booleans(cls, value, info):
        if isinstance(value, bool) and info.field_name not in ("insured", "insurance_confirmed", "release_support_confirmed"):
            raise ValueError("Boolean is not a numeric input")
        return value


class StockGroup(InputModel):
    size_group: Literal["fry", "mid", "market"]
    count: Count
    avg_weight_kg: Weight | None = None
    price_per_kg: Amount | None = None
    insured: Annotated[bool, Field(strict=True)] = False


class ComparisonRequest(InputModel):
    cage_id: Identifier
    farm_id: Identifier
    sea_area_id: Identifier
    species: Identifier
    level: Literal["normal", "caution", "warning", "danger"]
    stock: Annotated[list[StockGroup], Field(min_length=1, max_length=3)]
    # Excludes the request's stock; None means the area-wide total is unknown.
    other_area_release_count: Count | None = None
    insurance_confirmed: Annotated[bool, Field(strict=True)] = False
    release_support_confirmed: Annotated[bool, Field(strict=True)] = False
    farm_subsidy_remaining_krw: Amount | None = None
    unavailable_actions: dict[str, Annotated[str, Field(min_length=1)]] = Field(default_factory=dict)
    primary_rule_id: Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"] | None = None

    @field_validator("unavailable_actions")
    @classmethod
    def public_actions(cls, value):
        result = {}
        for key, reason in value.items():
            action = canonical_action(key)
            if action in result:
                raise ValueError("Duplicate aliases for one unavailable action")
            result[action] = reason
        return result

    @model_validator(mode="after")
    def coherent_stock(self):
        if len({s.size_group for s in self.stock}) != len(self.stock):
            raise ValueError("Stock groups must be unique")
        if not any(s.count for s in self.stock):
            raise ValueError("At least one fish is required; missing stock cannot be valued as zero")
        if self.release_support_confirmed and self.farm_subsidy_remaining_krw is None:
            raise ValueError("Confirmed support requires the remaining farm-wide subsidy budget")
        return self
