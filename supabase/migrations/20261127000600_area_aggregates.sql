-- Official-only area aggregates; raw stock/recommendation RLS is unchanged.
begin;

-- Internal facts are never executable by clients. Latest generation is selected
-- before freshness checks, so a stale/new empty cycle cannot revive older advice.
create function private.area_cage_facts(p_area_id bigint,p_revision text)
returns table(assessed boolean,held boolean,level_rank integer,score double precision,
              analysis_at timestamptz,demand_known boolean,release_count numeric)
language sql stable security definer set search_path='' as $$
  with latest as (
    select c.id,a.ts,a.status,a.recommendation_reason,r.snapshot,
      a.ts<=now() and a.valid_until>=now() and a.config_revision=p_revision as current,
      a.stock_version=private.stock_version(c.id) as stock_matches,
      coalesce((select sum(s.count) from public.stock s where s.cage_id=c.id),0) as fish_count
    from public.cage c join public.farm f on f.id=c.farm_id
    left join lateral (select ar.* from private.analysis_run ar where ar.cage_id=c.id
      order by ar.ts desc,ar.id desc limit 1) a on true
    left join public.recommendation r on r.id=a.recommendation_id
    where f.sea_area_id=p_area_id
  ), state as (
    select *,coalesce(current and status->>'data_status'='ready' and status->>'baseline_status'='ready'
      and jsonb_typeof(status->'stress_score')='number' and status->>'level' in ('정상','주의','경고','위험'),false) as ready,
      coalesce(current and status->>'data_status'='insufficient_data' and status->'level_held_due_to_missing'='true'::jsonb
        and status->>'level' in ('정상','주의','경고','위험'),false) as is_held
    from latest
  ), facts as (
    select *,coalesce(ready and (
      (snapshot is null and recommendation_reason='no_current_cause')
      or snapshot->>'mode'='first_aid_required'
      or (snapshot->>'mode'='loss_comparison' and stock_matches and fish_count>0
        and exists(select 1 from jsonb_array_elements(snapshot->'options') o
          where o->>'id'=snapshot->>'recommended_option' and o->'available'='true'::jsonb))
      ),false) as known
    from state
  )
  select ready,is_held,
    case when ready or is_held then case status->>'level' when '정상' then 0 when '주의' then 1 when '경고' then 2 when '위험' then 3 end end,
    case when ready then (status->>'stress_score')::double precision end,
    ts,known,case when known and snapshot->>'mode'='loss_comparison' and snapshot->>'recommended_option'='release'
      then fish_count else 0 end
  from facts;
$$;

create function private.area_stress_map(p_area_ids bigint[],p_config_revision text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb;
begin
  if auth.uid() is null or coalesce(auth.jwt()->'app_metadata'->>'role','')<>'official'
    or p_area_ids is null or exists(select 1 from unnest(p_area_ids) id where id is null or not private.official_in_area(id)) then
    raise exception 'Official area access required' using errcode='42501';
  end if;
  if p_config_revision is null or p_config_revision !~ '^[a-f0-9]{64}$' then
    raise exception 'Invalid configuration revision' using errcode='22023';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object(
    'area_id',a.id,'name',a.name,'level',(array['정상','주의','경고','위험'])[s.max_level+1],
    'stress_score',s.max_score,'cage_count',s.total,'assessed_cage_count',s.assessed,
    'held_cage_count',s.held,'unavailable_cage_count',s.total-s.assessed-s.held,
    'data_status',case when s.total>0 and s.assessed=s.total then 'complete'
      when s.assessed+s.held>0 then 'partial' else 'unavailable' end,
    'latest_analysis_at',s.latest_at) order by a.id),'[]'::jsonb) into result
  from public.sea_area a cross join lateral (
    select count(*) as total,count(*) filter(where assessed) as assessed,count(*) filter(where held) as held,
      max(level_rank) as max_level,max(score) as max_score,max(analysis_at) as latest_at
    from private.area_cage_facts(a.id,p_config_revision)
  ) s where a.id=any(p_area_ids);
  return jsonb_build_object('ts',now(),'config_revision',p_config_revision,
    'aggregation_method','max_current_level_and_score','areas',result);
end;
$$;

create function private.area_release_demand(p_area_id bigint,p_config_revision text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare s record;
begin
  if auth.uid() is null or p_area_id is null or not private.official_in_area(p_area_id) then
    raise exception 'Official area access required' using errcode='42501';
  end if;
  if p_config_revision is null or p_config_revision !~ '^[a-f0-9]{64}$' then
    raise exception 'Invalid configuration revision' using errcode='22023';
  end if;
  if not exists(select 1 from public.sea_area where id=p_area_id) then
    return jsonb_build_object('unavailable_reason','area_not_found');
  end if;
  select count(*) as total,count(*) filter(where demand_known) as assessed,
    count(*) filter(where release_count>0) as recommended,coalesce(sum(release_count),0) as fish_count
    into s from private.area_cage_facts(p_area_id,p_config_revision);
  if s.fish_count>9007199254740991 then
    raise exception 'Aggregate exceeds exact JSON integer range' using errcode='22003';
  end if;
  return jsonb_build_object('area_id',p_area_id,'ts',now(),'config_revision',p_config_revision,
    'basis','recommended_not_chosen','cage_count',s.total,'assessed_cage_count',s.assessed,
    'recommended_cage_count',s.recommended,'unavailable_cage_count',s.total-s.assessed,
    'estimated_fish_count',case when s.total>0 and s.assessed=0 then null else s.fish_count end,
    'data_status',case when s.assessed=s.total then 'complete' when s.assessed>0 then 'partial' else 'unavailable' end);
end;
$$;

create function public.area_stress_map(p_area_ids bigint[],p_config_revision text) returns jsonb
language sql stable security invoker set search_path='' as $$
  select private.area_stress_map(p_area_ids,p_config_revision);
$$;
create function public.area_release_demand(p_area_id bigint,p_config_revision text) returns jsonb
language sql stable security invoker set search_path='' as $$
  select private.area_release_demand(p_area_id,p_config_revision);
$$;
revoke all on function private.area_cage_facts(bigint,text),private.area_stress_map(bigint[],text),
  private.area_release_demand(bigint,text),public.area_stress_map(bigint[],text),public.area_release_demand(bigint,text)
  from public,anon,authenticated;
grant execute on function private.area_stress_map(bigint[],text),private.area_release_demand(bigint,text),
  public.area_stress_map(bigint[],text),public.area_release_demand(bigint,text) to authenticated;
commit;
