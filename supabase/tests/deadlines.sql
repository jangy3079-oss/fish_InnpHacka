set role authenticated;
set request.jwt.claims='{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"analysis","analysis_cage_ids":[3]}}';
do $$
declare t timestamptz=now(); input jsonb; b jsonb; bad jsonb; receipt jsonb; again jsonb; actual timestamptz;
begin
  input=public.analysis_input(3,t-interval '1 day',t,50000);
  b=pg_temp.analysis_bundle(t);
  b=jsonb_set(b,'{recommendation,deadline}',to_jsonb(t+interval '15 minutes'));
  b=jsonb_set(b,'{recommendation,deadlines}',jsonb_build_array(
    jsonb_build_object('kind','next_assessment','label','다음 판단 시점','action_id',null,'applicable',true,
      'at',t+interval '15 minutes','source_status','assumed','basis','relative','source','config/costs.yaml','expired',false)));
  bad=jsonb_set(b,'{recommendation,deadline}',to_jsonb(t+interval '30 minutes'));
  perform pg_temp.expect_error(format('select public.publish_analysis(3,%L,%L,50000,%L,%L,%L::jsonb)',
    t-interval '1 day',t,input->>'input_version','c0101010-1010-4010-8010-101010101010',bad::text),
    '22023','mismatched deadline rolls back analysis');
  bad=jsonb_set(b,'{recommendation,deadlines,0,source_status}','"pending"'::jsonb);
  perform pg_temp.expect_error(format('select public.publish_analysis(3,%L,%L,50000,%L,%L,%L::jsonb)',
    t-interval '1 day',t,input->>'input_version','c0101010-1010-4010-8010-101010101010',bad::text),
    '22023','pending deadline cannot claim a time');
  bad=jsonb_set(b,'{recommendation,deadlines,0,at}','"2026-11-27T22:00:00"'::jsonb);
  perform pg_temp.expect_error(format('select public.publish_analysis(3,%L,%L,50000,%L,%L,%L::jsonb)',
    t-interval '1 day',t,input->>'input_version','c0101010-1010-4010-8010-101010101010',bad::text),
    '22023','naive deadline rejected');
  receipt=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','c0101010-1010-4010-8010-101010101010',b);
  again=public.publish_analysis(3,t-interval '1 day',t,50000,input->>'input_version','c0101010-1010-4010-8010-101010101010',b);
  perform pg_temp.check_ok(receipt=again,'deadline publication replay preserves original time');
  perform set_config('test.deadline_recommendation',receipt->>'recommendation_id',false);
  perform set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000001"}',false);
  select deadline into actual from public.recommendation where id=(receipt->>'recommendation_id')::bigint;
  perform pg_temp.check_ok(actual=t+interval '15 minutes','existing deadline column persisted atomically');
  perform pg_temp.check_ok((public.farmer_recommendation(3)->>'deadline')::timestamptz=actual,
    'owner RPC returns the immutable stored deadline');
  perform pg_temp.check_ok(public.farmer_recommendation(3)->'deadlines'->0->>'source_status'='assumed',
    'RPC preserves assumed policy provenance');
end;
$$;
reset role;
do $$
declare rid bigint=current_setting('test.deadline_recommendation')::bigint; old timestamptz;
begin
  select deadline into old from public.recommendation where id=rid;
  update public.recommendation set deadline=old+interval '1 day' where id=rid;
  perform pg_temp.check_ok((select deadline=old from public.recommendation where id=rid),
    'column-only edit cannot silently extend snapshot deadline');
end;
$$;
set role anon;
select pg_temp.expect_error($q$select public.farmer_recommendation(3)$q$,'42501','deadline RPC remains protected');
reset role;
