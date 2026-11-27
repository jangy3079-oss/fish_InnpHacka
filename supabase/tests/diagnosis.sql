-- Current server-owned observations, never accepted as a caller-supplied packet.
update public.cause_estimate set ts=date_trunc('minute',now())-interval '1 minute',cause='질병 의심',rule_id='R5',probability=1 where id=1;
insert into public.stress_score(cage_id,ts,score,level,baseline_status,model_version)
select 3,ts,65,'경고','ready','test-v1' from public.cause_estimate where id=1;
insert into public.metric(cage_id,ts,source,metric,value,confidence)
select 3,ts,'sonar','activity','0.4',0.9 from public.cause_estimate where id=1;
select pg_temp.check_ok((select relrowsecurity from pg_class where oid='private.diagnosis_request'::regclass),'private request RLS enabled');
select pg_temp.check_ok(not has_table_privilege('authenticated','private.diagnosis_request','INSERT'),'no direct request insert grant');

set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
do $$
declare v jsonb; r jsonb; again jsonb;
begin
  v=public.diagnosis_context(3,'11111111-1111-4111-8111-111111111111',72,1000,60);
  perform pg_temp.check_ok(v->>'persisted'='false','read context does not persist');
  perform pg_temp.check_ok(v->'context'->'causes'->0->>'rule_id'='R5','context uses actual cause row');
  perform pg_temp.check_ok(jsonb_array_length(v->'context'->'metrics')>0,'context contains observed graph points');
  perform pg_temp.check_ok(jsonb_array_length(v->'context'->'stress')>0,'context contains observed stress');
  r=public.create_diagnosis_request(3,'11111111-1111-4111-8111-111111111111',v->>'context_version',1,repeat('a',64),72,1000,60);
  perform pg_temp.check_ok(r->>'persisted'='true' and r->>'status'='pending','request stored pending, not sent');
  again=public.create_diagnosis_request(3,'11111111-1111-4111-8111-111111111111','outdated',1,repeat('b',64),72,1000,60);
  perform pg_temp.check_ok(r=again,'same request key returns identical stored receipt');
end;
$$;
select pg_temp.expect_error($q$select public.create_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','outdated',1,repeat('a',64),72,1000,60)$q$,'40001','stale snapshot cannot be submitted');
select pg_temp.expect_error($q$select public.diagnosis_context(3,'22222222-2222-4222-8222-222222222222',1000,1000,60)$q$,'22023','lookback resource bound');
select pg_temp.expect_error($q$select public.diagnosis_context(3,'22222222-2222-4222-8222-222222222222',72,1001,60)$q$,'22023','point resource bound');
select pg_temp.expect_error($q$select public.diagnosis_context(3,null,72,1000,60)$q$,'22023','idempotency key required');
select pg_temp.expect_error($q$select * from private.diagnosis_request$q$,'42501','direct request read blocked');
select pg_temp.expect_error($q$delete from private.diagnosis_request$q$,'42501','direct request delete blocked');
select pg_temp.expect_error($q$select public.diagnosis_context(4,'11111111-1111-4111-8111-111111111111',72,1000,60)$q$,'42501','owner cannot read other farm');

set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000002","user_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.expect_error($q$select public.diagnosis_context(3,'11111111-1111-4111-8111-111111111111',72,1000,60)$q$,'42501','metadata spoof cannot read request');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.expect_error($q$select public.diagnosis_context(3,'11111111-1111-4111-8111-111111111111',72,1000,60)$q$,'42501','read-only official cannot create clinical request');
reset role;
select pg_temp.check_ok((select count(*)=1 from private.diagnosis_request),'failures leave no extra request');

-- A retry returns the immutable snapshot even if the current cause has changed.
update public.cause_estimate set cause='고수온',rule_id='R1' where id=1;
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(public.diagnosis_context(3,'11111111-1111-4111-8111-111111111111',72,1000,60)->'context'->'causes'->0->>'rule_id'='R5','retry preserves original evidence');
do $$
declare v jsonb;
begin
  v=public.diagnosis_context(3,'33333333-3333-4333-8333-333333333333',72,1000,60);
  perform pg_temp.expect_error(format('select public.create_diagnosis_request(3,%L,%L,1,%L,72,1000,60)',
    '33333333-3333-4333-8333-333333333333',v->>'context_version',repeat('a',64)),'40001','non-disease cause cannot be committed');
end;
$$;
reset role;
update public.cause_estimate set ts=now()-interval '2 days' where id=1;
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.expect_error($q$select public.diagnosis_context(3,'44444444-4444-4444-8444-444444444444',72,1000,60)$q$,'40001','stale cause cannot create a fresh request');
set request.jwt.claims='{}';
select pg_temp.expect_error($q$select public.diagnosis_context(3,'11111111-1111-4111-8111-111111111111',72,1000,60)$q$,'42501','no identity fails closed');
reset role;
set role anon;
select pg_temp.expect_error($q$select public.diagnosis_context(3,'11111111-1111-4111-8111-111111111111',72,1000,60)$q$,'42501','anonymous RPC blocked');
reset role;
