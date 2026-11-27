-- #198: narrow edge-only operation; observations are never overwritten.
begin;
create function private.ingest_metrics(p_records jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare item jsonb; n integer; added integer := 0; affected integer;
  old_value jsonb; old_confidence numeric;
begin
  if auth.uid() is null or auth.jwt()->'app_metadata'->>'role' is distinct from 'edge' then
    raise insufficient_privilege using message='Edge ingestion required';
  end if;
  if p_records is null or jsonb_typeof(p_records)<>'array' then
    raise invalid_parameter_value using message='Invalid metric batch';
  end if;
  n := jsonb_array_length(p_records);
  if n not between 1 and 1000 then
    raise invalid_parameter_value using message='Invalid metric batch size';
  end if;
  for item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(item)<>'object' then
      raise invalid_parameter_value using message='Invalid metric record';
    end if;
    if (select count(*) from jsonb_object_keys(item))<>6
      or not item ?& array['cage_id','ts','source','metric','value','confidence']
      or jsonb_typeof(item->'cage_id')<>'number'
      or jsonb_typeof(item->'ts')<>'string'
      or jsonb_typeof(item->'source')<>'string'
      or jsonb_typeof(item->'metric')<>'string'
      or jsonb_typeof(item->'confidence')<>'number' then
      raise invalid_parameter_value using message='Invalid metric record fields';
    end if;
    if (item->>'cage_id')::numeric not between 1 and 9223372036854775807
      or trunc((item->>'cage_id')::numeric)<>(item->>'cage_id')::numeric
      or (item->>'ts')!~'(Z|[+-][0-9]{2}:[0-9]{2})$' then
      raise invalid_parameter_value using message='Invalid metric identity';
    end if;
    if not private.can_ingest_cage((item->>'cage_id')::bigint) then
      raise insufficient_privilege using message='Cage ingestion not permitted';
    end if;
  end loop;
  if (select count(*) from (select distinct (value->>'cage_id')::bigint,
      (value->>'ts')::timestamptz,value->>'source',value->>'metric'
      from jsonb_array_elements(p_records)) identities)<>n then
    raise invalid_parameter_value using message='Duplicate identity within batch';
  end if;
  -- Every caller visits keys in the same order to avoid reversed-batch deadlocks.
  -- A conflicting unique insert waits for its concurrent writer. At READ COMMITTED
  -- the following SELECT gets a fresh snapshot of that committed row.
  for item in select value from jsonb_array_elements(p_records)
      order by (value->>'cage_id')::bigint,(value->>'ts')::timestamptz,
               value->>'source',value->>'metric' loop
    insert into public.metric(cage_id,ts,source,metric,value,confidence)
      values((item->>'cage_id')::bigint,(item->>'ts')::timestamptz,
        item->>'source',item->>'metric',item->'value',(item->>'confidence')::numeric)
      on conflict(cage_id,ts,source,metric) do nothing;
    get diagnostics affected = row_count;
    if affected=1 then
      added := added+1;
    else
      select m.value,m.confidence into old_value,old_confidence from public.metric m
        where m.cage_id=(item->>'cage_id')::bigint and m.ts=(item->>'ts')::timestamptz
          and m.source=item->>'source' and m.metric=item->>'metric';
      if not found then
        raise serialization_failure using message='Concurrent metric change; retry original batch';
      end if;
      if old_value is distinct from item->'value' or old_confidence is distinct from (item->>'confidence')::numeric then
        raise unique_violation using message='Metric content conflict; batch rejected';
      end if;
    end if;
  end loop;
  return jsonb_build_object('persisted',true,'received_count',n,
    'inserted_count',added,'duplicate_count',n-added);
end;
$$;
revoke all on function private.ingest_metrics(jsonb) from public,anon,authenticated;
grant execute on function private.ingest_metrics(jsonb) to authenticated;
create function public.ingest_metrics(p_records jsonb) returns jsonb
language sql security invoker set search_path = '' as $$
  select private.ingest_metrics(p_records);
$$;
revoke all on function public.ingest_metrics(jsonb) from public,anon,authenticated;
grant execute on function public.ingest_metrics(jsonb) to authenticated;
comment on function public.ingest_metrics(jsonb) is
  'Edge allow-list only. Same value/confidence succeeds as duplicate; content conflict rolls back batch. No read/update grant.';
commit;
