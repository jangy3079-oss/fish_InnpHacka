begin;
alter table public.recommendation add column snapshot jsonb not null default '{}'::jsonb check(jsonb_typeof(snapshot)='object');

create table private.analysis_run (
  id bigint generated always as identity primary key,
  cage_id bigint not null references public.cage(id),
  ts timestamptz not null check(isfinite(ts)),
  actor_id uuid not null references auth.users(id),
  request_key uuid not null,
  input_version text not null,
  stock_version text not null,
  payload_hash text not null,
  config_revision text not null check(config_revision ~ '^[0-9a-f]{64}$'),
  valid_until timestamptz not null check(isfinite(valid_until)),
  status jsonb not null check(jsonb_typeof(status)='object'),
  recommendation_id bigint references public.recommendation(id),
  recommendation_reason text,
  created_at timestamptz not null default now(),
  unique(cage_id,ts), unique(actor_id,request_key)
);
create table private.recommendation_choice (
  recommendation_id bigint primary key references public.recommendation(id),
  analysis_id bigint not null references private.analysis_run(id),
  user_id uuid not null references auth.users(id),
  request_key uuid not null,
  option_id text not null,
  chosen_at timestamptz not null default now(),
  unique(user_id,request_key)
);
alter table private.analysis_run enable row level security;
alter table private.recommendation_choice enable row level security;
revoke all on private.analysis_run,private.recommendation_choice from public,anon,authenticated;
revoke all on sequence private.analysis_run_id_seq from public,anon,authenticated;
grant select on private.analysis_run,private.recommendation_choice to service_role;

create function private.analysis_allowed(p_cage_id bigint) returns boolean language sql stable set search_path='' as $$
  select auth.uid() is not null and coalesce(auth.jwt()->'app_metadata'->>'role'='analysis',false)
    and coalesce(auth.jwt()->'app_metadata'->'analysis_cage_ids' @> jsonb_build_array(p_cage_id),false);
$$;
create function private.stock_version(p_cage_id bigint) returns text language sql stable security definer set search_path='' as $$
  select md5(jsonb_build_object('farm',jsonb_build_object('id',f.id,'owner',f.owner_user_id,'area',f.sea_area_id),
    'stock',coalesce((select jsonb_agg(to_jsonb(s) order by s.size_group) from public.stock s where s.cage_id=c.id),'[]'::jsonb))::text)
    from public.cage c join public.farm f on f.id=c.farm_id where c.id=p_cage_id;
$$;
create function private.analysis_input_core(p_cage_id bigint,p_from timestamptz,p_to timestamptz,p_limit integer)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare v jsonb; v_metrics jsonb; v_truncated boolean;
begin
  if p_from is null or p_to is null or not isfinite(p_from) or not isfinite(p_to) or p_from>=p_to
    or p_to>now() or p_to-p_from>interval '7 days' or p_limit is null or p_limit not between 1 and 50000 then
    raise exception 'Invalid analysis window' using errcode='22023';
  end if;
  with recent as (select * from public.metric where cage_id=p_cage_id and ts>=p_from and ts<p_to order by ts desc,source,metric limit p_limit+1),
    page as (select * from recent order by ts desc,source,metric limit p_limit)
  select coalesce((select jsonb_agg(jsonb_build_object('cage_id',cage_id::text,'ts',ts,'source',source,'metric',metric,
    'value',value,'confidence',confidence) order by ts,source,metric) from page),'[]'::jsonb),
    (select count(*)>p_limit from recent) into v_metrics,v_truncated;
  select jsonb_build_object('cage_id',c.id,'farm_id',f.id,'sea_area_id',f.sea_area_id,
    'stock',coalesce((select jsonb_agg(jsonb_build_object('size_group',s.size_group,'count',s.count,'avg_weight_kg',s.avg_weight_kg,
      'insured',s.insured) order by size_group) from public.stock s where s.cage_id=c.id),'[]'::jsonb),
    'stock_version',private.stock_version(c.id),'metrics',v_metrics,'metrics_truncated',v_truncated,'from',p_from,'to',p_to)
    into v from public.cage c join public.farm f on f.id=c.farm_id where c.id=p_cage_id;
  if v is null then raise exception 'Cage not found' using errcode='22023'; end if;
  return v;
end;
$$;
create function private.analysis_input(p_cage_id bigint,p_from timestamptz,p_to timestamptz,p_limit integer) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare v jsonb;
begin
  if not private.analysis_allowed(p_cage_id) then raise exception 'Analysis access required' using errcode='42501'; end if;
  v=private.analysis_input_core(p_cage_id,p_from,p_to,p_limit);
  return jsonb_build_object('input',v,'input_version',md5(v::text));
end;
$$;
create function private.analysis_receipt(r private.analysis_run) returns jsonb language sql stable set search_path='' as $$
  select jsonb_build_object('persisted',true,'analysis_id',r.id,'cage_id',r.cage_id,'ts',r.ts,
    'recommendation_id',r.recommendation_id,'recommendation_reason',r.recommendation_reason,'config_revision',r.config_revision);
$$;
create function private.publish_analysis(p_cage_id bigint,p_from timestamptz,p_to timestamptz,p_limit integer,
  p_input_version text,p_request_key uuid,p_bundle jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_input jsonb; v_row private.analysis_run; v_hash text; v_status jsonb; v_causes jsonb; v_rec jsonb;
  v_c jsonb; v_primary bigint; v_cid bigint; v_recid bigint; v_count integer; v_sum numeric;
begin
  if not private.analysis_allowed(p_cage_id) then raise exception 'Analysis access required' using errcode='42501'; end if;
  if p_request_key is null or coalesce(jsonb_typeof(p_bundle),'')<>'object' or p_input_version is null
    or not(p_bundle ?& array['status','causes','recommendation','config_revision','valid_until']) then raise exception 'Invalid bundle' using errcode='22023'; end if;
  v_hash=md5(jsonb_build_object('bundle',p_bundle,'input_version',p_input_version,'from',p_from,'to',p_to)::text);
  perform 1 from public.cage c join public.farm f on f.id=c.farm_id where c.id=p_cage_id for update of c,f;
  if not found then raise exception 'Cage not found' using errcode='22023'; end if;
  perform 1 from public.stock where cage_id=p_cage_id for share;
  select * into v_row from private.analysis_run where actor_id=auth.uid() and request_key=p_request_key;
  if found then
    if v_row.cage_id<>p_cage_id or v_row.payload_hash<>v_hash then raise exception 'Publication key conflict' using errcode='40001'; end if;
    return private.analysis_receipt(v_row);
  end if;
  select * into v_row from private.analysis_run where cage_id=p_cage_id and ts=p_to;
  if found then
    if v_row.payload_hash<>v_hash then raise exception 'Analysis cycle conflict' using errcode='40001'; end if;
    return private.analysis_receipt(v_row);
  end if;
  v_input=private.analysis_input_core(p_cage_id,p_from,p_to,p_limit);
  if md5(v_input::text)<>p_input_version or (v_input->>'metrics_truncated')::boolean then
    raise exception 'Analysis inputs changed or truncated' using errcode='40001';
  end if;
  v_status=p_bundle->'status'; v_causes=p_bundle->'causes'; v_rec=p_bundle->'recommendation';
  if coalesce(jsonb_typeof(v_status),'')<>'object' or not coalesce(v_status ?& array['cage_id','ts','baseline_status','data_status','evidence','model_version','stress_score','level','missing_sources','camera_weight'],false)
    or (v_status->>'cage_id')::bigint<>p_cage_id or (v_status->>'ts')::timestamptz<>p_to
    or coalesce(v_status->>'baseline_status','') not in ('ready','warming_up')
    or coalesce(v_status->>'data_status','') not in ('ready','warming_up','insufficient_data')
    or jsonb_typeof(v_status->'evidence')<>'object' or coalesce(length(v_status->>'model_version'),0)=0
    or coalesce(v_status->>'model_version','')='null'
    or not ((v_status->'stress_score'='null'::jsonb) or (jsonb_typeof(v_status->'stress_score')='number' and (v_status->>'stress_score')::numeric between 0 and 100))
    or (v_status->'level'<>'null'::jsonb and v_status->>'level' not in ('정상','주의','경고','위험'))
    or jsonb_typeof(v_causes)<>'array' or jsonb_array_length(v_causes)>8
    or coalesce(p_bundle->>'config_revision','') !~ '^[0-9a-f]{64}$'
    or p_bundle->>'valid_until' is null or not isfinite((p_bundle->>'valid_until')::timestamptz) or (p_bundle->>'valid_until')::timestamptz>p_to+interval '1 day'
    or jsonb_typeof(v_status->'camera_weight')<>'number' or not((v_status->>'camera_weight')::numeric between 0 and 0.5)
    or jsonb_typeof(v_status->'missing_sources')<>'array' or exists(select 1 from jsonb_array_elements_text(v_status->'missing_sources') s where s not in ('sonar','camera','env')) then
    raise exception 'Invalid status or policy' using errcode='22023';
  end if;
  if (v_status->>'baseline_status'='warming_up' and (v_status->'stress_score'<>'null'::jsonb or v_status->'level'<>'null'::jsonb))
    or (v_status->>'data_status'='ready' and (v_status->>'baseline_status'<>'ready' or v_status->'stress_score'='null'::jsonb or v_status->'level'='null'::jsonb))
    or (v_status->>'data_status'='insufficient_data' and v_status->'stress_score'<>'null'::jsonb)
    or (v_status->>'data_status'<>'ready' and jsonb_array_length(v_causes)>0) then
    raise exception 'Invalid insufficient-data bundle' using errcode='22023';
  end if;
  for v_c in select value from jsonb_array_elements(v_causes) loop
    if jsonb_typeof(v_c->'cause')<>'string' or coalesce(length(v_c->>'cause'),0)=0
      or coalesce(v_c->>'rule_id','') !~ '^R[1-8]$' or jsonb_typeof(v_c->'probability')<>'number'
      or not((v_c->>'probability')::numeric>0 and (v_c->>'probability')::numeric<=1)
      or jsonb_typeof(v_c->'evidence')<>'object' then raise exception 'Invalid cause' using errcode='22023'; end if;
  end loop;
  select count(distinct value->>'rule_id'),sum((value->>'probability')::numeric) into v_count,v_sum from jsonb_array_elements(v_causes);
  if v_count<>jsonb_array_length(v_causes) or (v_count>0 and abs(v_sum-1)>0.000001) then raise exception 'Invalid cause distribution' using errcode='22023'; end if;
  if exists(select 1 from public.stress_score where cage_id=p_cage_id and ts=p_to)
    or exists(select 1 from public.cause_estimate where cage_id=p_cage_id and ts=p_to) then
    raise exception 'Legacy analysis already exists at this cycle' using errcode='40001';
  end if;
  if v_status->>'baseline_status'='warming_up' or (v_status->'stress_score'<>'null'::jsonb and v_status->'level'<>'null'::jsonb) then
    insert into public.stress_score(cage_id,ts,score,level,baseline_status,model_version,evidence,missing_sources,camera_weight)
      values(p_cage_id,p_to,(v_status->>'stress_score')::numeric,v_status->>'level',v_status->>'baseline_status',v_status->>'model_version',
        v_status->'evidence',array(select jsonb_array_elements_text(v_status->'missing_sources')),(v_status->>'camera_weight')::numeric);
  end if;
  for v_c in select value from jsonb_array_elements(v_causes) loop
    insert into public.cause_estimate(cage_id,ts,cause,probability,rule_id,evidence)
      values(p_cage_id,p_to,v_c->>'cause',(v_c->>'probability')::numeric,v_c->>'rule_id',v_c->'evidence') returning id into v_cid;
    if v_primary is null then v_primary=v_cid; end if;
  end loop;
  if v_rec is not null and v_rec<>'null'::jsonb then
    if v_primary is null or jsonb_typeof(v_rec)<>'object' or not(v_rec ?& array['mode','options','recommended_option']) or jsonb_typeof(v_rec->'options')<>'array'
      or jsonb_array_length(v_rec->'options') not between 1 and 12 or v_rec->>'mode' not in ('loss_comparison','first_aid_required') then
      raise exception 'Invalid recommendation' using errcode='22023';
    end if;
    if (select count(*)<>count(distinct value->>'id') from jsonb_array_elements(v_rec->'options')) then
      raise exception 'Duplicate options' using errcode='22023';
    end if;
    if ((v_causes->0->>'rule_id' in ('R5','R8')) is distinct from (v_rec->>'mode'='first_aid_required'))
      or (v_rec->>'mode'='first_aid_required' and (v_rec->'recommended_option'<>'null'::jsonb
        or exists(select 1 from jsonb_array_elements(v_rec->'options') o where o->'estimated_loss_krw'<>'null'::jsonb))) then
      raise exception 'Disease family requires non-economic first aid' using errcode='22023';
    end if;
    for v_c in select value from jsonb_array_elements(v_rec->'options') loop
      if coalesce(v_c->>'id','')='' or jsonb_typeof(v_c->'available')<>'boolean' then raise exception 'Invalid option' using errcode='22023'; end if;
    end loop;
    if v_rec->'recommended_option'<>'null'::jsonb and not exists(select 1 from jsonb_array_elements(v_rec->'options') o
      where o->>'id'=v_rec->>'recommended_option' and o->'available'='true'::jsonb) then
      raise exception 'Unavailable recommended option' using errcode='22023';
    end if;
    insert into public.recommendation(cause_estimate_id,options,snapshot)
      values(v_primary,v_rec->'options',v_rec||jsonb_build_object('config_revision',p_bundle->>'config_revision')) returning id into v_recid;
  end if;
  v_status=jsonb_set(v_status,'{cause}',coalesce((select jsonb_agg(jsonb_build_object('name',value->>'cause',
    'probability',value->'probability','rule_id',value->>'rule_id')) from jsonb_array_elements(v_causes)),'[]'::jsonb));
  insert into private.analysis_run(cage_id,ts,actor_id,request_key,input_version,stock_version,payload_hash,config_revision,valid_until,status,recommendation_id,recommendation_reason)
    values(p_cage_id,p_to,auth.uid(),p_request_key,p_input_version,v_input->>'stock_version',v_hash,p_bundle->>'config_revision',
      (p_bundle->>'valid_until')::timestamptz,v_status,v_recid,p_bundle->>'recommendation_reason') returning * into v_row;
  return private.analysis_receipt(v_row);
end;
$$;

create function private.farmer_status(p_cage_id bigint) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare r private.analysis_run;
begin
  if not private.read_cage(p_cage_id) then raise exception 'Cage access required' using errcode='42501'; end if;
  select * into r from private.analysis_run where cage_id=p_cage_id order by ts desc limit 1;
  if not found then return jsonb_build_object('unavailable_reason','analysis_not_found'); end if;
  return r.status || jsonb_build_object('analysis_id',r.id,'config_revision',r.config_revision,'valid_until',r.valid_until);
end;
$$;
create function private.farmer_recommendation(p_cage_id bigint) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare a private.analysis_run; r public.recommendation;
begin
  if not private.read_cage(p_cage_id,true) then raise exception 'Financial access required' using errcode='42501'; end if;
  select * into a from private.analysis_run where cage_id=p_cage_id order by ts desc limit 1;
  if not found then return jsonb_build_object('unavailable_reason','analysis_not_found'); end if;
  if a.recommendation_id is null then return jsonb_build_object('unavailable_reason',coalesce(a.recommendation_reason,'no_current_cause')); end if;
  select * into r from public.recommendation where id=a.recommendation_id;
  return r.snapshot || jsonb_build_object('id',r.id,'cage_id',p_cage_id,'ts',a.ts,'analysis_id',a.id,
    'options',r.options,'chosen_option',r.chosen_option,'config_revision',a.config_revision,
    'stock_changed',a.stock_version is distinct from private.stock_version(p_cage_id),'valid_until',a.valid_until);
end;
$$;
create function private.choice_receipt(r private.recommendation_choice) returns jsonb language sql stable set search_path='' as $$
  select jsonb_build_object('persisted',true,'resource_id',r.recommendation_id,'option_id',r.option_id,
    'request_key',r.request_key,'chosen_at',r.chosen_at,'execution_status','not_executed');
$$;
create function private.choose_recommendation(p_recommendation_id bigint,p_option_id text,p_request_key uuid,p_config_revision text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare a private.analysis_run; r public.recommendation; c private.recommendation_choice; v_latest bigint;
begin
  select * into a from private.analysis_run where recommendation_id=p_recommendation_id;
  if not found then return jsonb_build_object('unavailable_reason','recommendation_not_found'); end if;
  if auth.uid() is null or not private.owns_cage(a.cage_id) then raise exception 'Owner access required' using errcode='42501'; end if;
  if p_request_key is null or p_option_id is null then raise exception 'Choice key required' using errcode='22023'; end if;
  perform 1 from public.cage g join public.farm f on f.id=g.farm_id where g.id=a.cage_id and f.owner_user_id=auth.uid() for update of g,f;
  if not found then raise exception 'Owner access required' using errcode='42501'; end if;
  perform 1 from public.stock where cage_id=a.cage_id for share;
  select * into c from private.recommendation_choice where user_id=auth.uid() and request_key=p_request_key;
  if found then
    if c.recommendation_id<>p_recommendation_id or c.option_id<>p_option_id then raise exception 'Choice key conflict' using errcode='40001'; end if;
    return private.choice_receipt(c);
  end if;
  select * into r from public.recommendation where id=p_recommendation_id for update;
  select id into v_latest from private.analysis_run where cage_id=a.cage_id order by ts desc limit 1;
  if v_latest<>a.id or now()>a.valid_until or a.config_revision is distinct from p_config_revision
    or a.stock_version is distinct from private.stock_version(a.cage_id) then
    raise exception 'Recommendation is stale' using errcode='40001';
  end if;
  if r.chosen_option is not null or exists(select 1 from private.recommendation_choice where recommendation_id=p_recommendation_id) then
    raise exception 'A choice already exists' using errcode='40001';
  end if;
  if not exists(select 1 from jsonb_array_elements(r.options) o where o->>'id'=p_option_id and o->'available'='true'::jsonb) then
    raise exception 'Unavailable choice' using errcode='22023';
  end if;
  insert into private.recommendation_choice(recommendation_id,analysis_id,user_id,request_key,option_id)
    values(p_recommendation_id,a.id,auth.uid(),p_request_key,p_option_id) returning * into c;
  update public.recommendation set chosen_option=p_option_id where id=p_recommendation_id;
  return private.choice_receipt(c);
end;
$$;

create function public.analysis_input(p_cage_id bigint,p_from timestamptz,p_to timestamptz,p_limit integer) returns jsonb
language sql stable security invoker set search_path='' as $$ select private.analysis_input(p_cage_id,p_from,p_to,p_limit); $$;
create function public.publish_analysis(p_cage_id bigint,p_from timestamptz,p_to timestamptz,p_limit integer,p_input_version text,p_request_key uuid,p_bundle jsonb) returns jsonb
language sql security invoker set search_path='' as $$ select private.publish_analysis(p_cage_id,p_from,p_to,p_limit,p_input_version,p_request_key,p_bundle); $$;
create function public.farmer_status(p_cage_id bigint) returns jsonb language sql stable security invoker set search_path='' as $$ select private.farmer_status(p_cage_id); $$;
create function public.farmer_recommendation(p_cage_id bigint) returns jsonb language sql stable security invoker set search_path='' as $$ select private.farmer_recommendation(p_cage_id); $$;
create function public.choose_recommendation(p_recommendation_id bigint,p_option_id text,p_request_key uuid,p_config_revision text) returns jsonb
language sql security invoker set search_path='' as $$ select private.choose_recommendation(p_recommendation_id,p_option_id,p_request_key,p_config_revision); $$;

-- Preserve the existing function identity while adding the current-generation guard.
create or replace function private.diagnosis_context(p_cage_id bigint, p_request_key uuid,
  p_lookback_hours double precision, p_max_points integer, p_max_age_minutes double precision) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare v_existing private.diagnosis_request; v_ts timestamptz; v_causes jsonb; v_metric jsonb;
  v_scores jsonb; v_mt boolean; v_st boolean; v_context jsonb;
begin
  if auth.uid() is null or not private.owns_cage(p_cage_id) then
    raise exception 'Owner access required' using errcode='42501';
  end if;
  if p_request_key is null or p_lookback_hours is null or not(p_lookback_hours>0 and p_lookback_hours<=168)
      or p_max_points is null or p_max_points not between 1 and 1000 or p_max_age_minutes is null
      or not(p_max_age_minutes>0 and p_max_age_minutes<=1440) then
    raise exception 'Invalid diagnosis policy' using errcode='22023';
  end if;
  select * into v_existing from private.diagnosis_request where creator_id=auth.uid() and request_key=p_request_key;
  if found then
    if v_existing.cage_id<>p_cage_id then raise exception 'Request key conflict' using errcode='40001'; end if;
    return private.diagnosis_receipt(v_existing);
  end if;
  select max(ts) into v_ts from public.cause_estimate where cage_id=p_cage_id;
  if exists(select 1 from private.analysis_run where cage_id=p_cage_id and ts>v_ts) then
    raise exception 'Current analysis has no matching disease cause' using errcode='40001';
  end if;
  if v_ts is null or v_ts>now() or v_ts<now()-make_interval(secs=>p_max_age_minutes*60) then
    raise exception 'No current cause context' using errcode='40001';
  end if;
  if (select count(*)>8 or count(*)<>count(distinct rule_id) from public.cause_estimate where cage_id=p_cage_id and ts=v_ts) then
    raise exception 'Ambiguous cause context' using errcode='40001';
  end if;
  select jsonb_agg(jsonb_build_object('id',id,'ts',ts,'cause',cause,'rule_id',rule_id,'probability',probability) order by id)
    into v_causes from public.cause_estimate where cage_id=p_cage_id and ts=v_ts;
  with recent as (
    select ts,source,metric,value,confidence from public.metric
    where cage_id=p_cage_id and ts between v_ts-make_interval(secs=>p_lookback_hours*3600) and v_ts
    order by ts desc,metric,source limit p_max_points+1
  ), page as (select * from recent order by ts desc,metric,source limit p_max_points)
  select coalesce((select jsonb_agg(jsonb_build_object('cage_id',p_cage_id::text,'ts',ts,'source',source,
    'metric',metric,'value',value,'confidence',confidence) order by ts,metric,source) from page),'[]'::jsonb),
    (select count(*)>p_max_points from recent) into v_metric,v_mt;
  with recent as (
    select ts,score,baseline_status from public.stress_score
    where cage_id=p_cage_id and ts between v_ts-make_interval(secs=>p_lookback_hours*3600) and v_ts
    order by ts desc limit p_max_points+1
  ), page as (select * from recent order by ts desc limit p_max_points)
  select coalesce((select jsonb_agg(jsonb_build_object('ts',ts,'score',score,'baseline_status',baseline_status) order by ts) from page),'[]'::jsonb),
    (select count(*)>p_max_points from recent) into v_scores,v_st;
  v_context=jsonb_build_object('cage_id',p_cage_id,'cause_ts',v_ts,'causes',v_causes,'metrics',v_metric,
    'stress',v_scores,'metrics_truncated',v_mt,'stress_truncated',v_st,'lookback_hours',p_lookback_hours);
  return jsonb_build_object('persisted',false,'context',v_context,'context_version',md5(v_context::text));
end;
$$;

revoke all on function private.analysis_allowed(bigint),private.stock_version(bigint),
  private.analysis_input_core(bigint,timestamptz,timestamptz,integer),private.analysis_receipt(private.analysis_run),
  private.choice_receipt(private.recommendation_choice),
  private.diagnosis_context(bigint,uuid,double precision,integer,double precision),
  private.analysis_input(bigint,timestamptz,timestamptz,integer),
  private.publish_analysis(bigint,timestamptz,timestamptz,integer,text,uuid,jsonb),
  private.farmer_status(bigint),private.farmer_recommendation(bigint),private.choose_recommendation(bigint,text,uuid,text),
  public.analysis_input(bigint,timestamptz,timestamptz,integer),public.publish_analysis(bigint,timestamptz,timestamptz,integer,text,uuid,jsonb),
  public.farmer_status(bigint),public.farmer_recommendation(bigint),public.choose_recommendation(bigint,text,uuid,text) from public,anon,authenticated;
grant execute on function private.diagnosis_context(bigint,uuid,double precision,integer,double precision),
  private.analysis_input(bigint,timestamptz,timestamptz,integer),private.publish_analysis(bigint,timestamptz,timestamptz,integer,text,uuid,jsonb),
  private.farmer_status(bigint),private.farmer_recommendation(bigint),private.choose_recommendation(bigint,text,uuid,text),
  public.analysis_input(bigint,timestamptz,timestamptz,integer),public.publish_analysis(bigint,timestamptz,timestamptz,integer,text,uuid,jsonb),
  public.farmer_status(bigint),public.farmer_recommendation(bigint),public.choose_recommendation(bigint,text,uuid,text) to authenticated;
commit;
