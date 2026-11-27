"""python -m pipeline.analyze_db --cage-id 3 --producer-id cage-003 --species 조피볼락 --model models/cage-003.pkl"""

import argparse
from datetime import datetime,timedelta,timezone
import json

import httpx

from common.config_loader import ConfigLoader
from fusion.fuse import _bucket
from stress.model import StressModel
from .analysis import build_bundle,parse_time
from .analysis_store import AnalysisStore


def run(store,model,cage_id,producer_id,species,start,end,loader,advisory_active=None):
    snapshot = loader.snapshot()
    store.authenticate(cage_id)
    data = store.fetch(cage_id,start,end)
    if data['input']['cage_id']!=cage_id:
        raise ValueError('Analysis input cage mismatch')
    bundle = build_bundle(data['input'],model,producer_id,species,snapshot,advisory_active=advisory_active)
    if loader.snapshot().revision!=snapshot.revision:
        raise ValueError('Config changed during analysis; recompute before publication')
    return store.publish(cage_id,start,end,data,bundle)


def main(argv=None):
    parser = argparse.ArgumentParser(description='DB 지표를 분석해 상태·원인·추천을 원자적으로 저장')
    parser.add_argument('--cage-id',required=True,type=int)
    parser.add_argument('--producer-id',required=True)
    parser.add_argument('--species',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--hours',type=int,default=96)
    parser.add_argument('--to')
    parser.add_argument('--advisory-state',choices=['unknown','clear','active'],default='unknown')
    args = parser.parse_args(argv)
    if args.cage_id<=0 or not 1<=args.hours<=168:
        parser.error('가두리 ID와 1~168시간 범위를 확인하세요.')
    loader = ConfigLoader()
    end = parse_time(args.to) if args.to else _bucket(datetime.now(timezone.utc),int(loader.snapshot()['thresholds']['cycle_minutes']))
    start = end-timedelta(hours=args.hours)
    model = StressModel.load(args.model, snapshot=loader.snapshot())
    with httpx.Client(timeout=60,follow_redirects=False) as client:
        receipt = run(AnalysisStore.from_env(client),model,args.cage_id,args.producer_id,args.species,start,end,loader,
                      {'unknown':None,'clear':False,'active':True}[args.advisory_state])
    print(json.dumps(receipt,ensure_ascii=False,allow_nan=False))


if __name__=='__main__':
    main()
