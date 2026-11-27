"""Owner-authenticated, versioned diagnosis snapshot creation. No external dispatch."""

from datetime import timedelta
from math import isclose
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from common import MetricRecord
from common.config_loader import ConfigError, ConfigLoader
from decision.first_aid import UnsafeFirstAid, ensure_safe, reviewed_guide
from .farmer_data import data_error
from .schemas import DiagnosisReceipt, DiagnosisSummary


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SuspectedCause(ContextModel):
    id: Annotated[int, Field(strict=True, gt=0)]
    ts: AwareDatetime
    cause: Annotated[str, Field(min_length=1)]
    rule_id: Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
    probability: Annotated[float, Field(strict=True, ge=0, le=1)]


class StressPoint(ContextModel):
    ts: AwareDatetime
    score: Annotated[float, Field(strict=True, ge=0, le=100)] | None
    baseline_status: Literal["ready", "warming_up"]


class DiagnosisContext(ContextModel):
    cage_id: Annotated[int, Field(strict=True, gt=0)]
    cause_ts: AwareDatetime
    causes: Annotated[list[SuspectedCause], Field(min_length=1, max_length=8)]
    metrics: Annotated[list[MetricRecord], Field(max_length=1000)]
    stress: Annotated[list[StressPoint], Field(max_length=1000)]
    metrics_truncated: bool
    stress_truncated: bool
    lookback_hours: Annotated[float, Field(gt=0, le=168)]


def build_packet(raw, cage_id, snapshot, selected_id=None):
    context = DiagnosisContext.model_validate(raw)
    if context.cage_id != cage_id or any(c.ts != context.cause_ts for c in context.causes):
        raise ValueError("Invalid diagnosis context scope")
    if len({c.rule_id for c in context.causes}) != len(context.causes) or not isclose(
            sum(c.probability for c in context.causes), 1, rel_tol=0, abs_tol=1e-6):
        raise ValueError("Invalid cause distribution")
    priority = snapshot["cause_rules"]["probability"]["tie_break_order"]
    order = lambda c: (-c.probability, priority.index(c.rule_id) if c.rule_id in priority else len(priority), c.id)
    causes = sorted(context.causes, key=order)
    selected = causes[0] if selected_id is None else next((c for c in causes if c.id == selected_id), None)
    if selected is None or selected.rule_id not in ("R5", "R8") or selected.probability < causes[0].probability:
        raise data_error(409, "disease_cause_required", "A leading disease-family cause is required")
    cycle = timedelta(minutes=snapshot["thresholds"]["cycle_minutes"])
    start = context.cause_ts - timedelta(hours=context.lookback_hours)
    stress = sorted(context.stress, key=lambda s: s.ts)
    if len({s.ts for s in stress}) != len(stress) or not stress or stress[-1].ts != context.cause_ts or any(not start<=s.ts<=context.cause_ts for s in stress):
        raise data_error(409, "diagnosis_data_missing", "Current stress observations are required")
    eligible = lambda s: s.baseline_status == "ready" and s.score is not None and s.score >= snapshot["thresholds"]["cause_min_stress"]
    if not eligible(stress[-1]):
        raise data_error(409, "diagnosis_data_missing", "Current abnormal stress observations are required")
    onset = stress[-1].ts
    censored = True
    for previous in reversed(stress[:-1]):
        if onset - previous.ts != cycle:
            break
        if not eligible(previous):
            censored = False
            break
        onset = previous.ts
    graphs = {}
    identities = set()
    for m in sorted(context.metrics, key=lambda m: (m.ts, m.metric.value, m.source.value)):
        key = (m.ts, m.metric, m.source)
        if m.cage_id != str(cage_id) or not start <= m.ts <= context.cause_ts or key in identities:
            raise ValueError("Invalid graph observation scope")
        identities.add(key)
        graphs.setdefault(m.metric.value, []).append({"ts": m.ts.isoformat(), "value": m.value,
                                                     "source": m.source.value, "confidence": m.confidence})
    if not set(graphs) & {"activity", "cohesion", "feeding", "still_ratio", "rubbing_rate", "vertical_dist"}:
        raise data_error(409, "diagnosis_data_missing", "Behavior observations are required")
    packet = {"cage_id": cage_id, "observed_at": context.cause_ts.isoformat(),
              "onset_at": onset.isoformat(), "onset_kind": "observed_abnormal_stress_run",
              "onset_policy": {"cause_min_stress": snapshot["thresholds"]["cause_min_stress"],
                               "cycle_minutes": snapshot["thresholds"]["cycle_minutes"], "config_revision": snapshot.revision},
              "onset_censored": censored, "primary_cause_id": selected.id,
              "suspected_causes": [c.model_dump(mode="json") for c in causes],
              "graphs": graphs, "metrics_truncated": context.metrics_truncated,
              "stress_truncated": context.stress_truncated,
              "first_aid": reviewed_guide(selected.rule_id, snapshot)}
    ensure_safe(packet, snapshot)
    return packet


def get_diagnosis_loader():
    return ConfigLoader()


def read_request(store, cage_id, request_key=None):
    store.require_cage(cage_id)
    data = store.request('POST', '/rest/v1/rpc/read_diagnosis_request', json={
        'p_cage_id': cage_id, 'p_request_key': str(request_key) if request_key is not None else None})
    if data is None:
        raise data_error(404, 'diagnosis_request_not_found', 'No matching diagnosis request')
    try:
        summary = DiagnosisSummary.model_validate(data)
        if summary.cage_id != cage_id or (request_key is not None and summary.request_key != request_key):
            raise ValueError
        return summary
    except (ValueError, TypeError):
        raise data_error(503, 'invalid_diagnosis_receipt', 'Invalid diagnosis receipt') from None


def create_request(store, cage_id, request, loader):
    try:
        snapshot = loader.snapshot()
        policy = snapshot["first_aid"]["diagnosis_policy"]
        params = {"p_cage_id": cage_id, "p_request_key": str(request.request_key),
                  "p_lookback_hours": policy["lookback_hours"], "p_max_points": policy["max_points"],
                  "p_max_age_minutes": policy["max_cause_age_minutes"]}
        store.require_cage(cage_id)
        data = store.request("POST", "/rest/v1/rpc/diagnosis_context", json=params)
        packet = build_packet(data["context"], cage_id, snapshot, data.get("primary_cause_id"))
        if not data.get("persisted"):
            data = store.request("POST", "/rest/v1/rpc/create_diagnosis_request", json={**params,
                "p_context_version": data["context_version"], "p_primary_cause_id": packet["primary_cause_id"],
                "p_config_revision": snapshot.revision, "p_min_interval_minutes": policy['min_request_interval_minutes']})
        if data.get("persisted") is not True or data.get("cage_id") != cage_id or data.get("request_key") != str(request.request_key):
            raise ValueError("Invalid receipt scope")
        # Construct from the committed snapshot (including idempotent replays), never a client packet.
        packet = build_packet(data["context"], cage_id, snapshot, data["primary_cause_id"])
        receipt = DiagnosisReceipt.model_validate({"persisted": True, "id": data["id"], "cage_id": cage_id,
            "request_key": data["request_key"], "status": data["status"], "dispatch_status": "pending",
            "created_at": data["created_at"], "creation_config_revision": data["config_revision"], "packet": packet})
        ensure_safe(receipt.model_dump(mode="json"), snapshot)
        return receipt
    except (ConfigError, UnsafeFirstAid):
        raise data_error(503, "first_aid_unavailable", "Reviewed response content is unavailable") from None
    except (ValidationError, ValueError, KeyError, TypeError, AttributeError):
        raise data_error(503, "invalid_diagnosis_context", "Invalid diagnosis context; check stored requests before retrying") from None
