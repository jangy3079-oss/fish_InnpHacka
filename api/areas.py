"""Official-only aggregates. Forward the user's token; never fetch raw finances."""

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Path
from pydantic import ValidationError

from common.config_loader import ConfigError, ConfigLoader
from .analysis import get_analysis_loader
from .farmer_data import (
    FarmerDataStore, SupabaseSettings, data_error, get_farmer_http_client, require_farmer_token,
)
from .schemas import ReleaseDemand, StressMap

MAX_ID = 9_007_199_254_740_991
router = APIRouter(prefix="/areas", tags=["areas"])


class OfficialDataStore(FarmerDataStore):
    def authenticate(self):
        user = self.request("GET", "/auth/v1/user")
        try:
            UUID(user["id"])
            if user.get("is_anonymous") is True:
                raise ValueError
        except (TypeError, KeyError, ValueError, AttributeError):
            raise data_error(401, "invalid_user", "A registered Supabase user is required") from None
        metadata = user.get("app_metadata")
        if not isinstance(metadata, dict) or metadata.get("role") != "official":
            raise data_error(403, "area_forbidden", "Official area access required")
        ids = metadata.get("area_ids", [])
        if not isinstance(ids, list) or any(type(i) is not int or not 0 < i <= MAX_ID for i in ids):
            raise data_error(403, "area_forbidden", "Invalid official area scope")
        self.area_ids = sorted(set(ids))


def get_official_store(token: Annotated[str, Depends(require_farmer_token)],
                       client: Annotated[httpx.Client, Depends(get_farmer_http_client)]):
    store = OfficialDataStore(SupabaseSettings.from_env(), token, client)
    store.authenticate()
    return store


def aggregate(store, loader, model, rpc, **params):
    try:
        revision = loader.snapshot().revision
        data = store.request("POST", "/rest/v1/rpc/" + rpc,
                             json={**params, "p_config_revision": revision})
        if data == {"unavailable_reason": "area_not_found"}:
            raise data_error(404, "area_not_found", "Area not found")
        result = model.model_validate(data)
        if result.config_revision != revision:
            raise ValueError
        if isinstance(result, StressMap):
            ids = [a.area_id for a in result.areas]
            if len(ids) != len(set(ids)) or not set(ids).issubset(store.area_ids):
                raise ValueError
        elif result.area_id != params["p_area_id"]:
            raise ValueError
        return result
    except ConfigError:
        raise data_error(503, "area_config_unavailable", "Current analysis configuration is unavailable") from None
    except (ValidationError, ValueError, TypeError):
        raise data_error(503, "invalid_area_response", "Invalid area aggregate response") from None


@router.get("/stress-map", response_model=StressMap,
            description="허용된 해역만 집계합니다. 단계·지수는 현재 게시 회차의 최댓값이며 미집계는 null입니다.")
def stress_map(store: Annotated[OfficialDataStore, Depends(get_official_store)],
               loader: Annotated[ConfigLoader, Depends(get_analysis_loader)]):
    return aggregate(store, loader, StressMap, "area_stress_map", p_area_ids=store.area_ids)


@router.get("/{id}/release-demand", response_model=ReleaseDemand,
            description="현재 유효한 방류 추천 가두리의 재고 합계입니다. 선택·실행량과 다르며 일부 미집계는 하한입니다.")
def release_demand(id: Annotated[int, Path(gt=0, le=MAX_ID)],
                   store: Annotated[OfficialDataStore, Depends(get_official_store)],
                   loader: Annotated[ConfigLoader, Depends(get_analysis_loader)]):
    if id not in store.area_ids:
        raise data_error(403, "area_forbidden", "Area is not assigned to this official")
    return aggregate(store, loader, ReleaseDemand, "area_release_demand", p_area_id=id)
