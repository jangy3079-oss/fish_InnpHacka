-- #45 작업 기록(R6)·적조 특보(R4) 입력. schema_rls.sql의 사용자·가두리 픽스처를 쓴다.
reset role;
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
do $$
declare r jsonb; again jsonb; t timestamptz = date_trunc('minute', now()) - interval '2 hours';
begin
  r = public.record_work_log(3, 'net_change', t, '30303030-3030-4030-8030-303030303030');
  perform pg_temp.check_ok(r->>'persisted'='true' and r->>'kind'='net_change' and (r->>'cage_id')::bigint=3, 'owner records work log');
  again = public.record_work_log(3, 'net_change', t, '30303030-3030-4030-8030-303030303030');
  perform pg_temp.check_ok(again->>'work_log_id' = r->>'work_log_id', 'same work log key replays receipt');
  perform pg_temp.expect_error(format('select public.record_work_log(3,%L,%L,%L)', 'sorting', t,
    '30303030-3030-4030-8030-303030303030'), '40001', 'same key with different work rejected');
  perform pg_temp.expect_error(format('select public.record_work_log(3,%L,%L,%L)', 'feeding', t,
    '31313131-3131-4131-8131-313131313131'), '22023', 'unknown work kind rejected');
  perform pg_temp.expect_error(format('select public.record_work_log(3,%L,%L,%L)', 'sorting', now() + interval '1 hour',
    '32323232-3232-4232-8232-323232323232'), '22023', 'future work time rejected');
  perform pg_temp.expect_error(format('select public.record_work_log(3,%L,%L,%L)', 'sorting', now() - interval '8 days',
    '33333333-3333-4333-8333-333333333333'), '22023', 'stale work time rejected');
  perform pg_temp.expect_error(format('select public.record_work_log(4,%L,%L,%L)', 'sorting', t,
    '34343434-3434-4434-8434-343434343434'), '42501', 'cannot log work on another farm cage');
end;
$$;
select pg_temp.expect_error($q$select count(*) from private.work_log$q$, '42501', 'farmer cannot read work log table directly');
select pg_temp.expect_error($q$select public.load_red_tide_advisories('[{"sea_area_id":1,"stage":"advisory","starts_at":"2026-08-01T00:00:00Z","source":"x","source_ref":"y"}]')$q$,
  '42501', 'farmer cannot load red tide advisories');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.record_work_log(3,'sorting',now(),'35353535-3535-4535-8535-353535353535')$q$,
  '42501', 'analysis worker cannot record farmer work');
reset role;

set role service_role;
select pg_temp.check_ok((public.load_red_tide_advisories(jsonb_build_array(
  jsonb_build_object('sea_area_id',1,'stage','advisory','starts_at',now()-interval '3 hours','source','국립수산과학원 적조속보','source_ref','test-1'),
  jsonb_build_object('sea_area_id',1,'stage','watch','starts_at',now()-interval '30 days','ends_at',now()-interval '20 days','source','국립수산과학원 적조속보','source_ref','test-old'),
  jsonb_build_object('sea_area_id',2,'stage','warning','starts_at',now()-interval '3 hours','source','국립수산과학원 적조속보','source_ref','test-2')
)))->>'received_count'='3', 'operator loads red tide advisories');
select public.load_red_tide_advisories(jsonb_build_array(jsonb_build_object('sea_area_id',1,'stage','advisory',
  'starts_at',(select starts_at from private.red_tide_advisory where source_ref='test-1'),'ends_at',now()-interval '1 hour',
  'source','국립수산과학원 적조속보','source_ref','test-1-lifted')));
select pg_temp.check_ok((select count(*)=1 and min(source_ref)='test-1-lifted' and bool_and(ends_at is not null)
  from private.red_tide_advisory where sea_area_id=1 and stage='advisory'), 'repeat advisory updates lift time only');
select pg_temp.expect_error($q$select public.load_red_tide_advisories('[{"sea_area_id":1,"stage":"advisory","starts_at":"2026-08-01T00:00:00Z","source":"x","source_ref":"y","cage_id":3}]')$q$,
  '22023', 'advisory rows reject extra fields');
select pg_temp.expect_error($q$select public.load_red_tide_advisories('[{"sea_area_id":1,"stage":"lifted","starts_at":"2026-08-01T00:00:00Z","source":"x","source_ref":"y"}]')$q$,
  '23514', 'advisory stage constrained');
select pg_temp.expect_error($q$select public.load_red_tide_advisories('[]')$q$, '22023', 'empty advisory load rejected');
reset role;

set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
do $$
declare v jsonb; t timestamptz = date_trunc('minute', now()) - interval '1 minute';
begin
  v = public.analysis_input(3, t - interval '1 day', t, 50000)->'input';
  perform pg_temp.check_ok(jsonb_array_length(v->'work_logs')=1 and v->'work_logs'->0->>'kind'='net_change',
    'analysis input carries cage work logs');
  perform pg_temp.check_ok(jsonb_array_length(v->'red_tide_advisories')=1
    and v->'red_tide_advisories'->0->>'source_ref'='test-1-lifted', 'analysis input carries only overlapping own-area advisories');
end;
$$;
reset role;
