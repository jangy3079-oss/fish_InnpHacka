-- #44: retain the existing public recommendation.deadline column and immutable snapshot metadata (after #45/007).
-- No new table, role, broad grant, or existing RPC identity change.
create function private.sync_recommendation_deadline() returns trigger
language plpgsql set search_path='' as $$
declare e jsonb; stamp timestamptz; first_time timestamptz; seen text[]='{}';
begin
  if not (new.snapshot ? 'deadlines') then
    return new; -- Legacy rows have no policy metadata; do not invent a deadline on upgrade.
  end if;
  if jsonb_typeof(new.snapshot->'deadlines') is distinct from 'array'
      or jsonb_array_length(new.snapshot->'deadlines') not between 1 and 3
      or not(new.snapshot ? 'deadline') then
    raise exception 'Invalid deadline metadata' using errcode='22023';
  end if;
  for e in select value from jsonb_array_elements(new.snapshot->'deadlines') loop
    if not(e ?& array['kind','label','action_id','applicable','at','source_status','basis','source'])
      or coalesce(e->>'kind','') not in ('next_assessment','release_application','harvest')
      or e->>'kind'=any(seen) or jsonb_typeof(e->'label') is distinct from 'string'
      or jsonb_typeof(e->'source') not in ('null','string')
      or jsonb_typeof(e->'applicable') is distinct from 'boolean'
      or coalesce(e->>'source_status','') not in ('pending','assumed','confirmed')
      or coalesce(e->>'basis','') not in ('relative','absolute','unconfirmed') then
      raise exception 'Invalid deadline entry' using errcode='22023';
    end if;
    seen=array_append(seen,e->>'kind');
    if (e->>'kind'='next_assessment' and (e->'action_id'<>'null'::jsonb or e->'applicable'<>'true'::jsonb))
      or (e->>'kind'='release_application' and (e->>'action_id' is distinct from 'release'))
      or (e->>'kind'='harvest' and (e->>'action_id' is distinct from 'harvest')) then
      raise exception 'Invalid deadline action' using errcode='22023';
    end if;
    if e->>'kind'<>'next_assessment' and not exists(
        select 1 from jsonb_array_elements(new.options) o
        where o->>'id'=e->>'action_id' and o->'available'=e->'applicable') then
      raise exception 'Deadline option mismatch' using errcode='22023';
    end if;
    if (e->'at'='null'::jsonb) is distinct from (e->>'source_status'='pending')
      or (e->'at'='null'::jsonb) is distinct from (e->>'basis'='unconfirmed')
      or (e->>'source_status'='confirmed' and (jsonb_typeof(e->'source') is distinct from 'string' or length(e->>'source')=0)) then
      raise exception 'Invalid deadline source state' using errcode='22023';
    end if;
    if e->'at'<>'null'::jsonb then
      if jsonb_typeof(e->'at') is distinct from 'string' or (e->>'at')!~'(Z|[+-][0-9]{2}:[0-9]{2})$' then
        raise exception 'Aware deadline required' using errcode='22023';
      end if;
      stamp=(e->>'at')::timestamptz;
      if not isfinite(stamp) then raise exception 'Finite deadline required' using errcode='22023'; end if;
      if e->'applicable'='true'::jsonb then first_time=least(first_time,stamp); end if;
    end if;
  end loop;
  if not ('next_assessment'=any(seen)) then
    raise exception 'Next assessment is required' using errcode='22023';
  end if;
  if new.snapshot->'deadline'<>'null'::jsonb and (jsonb_typeof(new.snapshot->'deadline') is distinct from 'string'
      or (new.snapshot->>'deadline')!~'(Z|[+-][0-9]{2}:[0-9]{2})$') then
    raise exception 'Aware primary deadline required' using errcode='22023';
  end if;
  if first_time is distinct from (new.snapshot->>'deadline')::timestamptz then
    raise exception 'Primary deadline mismatch' using errcode='22023';
  end if;
  new.deadline=first_time;
  return new;
end;
$$;
revoke all on function private.sync_recommendation_deadline() from public,anon,authenticated;
create trigger recommendation_deadline_snapshot
  before insert or update of snapshot,deadline on public.recommendation
  for each row execute function private.sync_recommendation_deadline();
