-- #198 RPC does not grant edge SELECT/UPDATE, nor farmer ingestion.
set role anon;
select pg_temp.expect_error($q$select public.ingest_metrics('[]')$q$,'42501','anonymous RPC denied');
set role authenticated;
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.expect_error($q$select public.ingest_metrics('[]')$q$,'42501','farmer RPC denied');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000004","user_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.ingest_metrics('[]')$q$,'42501','forged user metadata RPC denied');
set request.jwt.claims = '{"app_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.ingest_metrics('[]')$q$,'42501','missing identity RPC denied');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.ingest_metrics('[]')$q$,'22023','empty RPC rejected');
select pg_temp.expect_error($q$select public.ingest_metrics('[{}]')$q$,'22023','missing RPC fields rejected');
select pg_temp.expect_error($q$select public.ingest_metrics('[{"cage_id":4,"ts":"2027-01-01T00:00:00Z","source":"env","metric":"do","value":6,"confidence":1}]')$q$,
  '42501','unassigned cage RPC denied before comparing values');
select pg_temp.check_ok(public.ingest_metrics('[{"cage_id":3,"ts":"2027-01-02T00:00:00Z","source":"env","metric":"do","value":6,"confidence":1}]')=
  '{"persisted":true,"received_count":1,"inserted_count":1,"duplicate_count":0}'::jsonb,'first RPC insert');
select pg_temp.check_ok(public.ingest_metrics('[{"cage_id":3,"ts":"2027-01-02T09:00:00+09:00","source":"env","metric":"do","value":6.0,"confidence":1.0}]')=
  '{"persisted":true,"received_count":1,"inserted_count":0,"duplicate_count":1}'::jsonb,'UTC numeric equivalent replay succeeds');
select pg_temp.check_ok(public.ingest_metrics('[{"cage_id":3,"ts":"2027-01-02T00:00:00Z","source":"env","metric":"do","value":6,"confidence":1},{"cage_id":3,"ts":"2027-01-03T00:00:00Z","source":"env","metric":"do","value":7,"confidence":0.8}]')=
  '{"persisted":true,"received_count":2,"inserted_count":1,"duplicate_count":1}'::jsonb,'mixed new and duplicate receipt');
select pg_temp.expect_error($q$select public.ingest_metrics('[{"cage_id":3,"ts":"2027-01-01T00:00:00Z","source":"env","metric":"do","value":8,"confidence":1},{"cage_id":3,"ts":"2027-01-02T00:00:00Z","source":"env","metric":"do","value":9,"confidence":1}]')$q$,
  '23505','content conflict rolls back earlier new row');
select pg_temp.expect_error($q$select public.ingest_metrics('[{"cage_id":3,"ts":"2027-01-02T00:00:00Z","source":"env","metric":"do","value":6,"confidence":0.9}]')$q$,
  '23505','confidence change conflicts');
select pg_temp.expect_error($q$select public.ingest_metrics('[{"cage_id":3,"ts":"2027-01-02T00:00:00Z","source":"env","metric":"do","value":6,"confidence":1},{"cage_id":3,"ts":"2027-01-02T09:00:00+09:00","source":"env","metric":"do","value":6,"confidence":1}]')$q$,
  '22023','same identity within batch rejected after UTC normalization');
select pg_temp.check_ok((select count(*)=0 from public.metric),'edge still cannot SELECT after RPC');
reset role;
select pg_temp.check_ok((select count(*)=0 from public.metric where ts='2027-01-01'),'conflicting batch leaves no partial row');
select pg_temp.check_ok((select value='6'::jsonb and confidence=1 from public.metric where cage_id=3 and ts='2027-01-02' and metric='do'),
  'existing observation unchanged');
