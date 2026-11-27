"""Current Auth + DB ownership/JWT intersection. No financial payloads in /me."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from common.config_loader import ConfigError, ConfigLoader
from decision.first_aid import UnsafeFirstAid, ensure_safe
from .analysis import get_analysis_loader
from .farmer_data import FarmerDataStore, data_error, get_farmer_store
from .schemas import UserAccess, StockView, AreaShapes, AreaCauseDistribution

router = APIRouter(tags=['access'])
MAX_ID = 9_007_199_254_740_991


def current_access(store):
    metadata = store.user.get('app_metadata') or {}
    if not isinstance(metadata, dict):
        raise data_error(403,'access_forbidden','Invalid access metadata')
    official = metadata.get('role') == 'official'
    area_ids = metadata.get('area_ids',[]) if official else []
    financial = metadata.get('financial_farm_ids',[]) if official else []
    if any(not isinstance(ids,list) or any(type(i) is not int or not 0<i<=MAX_ID for i in ids)
           for ids in (area_ids,financial)):
        raise data_error(403,'access_forbidden','Invalid area scope')
    data = store.request('POST','/rest/v1/rpc/user_access',json={'p_area_ids':sorted(set(area_ids))})
    try:
        owned = any(c['can_write_stock'] is True for c in data['cages'])
        role = metadata.get('role') or ('farmer' if owned else 'unassigned')
        if role not in ('official','farmer','edge','analysis','admin'):
            role='unassigned'
        result=UserAccess.model_validate({**data,'role':role})
        if result.user_id != UUID(store.user['id']) or len({c.id for c in result.cages})!=len(result.cages):
            raise ValueError
        if any(not c.can_write_stock and (not official or c.sea_area_id not in area_ids) for c in result.cages):
            raise ValueError
        owned_areas={c.sea_area_id for c in result.cages if c.can_write_stock}
        if not set(result.area_ids).issubset(set(area_ids)|owned_areas):
            raise ValueError
        for cage in result.cages:
            cage.can_read_financial = cage.can_read_financial and (cage.can_write_stock or cage.farm_id in financial)
        return result
    except (ValueError,TypeError,KeyError):
        raise data_error(503,'invalid_access_response','Invalid access response') from None


@router.get('/me',response_model=UserAccess)
def me(store: Annotated[FarmerDataStore,Depends(get_farmer_store)]):
    return current_access(store)


@router.get('/cages/{id}/stock',response_model=StockView)
def stock(id: Annotated[int,Path(gt=0,le=MAX_ID)],store: Annotated[FarmerDataStore,Depends(get_farmer_store)]):
    store.require_cage(id)
    access=current_access(store)
    cage=next((c for c in access.cages if c.id==id),None)
    if cage is None or not cage.can_read_financial:
        raise data_error(403,'stock_forbidden','Financial read access required')
    data=store.request('POST','/rest/v1/rpc/cage_stock',json={'p_cage_id':id})
    try:
        result=StockView.model_validate(data)
        states={s.insured for s in result.stock}
        if result.cage_id!=id or len({s.size_group for s in result.stock})!=len(result.stock):
            raise ValueError
        expected=next(iter(states)) if len(states)==1 else None
        if result.insured!=expected or result.insured_mixed!=(len(states)>1):
            raise ValueError
        return result
    except (ValueError,TypeError):
        raise data_error(503,'invalid_stock_response','Invalid stock response') from None


@router.get('/areas/shapes',response_model=AreaShapes)
def shapes(store: Annotated[FarmerDataStore,Depends(get_farmer_store)]):
    access=current_access(store)
    data=store.request('POST','/rest/v1/rpc/area_shapes',json={'p_area_ids':access.area_ids})
    try:
        result=AreaShapes.model_validate(data)
        ids=[f.properties.area_id for f in result.features]+result.unverified_area_ids
        if len(ids)!=len(set(ids)) or not set(ids).issubset(access.area_ids):
            raise ValueError
        return result
    except (ValueError,TypeError):
        raise data_error(503,'invalid_shapes_response','Invalid area boundary response') from None


@router.get('/areas/cause-distribution',response_model=AreaCauseDistribution)
def distribution(store: Annotated[FarmerDataStore,Depends(get_farmer_store)],
                 loader: Annotated[ConfigLoader,Depends(get_analysis_loader)]):
    access=current_access(store)
    if access.role!='official':
        raise data_error(403,'area_forbidden','Official access required')
    try:
        snapshot=loader.snapshot()
        revision=snapshot.revision
        result=AreaCauseDistribution.model_validate(store.request('POST','/rest/v1/rpc/area_cause_distribution',
            json={'p_area_ids':access.area_ids,'p_config_revision':revision}))
        if result.config_revision!=revision:
            raise ValueError
        ensure_safe(result.model_dump(mode='json'),snapshot)
        return result
    except (ConfigError, UnsafeFirstAid):
        raise data_error(503,'area_config_unavailable','Analysis configuration unavailable') from None
    except (ValueError,TypeError):
        raise data_error(503,'invalid_area_response','Invalid cause distribution') from None
