begin;

-- Private storage preserves the ten-table public schema. No direct browser writes.
create table private.diagnosis_request (
  id bigint generated always as identity primary key,
  cage_id bigint not null references public.cage(id),
  creator_id uuid not null references auth.users(id),
  request_key uuid not null,
  primary_cause_id bigint not null references public.cause_estimate(id),
  snapshot jsonb not null check (jsonb_typeof(snapshot)='object'),
  context_version text not null,
  config_revision text not null check (config_revision ~ '^[0-9a-f]{64}$'),
  status text not null default 'pending' check (status='pending'),
  created_at timestamptz not null default now(),
  unique(creator_id, request_key)
);
alter table private.diagnosis_request enable row level security;
revoke all on private.diagnosis_request from public, anon, authenticated;
revoke all on sequence private.diagnosis_request_id_seq from public, anon, authenticated;
grant select on private.diagnosis_request to service_role;

create function private.diagnosis_receipt(r private.diagnosis_request) returns jsonb
language sql stable set search_path='' as $$
  select jsonb_build_object('persisted',true,'id',r.id,'cage_id',r.cage_id,'request_key',r.request_key,
    'primary_cause_id',r.primary_cause_id,'status',r.status,'created_at',r.created_at,
    'config_revision',r.config_revision,'context_version',r.context_version,'context',r.snapshot);
$$;

create function private.diagnosis_context(p_cage_id bigint, p_request_key uuid,
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

create function private.create_diagnosis_request(p_cage_id bigint,p_request_key uuid,p_context_version text,
  p_primary_cause_id bigint,p_config_revision text,p_lookback_hours double precision,p_max_points integer,
  p_max_age_minutes double precision) returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_data jsonb; v_row private.diagnosis_request; v_primary jsonb; v_max numeric;
begin
  if auth.uid() is null or not private.owns_cage(p_cage_id) then
    raise exception 'Owner access required' using errcode='42501';
  end if;
  perform 1 from public.cage c join public.farm f on f.id=c.farm_id
    where c.id=p_cage_id and f.owner_user_id=auth.uid() for update of c,f;
  if not found then raise exception 'Owner access required' using errcode='42501'; end if;
  v_data=private.diagnosis_context(p_cage_id,p_request_key,p_lookback_hours,p_max_points,p_max_age_minutes);
  if (v_data->>'persisted')::boolean then return v_data; end if;
  if p_context_version is null or v_data->>'context_version'<>p_context_version then
    raise exception 'Diagnosis context changed' using errcode='40001';
  end if;
  if p_config_revision is null or p_config_revision !~ '^[0-9a-f]{64}$' then
    raise exception 'Invalid config revision' using errcode='22023';
  end if;
  select value into v_primary from jsonb_array_elements(v_data->'context'->'causes') where (value->>'id')::bigint=p_primary_cause_id;
  select max((value->>'probability')::numeric) into v_max from jsonb_array_elements(v_data->'context'->'causes');
  if v_primary is null or v_max is null or v_max<=0 or v_primary->>'rule_id' not in ('R5','R8') or (v_primary->>'probability')::numeric<>v_max
    or exists(select 1 from jsonb_array_elements(v_data->'context'->'causes') c where c->>'rule_id' not in ('R5','R8') and (c->>'probability')::numeric>=v_max) then
    raise exception 'A leading disease-family cause is required' using errcode='40001';
  end if;
  insert into private.diagnosis_request(cage_id,creator_id,request_key,primary_cause_id,snapshot,context_version,config_revision)
    values(p_cage_id,auth.uid(),p_request_key,p_primary_cause_id,v_data->'context',p_context_version,p_config_revision)
    on conflict(creator_id,request_key) do nothing;
  select * into v_row from private.diagnosis_request where creator_id=auth.uid() and request_key=p_request_key;
  if v_row.cage_id<>p_cage_id then raise exception 'Request key conflict' using errcode='40001'; end if;
  return private.diagnosis_receipt(v_row);
end;
$$;

create function public.diagnosis_context(p_cage_id bigint,p_request_key uuid,p_lookback_hours double precision,
  p_max_points integer,p_max_age_minutes double precision) returns jsonb language sql stable security invoker set search_path='' as $$
  select private.diagnosis_context(p_cage_id,p_request_key,p_lookback_hours,p_max_points,p_max_age_minutes);
$$;
create function public.create_diagnosis_request(p_cage_id bigint,p_request_key uuid,p_context_version text,
  p_primary_cause_id bigint,p_config_revision text,p_lookback_hours double precision,p_max_points integer,
  p_max_age_minutes double precision) returns jsonb language sql security invoker set search_path='' as $$
  select private.create_diagnosis_request(p_cage_id,p_request_key,p_context_version,p_primary_cause_id,p_config_revision,
    p_lookback_hours,p_max_points,p_max_age_minutes);
$$;

revoke all on function private.diagnosis_receipt(private.diagnosis_request),
  private.diagnosis_context(bigint,uuid,double precision,integer,double precision),
  private.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision),
  public.diagnosis_context(bigint,uuid,double precision,integer,double precision),
  public.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision) from public,anon,authenticated;
grant execute on function private.diagnosis_context(bigint,uuid,double precision,integer,double precision),
  private.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision),
  public.diagnosis_context(bigint,uuid,double precision,integer,double precision),
  public.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision) to authenticated;
commit;
