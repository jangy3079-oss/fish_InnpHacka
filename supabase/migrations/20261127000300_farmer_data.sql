-- #29 phase 1. No dependency on the #10 edge ingestion migration.
begin;

create function private.replace_cage_stock(p_cage_id bigint, p_stock jsonb, p_insured boolean)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare item jsonb; owner_id uuid; result jsonb;
begin
  -- Lock only a caller-owned cage; serialize concurrent full replacements.
  select f.owner_user_id into owner_id from public.cage c join public.farm f on f.id=c.farm_id
    where c.id=p_cage_id and f.owner_user_id=auth.uid() for update of c;
  if owner_id is null then raise insufficient_privilege using message='Stock update not permitted'; end if;
  if p_insured is null or p_stock is null or jsonb_typeof(p_stock) <> 'array' then
    raise invalid_parameter_value using message='Invalid stock payload';
  end if;
  if jsonb_array_length(p_stock) not between 1 and 3 then
    raise invalid_parameter_value using message='Supply one to three stock groups';
  end if;
  for item in select value from jsonb_array_elements(p_stock) loop
    if jsonb_typeof(item) <> 'object' then raise invalid_parameter_value using message='Invalid stock item'; end if;
    if (select count(*) from jsonb_object_keys(item)) <> 3
      or not item ?& array['size_group','count','avg_weight_kg']
      or jsonb_typeof(item->'size_group') <> 'string'
      or item->>'size_group' not in ('fry','mid','market')
      or jsonb_typeof(item->'count') <> 'number'
      or jsonb_typeof(item->'avg_weight_kg') <> 'number' then
      raise invalid_parameter_value using message='Invalid stock item fields';
    end if;
    if (item->>'count')::numeric not between 0 and 9223372036854775807
      or trunc((item->>'count')::numeric) <> (item->>'count')::numeric
      or (item->>'avg_weight_kg')::numeric <= 0 then
      raise invalid_parameter_value using message='Invalid stock quantities';
    end if;
  end loop;
  if (select count(distinct value->>'size_group') from jsonb_array_elements(p_stock)) <> jsonb_array_length(p_stock) then
    raise invalid_parameter_value using message='Duplicate stock groups';
  end if;
  -- PUT replaces the complete set. Omitted groups are removed, never left stale.
  delete from public.stock s where s.cage_id=p_cage_id
    and s.size_group not in (select value->>'size_group' from jsonb_array_elements(p_stock));
  insert into public.stock(cage_id,size_group,count,avg_weight_kg,insured)
    select p_cage_id, value->>'size_group', (value->>'count')::bigint,
      (value->>'avg_weight_kg')::numeric, p_insured from jsonb_array_elements(p_stock)
    on conflict(cage_id,size_group) do update set count=excluded.count,
      avg_weight_kg=excluded.avg_weight_kg, insured=excluded.insured;
  select jsonb_build_object('persisted',true,'cage_id',p_cage_id,'received_count',jsonb_array_length(p_stock),
    'insured',p_insured,'stock',coalesce(jsonb_agg(jsonb_build_object('size_group',s.size_group,
      'count',s.count,'avg_weight_kg',s.avg_weight_kg,'updated_at',s.updated_at) order by s.size_group),'[]'::jsonb))
    into result from public.stock s where s.cage_id=p_cage_id;
  return result;
end;
$$;
revoke all on function private.replace_cage_stock(bigint,jsonb,boolean) from public,anon,authenticated;
grant execute on function private.replace_cage_stock(bigint,jsonb,boolean) to authenticated;

-- Exposed wrapper is security invoker; only the private, owner-checked operation elevates.
create function public.replace_cage_stock(p_cage_id bigint, p_stock jsonb, p_insured boolean)
returns jsonb language sql security invoker set search_path = '' as $$
  select private.replace_cage_stock(p_cage_id,p_stock,p_insured);
$$;
revoke all on function public.replace_cage_stock(bigint,jsonb,boolean) from public,anon,authenticated;
grant execute on function public.replace_cage_stock(bigint,jsonb,boolean) to authenticated;

create function public.cage_metric_series(p_cage_id bigint, p_metric text,
  p_from timestamptz default null, p_to timestamptz default null,
  p_source text default null, p_limit integer default 1000, p_before timestamptz default null)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare result jsonb;
begin
  if p_metric is null or p_metric not in ('vertical_dist','activity','cohesion','feeding','still_ratio',
    'rubbing_rate','quality','temp_surface','temp_mid','do')
    or (p_source is not null and p_source not in ('sonar','camera','env'))
    or p_limit is null or p_limit not between 1 and 1000
    or (p_from is not null and not isfinite(p_from)) or (p_to is not null and not isfinite(p_to))
    or (p_before is not null and not isfinite(p_before)) or p_from > p_to then
    raise invalid_parameter_value using message='Invalid series query';
  end if;
  -- Metric RLS remains active; no aggregate/lookup runs as service_role.
  with ranked as (
    select m.ts,m.value,m.source,m.confidence,
      row_number() over(partition by m.ts order by case
        when p_metric in ('vertical_dist','activity','cohesion') then
          case m.source when 'sonar' then 0 when 'camera' then 1 else 2 end
        when p_metric in ('temp_surface','temp_mid','do') then
          case m.source when 'env' then 0 else 1 end
        else case m.source when 'camera' then 0 else 1 end end, m.source) as priority
    from public.metric m where m.cage_id=p_cage_id and m.metric=p_metric
      and (p_from is null or m.ts>=p_from) and (p_to is null or m.ts<=p_to)
      and (p_before is null or m.ts<p_before) and (p_source is null or m.source=p_source)
  ), recent as (
    select * from ranked where priority=1 order by ts desc limit p_limit+1
  ), page as (
    select * from recent order by ts desc limit p_limit
  )
  select jsonb_build_object('cage_id',p_cage_id,'metric',p_metric,
    'points',coalesce((select jsonb_agg(jsonb_build_object('ts',ts,'value',value,'source',source,
      'confidence',confidence) order by ts) from page),'[]'::jsonb),
    'has_more',(select count(*)>p_limit from recent),
    'next_before',case when (select count(*)>p_limit from recent) then (select min(ts) from page) else null end)
  into result;
  return result;
end;
$$;
revoke all on function public.cage_metric_series(bigint,text,timestamptz,timestamptz,text,integer,timestamptz)
  from public,anon,authenticated;
grant execute on function public.cage_metric_series(bigint,text,timestamptz,timestamptz,text,integer,timestamptz)
  to authenticated;

comment on function public.replace_cage_stock(bigint,jsonb,boolean) is
  'Owner-only atomic full stock replacement. All omitted size groups are removed.';
comment on function public.cage_metric_series(bigint,text,timestamptz,timestamptz,text,integer,timestamptz) is
  'Raw sensor observations with deterministic source preference; not fused features. Newest page, ascending points.';
commit;
