"""One real concurrent duplicate-observation check. Fresh disposable Docker DB only.

docker run --name fish203-pg -e POSTGRES_HOST_AUTH_METHOD=trust -d postgres:17-alpine
python supabase/tests/diagnosis_concurrency.py --container fish203-pg
docker rm -f fish203-pg
"""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',required=True)
    container=parser.parse_args().container
    command=['docker','exec','-i',container,'psql','-U','postgres','-qAt','-v','ON_ERROR_STOP=1']
    def sql(statement):
        result=subprocess.run(command,input=statement,text=True,capture_output=True,timeout=30)
        assert result.returncode==0,result.stderr
        return result.stdout.strip()
    root=Path(__file__).parent
    files=[root/'bootstrap.sql',*sorted((root.parent/'migrations').glob('*.sql')),root.parent/'seed.sql']
    sql('\n'.join(p.read_text(encoding='utf-8') for p in files))
    sql("insert into public.cause_estimate(cage_id,ts,cause,rule_id,probability,evidence) "
        "select 3,t,'의심 원인',r,0.5,'{}' from (select now()-interval '5 seconds' t) x cross join (values('R5'),('R8')) v(r);")
    auth="set role authenticated; set request.jwt.claims='"+json.dumps({'sub':'00000000-0000-4000-8000-000000000001'})+"';\n"
    keys=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']
    data=json.loads(sql(auth+f"select public.diagnosis_context(3,'{keys[0]}',72,1000,60);"))
    def call(key,cause_id):
        return f"select public.create_diagnosis_request(3,'{key}','{data['context_version']}',{cause_id},repeat('a',64),72,1000,60);\n"
    a=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    b=None
    try:
        a.stdin.write('begin;\n'+auth+call(keys[0],data['context']['causes'][0]['id'])); a.stdin.flush()
        assert json.loads(a.stdout.readline())['persisted'] is True
        b=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        b.stdin.write("set application_name='diagnosis-waiter';\n"+auth+call(keys[1],data['context']['causes'][-1]['id'])); b.stdin.flush()
        deadline=time.monotonic()+10
        while sql("select count(*) from pg_stat_activity where application_name='diagnosis-waiter' and wait_event_type='Lock';")=='0':
            assert b.poll() is None and time.monotonic()<deadline,'concurrent caller did not wait for cage lock'
            time.sleep(0.05)
        a.stdin.write('commit;\n'); a.stdin.flush(); a.stdin.close(); a.stdin=None
        _,error=a.communicate(timeout=15); assert a.returncode==0,error
        _,error=b.communicate(timeout=15)
        assert b.returncode!=0 and 'Diagnosis already requested for this observation' in error,error
        assert sql('select count(*) from private.diagnosis_request;')=='1'
    finally:
        for process in (a,b):
            if process is not None and process.poll() is None:
                process.kill(); process.communicate(timeout=15)
    print('PASS: simultaneous R5/R8 requests with different UUIDs create one observation request')


if __name__=='__main__':
    main()
