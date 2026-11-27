set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(public.read_diagnosis_request(3)->>'request_key'='11111111-1111-4111-8111-111111111111','owner reads stored receipt after cause changes');
select pg_temp.check_ok(public.read_diagnosis_request(3,'11111111-1111-4111-8111-111111111111')=public.read_diagnosis_request(3),'key lookup matches latest');
select pg_temp.check_ok(not(public.read_diagnosis_request(3) ?| array['context','packet','creator_id','graphs']),'summary excludes private packet');
select pg_temp.check_ok(public.read_diagnosis_request(3,'99999999-9999-4999-8999-999999999999') is null,'missing lookup has no side effects');
select pg_temp.expect_error($q$select public.read_diagnosis_request(4)$q$,'42501','other farm denied');
select pg_temp.expect_error($q$select private.commit_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','digest',1,repeat('a',64),72,1000,60)$q$,'42501','unguarded helper denied');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.expect_error($q$select public.read_diagnosis_request(3)$q$,'42501','official denied');
reset role;
set role anon;
select pg_temp.expect_error($q$select public.read_diagnosis_request(3)$q$,'42501','anonymous denied');
reset role;
select pg_temp.check_ok((select count(*)=1 from private.diagnosis_request),'reads leave exactly original request');
commit;

-- Independent current-analysis fixture; roll it back to preserve later suites.
begin;
select setval('public.cause_estimate_id_seq',(select max(id) from public.cause_estimate),true);
insert into public.cause_estimate(cage_id,ts,cause,probability,rule_id,evidence)
  values(3,date_trunc('minute',now()),'질병 의심',1,'R5','{}');
insert into public.stress_score(cage_id,ts,score,level,baseline_status,model_version)
  values(3,date_trunc('minute',now()),65,'경고','ready','test-v1');
insert into public.metric(cage_id,ts,source,metric,value,confidence)
  values(3,date_trunc('minute',now()),'sonar','activity','0.4',0.9);
insert into private.analysis_run(cage_id,ts,actor_id,request_key,input_version,stock_version,payload_hash,config_revision,valid_until,status)
  select 3,max(ts),'00000000-0000-4000-8000-000000000001','cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    'input','stock','payload',repeat('a',64),now()+interval '1 hour','{"data_status":"ready","cause":[]}'
  from public.cause_estimate where cage_id=3;
set local role authenticated;
set local request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.expect_error($q$select public.create_diagnosis_request(3,'77777777-7777-4777-8777-777777777777','stale',
  (select max(id) from public.cause_estimate where cage_id=3),repeat('a',64),72,1000,60)$q$,'40001','empty current cause rejects old R5');
select pg_temp.check_ok(public.create_diagnosis_request(3,'11111111-1111-4111-8111-111111111111','stale',1,repeat('b',64),72,1000,60)->>'persisted'='true','original UUID survives cause disappearance');
reset role;
update private.analysis_run set status='{"data_status":"ready","cause":[{"rule_id":"R5"}]}';
set local role authenticated;
do $$
declare v jsonb; r jsonb; cause_id bigint;
begin
  v=public.diagnosis_context(3,'77777777-7777-4777-8777-777777777777',72,1000,60);
  cause_id=(v->'context'->'causes'->0->>'id')::bigint;
  r=public.create_diagnosis_request(3,'77777777-7777-4777-8777-777777777777',v->>'context_version',cause_id,repeat('a',64),72,1000,60);
  perform pg_temp.check_ok(r->>'persisted'='true','new observation can be requested immediately');
  perform pg_temp.check_ok(public.create_diagnosis_request(3,'77777777-7777-4777-8777-777777777777','stale',cause_id,repeat('b',64),72,1000,60)=r,'same UUID is immutable');
  perform pg_temp.expect_error(format('select public.create_diagnosis_request(3,%L,%L,%s,%L,72,1000,60)',
    '88888888-8888-4888-8888-888888888888',v->>'context_version',cause_id,repeat('a',64)),'40001','new UUID cannot repeat observation');
end;
$$;
rollback;
select pg_temp.check_ok((select count(*)=1 from private.diagnosis_request),'request fixtures rolled back');
