-- Uses the role/data fixtures and assertion helpers in schema_rls.sql.
insert into public.metric values
 (3,'2026-12-01T00:00:00Z','sonar','activity','0.6',0.9),
 (3,'2026-12-01T00:00:00Z','camera','activity','0.1',0.8),
 (3,'2026-12-01T01:00:00Z','camera','activity','0.2',0.7),
 (3,'2026-12-01T02:00:00Z','sonar','activity','0.9',0.9),
 (4,'2026-12-01T00:00:00Z','sonar','activity','9',0.9);

set role authenticated;
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(
  public.replace_cage_stock(3,'[{"size_group":"fry","count":0,"avg_weight_kg":0.01},{"size_group":"market","count":20,"avg_weight_kg":0.6}]',true)->>'persisted' = 'true',
  'stock RPC confirms persistence');
select pg_temp.check_ok((select count(*)=2 from public.stock where cage_id=3), 'replacement has exactly supplied groups');
select pg_temp.check_ok(not exists(select 1 from public.stock where cage_id=3 and size_group='mid'), 'omitted stock group removed');
select pg_temp.check_ok((select count=0 and insured from public.stock where cage_id=3 and size_group='fry'), 'zero stock and insurance preserved');
select public.replace_cage_stock(3,'[{"size_group":"market","count":20,"avg_weight_kg":0.6}]',false);
select public.replace_cage_stock(3,'[{"size_group":"market","count":20,"avg_weight_kg":0.6}]',false);
select pg_temp.check_ok((select count(*)=1 and bool_and(not insured) from public.stock where cage_id=3), 'repeat PUT is semantically idempotent');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[{"size_group":"fry","count":10,"avg_weight_kg":0.1},{"size_group":"mid","count":-1,"avg_weight_kg":0.2}]',true)$q$,
  '22023','invalid second row rolls back full replacement');
select pg_temp.check_ok((select count(*)=1 and min(size_group)='market' and min(count)=20 from public.stock where cage_id=3), 'failed stock PUT leaves old state intact');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[]',true)$q$,'22023','empty replacement rejected');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[{"size_group":"fry","count":1.5,"avg_weight_kg":0.1}]',true)$q$,'22023','fractional count rejected at RPC boundary');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[{"size_group":"fry","count":1,"avg_weight_kg":0.1,"cage_id":4}]',true)$q$,'22023','RPC cannot override cage with extra item fields');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[{"size_group":"fry","count":1,"avg_weight_kg":0.1},{"size_group":"fry","count":2,"avg_weight_kg":0.1}]',true)$q$,'22023','duplicate replacement groups rejected');
select pg_temp.expect_error($q$select public.replace_cage_stock(4,'[{"size_group":"fry","count":1,"avg_weight_kg":0.1}]',true)$q$,'42501','RPC cannot modify another farm');

select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02')->'points')=3,
  'timeseries deduplicates sensor timestamps');
select pg_temp.check_ok(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02')->'points'->0->>'source'='sonar',
  'sonar preferred over camera for activity');
select pg_temp.check_ok(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02')->'points'->1->>'source'='camera',
  'camera fallback retains provenance');
select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02','camera')->'points')=2,
  'explicit source filter');
select pg_temp.check_ok((public.cage_metric_series(3,'activity','2026-12-01','2026-12-02',null,2)->>'has_more')::boolean,
  'pagination indicates older observations');
select pg_temp.check_ok(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02',null,2)->'points'->0->>'value'='0.2',
  'newest page is returned chronologically');
select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02',null,2,'2026-12-01T01:00:00Z')->'points')=1,
  'exclusive before cursor has no overlap');
select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity','2026-12-01T01:00:00Z','2026-12-01T01:00:00Z')->'points')=1,
  'from and to include boundary timestamp');
select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity','2027-01-01','2027-01-02')->'points')=0,
  'empty series returns empty array');
select pg_temp.expect_error($q$select public.cage_metric_series(3,'activity',null,null,null,1001)$q$,'22023','RPC enforces page bound');
select pg_temp.expect_error($q$select public.cage_metric_series(3,'unknown')$q$,'22023','RPC rejects unknown metric');
select pg_temp.expect_error($q$select public.cage_metric_series(3,'activity','2026-12-02','2026-12-01')$q$,'22023','RPC validates time window');

set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000003"}';
select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity')->'points')=0, 'series RPC respects RLS for strangers');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.check_ok(jsonb_array_length(public.cage_metric_series(3,'activity','2026-12-01','2026-12-02')->'points')=3, 'assigned official may read metrics');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[{"size_group":"market","count":1,"avg_weight_kg":0.5}]',true)$q$,
  '42501','official cannot replace owner stock');
reset role;
set role anon;
select pg_temp.expect_error($q$select public.cage_metric_series(3,'activity')$q$,'42501','anonymous cannot invoke series RPC');
select pg_temp.expect_error($q$select public.replace_cage_stock(3,'[]',true)$q$,'42501','anonymous cannot invoke stock RPC');
reset role;
select pg_temp.check_ok((select count=200 from public.stock where cage_id=4 and size_group='mid'), 'other farm stock remained intact');
