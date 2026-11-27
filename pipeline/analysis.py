"""DB observations -> M5/cause/decision -> one coherent publishable bundle (#104)."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from common import MetricName, MetricRecord
from common.config_loader import ConfigError
from cause import CauseContext, CauseEngine
from decision import ComparisonRequest, LossCalculator
from decision.actions import ACTIONS, canonical_action
from decision.first_aid import ensure_safe, reviewed_guide
from decision.deadlines import with_deadlines
from fusion.fuse import FusedFeatures, _bucket, fuse
from stress.levels import apply_levels
from stress import model as model_config


class FixedSnapshot:
    def __init__(self, value):
        self.value = value

    def snapshot(self):
        return self.value


def parse_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('An aware timestamp is required')
    return stamp.astimezone(timezone.utc)


def reliability(fused, records, policy):
    by_metric = defaultdict(dict)
    for r in records:
        current = by_metric[r.metric.value].get(r.source.value)
        by_metric[r.metric.value][r.source.value] = min(current, r.confidence) if current is not None else r.confidence
    result = {}
    for metric in MetricName:
        name = metric.value
        value = fused.quality if name == 'quality' else fused.env.get(name) if name in ('temp_surface', 'temp_mid', 'do') else fused.features.get(name)
        source_weights = {}
        if value is not None:
            if name in ('vertical_dist', 'activity'):
                w = fused.weights.get(name, 0)
                source_weights = {k: weight for k, weight in (('sonar', 1-w), ('camera', w)) if weight>0}
            elif name in ('temp_surface', 'temp_mid', 'do'):
                source_weights = {'env': 1}
            elif name == 'cohesion':
                source_weights = {'sonar': 1}
            else:
                source_weights = {'camera': 1}
        available = by_metric[name]
        if name == 'quality' and not available:
            confs = [r.confidence for r in records if r.source.value == 'camera']
            available = {'camera': min(confs)} if confs else {}
        confidence = sum(weight * available[src] for src, weight in source_weights.items()) if source_weights and all(src in available for src in source_weights) else None
        grade = ('unavailable' if confidence is None else 'good' if confidence>=policy['confidence_good_min'] else
                 'limited' if confidence>=policy['confidence_limited_min'] else 'low')
        result[name] = {'grade': grade, 'confidence': confidence, 'sources': list(source_weights) if confidence is not None else []}
    return result


def recommendation_for(context, primary, level, snapshot):
    if primary is None:
        return None, 'no_current_cause'
    if primary.rule_id in ('R5', 'R8'):
        guide = reviewed_guide(primary.rule_id, snapshot)
        guide.pop('message')
        guide['dispatch_status'] = 'not_created'
        options = [{'id': canonical_action(a['id']) if a['id'] in ('feed_reduction','density_control','oxygen_boost') else a['id'],
                    'label': a['name'], 'available': True, 'estimated_loss_krw': None, 'reason': None,
                    'source': a['source'], 'text': a['text']} for a in guide['actions']]
        result = {'mode': 'first_aid_required', 'recommended_option': None, 'options': options,
                  'first_aid': guide, 'diagnosis_request_required': True, 'details': {}}
        ensure_safe(result, snapshot)
        return result, None
    if not context.get('stock') or not any(row.get('count',0)>0 for row in context['stock']):
        return None, 'stock_required'
    rule = snapshot['cause_rules']['rules'][primary.rule_id]
    allowed = {'keep'}
    for action in (*rule['first_action'], *rule.get('compare_options', ())):
        if action in ('immediate_alert', 'compare_options'):
            continue
        try:
            allowed.add(canonical_action(action))
        except ValueError:
            raise ConfigError('Unsupported economic cause action') from None
    unavailable = {k: '현재 의심 원인의 비교 대상이 아닙니다.' for k in ACTIONS if k not in allowed}
    try:
        request = ComparisonRequest.model_validate({'cage_id': str(context['cage_id']), 'farm_id': str(context['farm_id']),
            'sea_area_id': str(context['sea_area_id']), 'species': context['species'], 'level': level,
            'primary_rule_id': primary.rule_id, 'stock': context['stock'], 'other_area_release_count': None,
            'unavailable_actions': unavailable})
    except ValidationError:
        return None, 'stock_input_invalid'
    result = LossCalculator(FixedSnapshot(snapshot)).compare(request)
    result.pop('config_loaded_at',None)  # Publication identity must not change when an identical config is reloaded.
    recommended = result['lowest_loss_option']
    if primary.rule_id=='R7':
        recommended = 'reduce_feed' if any(o['id']=='reduce_feed' and o['available'] for o in result['options']) else None
        result['recommendation_basis'] = 'R7_feeding_management'  # Rule guidance differs from the monetary minimum.
    return {'mode': result['mode'], 'recommended_option': recommended,
            'options': result['options'], 'first_aid': None, 'diagnosis_request_required': False, 'details': result}, None


def build_bundle(context, model, producer_id, species, snapshot, *, advisory_active=None):
    if model.cage_id != producer_id:
        raise ValueError('The trained model must match the explicit producer ID')
    thresholds = snapshot['thresholds']
    cycle = timedelta(minutes=thresholds['cycle_minutes'])
    if thresholds['cycle_minutes'] != model_config.CYCLE_MINUTES or thresholds['baseline']['window_hours'] != model_config.WINDOW_HOURS:
        raise ConfigError('M5 process settings changed; reload/retrain with matching settings')
    start, end = parse_time(context['from']), parse_time(context['to'])
    if end<=start or end-start>timedelta(days=7) or start!=_bucket(start, int(thresholds['cycle_minutes'])) or end!=_bucket(end,int(thresholds['cycle_minutes'])):
        raise ValueError('Analysis window must contain whole configured cycles within seven days')
    if context.get('metrics_truncated'):
        raise ValueError('Truncated analysis inputs cannot be published')
    raw = [MetricRecord.model_validate(r) for r in context['metrics']]
    if any(r.cage_id!=str(context['cage_id']) or not start<=r.ts<end for r in raw):
        raise ValueError('Input scope mismatch')
    records = [r.model_copy(update={'cage_id': producer_id}) for r in raw]
    fusion_cfg = {'q_min': thresholds['fusion']['gate_low'], 'q_full': thresholds['fusion']['full_at'],
                  'w_max': thresholds['fusion']['max_camera_weight'], 'q_camera_only': thresholds['quality']['camera_only_min']}
    observed = {f.ts: f for f in fuse(records, int(thresholds['cycle_minutes']), configuration=fusion_cfg)}
    fused = []
    stamp = start
    while stamp<end:
        fused.append(observed.get(stamp) or FusedFeatures(producer_id,stamp,{}, {},None,0,missing_sources=['sonar','camera','env']))
        stamp += cycle
    scores = model.score_series(fused)
    if len(scores)!=len(fused):
        raise ValueError('Model output alignment mismatch')
    for s, f in zip(scores, fused):
        if f.features.get('activity') is None or f.features.get('vertical_dist') is None:
            s.score, s.evidence, s.components = None, {}, {}
    causes = CauseEngine(FixedSnapshot(snapshot)).evaluate_series(scores, fused, contexts=[CauseContext(advisory_active=advisory_active)]*len(scores))
    apply_levels(scores,[c.immediate_danger for c in causes],cfg=snapshot.to_dict()['thresholds'])
    last, f, cause = scores[-1], fused[-1], causes[-1]
    warm = last.baseline_status=='warming_up'
    state = 'warming_up' if warm else 'insufficient_data' if last.score is None else 'ready'
    latest_metrics = [r for r in records if _bucket(r.ts,int(thresholds['cycle_minutes']))==f.ts]
    evidence = {**(last.evidence or {}), **{k:v for k,v in f.env.items() if v is not None}}
    status = {'cage_id': context['cage_id'], 'ts': end.isoformat(), 'stress_score': None if warm else last.score,
              'level': None if warm else last.level, 'baseline_status': last.baseline_status, 'data_status': state,
              'evidence': evidence, 'camera_weight': f.camera_weight, 'missing_sources': f.missing_sources,
              'metric_reliability': reliability(f,latest_metrics,thresholds['analysis']), 'model_version': last.model_version or 'untrained',
              'baseline_windows': last.baseline_windows, 'cause': cause.status_causes(),
              'level_held_due_to_missing': not warm and last.score is None,
              'last_observation_at': max((r.ts for r in records), default=None).isoformat() if records else None}
    r7_evidence = next((c.evidence for c in cause.estimates if c.rule_id=='R7'),{})
    status.update(r7_held_due_to_missing=r7_evidence.get('feeding_held_due_to_missing',False),
                  feeding_last_observed_at=r7_evidence.get('feeding_last_observed_at'))
    estimates = [c.to_dict() for c in cause.estimates] if state=='ready' else []
    primary = cause.estimates[0] if estimates else None
    level_map = {'정상':'normal','주의':'caution','경고':'warning','위험':'danger'}
    economic_context = {**context,'species':species}
    rec, reason = recommendation_for(economic_context,primary,level_map.get(last.level,'normal'),snapshot)
    rec = with_deadlines(rec,end,level_map.get(last.level,'normal'),snapshot)
    if primary is not None and primary.rule_id in ('R5','R8'):
        ensure_safe(status,snapshot)
    last_seen = max((r.ts for r in raw),default=start)
    return {'status': status, 'causes': estimates, 'recommendation': rec, 'recommendation_reason': reason,
            'config_revision': snapshot.revision,
            'valid_until': (last_seen+timedelta(minutes=thresholds['analysis']['max_age_minutes'])).isoformat()}
