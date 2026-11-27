-- Aggregate tests use private fixtures in this disposable database only.
insert into public.sea_area(id,name,geom) overriding system value
select n,'Area '||n,'{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}'::jsonb from generate_series(35,39) n;
insert into public.farm(id,sea_area_id,owner_user_id,name,location) overriding system value
select n,n,'00000000-0000-4000-8000-000000000001','Farm '||n,'{}'::jsonb from generate_series(35,39) n;
insert into public.cage(id,farm_id,depth_m,net_depth_m) overriding system value
values(350,35,20,15),(351,35,20,15),(352,35,20,15),(353,35,20,15),(354,35,20,15),(355,35,20,15),
 (356,37,20,15),(357,37,20,15),(358,37,20,15),(359,38,20,15),(360,39,20,15);
insert into public.stock(cage_id,size_group,count,avg_weight_kg)
select id,'mid',500,0.3 from public.cage where id between 350 and 360;
update public.stock set count=400 where cage_id=350;
insert into public.stock(cage_id,size_group,count,avg_weight_kg) values(350,'fry',300,0.01);

create function pg_temp.area_run(c bigint,ds text,score numeric,lv text,opt text default null,
  mode text default 'loss_comparison',reason text default null,age integer default 5,expiry integer default 30)
returns void language plpgsql as $$
declare cid bigint; rid bigint; t timestamptz=now()-make_interval(mins=>age);
begin
  if opt is not null or mode='first_aid_required' then
    insert into public.cause_estimate(cage_id,ts,cause,probability,rule_id,evidence)
      values(c,t,'private cause',1,case when mode='first_aid_required' then 'R5' else 'R1' end,'{}') returning id into cid;
    insert into public.recommendation(cause_estimate_id,options,snapshot,chosen_option)
      values(cid,'[]',jsonb_build_object('mode',mode,'recommended_option',opt,'options',jsonb_build_array(
        jsonb_build_object('id',coalesce(opt,'reduce_feed'),'available',true,'estimated_loss_krw',999999)),
        'private_details','secret'),case when opt='release' then 'keep' end) returning id into rid;
  end if;
  insert into private.analysis_run(cage_id,ts,actor_id,request_key,input_version,stock_version,payload_hash,
    config_revision,valid_until,status,recommendation_id,recommendation_reason)
  values(c,t,'00000000-0000-4000-8000-000000000004',gen_random_uuid(),'version',private.stock_version(c),'hash',
    repeat('a',64),now()+make_interval(mins=>expiry),jsonb_build_object('data_status',ds,
    'baseline_status',case when ds='warming_up' then 'warming_up' else 'ready' end,'stress_score',score,
    'level',lv,'level_held_due_to_missing',ds='insufficient_data'),rid,reason);
end;
$$;
select pg_temp.area_run(350,'ready',40,'주의','release');
select pg_temp.area_run(351,'ready',80,'경고','keep');
select pg_temp.area_run(352,'insufficient_data',null,'위험');
select pg_temp.area_run(354,'ready',99,'위험','release','loss_comparison',null,15,30);
select pg_temp.area_run(354,'ready',10,'정상',null,'loss_comparison','no_current_cause',5,-1);
select pg_temp.area_run(355,'warming_up',null,null);
select pg_temp.area_run(356,'ready',60,'경고',null,'first_aid_required');
select pg_temp.area_run(357,'ready',20,'정상',null,'loss_comparison','no_current_cause');
select pg_temp.area_run(358,'ready',30,'주의',null,'loss_comparison','stock_required');
select pg_temp.area_run(359,'ready',25,'정상','release');
select pg_temp.area_run(360,'ready',100,'위험','release');
update public.stock set count=501 where cage_id=359;

set role anon;
select pg_temp.expect_error($q$select public.area_stress_map(array[35],repeat('a',64))$q$,'42501','anonymous map denied');
select pg_temp.expect_error($q$select public.area_release_demand(35,repeat('a',64))$q$,'42501','anonymous demand denied');
set role authenticated;
set request.jwt.claims='{"app_metadata":{"role":"official","area_ids":[35]}}';
select pg_temp.expect_error($q$select public.area_stress_map(array[35],repeat('a',64))$q$,'42501','official claims without user denied');
select pg_temp.expect_error($q$select public.area_release_demand(35,repeat('a',64))$q$,'42501','demand needs authenticated user identity');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.expect_error($q$select public.area_stress_map(array[35],repeat('a',64))$q$,'42501','farmer map denied even in own area');
select pg_temp.expect_error($q$select public.area_release_demand(35,repeat('a',64))$q$,'42501','farmer demand denied');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","user_metadata":{"role":"official","area_ids":[35]}}';
select pg_temp.expect_error($q$select public.area_stress_map(array[35],repeat('a',64))$q$,'42501','spoofed official map denied');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[35,36,37,38,40]}}';
select pg_temp.expect_error($q$select public.area_stress_map(array[35,39],repeat('a',64))$q$,'42501','one unauthorized area rejects entire map');
select pg_temp.expect_error($q$select public.area_release_demand(39,repeat('a',64))$q$,'42501','cross-area demand denied');
select pg_temp.expect_error($q$select public.area_release_demand(999,repeat('a',64))$q$,'42501','unknown unassigned area is forbidden');
select pg_temp.expect_error($q$select public.area_stress_map(array[null]::bigint[],repeat('a',64))$q$,'42501','null scope denied');
select pg_temp.expect_error($q$select public.area_stress_map(null,repeat('a',64))$q$,'42501','missing scope denied');
select pg_temp.expect_error($q$select public.area_stress_map(array[35],'invalid')$q$,'22023','invalid revision rejected');
select pg_temp.expect_error($q$select * from private.area_cage_facts(35,repeat('a',64))$q$,'42501','internal per-cage facts inaccessible');
select pg_temp.expect_error($q$select * from private.analysis_run$q$,'42501','private analysis inaccessible');
select pg_temp.check_ok((select count(*)=0 from public.stock) and (select count(*)=0 from public.recommendation),
  'aggregate privilege never grants raw finances');
do $$
declare m jsonb; a jsonb; d jsonb;
begin
  m=public.area_stress_map(array[35,35,36,37,38,40],repeat('a',64));
  perform pg_temp.check_ok(jsonb_array_length(m->'areas')=4,'deduplicated existing authorized areas only');
  a=m->'areas'->0;
  perform pg_temp.check_ok(a->>'area_id'='35' and a->>'cage_count'='6','area includes all cages, not analysis row count');
  perform pg_temp.check_ok(a->>'level'='위험' and a->>'stress_score'='80','held danger preserved and numeric max excludes stale score');
  perform pg_temp.check_ok(a->>'assessed_cage_count'='2' and a->>'held_cage_count'='1'
    and a->>'unavailable_cage_count'='3' and a->>'data_status'='partial','map coverage distinguishes held, stale, warm, absent');
  perform pg_temp.check_ok((select array_agg(k order by k) from jsonb_object_keys(a) k)=array[
    'area_id','assessed_cage_count','cage_count','data_status','held_cage_count','latest_analysis_at','level','name','stress_score','unavailable_cage_count'],
    'map response exact allowlist, no individual IDs or losses');
  a=m->'areas'->1;
  perform pg_temp.check_ok(a->'level'='null'::jsonb and a->'stress_score'='null'::jsonb
    and a->>'cage_count'='0' and a->>'data_status'='unavailable','empty map area is not normal or zero score');
  d=public.area_release_demand(35,repeat('a',64));
  perform pg_temp.check_ok(d->>'estimated_fish_count'='700' and d->>'recommended_cage_count'='1',
    'release counts whole matching cage stock once across size groups despite chosen keep');
  perform pg_temp.check_ok(d->>'assessed_cage_count'='2' and d->>'unavailable_cage_count'='4'
    and d->>'data_status'='partial' and d->>'basis'='recommended_not_chosen','partial demand lower bound, no stale release revival');
  perform pg_temp.check_ok((select array_agg(k order by k) from jsonb_object_keys(d) k)=array[
    'area_id','assessed_cage_count','basis','cage_count','config_revision','data_status','estimated_fish_count',
    'recommended_cage_count','ts','unavailable_cage_count'],'demand exact allowlist, no financial snapshots');
  d=public.area_release_demand(36,repeat('a',64));
  perform pg_temp.check_ok(d->>'estimated_fish_count'='0' and d->>'data_status'='complete','empty demand genuinely zero');
  d=public.area_release_demand(37,repeat('a',64));
  perform pg_temp.check_ok(d->>'estimated_fish_count'='0' and d->>'assessed_cage_count'='2'
    and d->>'unavailable_cage_count'='1','first aid and no cause known zero, missing stock unknown');
  d=public.area_release_demand(38,repeat('a',64));
  perform pg_temp.check_ok(d->'estimated_fish_count'='null'::jsonb and d->>'data_status'='unavailable','stock change excludes obsolete release count');
  d=public.area_release_demand(35,repeat('b',64));
  perform pg_temp.check_ok(d->'estimated_fish_count'='null'::jsonb and d->>'assessed_cage_count'='0','config revision change invalidates demand');
  a=public.area_stress_map(array[35],repeat('b',64))->'areas'->0;
  perform pg_temp.check_ok(a->'level'='null'::jsonb and a->'stress_score'='null'::jsonb and a->>'data_status'='unavailable',
    'config revision change never fabricates normal');
  perform pg_temp.check_ok(public.area_release_demand(40,repeat('a',64))='{"unavailable_reason":"area_not_found"}'::jsonb,
    'assigned nonexistent area distinguished');
  perform pg_temp.check_ok(public.area_stress_map('{}'::bigint[],repeat('a',64))->'areas'='[]'::jsonb,'empty area scope reveals nothing');
end;
$$;
reset role;

-- A newer missing/normal cycle must supersede the old release/danger cycle.
select pg_temp.area_run(350,'ready',15,'정상',null,'loss_comparison','no_current_cause',1,30);
select pg_temp.area_run(352,'warming_up',null,null,null,'loss_comparison',null,1,30);
update public.recommendation set snapshot=jsonb_set(snapshot,'{options,0,available}','false')
 where id=(select recommendation_id from private.analysis_run where cage_id=351 order by ts desc limit 1);
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[35]}}';
select pg_temp.check_ok(public.area_release_demand(35,repeat('a',64))->>'estimated_fish_count'='0'
  and public.area_release_demand(35,repeat('a',64))->>'assessed_cage_count'='1','new no-cause cycle removes prior release, invalid option is unknown');
select pg_temp.check_ok(public.area_stress_map(array[35],repeat('a',64))->'areas'->0->>'level'='경고','new warming cycle supersedes old held danger');
reset role;

-- Exercise release-unavailable, stock refresh, future cycles and overflow too.
update private.analysis_run set stock_version=private.stock_version(359) where cage_id=359;
update public.recommendation set snapshot=jsonb_set(snapshot,'{options,0,available}','false')
 where id=(select recommendation_id from private.analysis_run where cage_id=359);
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[38]}}';
select pg_temp.check_ok(public.area_release_demand(38,repeat('a',64))->'estimated_fish_count'='null'::jsonb,'unavailable release option cannot count');
reset role;
update public.recommendation set snapshot=jsonb_set(snapshot,'{options,0,available}','true')
 where id=(select recommendation_id from private.analysis_run where cage_id=359);
set role authenticated;
select pg_temp.check_ok(public.area_release_demand(38,repeat('a',64))->>'estimated_fish_count'='501'
  and public.area_release_demand(38,repeat('a',64))->>'data_status'='complete','matching release snapshot yields complete demand');
select pg_temp.check_ok(public.area_stress_map(array[38],repeat('a',64))->'areas'->0->>'data_status'='complete',
  'all current assessed cages yield complete map');
reset role;
update private.analysis_run set ts=now()+interval '1 minute' where cage_id=359;
set role authenticated;
select pg_temp.check_ok(public.area_release_demand(38,repeat('a',64))->'estimated_fish_count'='null'::jsonb,'future generation cannot count');
reset role;
update private.analysis_run set ts=now()-interval '1 minute' where cage_id=359;
update public.stock set count=9007199254740992 where cage_id=359;
update private.analysis_run set stock_version=private.stock_version(359) where cage_id=359;
set role authenticated;
select pg_temp.expect_error($q$select public.area_release_demand(38,repeat('a',64))$q$,'22003','large release sum rejected without precision loss');
reset role;
