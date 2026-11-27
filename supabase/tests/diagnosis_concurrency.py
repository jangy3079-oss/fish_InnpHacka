"""Fresh disposable PostgreSQL Docker container only; never a production DB.

docker run --name fish203-pg -e POSTGRES_HOST_AUTH_METHOD=trust -d postgres:17-alpine
python supabase/tests/diagnosis_concurrency.py --container fish203-pg
docker rm -f fish203-pg
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',required=True)
    container=parser.parse_args().container
    subprocess.run([sys.executable,str(Path(__file__).with_name('ingest_concurrency.py')),
                    '--container',container],check=True)
    command=['docker','exec','-i',container,'psql','-U','postgres','-qAt','-v','ON_ERROR_STOP=1']
    def sql(statement):
        result=subprocess.run(command,input=statement,text=True,capture_output=True,timeout=30)
        assert result.returncode==0,result.stderr
        return result.stdout.strip()
    auth="set role authenticated; set request.jwt.claims='"+json.dumps({'sub':'00000000-0000-4000-8000-000000000001'})+"';\n"
    keys=['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']
    for variant in ('same_key','same_event_new_key','different_event_cooldown'):
        sql("delete from private.recommendation_choice; delete from private.analysis_run; delete from private.diagnosis_request; "
            "update public.cause_estimate set ts=clock_timestamp()-interval '2 days'; "
            "insert into public.cause_estimate(cage_id,ts,cause,rule_id,probability,evidence) "
            "select 3,t,'질병 의심',r,0.5,'{}' from (select clock_timestamp()-interval '5 seconds' t) x cross join (values('R5'),('R8')) v(r);")
        sql("insert into private.analysis_run(cage_id,ts,actor_id,request_key,input_version,stock_version,payload_hash,config_revision,valid_until,status) "
            f"select 3,max(ts),'00000000-0000-4000-8000-000000000001','{uuid4()}',"
            "'input','stock','payload',repeat('a',64),now()+interval '1 hour','{\"data_status\":\"ready\",\"cause\":[{\"rule_id\":\"R5\"},{\"rule_id\":\"R8\"}]}' "
            "from public.cause_estimate where cage_id=3;")
        data=json.loads(sql(auth+f"select public.diagnosis_context(3,'{keys[0]}',72,1000,60);"))
        causes=data['context']['causes']
        def call(key,cause_id):
            return f"select public.create_diagnosis_request(3,'{key}','{data['context_version']}',{cause_id},repeat('a',64),72,1000,60,60);\n"
        a=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        b=None
        try:
            a.stdin.write('begin;\n'+auth+call(keys[0],causes[0]['id'])); a.stdin.flush()
            initial=json.loads(a.stdout.readline())
            b=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            key=keys[0] if variant=='same_key' else keys[1]
            selected=causes[-1]['id'] if variant=='different_event_cooldown' else causes[0]['id']
            b.stdin.write("set application_name='diagnosis-203-waiter';\n"+auth+call(key,selected)); b.stdin.flush()
            deadline=time.monotonic()+10
            while sql("select count(*) from pg_stat_activity where application_name='diagnosis-203-waiter' and wait_event_type='Lock';")=='0':
                assert b.poll() is None,'waiter finished before first commit'
                if time.monotonic()>deadline:
                    raise AssertionError('waiter never waited for cage lock')
                time.sleep(0.05)
            a.stdin.write('commit;\n'); a.stdin.flush(); a.stdin.close(); a.stdin=None
            _,error=a.communicate(timeout=15)
            assert a.returncode==0,error
            output,error=b.communicate(timeout=15)
            if variant=='same_key':
                assert b.returncode==0,error
                assert json.loads(output)==initial
            else:
                assert b.returncode!=0 and 'Diagnosis already requested or interval not elapsed' in error,error
            assert sql('select count(*) from private.diagnosis_request;')=='1'
        finally:
            for process in (a,b):
                if process is not None and process.poll() is None:
                    process.kill(); process.communicate(timeout=15)
    print('PASS: 3 lock-observed diagnosis races: same UUID replay, new UUID same event, different-event cooldown',flush=True)


if __name__=='__main__':
    main()
