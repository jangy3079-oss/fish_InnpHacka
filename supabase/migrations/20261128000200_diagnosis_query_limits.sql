begin;
create index diagnosis_event_idx on private.diagnosis_request(creator_id,cage_id,primary_cause_id);
create index diagnosis_recent_idx on private.diagnosis_request(creator_id,cage_id,created_at desc,id desc);

-- Historical duplicates are retained. All new writes go through the locked guard.
-- Keep the old implementation private, but revoke the old callable signatures.
alter function private.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision)
  rename to commit_diagnosis_request;
revoke all on function private.commit_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision),
  public.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision) from public,anon,authenticated;

create function private.create_diagnosis_request(p_cage_id bigint,p_request_key uuid,p_context_version text,
  p_primary_cause_id bigint,p_config_revision text,p_lookback_hours double precision,p_max_points integer,
  p_max_age_minutes double precision,p_min_interval_minutes integer) returns jsonb
language plpgsql security definer set search_path='' as $$
declare latest private.analysis_run; cause_time timestamptz;
begin
  if auth.uid() is null then raise insufficient_privilege using message='Owner access required'; end if;
  perform 1 from public.cage c join public.farm f on f.id=c.farm_id
    where c.id=p_cage_id and f.owner_user_id=auth.uid() for update of c,f;
  if not found then raise insufficient_privilege using message='Owner access required'; end if;
  if p_min_interval_minutes is null or p_min_interval_minutes not between 1 and 1440 then
    raise invalid_parameter_value using message='Invalid diagnosis interval';
  end if;
  -- A retry must not consume quota or become invalid when the current cause changes.
  if not exists(select 1 from private.diagnosis_request where creator_id=auth.uid() and request_key=p_request_key) then
    -- Once published analyses exist, a past cause row is not a current diagnosis.
    select * into latest from private.analysis_run where cage_id=p_cage_id order by ts desc limit 1;
    if found then
      select ts into cause_time from public.cause_estimate where id=p_primary_cause_id and cage_id=p_cage_id;
      if cause_time is distinct from latest.ts or latest.status->>'data_status' is distinct from 'ready'
        or latest.valid_until<now() or latest.config_revision is distinct from p_config_revision
        or not exists(select 1 from jsonb_array_elements(coalesce(latest.status->'cause','[]'::jsonb)) c
                      where c->>'rule_id' in ('R5','R8')) then
        raise serialization_failure using message='Current diagnosis context is unavailable';
      end if;
    end if;
    if exists(select 1 from private.diagnosis_request where creator_id=auth.uid() and cage_id=p_cage_id
        and (primary_cause_id=p_primary_cause_id or created_at>now()-make_interval(mins=>p_min_interval_minutes))) then
      raise serialization_failure using message='Diagnosis already requested or interval not elapsed';
    end if;
  end if;
  return private.commit_diagnosis_request(p_cage_id,p_request_key,p_context_version,p_primary_cause_id,
    p_config_revision,p_lookback_hours,p_max_points,p_max_age_minutes);
end;
$$;
create function public.create_diagnosis_request(p_cage_id bigint,p_request_key uuid,p_context_version text,
  p_primary_cause_id bigint,p_config_revision text,p_lookback_hours double precision,p_max_points integer,
  p_max_age_minutes double precision,p_min_interval_minutes integer) returns jsonb
language sql security invoker set search_path='' as $$
  select private.create_diagnosis_request(p_cage_id,p_request_key,p_context_version,p_primary_cause_id,
    p_config_revision,p_lookback_hours,p_max_points,p_max_age_minutes,p_min_interval_minutes);
$$;
revoke all on function private.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision,integer),
  public.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision,integer) from public,anon,authenticated;
grant execute on function private.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision,integer),
  public.create_diagnosis_request(bigint,uuid,text,bigint,text,double precision,integer,double precision,integer) to authenticated;

create function private.read_diagnosis_request(p_cage_id bigint,p_request_key uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare r private.diagnosis_request;
begin
  if auth.uid() is null or not private.owns_cage(p_cage_id) then
    raise insufficient_privilege using message='Owner access required';
  end if;
  select * into r from private.diagnosis_request where cage_id=p_cage_id and creator_id=auth.uid()
    and (p_request_key is null or request_key=p_request_key) order by created_at desc,id desc limit 1;
  if not found then return null; end if;
  return jsonb_build_object('persisted',true,'id',r.id,'cage_id',r.cage_id,'request_key',r.request_key,
    'primary_cause_id',r.primary_cause_id,'status',r.status,'dispatch_status','pending',
    'created_at',r.created_at,'observed_at',r.snapshot->>'cause_ts');
end;
$$;
create function public.read_diagnosis_request(p_cage_id bigint,p_request_key uuid default null) returns jsonb
language sql stable security invoker set search_path='' as $$
  select private.read_diagnosis_request(p_cage_id,p_request_key);
$$;
revoke all on function private.read_diagnosis_request(bigint,uuid),public.read_diagnosis_request(bigint,uuid) from public,anon,authenticated;
grant execute on function private.read_diagnosis_request(bigint,uuid),public.read_diagnosis_request(bigint,uuid) to authenticated;
commit;
