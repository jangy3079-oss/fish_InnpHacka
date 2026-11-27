import copy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml
from pydantic import ValidationError

from api.analysis import read_recommendation
from api.schemas import Recommendation
from common.config_loader import ConfigLoader,ConfigError,DEFAULT_DIRECTORY
from decision.deadlines import with_deadlines

T=datetime(2026,11,27,13,0,tzinfo=timezone.utc)


def recommendation():
    return {'id':1,'cage_id':3,'ts':T.isoformat(),'recommended_option':'harvest','options':[
        {'id':key,'label':key,'available':True,'estimated_loss_krw':0,'reason':None} for key in ('keep','release','harvest')]}


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        shutil.copytree(DEFAULT_DIRECTORY,self.root,dirs_exist_ok=True)
        self.loader=ConfigLoader(self.root)

    def config(self,name,mutate):
        path=self.root/(name+'.yaml')
        data=yaml.safe_load(path.read_text(encoding='utf-8'))
        mutate(data)
        path.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')

    def test_unknown_action_deadlines_are_not_invented(self):
        snapshot=self.loader.snapshot()
        r=with_deadlines(recommendation(),T,'warning',snapshot)
        parsed=Recommendation.model_validate(r)
        self.assertEqual(parsed.deadline,T+timedelta(minutes=15))
        self.assertTrue(all(e.at is None and e.source_status=='pending' for e in parsed.deadlines if e.action_id))
        self.assertEqual(r,with_deadlines(recommendation(),T,'warning',self.loader.snapshot()))

    def test_relative_and_absolute_policies_normalize_offsets(self):
        self.config('release_rules',lambda d:d.update(application_deadline={
            'source_status':'assumed','hours_from_assessment':0.1,'at':None,'source':'운영 가정'}))
        self.config('costs',lambda d:d.update(harvest_deadline={
            'source_status':'confirmed','hours_from_assessment':None,'at':'2026-11-27T22:03:00+09:00','source':'확인된 거래처 일정'}))
        r=with_deadlines(recommendation(),T,'warning',self.loader.snapshot())
        parsed=Recommendation.model_validate(r)
        self.assertEqual(parsed.deadline,T+timedelta(minutes=3))
        release=next(e for e in parsed.deadlines if e.action_id=='release')
        self.assertEqual(release.at,T+timedelta(minutes=6))
        self.assertEqual(release.source_status,'assumed')
        self.assertEqual(next(e for e in parsed.deadlines if e.action_id=='harvest').basis,'absolute')

    def test_unavailable_option_deadline_does_not_replace_next_assessment(self):
        self.config('release_rules',lambda d:d.update(application_deadline={
            'source_status':'assumed','hours_from_assessment':0.01,'at':None,'source':'운영 가정'}))
        rec=recommendation(); rec['options'][1]['available']=False
        r=with_deadlines(rec,T,'normal',self.loader.snapshot())
        self.assertEqual(Recommendation.model_validate(r).deadline,T+timedelta(hours=1))
        self.assertFalse(next(e['applicable'] for e in r['deadlines'] if e['action_id']=='release'))

    def test_elapsed_absolute_deadline_is_retained_instead_of_rolling_forward(self):
        self.config('costs',lambda d:d.update(harvest_deadline={
            'source_status':'assumed','hours_from_assessment':None,'at':(T-timedelta(hours=1)).isoformat(),'source':None}))
        r=with_deadlines(recommendation(),T,'danger',self.loader.snapshot())
        self.assertEqual(Recommendation.model_validate(r).deadline,T-timedelta(hours=1))
        self.assertTrue(r['deadline_expired'])

    def test_first_aid_contains_only_next_assessment_and_none_stays_none(self):
        snapshot=self.loader.snapshot()
        rec=recommendation(); rec['options']=[]; rec['recommended_option']=None
        r=with_deadlines(rec,T,'danger',snapshot)
        self.assertEqual(len(r['deadlines']),1)
        self.assertEqual(r['deadline'],(T+timedelta(minutes=5)).isoformat())
        self.assertIsNone(with_deadlines(None,T,'danger',snapshot))
        with self.assertRaises(ValueError): with_deadlines(rec,datetime(2026,1,1),'danger',snapshot)

    def test_policy_invalid_types_and_missing_confirmation_fail_closed(self):
        policies=[{'source_status':'confirmed','hours_from_assessment':1,'at':None,'source':None},
            {'source_status':'assumed','hours_from_assessment':0,'at':None,'source':None},
            {'source_status':'assumed','hours_from_assessment':True,'at':None,'source':None},
            {'source_status':'assumed','hours_from_assessment':1,'at':'2026-01-01T00:00:00Z','source':None},
            {'source_status':'assumed','hours_from_assessment':None,'at':'2026-01-01T00:00:00','source':None},
            {'source_status':'pending','hours_from_assessment':1,'at':None,'source':None}]
        for policy in policies:
            with self.subTest(policy=policy):
                self.config('release_rules',lambda d:d.update(application_deadline=policy))
                with self.assertRaises(ConfigError): self.loader.snapshot()

    def test_all_four_reassessment_levels_required(self):
        self.config('costs',lambda d:d['reassessment_minutes_by_level'].pop('normal'))
        with self.assertRaises(ConfigError): self.loader.snapshot()

    def test_response_rejects_mismatched_primary_pending_duplicate_and_action(self):
        original=with_deadlines(recommendation(),T,'normal',self.loader.snapshot())
        for mutate in (lambda r:r.update(deadline=T.isoformat()),lambda r:r['deadlines'].append(r['deadlines'][0]),
                       lambda r:r['deadlines'][1].update(at=T.isoformat()),lambda r:r['deadlines'][1].update(action_id='harvest')):
            r=copy.deepcopy(original); mutate(r)
            with self.assertRaises(ValidationError): Recommendation.model_validate(r)

    def test_published_snapshot_read_does_not_recalculate_deadline(self):
        snapshot=self.loader.snapshot()
        payload=with_deadlines(recommendation(),T,'danger',snapshot)
        payload.update(analysis_id=1,config_revision=snapshot.revision,valid_until=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        class Store:
            def require_cage(self,cage): pass
            def request(self,*args,**kw): return copy.deepcopy(payload)
        old=read_recommendation(Store(),3,self.loader)
        self.config('costs',lambda d:d['reassessment_minutes_by_level'].update(danger=30))
        new=read_recommendation(Store(),3,self.loader)
        self.assertEqual(old.deadline,new.deadline)
        self.assertTrue(new.config_changed)
        self.assertFalse(new.selectable)
        self.assertEqual(Recommendation.model_validate(recommendation()).deadlines,[])


if __name__=='__main__': unittest.main()
