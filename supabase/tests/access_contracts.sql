-- Runs last in the disposable DB; existing area aggregates are not changed.
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(public.user_access()->'cages'->0->>'id'='3','owner access lists own cage');
select pg_temp.check_ok(public.user_access()->'cages'->0->>'can_write_stock'='true','ownership is separate from financial read');
select pg_temp.check_ok(jsonb_array_length(public.area_shapes(array[1,2])->'features')=0,'demo polygon is not a verified boundary');
select pg_temp.expect_error($q$select public.cage_stock(4)$q$,'42501','other farm stock denied');
reset role;
delete from public.stock where cage_id=3;
set role authenticated;
select pg_temp.check_ok(public.cage_stock(3)->'insured'='null'::jsonb and public.cage_stock(3)->'stock'='[]'::jsonb,'empty stock insurance is unknown');
reset role;
insert into public.stock(cage_id,size_group,count,avg_weight_kg,insured) values(3,'fry',0,0.1,true),(3,'mid',30,0.3,false);
update public.sea_area set geometry_verified=true,geometry_source='https://example.invalid/test-boundary',nifs_station_id='001-A' where id=1;
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.check_ok(jsonb_array_length(public.user_access(array[1,2])->'cages')=2,'official sees only assigned area cages');
select pg_temp.check_ok(public.user_access(array[1])->'cages'->0->>'can_write_stock'='false','official read is not owner write');
select pg_temp.expect_error($q$select public.cage_stock(3)$q$,'42501','official without financial grant cannot read stock');
select pg_temp.check_ok(public.area_shapes(array[1,2])->'features'->0->'properties'->>'station_id'='001-A','station ID remains text');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1],"financial_farm_ids":[1]}}';
select pg_temp.check_ok(public.cage_stock(3)->>'insured_mixed'='true' and public.cage_stock(3)->'insured'='null'::jsonb,'mixed insurance is not false');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000002","user_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.check_ok(not exists(select 1 from jsonb_array_elements(public.user_access(array[1])->'cages') c where c->>'id'='3'),'user metadata cannot grant another farm');
reset role;

-- Actual timestamps and visibility are captured on publication, not backfilled on GET.
delete from private.recommendation_choice;
delete from private.analysis_run;
delete from public.metric where cage_id=3;
create function pg_temp.access_cycle() returns timestamptz language sql stable as $$
  select date_trunc('minute',now())-interval '15 seconds';
$$;
insert into public.metric(cage_id,ts,source,metric,value,confidence)
  values(3,date_trunc('minute',now())-interval '10 minutes','env','do','7',1),
        (3,pg_temp.access_cycle(),'camera','quality','0.9',1);
insert into private.analysis_run(cage_id,ts,actor_id,request_key,input_version,stock_version,payload_hash,config_revision,valid_until,status)
  values(3,pg_temp.access_cycle(),'00000000-0000-4000-8000-000000000001',
    'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee','input','stock','payload',repeat('a',64),now()+interval '1 hour',
    '{"data_status":"ready","level":"경고","cause_source":"cause_engine","cause":[{"rule_id":"R5","name":"질병 의심","probability":0.5,"display":false},{"rule_id":"R8","name":"기생충 의심","probability":0.5,"display":true}],"cause_pending":[],"feeding_reset_reason":"observed_normal"}');
select pg_temp.check_ok((select status->'last_seen'->>'env'=(date_trunc('minute',now())-interval '10 minutes')::text
    or (status->'last_seen'->>'env')::timestamptz=date_trunc('minute',now())-interval '10 minutes' from private.analysis_run where cage_id=3),'last seen uses actual observation');
select pg_temp.check_ok((select status->'last_seen'->'camera'='null'::jsonb from private.analysis_run where cage_id=3),'next cycle observation is excluded');
select pg_temp.check_ok((select status->'cause'->0->>'display'='false' and status->>'feeding_reset_reason'='observed_normal' from private.analysis_run where cage_id=3),'visibility and reset metadata survive publication');
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.check_ok(public.area_cause_distribution(array[1,2],repeat('a',64))->'cause_distribution'->0->>'rule_id'='R8','distribution ignores hidden causes and other areas');
reset role;
update private.analysis_run set valid_until=now()-interval '1 second';
set role authenticated;
select pg_temp.check_ok(public.area_cause_distribution(array[1],repeat('a',64))->'cause_distribution'='[]'::jsonb,'expired latest analysis does not count');
reset role;
set role anon;
select pg_temp.expect_error($q$select public.user_access()$q$,'42501','anonymous profile denied');
reset role;
