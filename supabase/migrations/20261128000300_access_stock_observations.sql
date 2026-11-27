begin;
-- Existing demo polygons are not verified real-world boundaries.
alter table public.sea_area add column geometry_source text;
alter table public.sea_area add column geometry_verified boolean not null default false;
alter table public.sea_area add constraint verified_geometry_source
  check(not geometry_verified or coalesce(length(btrim(geometry_source))>0,false));

create function private.user_access(p_area_ids bigint[]) returns jsonb
language plpgsql stable security definer set search_path='' as $$
begin
  if auth.uid() is null then raise insufficient_privilege; end if;
  return jsonb_build_object('user_id',auth.uid(),'area_ids',coalesce((select jsonb_agg(a.id order by a.id)
    from public.sea_area a where (a.id=any(coalesce(p_area_ids,'{}')) and private.official_in_area(a.id))
      or exists(select 1 from public.farm f join public.cage c on c.farm_id=f.id where f.sea_area_id=a.id and f.owner_user_id=auth.uid())),'[]'::jsonb),
    'cages',coalesce((select jsonb_agg(jsonb_build_object('id',c.id,'farm_id',f.id,'sea_area_id',f.sea_area_id,
      'name',f.name||' · 가두리 '||c.id,'can_write_stock',f.owner_user_id=auth.uid(),
      'can_read_financial',private.read_cage(c.id,true)) order by c.id)
      from public.cage c join public.farm f on f.id=c.farm_id where f.owner_user_id=auth.uid()
      or (f.sea_area_id=any(coalesce(p_area_ids,'{}')) and private.official_in_area(f.sea_area_id))),'[]'::jsonb));
end;
$$;
create function public.user_access(p_area_ids bigint[] default '{}') returns jsonb
language sql stable security invoker set search_path='' as $$ select private.user_access(p_area_ids); $$;

create function private.cage_stock(p_cage_id bigint) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare rows jsonb; kinds integer; common_value boolean;
begin
  if auth.uid() is null or not private.read_cage(p_cage_id,true) then raise insufficient_privilege; end if;
  select coalesce(jsonb_agg(jsonb_build_object('size_group',s.size_group,'count',s.count,
      'avg_weight_kg',s.avg_weight_kg,'insured',s.insured,'updated_at',s.updated_at) order by s.size_group),'[]'::jsonb),
      count(distinct s.insured),bool_and(s.insured) into rows,kinds,common_value
    from public.stock s where s.cage_id=p_cage_id;
  return jsonb_build_object('cage_id',p_cage_id,'stock',rows,'insured',case when kinds=1 then common_value else null end,'insured_mixed',kinds>1);
end;
$$;
create function public.cage_stock(p_cage_id bigint) returns jsonb
language sql stable security invoker set search_path='' as $$ select private.cage_stock(p_cage_id); $$;

create function private.area_shapes(p_area_ids bigint[]) returns jsonb
language plpgsql stable security definer set search_path='' as $$
begin
  if auth.uid() is null then raise insufficient_privilege; end if;
  return jsonb_build_object('type','FeatureCollection','features',coalesce((select jsonb_agg(jsonb_build_object(
    'type','Feature','geometry',a.geom,'properties',jsonb_build_object('area_id',a.id,'name',a.name,
      'source',a.geometry_source,'station_id',a.nifs_station_id)) order by a.id)
    from public.sea_area a where a.id=any(coalesce(p_area_ids,'{}')) and a.geometry_verified
      and (private.official_in_area(a.id) or exists(select 1 from public.farm f where f.sea_area_id=a.id and f.owner_user_id=auth.uid()))),'[]'::jsonb),
    'unverified_area_ids',coalesce((select jsonb_agg(a.id order by a.id) from public.sea_area a
      where a.id=any(coalesce(p_area_ids,'{}')) and not a.geometry_verified
        and (private.official_in_area(a.id) or exists(select 1 from public.farm f where f.sea_area_id=a.id and f.owner_user_id=auth.uid()))),'[]'::jsonb));
end;
$$;
create function public.area_shapes(p_area_ids bigint[]) returns jsonb
language sql stable security invoker set search_path='' as $$ select private.area_shapes(p_area_ids); $$;

create function private.area_cause_distribution(p_area_ids bigint[],p_config_revision text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb;
begin
  if auth.uid() is null or auth.jwt()->'app_metadata'->>'role' is distinct from 'official' then raise insufficient_privilege; end if;
  with latest as (
    select distinct on (a.cage_id) a.* from private.analysis_run a join public.cage c on c.id=a.cage_id
      join public.farm f on f.id=c.farm_id where f.sea_area_id=any(coalesce(p_area_ids,'{}'))
      and private.official_in_area(f.sea_area_id) order by a.cage_id,a.ts desc
  ), top_causes as (
    select picked.rule_id,picked.name from latest a cross join lateral (
      select value->>'rule_id' rule_id,value->>'name' name
      from jsonb_array_elements(coalesce(a.status->'cause','[]'::jsonb)) with ordinality
      where coalesce((value->>'display')::boolean,true)
      order by (value->>'probability')::numeric desc,ordinality limit 1
    ) picked where a.config_revision=p_config_revision and a.valid_until>=now() and a.ts<=now()
      and a.status->>'data_status'='ready' and a.status->>'level' in ('주의','경고','위험')
  ), counts as (select rule_id,name,count(*) n from top_causes group by rule_id,name)
  select coalesce(jsonb_agg(jsonb_build_object('rule_id',rule_id,'name',name,'count',n) order by rule_id),'[]'::jsonb) into result from counts;
  return jsonb_build_object('ts',now(),'config_revision',p_config_revision,'cause_distribution',result);
end;
$$;
create function public.area_cause_distribution(p_area_ids bigint[],p_config_revision text) returns jsonb
language sql stable security invoker set search_path='' as $$ select private.area_cause_distribution(p_area_ids,p_config_revision); $$;

-- Preserve visibility at publish time without changing the #35 aggregation code.
create function private.analysis_observation_metadata() returns trigger
language plpgsql set search_path='' as $$
declare causes jsonb; seen jsonb;
begin
  select coalesce(jsonb_agg(item.value||jsonb_build_object(
      'display',coalesce((c.evidence->>'display')::boolean,(item.value->>'display')::boolean,true),
      'cause_source',new.status->'cause_source') order by item.ordinality),'[]'::jsonb)
    into causes from jsonb_array_elements(coalesce(new.status->'cause','[]'::jsonb)) with ordinality item
      left join public.cause_estimate c on c.cage_id=new.cage_id and c.ts=new.ts and c.rule_id=item.value->>'rule_id';
  select jsonb_build_object('sonar',max(m.ts) filter(where m.source='sonar'),
    'camera',max(m.ts) filter(where m.source='camera'),'env',max(m.ts) filter(where m.source='env')) into seen
    from public.metric m where m.cage_id=new.cage_id and m.ts<new.ts;
  new.status=jsonb_set(new.status,'{cause}',causes)||jsonb_build_object('last_seen',seen);
  return new;
end;
$$;
revoke all on function private.analysis_observation_metadata() from public,anon,authenticated;
create trigger analysis_observation_metadata before insert on private.analysis_run
  for each row execute function private.analysis_observation_metadata();

revoke all on function private.user_access(bigint[]),public.user_access(bigint[]),private.cage_stock(bigint),public.cage_stock(bigint),
  private.area_shapes(bigint[]),public.area_shapes(bigint[]),private.area_cause_distribution(bigint[],text),public.area_cause_distribution(bigint[],text)
  from public,anon,authenticated;
grant execute on function private.user_access(bigint[]),public.user_access(bigint[]),private.cage_stock(bigint),public.cage_stock(bigint),
  private.area_shapes(bigint[]),public.area_shapes(bigint[]),private.area_cause_distribution(bigint[],text),public.area_cause_distribution(bigint[],text) to authenticated;
commit;
