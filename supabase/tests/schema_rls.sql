-- Executed on a fresh disposable PostgreSQL database after migration + seed.
create temp table test_results(name text);
grant insert on test_results to authenticated, anon, service_role;
create function pg_temp.check_ok(ok boolean, label text) returns void language plpgsql as $$
begin
  if ok is distinct from true then raise exception 'FAIL: %', label; end if;
  insert into test_results values(label);
end;
$$;
create function pg_temp.expect_error(statement text, expected text, label text) returns void language plpgsql as $$
declare actual text;
begin
  begin
    execute statement;
  exception when others then get stacked diagnostics actual = returned_sqlstate;
  end;
  if actual is distinct from expected then
    raise exception 'FAIL: % expected SQLSTATE %, got %', label, expected, coalesce(actual, 'success');
  end if;
  insert into test_results values(label);
end;
$$;

select pg_temp.check_ok((select count(*) = 10 from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r'), 'ten public tables');
select pg_temp.check_ok((select count(*) = 10 from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r' and c.relrowsecurity), 'RLS enabled on every table');
select pg_temp.check_ok(exists(select 1 from pg_indexes where schemaname='public' and tablename='metric'
  and indexname='metric_cage_ts_idx' and indexdef like '%(cage_id, ts)%'), 'time series index');
select pg_temp.check_ok((select count(*)=1 from public.sea_area) and (select count(*)=1 from public.farm)
  and (select count(*)=1 from public.cage where id=3 and is_sentinel), 'repeatable demo seed');
select pg_temp.check_ok((select count(*)=0 from pg_proc p join pg_namespace n on p.pronamespace=n.oid
  where n.nspname='public' and p.prosecdef), 'no exposed security definer RPC');
select pg_temp.check_ok((select bool_and(proconfig @> array['search_path=""']) from pg_proc p join pg_namespace n
  on p.pronamespace=n.oid where n.nspname='private' and p.prosecdef), 'definer search paths pinned');

insert into auth.users(id) values ('00000000-0000-4000-8000-000000000002'),
 ('00000000-0000-4000-8000-000000000003'), ('00000000-0000-4000-8000-000000000004');
insert into public.sea_area(id,name,geom) overriding system value
values (2,'Other area','{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}');
insert into public.farm(id,sea_area_id,owner_user_id,name,location) overriding system value values
 (2,2,'00000000-0000-4000-8000-000000000002','Other farm','{}'),
 (3,1,'00000000-0000-4000-8000-000000000002','Other farm in same area','{}');
insert into public.cage(id,farm_id,depth_m,net_depth_m) overriding system value values (4,2,20,15),(5,3,20,15);
insert into public.stock(cage_id,size_group,count,avg_weight_kg) values (3,'mid',100,0.2),(4,'mid',200,0.3),(5,'mid',300,0.4);
insert into public.sensor(cage_id,type,model) values (3,'sonar','test'),(4,'sonar','test');
insert into public.metric(cage_id,ts,source,metric,value,confidence) values
 (3,'2026-08-12T05:00:00Z','sonar','activity','0.5',0.9),
 (4,'2026-08-12T05:00:00Z','sonar','activity','0.6',0.9);
insert into public.stress_score(cage_id,ts,score,level,baseline_status,model_version) values
 (3,'2026-08-12T05:00:00Z',64,'경고','ready','test-v1'),
 (4,'2026-08-12T05:00:00Z',50,'주의','ready','test-v1');
insert into public.cause_estimate(id,cage_id,ts,cause,probability,rule_id,evidence) overriding system value values
 (1,3,now(),'heat',0.7,'R1','{}'),(2,4,now(),'heat',0.8,'R1','{}'),(3,5,now(),'heat',0.8,'R1','{}');
insert into public.recommendation(id,cause_estimate_id,options,deadline) overriding system value values
 (1,1,'[{"id":"oxygen","estimated_loss_krw":1000}]',now()+interval '1 day'),
 (2,2,'[{"id":"oxygen","estimated_loss_krw":2000}]',null),(3,3,'[]',null);
insert into public.alert(recommendation_id,user_id,channel,message) values
 (1,'00000000-0000-4000-8000-000000000001','mock','A'),
 (2,'00000000-0000-4000-8000-000000000002','mock','B');

-- Owners can see only their own farm, even in a shared sea area.
set role authenticated;
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000001","role":"authenticated"}';
select pg_temp.check_ok((select count(*)=1 from public.sea_area), 'owner area isolation');
select pg_temp.check_ok((select count(*)=1 from public.farm), 'owner farm isolation');
select pg_temp.check_ok((select count(*)=1 from public.cage), 'owner cage isolation');
select pg_temp.check_ok((select count(*)=1 from public.stock), 'owner stock isolation');
select pg_temp.check_ok((select count(*)=1 from public.sensor), 'owner sensor isolation');
select pg_temp.check_ok((select count(*)=1 from public.metric), 'owner metric isolation');
select pg_temp.check_ok((select count(*)=1 from public.stress_score), 'owner score isolation');
select pg_temp.check_ok((select count(*)=1 from public.cause_estimate), 'owner cause isolation');
select pg_temp.check_ok((select count(*)=1 from public.recommendation), 'owner recommendation isolation');
select pg_temp.check_ok((select count(*)=1 from public.alert), 'recipient alert isolation');
insert into public.stock(cage_id,size_group,count,avg_weight_kg,insured) values (3,'fry',500,0.01,true);
update public.stock set count=101 where cage_id=3 and size_group='mid';
select pg_temp.check_ok((select count=101 from public.stock where cage_id=3 and size_group='mid'), 'owner may update own stock');
select pg_temp.expect_error($q$insert into public.stock(cage_id,size_group,count,avg_weight_kg) values(4,'fry',1,0.1)$q$,'42501','cross-farm insert denied');
select pg_temp.expect_error($q$update public.stock set cage_id=4 where cage_id=3 and size_group='fry'$q$,'42501','cross-farm move denied by WITH CHECK');
select pg_temp.expect_error($q$update public.farm set owner_user_id='00000000-0000-4000-8000-000000000001' where id=2$q$,'42501','cannot take farm ownership');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'env','do','1',1)$q$,'42501','client cannot forge sensor metrics');
select pg_temp.expect_error($q$update public.recommendation set options='[]' where id=1$q$,'42501','client cannot forge recommendations');
select pg_temp.expect_error($q$update public.stock set updated_at=now()$q$,'42501','client cannot forge stock timestamps');
select pg_temp.expect_error($q$delete from public.stock where cage_id=3$q$,'42501','raw delete not granted');
with changed as (update public.stock set count=999 where cage_id=4 returning id)
select pg_temp.check_ok((select count(*)=0 from changed), 'other farm update affects zero rows');
reset role;

-- user_metadata must not confer official or financial access.
set role authenticated;
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000003","user_metadata":{"role":"official","area_ids":[1],"financial_farm_ids":[1]}}';
select pg_temp.check_ok((select count(*)=0 from public.stock) and (select count(*)=0 from public.farm), 'user metadata spoof ignored');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.check_ok((select count(*)=2 from public.farm), 'official sees assigned area farms only');
select pg_temp.check_ok((select count(*)=1 from public.metric), 'official metrics area scope');
select pg_temp.check_ok((select count(*)=0 from public.stock), 'area grant does not expose stock');
select pg_temp.check_ok((select count(*)=0 from public.recommendation), 'area grant does not expose financial recommendations');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1],"financial_farm_ids":[1,2]}}';
select pg_temp.check_ok((select count(*)=2 from public.stock), 'financial grant reads only explicit farm in assigned area');
select pg_temp.check_ok((select count(*)=1 from public.recommendation), 'financial grant recommendation scope');
select pg_temp.check_ok((select count(*)=0 from public.alert), 'official cannot read another recipient alert');
select pg_temp.expect_error($q$insert into public.stock(cage_id,size_group,count,avg_weight_kg) values(3,'market',1,0.5)$q$,'42501','financial official is read-only');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","financial_farm_ids":[1]}}';
select pg_temp.check_ok((select count(*)=0 from public.stock), 'financial farm grant still requires area grant');
set request.jwt.claims = '{}';
select pg_temp.check_ok((select count(*)=0 from public.stock), 'missing identity fails closed');
reset role;
set role anon;
select pg_temp.expect_error('select * from public.stock','42501','anonymous stock access denied');
select pg_temp.expect_error('select * from public.recommendation','42501','anonymous financial access denied');
select pg_temp.expect_error('select * from public.sea_area','42501','anonymous area access denied');
select pg_temp.expect_error('select * from public.farm','42501','anonymous farm access denied');
select pg_temp.expect_error('select * from public.cage','42501','anonymous cage access denied');
select pg_temp.expect_error('select * from public.sensor','42501','anonymous sensor access denied');
select pg_temp.expect_error('select * from public.metric','42501','anonymous metric access denied');
select pg_temp.expect_error('select * from public.stress_score','42501','anonymous score access denied');
select pg_temp.expect_error('select * from public.cause_estimate','42501','anonymous cause access denied');
select pg_temp.expect_error('select * from public.alert','42501','anonymous alert access denied');
select pg_temp.expect_error($q$select nextval('public.stock_id_seq')$q$,'42501','anonymous sequence access denied');
reset role;

-- Service-role is trusted backend only, bypasses RLS but not data constraints.
set role service_role;
select pg_temp.check_ok((select count(*)=4 from public.stock), 'service role sees all stock');
select pg_temp.expect_error($q$insert into public.stock(cage_id,size_group,count,avg_weight_kg) values(999,'mid',1,0.2)$q$,'23503','foreign key required');
select pg_temp.expect_error($q$insert into public.stock(cage_id,size_group,count,avg_weight_kg) values(3,'market',-1,0.2)$q$,'23514','negative stock rejected');
select pg_temp.expect_error($q$insert into public.sea_area(name,geom) values('bad','{}')$q$,'23514','missing GeoJSON fields rejected');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'camera','quality','1.5',0.9)$q$,'23514','quality bounds');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'sonar','vertical_dist','[0.1,0.2]',0.9)$q$,'23514','distribution sum');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'sonar','vertical_dist','[true,0]',0.9)$q$,'23514','distribution booleans rejected');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'sonar','activity','null',0.9)$q$,'23514','JSON null rejected');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'sonar','activity','"NaN"',0.9)$q$,'23514','string NaN rejected');
select pg_temp.expect_error($q$insert into public.metric values(3,now(),'unknown','activity','1',0.9)$q$,'23514','unknown sensor rejected');
select pg_temp.expect_error($q$insert into public.metric values(3,'2026-08-12T05:00:00Z','sonar','activity','1',0.9)$q$,'23505','duplicate metric identity rejected');
insert into public.metric values(3,'2026-08-13T14:00:00+09:00','sonar','vertical_dist','[0.333333,0.333333,0.333333]',0.9);
select pg_temp.check_ok(exists(select 1 from public.metric where ts='2026-08-13T05:00:00Z'), 'UTC-equivalent timestamps and rounded ratios');
insert into public.stress_score(cage_id,ts,baseline_status,model_version) values(3,now(),'warming_up','v1');
select pg_temp.check_ok(exists(select 1 from public.stress_score where baseline_status='warming_up' and score is null), 'warming up supports missing score');
select pg_temp.expect_error($q$insert into public.stress_score(cage_id,ts,baseline_status,model_version) values(3,now(),'ready','v1')$q$,'23514','ready requires score and level');
reset role;
