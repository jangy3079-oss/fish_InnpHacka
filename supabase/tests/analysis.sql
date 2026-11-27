select setval('public.cause_estimate_id_seq',(select max(id) from public.cause_estimate),true);
select setval('public.recommendation_id_seq',(select max(id) from public.recommendation),true);
create function pg_temp.analysis_time(n integer default 0) returns timestamptz language sql stable as $$
  select date_trunc('minute',now())-interval '20 minutes'+make_interval(mins=>5*n);
$$;
create function pg_temp.analysis_bundle(t timestamptz, rid text default 'R1') returns jsonb language sql as $$
  select jsonb_build_object('status',jsonb_build_object('cage_id',3,'ts',t,'stress_score',64,'level','경고','baseline_status','ready',
    'data_status','ready','evidence','{}'::jsonb,'model_version','test-model','camera_weight',0,'missing_sources','["camera"]'::jsonb),
    'causes',jsonb_build_array(jsonb_build_object('cause','의심 원인','rule_id',rid,'probability',1,'evidence','{}'::jsonb)),
    'recommendation',case when rid in ('R5','R8') then jsonb_build_object('mode','first_aid_required','recommended_option',null,
      'options','[{"id":"reduce_feed","label":"사료","available":true,"estimated_loss_krw":null,"reason":null}]'::jsonb)
      else jsonb_build_object('mode','loss_comparison','recommended_option','keep','options',
      '[{"id":"keep","label":"유지","available":true,"estimated_loss_krw":100,"reason":null},{"id":"oxygen","label":"산소","available":false,"estimated_loss_krw":null,"reason":"불가"}]'::jsonb) end,
    'config_revision',repeat('a',64),'valid_until',t+interval '1 hour');
$$;
insert into public.metric(cage_id,ts,source,metric,value,confidence) values(3,pg_temp.analysis_time()-interval '5 minutes','sonar','activity','0.5',0.9);

set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.expect_error($q$select public.analysis_input(3,pg_temp.analysis_time()-interval '1 day',pg_temp.analysis_time(),50000)$q$,'42501','farmer cannot publish analysis');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.analysis_input(3,pg_temp.analysis_time()-interval '1 day',pg_temp.analysis_time(),50000)$q$,'42501','edge cannot publish analysis');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","user_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.analysis_input(3,pg_temp.analysis_time()-interval '1 day',pg_temp.analysis_time(),50000)$q$,'42501','analysis metadata spoof rejected');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
select pg_temp.expect_error($q$select public.analysis_input(4,pg_temp.analysis_time()-interval '1 day',pg_temp.analysis_time(),50000)$q$,'42501','analysis cage grant scoped');
select pg_temp.check_ok((select count(*)=0 from public.stock),'worker has no broad farm table access');
select pg_temp.expect_error($q$update public.recommendation set chosen_option='keep'$q$,'42501','worker cannot choose directly');
do $$
declare input jsonb; b jsonb; bad jsonb; receipt jsonb; replay jsonb; t timestamptz=pg_temp.analysis_time();
begin
  input=public.analysis_input(3,t-interval '1 day',t,50000); b=pg_temp.analysis_bundle(t);
  perform pg_temp.check_ok(jsonb_array_length(input->'input'->'stock')>0,'worker gets assigned cage stock');
  perform pg_temp.check_ok(jsonb_array_length(input->'input'->'metrics')>0,'worker gets assigned cage observations');
  bad=jsonb_set(b,'{recommendation,options,0,available}','false'::jsonb);
  perform pg_temp.expect_error(format('select public.publish_analysis(3,%L,%L,50000,%L,%L,%L::jsonb)',t-interval '1 day',t,
    input->>'input_version','10101010-1010-4010-8010-101010101010',bad::text),'22023','invalid recommendation rolls back full bundle');
  receipt=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','20202020-2020-4020-8020-202020202020',b);
  perform pg_temp.check_ok(receipt->>'persisted'='true' and receipt->>'recommendation_id' is not null,'one atomic analysis published');
  perform set_config('test.first_recommendation',receipt->>'recommendation_id',false);
  replay=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','20202020-2020-4020-8020-202020202020',b);
  perform pg_temp.check_ok(replay=receipt,'publication replay idempotent');
  bad=jsonb_set(b,'{status,stress_score}','70'::jsonb);
  perform pg_temp.expect_error(format('select public.publish_analysis(3,%L,%L,50000,%L,%L,%L::jsonb)',t-interval '1 day',t,
    input->>'input_version','20202020-2020-4020-8020-202020202020',bad::text),'40001','publication key cannot change results');
end;
$$;
reset role;
select pg_temp.check_ok((select count(*)=1 from private.analysis_run),'failed bundle did not create partial run');
select pg_temp.check_ok((select count(*)=1 from public.cause_estimate where cage_id=3 and ts=pg_temp.analysis_time()),'failed bundle left no duplicate cause');
select pg_temp.check_ok((select count(*)=1 from public.stress_score where cage_id=3 and ts=pg_temp.analysis_time()),'status row committed with analysis');

set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(public.farmer_status(3)->'cause'->0->>'rule_id'='R1','status and cause from same cycle');
select pg_temp.check_ok((public.farmer_recommendation(3)->>'stock_changed')::boolean=false,'fresh stock snapshot matches');
select pg_temp.expect_error($q$select * from private.analysis_run$q$,'42501','private analysis storage not exposed');
select pg_temp.expect_error($q$select public.choose_recommendation(current_setting('test.first_recommendation')::bigint,'oxygen','30303030-3030-4030-8030-303030303030',repeat('a',64))$q$,'22023','unavailable option rejected');
select pg_temp.expect_error($q$select public.choose_recommendation(current_setting('test.first_recommendation')::bigint,'keep','30303030-3030-4030-8030-303030303030',repeat('b',64))$q$,'40001','config mismatch choice rejected');
update public.stock set count=count+1 where cage_id=3;
select pg_temp.check_ok((public.farmer_recommendation(3)->>'stock_changed')::boolean,'changed stock reported');
select pg_temp.expect_error($q$select public.choose_recommendation(current_setting('test.first_recommendation')::bigint,'keep','30303030-3030-4030-8030-303030303030',repeat('a',64))$q$,'40001','stock changed choice rejected');

set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000002"}';
select pg_temp.expect_error($q$select public.farmer_status(3)$q$,'42501','other farm cannot read status');
select pg_temp.expect_error($q$select public.choose_recommendation(current_setting('test.first_recommendation')::bigint,'keep','30303030-3030-4030-8030-303030303030',repeat('a',64))$q$,'42501','other farm cannot choose');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1]}}';
select pg_temp.check_ok(public.farmer_status(3)->>'analysis_id' is not null,'official can read assigned area status');
select pg_temp.expect_error($q$select public.farmer_recommendation(3)$q$,'42501','official financial access separate');
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000003","app_metadata":{"role":"official","area_ids":[1],"financial_farm_ids":[1]}}';
select pg_temp.check_ok(public.farmer_recommendation(3)->>'id' is not null,'financial official can read recommendation');
select pg_temp.expect_error($q$select public.choose_recommendation(current_setting('test.first_recommendation')::bigint,'keep','30303030-3030-4030-8030-303030303030',repeat('a',64))$q$,'42501','official cannot choose');

set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
do $$
declare input jsonb; r jsonb; t timestamptz=pg_temp.analysis_time(1);
begin
  input=public.analysis_input(3,t-interval '1 day',t,50000);
  perform pg_temp.expect_error(format('select public.publish_analysis(3,%L,%L,50000,%L,%L,%L::jsonb)',t-interval '1 day',t,
    'outdated','40404040-4040-4040-8040-404040404040',pg_temp.analysis_bundle(t)::text),'40001','changed input version rejects publication');
  r=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','40404040-4040-4040-8040-404040404040',pg_temp.analysis_bundle(t));
  perform set_config('test.second_recommendation',r->>'recommendation_id',false);
end;
$$;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.expect_error($q$select public.choose_recommendation(current_setting('test.first_recommendation')::bigint,'keep','50505050-5050-4050-8050-505050505050',repeat('a',64))$q$,'40001','old generation cannot be chosen');
do $$
declare r jsonb; again jsonb; id bigint=current_setting('test.second_recommendation')::bigint;
begin
  r=public.choose_recommendation(id,'keep','60606060-6060-4060-8060-606060606060',repeat('a',64));
  perform pg_temp.check_ok(r->>'persisted'='true' and r->>'execution_status'='not_executed','choice recorded without executing action');
  again=public.choose_recommendation(id,'keep','60606060-6060-4060-8060-606060606060',repeat('a',64));
  perform pg_temp.check_ok(again=r,'choice replay is idempotent');
  perform pg_temp.expect_error(format('select public.choose_recommendation(%s,%L,%L,%L)',id,'oxygen','60606060-6060-4060-8060-606060606060',repeat('a',64)),'40001','choice key cannot change action');
  perform pg_temp.expect_error(format('select public.choose_recommendation(%s,%L,%L,%L)',id,'keep','70707070-7070-4070-8070-707070707070',repeat('a',64)),'40001','a second choice cannot overwrite first');
end;
$$;
select pg_temp.check_ok(public.farmer_recommendation(3)->>'chosen_option'='keep','chosen option visible on read');

-- A newer no-cause cycle hides old recommendations and prevents an old R5 request.
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
do $$
declare input jsonb; b jsonb; r jsonb; t timestamptz;
begin
  t=pg_temp.analysis_time(2); input=public.analysis_input(3,t-interval '1 day',t,50000);
  b=jsonb_set(pg_temp.analysis_bundle(t,'R5'),'{valid_until}',to_jsonb(t-interval '1 minute'));
  r=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','80808080-8080-4080-8080-808080808080',b);
  perform set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000001"}',false);
  perform pg_temp.expect_error(format('select public.choose_recommendation(%s,%L,%L,%L)',(r->>'recommendation_id')::bigint,
    'reduce_feed','85858585-8585-4585-8585-858585858585',repeat('a',64)),'40001','expired recommendation cannot be chosen');
  perform set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}',false);
  t=pg_temp.analysis_time(3); input=public.analysis_input(3,t-interval '1 day',t,50000);
  b=pg_temp.analysis_bundle(t); b=jsonb_set(b,'{causes}','[]'::jsonb); b=jsonb_set(b,'{recommendation}','null'::jsonb);
  b=jsonb_set(b,'{status,stress_score}','30'::jsonb); b=jsonb_set(b,'{status,level}','"정상"'::jsonb);
  perform public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','90909090-9090-4090-8090-909090909090',b);
end;
$$;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000001"}';
select pg_temp.check_ok(public.farmer_recommendation(3)->>'unavailable_reason'='no_current_cause','normal cycle does not reuse old recommendation');
select pg_temp.expect_error($q$select public.diagnosis_context(3,'99999999-9999-4999-8999-999999999999',72,1000,60)$q$,'40001','normal analysis blocks diagnosis from previous R5');
reset role;
select pg_temp.check_ok((select count(*)=1 from private.recommendation_choice),'only one choice row committed');
set role anon;
select pg_temp.expect_error($q$select public.farmer_status(3)$q$,'42501','anonymous status RPC blocked');
reset role;

-- R7 can be published below the alert threshold, with a rule-based choice different from the loss minimum.
set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
do $$
declare input jsonb; b jsonb; r jsonb; t timestamptz=pg_temp.analysis_time(4);
begin
  input=public.analysis_input(3,t-interval '1 day',t,50000);
  b=pg_temp.analysis_bundle(t,'R7');
  b=jsonb_set(b,'{status,stress_score}','30'::jsonb);
  b=jsonb_set(b,'{status,level}','"정상"'::jsonb);
  b=jsonb_set(b,'{status,r7_held_due_to_missing}','true'::jsonb);
  b=jsonb_set(b,'{status,feeding_last_observed_at}',to_jsonb(t-interval '6 hours'));
  b=jsonb_set(b,'{recommendation,recommended_option}','"reduce_feed"'::jsonb);
  b=jsonb_set(b,'{recommendation,details}','{"lowest_loss_option":"keep","recommendation_basis":"R7_feeding_management"}'::jsonb);
  b=jsonb_set(b,'{recommendation,options}','[{"id":"keep","label":"유지","available":true,"estimated_loss_krw":0,"reason":null},{"id":"reduce_feed","label":"사료 감량","available":true,"estimated_loss_krw":0,"reason":null}]'::jsonb);
  r=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','a0101010-1010-4010-8010-101010101010',b);
  perform pg_temp.check_ok(r->>'persisted'='true','R7 low-score generation published');
  perform set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000001"}',false);
  perform pg_temp.check_ok(public.farmer_status(3)->'cause'->0->>'rule_id'='R7'
    and public.farmer_status(3)->>'r7_held_due_to_missing'='true','R7 status retains observation hold metadata');
  perform pg_temp.check_ok(public.farmer_recommendation(3)->>'recommended_option'='reduce_feed'
    and public.farmer_recommendation(3)->'details'->>'lowest_loss_option'='keep','R7 guidance differs from monetary minimum');
  perform pg_temp.check_ok(public.choose_recommendation((r->>'recommendation_id')::bigint,'reduce_feed',
    'b0101010-1010-4010-8010-101010101010',repeat('a',64))->>'persisted'='true','owner records R7 management choice');
end;
$$;
reset role;
