"""Read one published analysis generation and record owner choices (#104)."""

from datetime import datetime,timezone
from pydantic import ValidationError

from common.config_loader import ConfigError,ConfigLoader
from common import MetricName
from decision.first_aid import UnsafeFirstAid,ensure_safe
from .farmer_data import data_error
from .schemas import CageStatus,Recommendation,ChoiceReceipt


def get_analysis_loader():
    return ConfigLoader()


def read_status(store,cage_id,loader):
    try:
        snapshot = loader.snapshot()
        store.require_cage(cage_id)
        data = store.request('POST','/rest/v1/rpc/farmer_status',json={'p_cage_id':cage_id})
        if data is None or isinstance(data,dict) and data.get('unavailable_reason')=='analysis_not_found':
            raise data_error(404,'analysis_not_found','No published analysis is available')
        status = CageStatus.model_validate(data)
        if status.cage_id!=cage_id or status.analysis_id is None or status.valid_until is None or set(status.metric_reliability)!={m.value for m in MetricName}:
            raise ValueError('Invalid status scope')
        status.config_changed = status.config_revision != snapshot.revision
        status.stale = datetime.now(timezone.utc)>status.valid_until or status.config_changed or status.last_observation_at is None
        ensure_safe(status.model_dump(mode='json'),snapshot)
        return status
    except (ConfigError,UnsafeFirstAid):
        raise data_error(503,'analysis_content_unavailable','Reviewed analysis content is unavailable') from None
    except (ValidationError,ValueError,KeyError,TypeError):
        raise data_error(503,'invalid_analysis_response','Invalid analysis response') from None


def read_recommendation(store,cage_id,loader):
    try:
        snapshot = loader.snapshot()
        store.require_cage(cage_id)
        data = store.request('POST','/rest/v1/rpc/farmer_recommendation',json={'p_cage_id':cage_id})
        if data is None or isinstance(data,dict) and 'unavailable_reason' in data:
            reason = data.get('unavailable_reason') if isinstance(data,dict) else None
            code = 'recommendation_'+reason if reason in ('stock_required','stock_input_invalid','no_current_cause') else 'recommendation_not_found'
            raise data_error(404,code,'No current recommendation is available')
        result = Recommendation.model_validate(data)
        if result.cage_id!=cage_id or result.analysis_id is None or result.valid_until is None:
            raise ValueError('Invalid recommendation scope')
        result.config_changed = result.config_revision != snapshot.revision
        result.stale = datetime.now(timezone.utc)>result.valid_until or result.config_changed
        now = datetime.now(timezone.utc)
        result.deadline_expired = result.deadline is not None and now>=result.deadline
        for entry in result.deadlines:
            entry.expired = entry.at is not None and now>=entry.at
        result.selectable = not(result.stale or result.stock_changed or result.chosen_option is not None)
        ensure_safe(result.model_dump(mode='json'),snapshot)
        return result
    except (ConfigError,UnsafeFirstAid):
        raise data_error(503,'analysis_content_unavailable','Reviewed analysis content is unavailable') from None
    except (ValidationError,ValueError,KeyError,TypeError):
        raise data_error(503,'invalid_analysis_response','Invalid recommendation response') from None


def choose(store,recommendation_id,body,loader):
    try:
        snapshot = loader.snapshot()
        data = store.request('POST','/rest/v1/rpc/choose_recommendation',json={
            'p_recommendation_id':recommendation_id,'p_option_id':body.option_id,
            'p_request_key':str(body.request_key),'p_config_revision':snapshot.revision})
        if data is None or isinstance(data,dict) and data.get('unavailable_reason')=='recommendation_not_found':
            raise data_error(404,'recommendation_not_found','Recommendation not found or unavailable')
        receipt = ChoiceReceipt.model_validate(data)
        if receipt.resource_id!=recommendation_id or receipt.option_id!=body.option_id or receipt.request_key!=body.request_key:
            raise ValueError('Invalid choice scope')
        ensure_safe(receipt.model_dump(mode='json'),snapshot)
        return receipt
    except (ConfigError,UnsafeFirstAid):
        raise data_error(503,'analysis_content_unavailable','Reviewed analysis content is unavailable') from None
    except (ValidationError,ValueError,KeyError,TypeError):
        raise data_error(503,'invalid_choice_response','Invalid choice acknowledgement; check persistence before retrying') from None
