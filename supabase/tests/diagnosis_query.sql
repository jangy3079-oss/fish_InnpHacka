set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(public.read_diagnosis_request(3)->>'request_key'='11111111-1111-4111-8111-111111111111','owner reads latest receipt without current cause');
select pg_temp.check_ok(public.read_diagnosis_request(3,'11111111-1111-4111-8111-111111111111')=public.read_diagnosis_request(3),'key lookup matches latest');
select pg_temp.check_ok(not(public.read_diagnosis_request(3) ?| array['context','packet','creator_id','graphs']),'summary excludes private packet');
select pg_temp.check_ok(public.read_diagnosis_request(3,'99999999-9999-4999-8999-999999999999') is null,'unknown key has no side effects');
select pg_temp.expect_error($q$select public.read_diagnosis_request(4)$q$,'42501','other farm receipt denied');
select pg_temp.expect_error($q$select public.create_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','digest',1,repeat('a',64),72,1000,60,60)$q$,'40001','new UUID cannot duplicate event');
select pg_temp.expect_error($q$select public.create_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','digest',9999,repeat('a',64),72,1000,60,60)$q$,'40001','new event within interval blocked');
select pg_temp.expect_error($q$select public.create_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','digest',9999,repeat('a',64),72,1000,60,0)$q$,'22023','interval bounds enforced');
select pg_temp.expect_error($q$select public.create_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','digest',1,repeat('a',64),72,1000,60)$q$,'42501','legacy write signature cannot bypass guard');
select pg_temp.expect_error($q$select private.commit_diagnosis_request(3,'22222222-2222-4222-8222-222222222222','digest',1,repeat('a',64),72,1000,60)$q$,'42501','unguarded helper cannot bypass guard');
select pg_temp.check_ok(public.create_diagnosis_request(3,'11111111-1111-4111-8111-111111111111','stale',1,repeat('b',64),72,1000,60,60)->>'id'=public.read_diagnosis_request(3)->>'id','same UUID retry bypasses quota and stale current cause');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.expect_error($q$select public.read_diagnosis_request(3)$q$,'42501','official cannot read diagnosis summary');
set request.jwt.claims='{}';
select pg_temp.expect_error($q$select public.read_diagnosis_request(3)$q$,'42501','missing identity denied');
reset role;
set role anon;
select pg_temp.expect_error($q$select public.read_diagnosis_request(3)$q$,'42501','anonymous receipt denied');
reset role;
select pg_temp.check_ok((select count(*)=1 from private.diagnosis_request),'read/retry/failures leave exactly original request');

-- Roll back boundary fixtures, preserving historical tests' observations.
select setval('public.cause_estimate_id_seq',(select max(id) from public.cause_estimate),true);
commit;
begin;
update private.diagnosis_request set created_at=now()-interval '60 minutes';
insert into public.cause_estimate(cage_id,ts,cause,probability,rule_id,evidence)
  values(3,date_trunc('minute',now())-interval '1 minute','질병 의심',1,'R5','{}');
set local role authenticated;
set local request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
do $$
declare v jsonb; r jsonb; cause_id bigint;
begin
  v=public.diagnosis_context(3,'77777777-7777-4777-8777-777777777777',72,1000,60);
  cause_id=(v->'context'->'causes'->0->>'id')::bigint;
  perform pg_temp.expect_error(format('select public.create_diagnosis_request(3,%L,%L,%s,%L,72,1000,60,60)',
    '77777777-7777-4777-8777-777777777777','stale',cause_id,repeat('a',64)),'40001','stale snapshot rejected after quota allows');
  r=public.create_diagnosis_request(3,'77777777-7777-4777-8777-777777777777',v->>'context_version',cause_id,repeat('a',64),72,1000,60,60);
  perform pg_temp.check_ok(r->>'persisted'='true','exact interval boundary accepts different event');
  perform pg_temp.check_ok(public.read_diagnosis_request(3)->>'id'=r->>'id','latest summary is deterministic');
end;
$$;
reset role;
update private.diagnosis_request set created_at=now()-interval '2 hours';
set local role authenticated;
do $$
declare cause_id bigint;
begin
  select (public.read_diagnosis_request(3,'77777777-7777-4777-8777-777777777777')->>'primary_cause_id')::bigint into cause_id;
  perform pg_temp.expect_error(format('select public.create_diagnosis_request(3,%L,%L,%s,%L,72,1000,60,60)',
    '88888888-8888-4888-8888-888888888888','stale',cause_id,repeat('a',64)),'40001','same event stays deduplicated beyond cooldown');
end;
$$;
rollback;
select pg_temp.check_ok((select count(*)=1 from private.diagnosis_request),'boundary and new-event fixtures rolled back');
