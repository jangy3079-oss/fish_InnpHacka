"""Isolated synthetic replay: no database, outbound calls, artifacts or label causes (#30)."""

from collections import OrderedDict
from copy import deepcopy
from datetime import timedelta
import hashlib
import math
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field
import yaml

from cause import CauseEngine
from pipeline.contexts import scenario_contexts
from common import MetricName
from common.config_loader import ConfigError, ConfigLoader, UniqueSafeLoader, MAX_FILE_BYTES
from decision.first_aid import ensure_safe
from decision.schemas import StockGroup
from fusion.fuse import fuse
from pipeline.analysis import FixedSnapshot, recommendation_for, reliability
from pipeline.export import hourly_timeseries
from simulator.echogram import synthesize
from simulator.run import simulate
from simulator.scenario import SCENARIO_DIR, load_scenario
from sonar.metrics import extract
from stress import model as model_settings
from stress.levels import apply_levels
from stress.model import StressModel

SCENARIOS = ('heatwave_typhoon', 'turbidity_event', 'disease_suspect', 'parasite_suspect', 'feeding_drop')
ROOT = Path(__file__).resolve().parents[1]


class ReplayRangeError(ValueError):
    pass


class ReplaySettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cage_id: int = Field(gt=0)
    farm_id: int = Field(gt=0)
    sea_area_id: int = Field(gt=0)
    species: str = Field(min_length=1)
    stock: list[StockGroup] = Field(min_length=1, max_length=3)


class ReplayEngine:
    """Per-process bounded caches; serialize expensive preparation and copy all returned data."""

    def __init__(self, loader=None, settings_path=ROOT/'config/demo.yaml', scenario_dir=SCENARIO_DIR):
        self.loader = loader or ConfigLoader()
        self.settings_path = Path(settings_path)
        self.scenario_dir = Path(scenario_dir)
        self._lock = RLock()
        self._model = None
        self._model_key = None
        self._prepared = OrderedDict()
        self._responses = OrderedDict()

    @staticmethod
    def _remember(cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache)>limit:
            cache.popitem(last=False)
        return value

    def _records(self, scenario):
        sim = simulate(scenario)
        return sim.records + extract(synthesize(sim))

    def replay(self, name, t=0):
        if name not in SCENARIOS or isinstance(t, bool) or not math.isfinite(t) or t<0:
            raise ReplayRangeError('Invalid scenario or elapsed hours')
        with self._lock:
            snapshot = self.loader.snapshot()
            raw_settings = self.settings_path.read_bytes()
            if len(raw_settings)>MAX_FILE_BYTES:
                raise ConfigError('Demo settings are too large')
            settings = ReplaySettings.model_validate(yaml.load(raw_settings, Loader=UniqueSafeLoader))
            # Validate duplicate groups / nonempty synthetic stock with the existing economic input model.
            if len({s.size_group for s in settings.stock})!=len(settings.stock) or not any(s.count for s in settings.stock):
                raise ConfigError('Invalid synthetic stock')
            sc_path = self.scenario_dir/f'{name}.yaml'
            baseline_path = self.scenario_dir/'normal_14d.yaml'
            revision = hashlib.sha256(raw_settings+sc_path.read_bytes()+baseline_path.read_bytes()).hexdigest()
            sc = load_scenario(sc_path)
            duration = sc.duration_days*24
            if t>duration:
                raise ReplayRangeError('Elapsed hours exceed scenario duration')
            th = snapshot['thresholds']
            if sc.cycle_minutes!=th['cycle_minutes'] or th['cycle_minutes']!=model_settings.CYCLE_MINUTES or th['baseline']['window_hours']!=model_settings.WINDOW_HOURS:
                raise ConfigError('Replay and M5 cycle/window settings must match')
            cycle = timedelta(minutes=th['cycle_minutes'])
            # Decimal t is hours, not a frame index. Only fully completed cycles are visible.
            count = min(int(math.floor(t*60/th['cycle_minutes']+1e-9)), int(duration*60/th['cycle_minutes']))
            cutoff = sc.start+count*cycle
            key = (name,snapshot.revision,revision,count)
            if key in self._responses:
                self._responses.move_to_end(key)
                result = deepcopy(self._responses[key])
                result['t'] = t
                return result
            fusion_cfg = {'q_min':th['fusion']['gate_low'], 'q_full':th['fusion']['full_at'],
                          'w_max':th['fusion']['max_camera_weight'], 'q_camera_only':th['quality']['camera_only_min']}
            model_key = (snapshot.revision,hashlib.sha256(baseline_path.read_bytes()).hexdigest())
            if self._model_key!=model_key:
                baseline = load_scenario(baseline_path)
                if baseline.start+timedelta(days=baseline.duration_days)>sc.start or baseline.cage_id!=sc.cage_id or baseline.cycle_minutes!=sc.cycle_minutes:
                    raise ConfigError('Replay baseline must be independent, earlier and match the cage/cycle')
                model = StressModel(baseline.cage_id).fit(fuse(self._records(baseline), sc.cycle_minutes, configuration=fusion_cfg))
                self._model, self._model_key = model, model_key
            if self._model.cage_id!=sc.cage_id:
                raise ConfigError('Replay model cage mismatch')
            prep_key = (name,snapshot.revision,revision)
            if prep_key not in self._prepared:
                records = self._records(sc)
                fused = fuse(records,sc.cycle_minutes,configuration=fusion_cfg)
                self._remember(self._prepared,prep_key,(records,fused),len(SCENARIOS))
            records, all_fused = self._prepared[prep_key]
            self._prepared.move_to_end(prep_key)
            fused = [f for f in all_fused if f.ts+cycle<=cutoff]
            visible_records = [r for r in records if r.ts<cutoff]
            scores = self._model.score_series(fused)
            causes = CauseEngine(FixedSnapshot(snapshot)).evaluate_series(scores,fused,
                contexts=scenario_contexts(sc,[s.ts for s in scores]))
            apply_levels(scores,[c.immediate_danger for c in causes],cfg=snapshot.to_dict()['thresholds'])
            context = {**settings.model_dump(mode='json'), 'stock':[s.model_dump(mode='json') for s in settings.stock]}
            rec, reason = None, 'no_current_cause'
            if scores:
                last, f, cause = scores[-1], fused[-1], causes[-1]
                warm = last.baseline_status=='warming_up'
                state = 'warming_up' if warm else 'insufficient_data' if last.score is None else 'ready'
                current = [r for r in visible_records if f.ts<=r.ts<f.ts+cycle]
                status = {'cage_id':settings.cage_id,'ts':cutoff.isoformat(),'stress_score':None if warm else last.score,
                    'level':None if warm else last.level,'baseline_status':last.baseline_status,'data_status':state,
                    'evidence':{**last.evidence,**f.env},'cause':cause.status_causes() if state=='ready' else [],
                    'camera_weight':f.camera_weight,'missing_sources':f.missing_sources,
                    'metric_reliability':reliability(f,current,th['analysis']),
                    'baseline_windows':last.baseline_windows,'model_version':last.model_version,
                    'last_observation_at':max((r.ts for r in visible_records),default=None)}
                primary = cause.estimates[0] if state=='ready' and cause.estimates else None
                rec, reason = recommendation_for(context,primary,{'정상':'normal','주의':'caution','경고':'warning','위험':'danger'}.get(last.level,'normal'),snapshot)
            else:
                status = {'cage_id':settings.cage_id,'ts':sc.start.isoformat(),'stress_score':None,'level':None,
                    'baseline_status':self._model.status,
                    'data_status':'warming_up' if self._model.status=='warming_up' else 'insufficient_data','evidence':{},'cause':[],
                    'camera_weight':0,'missing_sources':['sonar','camera','env'], 'model_version':self._model.model_version,
                    'baseline_windows':self._model.n_windows,
                    'metric_reliability':{m.value:{'grade':'unavailable','confidence':None,'sources':[]} for m in MetricName}}
            if rec is not None:
                rec = {**rec,'id':0,'cage_id':settings.cage_id,'ts':cutoff.isoformat(),
                       'config_revision':snapshot.revision,'selectable':False}
            r7_evidence = next((c.evidence for c in causes[-1].estimates if c.rule_id=='R7'),{}) if causes else {}
            status.update(r7_held_due_to_missing=r7_evidence.get('feeding_held_due_to_missing',False),
                          feeding_last_observed_at=r7_evidence.get('feeding_last_observed_at'))
            status['config_revision'] = snapshot.revision
            # Hourly aggregation uses the visible prefix only; no partial hour can read future observations.
            series = hourly_timeseries(fused,scores)
            result = {'mock':False,'simulated':True,'persisted':False,'scenario':name,'t':t,'t_unit':'hours',
                'effective_t':count*th['cycle_minutes']/60,'duration_h':duration,'status':status,
                'recommendation':rec,'recommendation_reason':reason,'timeseries':series,
                'config_revision':snapshot.revision,'scenario_revision':revision,'producer_id':sc.cage_id,
                'cause_source':'cause_engine','stock_source':'synthetic_demo_config'}
            ensure_safe({'status':status,'recommendation':rec,'timeseries':series},snapshot)
            self._remember(self._responses,key,deepcopy(result),32)
            return result
