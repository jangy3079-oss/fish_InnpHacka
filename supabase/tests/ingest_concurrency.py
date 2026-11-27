"""Real PostgreSQL sessions in a fresh, disposable Docker container (no exposed port).

docker run --name fish198-pg -e POSTGRES_HOST_AUTH_METHOD=trust -d postgres:17-alpine
python supabase/tests/ingest_concurrency.py --container fish198-pg
docker rm -f fish198-pg

Requires a NEW database: applies bootstrap, migrations, seed, SQL contract tests.
Never point this at a production database/container.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

CLAIMS = json.dumps({'sub':'00000000-0000-4000-8000-000000000004',
    'app_metadata':{'role':'edge','ingest_cage_ids':[3]}})
AUTH = f"set role authenticated; set request.jwt.claims='{CLAIMS}';\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    container = parser.parse_args().container
    command = ['docker','exec','-i',container,'psql','-U','postgres','-qAt','-v','ON_ERROR_STOP=1']

    def sql(text):
        result = subprocess.run(command,input=text,text=True,capture_output=True,timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    root = Path(__file__).parent
    files = [root/'bootstrap.sql', *sorted((root.parent/'migrations').glob('*.sql')),
             root.parent/'seed.sql',root.parent/'seed.sql']
    files += [root/name for name in ('schema_rls.sql','farmer_data.sql','ingestion_rls.sql',
              'idempotent_ingestion.sql','diagnosis.sql','diagnosis_query.sql','analysis.sql','areas.sql','work_log.sql','deadlines.sql')]
    output = sql('\n'.join(file.read_text(encoding='utf-8') for file in files)+'\nselect count(*) from test_results;')
    print('PostgreSQL SQL/RLS assertions:',output.splitlines()[-1],flush=True)

    def row(day, value=6):
        return {'cage_id':3,'ts':f'2027-02-{day:02}T00:00:00Z','source':'env','metric':'do','value':value,'confidence':1}

    def call(rows):
        return "select public.ingest_metrics('"+json.dumps(rows)+"');\n"

    def race(first, second, *, rollback=False, conflict=False):
        a = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        b = None
        try:
            a.stdin.write('begin;\n'+AUTH+call(first)); a.stdin.flush()
            initial = a.stdout.readline().strip()
            assert initial and json.loads(initial)['inserted_count']==len(first), initial
            b = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            b.stdin.write("set application_name='ingest-198-waiter';\n"+AUTH+call(second)); b.stdin.flush()
            deadline = time.monotonic()+10
            while sql("select count(*) from pg_stat_activity where application_name='ingest-198-waiter' and wait_event_type='Lock';")=='0':
                assert b.poll() is None, 'second session unexpectedly finished before lock release'
                if time.monotonic()>deadline:
                    raise AssertionError('second session never waited for the unique-key lock')
                time.sleep(0.05)
            a.stdin.write('rollback;\n' if rollback else 'commit;\n'); a.stdin.flush()
            a.stdin.close(); a.stdin=None
            _, err_a = a.communicate(timeout=15)
            assert a.returncode==0,err_a
            out_b,err_b = b.communicate(timeout=15)
            if conflict:
                assert b.returncode!=0 and 'Metric content conflict' in err_b,err_b
            else:
                assert b.returncode==0,err_b
                receipt=json.loads(out_b.strip())
                expected={'persisted':True,'received_count':len(second),
                    'inserted_count':len(second) if rollback else 0,'duplicate_count':0 if rollback else len(second)}
                assert receipt==expected,receipt
        finally:
            for process in (a,b):
                if process is not None and process.poll() is None:
                    process.kill(); process.communicate(timeout=15)

    race([row(1)],[row(1)])
    race([row(3)],[row(2),row(3,9)],conflict=True)
    assert sql("select count(*) from public.metric where ts='2027-02-02';")=='0'
    assert sql("select value from public.metric where ts='2027-02-03';")=='6'
    race([row(4),row(5)],[row(5),row(4)])
    race([row(6)],[row(6)],rollback=True)
    print('PASS: 4 lock-observed races (same payload, changed payload atomic rollback, reversed batch, first-writer rollback)',flush=True)


if __name__=='__main__':
    main()
