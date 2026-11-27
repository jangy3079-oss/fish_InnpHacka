-- #45: R4 적조 특보·R6 작업 기록 입력. 두 테이블은 private에 두고 RPC로만 다룬다.
-- 분석 입력(analysis_input_core)에 포함되므로 input_version이 기록 변경을 함께 잡는다.
begin;

create table private.work_log (
  id bigint generated always as identity primary key,
  cage_id bigint not null references public.cage(id),
  kind text not null check (kind in ('net_change','sorting')),
  performed_at timestamptz not null check (isfinite(performed_at)),
  created_by uuid not null references auth.users(id),
  request_key uuid not null,
  created_at timestamptz not null default now(),
  unique (created_by, request_key)
);
create index work_log_cage_time_idx on private.work_log(cage_id, performed_at);

-- 수과원 적조 특보 단계: watch=예비주의보, advisory=주의보, warning=경보. 해제는 ends_at으로 기록한다.
create table private.red_tide_advisory (
  id bigint generated always as identity primary key,
  sea_area_id bigint not null references public.sea_area(id),
  stage text not null check (stage in ('watch','advisory','warning')),
  starts_at timestamptz not null check (isfinite(starts_at)),
  ends_at timestamptz check (ends_at is null or (isfinite(ends_at) and ends_at > starts_at)),
  source text not null check (length(btrim(source)) > 0),
  source_ref text not null check (length(btrim(source_ref)) > 0),
  created_at timestamptz not null default now(),
  unique (sea_area_id, stage, starts_at)
);
create index red_tide_area_time_idx on private.red_tide_advisory(sea_area_id, starts_at);

alter table private.work_log enable row level security;
alter table private.red_tide_advisory enable row level security;
revoke all on private.work_log, private.red_tide_advisory from public, anon, authenticated;
revoke all on sequence private.work_log_id_seq, private.red_tide_advisory_id_seq from public, anon, authenticated;
grant select on private.work_log, private.red_tide_advisory to service_role;

create function private.work_log_receipt(r private.work_log) returns jsonb language sql stable set search_path = '' as $$
  select jsonb_build_object('persisted', true, 'work_log_id', r.id, 'cage_id', r.cage_id, 'kind', r.kind,
    'performed_at', r.performed_at, 'created_at', r.created_at);
$$;

-- 소유자만 기록한다. 같은 request_key 재전송은 같은 영수증, 내용이 다르면 409.
create function private.record_work_log(p_cage_id bigint, p_kind text, p_performed_at timestamptz, p_request_key uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_row private.work_log;
begin
  perform 1 from public.cage c join public.farm f on f.id = c.farm_id
    where c.id = p_cage_id and f.owner_user_id = auth.uid() for update of c;
  if not found then raise insufficient_privilege using message = 'Work log not permitted'; end if;
  if p_request_key is null or p_kind is null or p_kind not in ('net_change','sorting')
    or p_performed_at is null or not isfinite(p_performed_at)
    or p_performed_at > now() + interval '5 minutes' or p_performed_at < now() - interval '7 days' then
    raise invalid_parameter_value using message = 'Invalid work log';
  end if;
  select * into v_row from private.work_log where created_by = auth.uid() and request_key = p_request_key;
  if found then
    if v_row.cage_id <> p_cage_id or v_row.kind <> p_kind or v_row.performed_at <> p_performed_at then
      raise exception 'Work log key conflict' using errcode = '40001';
    end if;
    return private.work_log_receipt(v_row);
  end if;
  insert into private.work_log(cage_id, kind, performed_at, created_by, request_key)
    values (p_cage_id, p_kind, p_performed_at, auth.uid(), p_request_key) returning * into v_row;
  return private.work_log_receipt(v_row);
end;
$$;

-- 운영자(service_role) 전용: 특보 자료를 한 번에 넣는다. 같은 (해역, 단계, 시작)은 해제 시각만 갱신한다.
create function private.load_red_tide_advisories(p_rows jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare item jsonb; v_count integer = 0;
begin
  if coalesce(jsonb_typeof(p_rows), '') <> 'array' or jsonb_array_length(p_rows) not between 1 and 1000 then
    raise invalid_parameter_value using message = 'Supply 1 to 1000 advisories';
  end if;
  for item in select value from jsonb_array_elements(p_rows) loop
    if jsonb_typeof(item) <> 'object'
      or not item ?& array['sea_area_id','stage','starts_at','source','source_ref']
      or exists (select 1 from jsonb_object_keys(item) k where k not in ('sea_area_id','stage','starts_at','ends_at','source','source_ref')) then
      raise invalid_parameter_value using message = 'Invalid advisory fields';
    end if;
    insert into private.red_tide_advisory(sea_area_id, stage, starts_at, ends_at, source, source_ref)
      values ((item->>'sea_area_id')::bigint, item->>'stage', (item->>'starts_at')::timestamptz,
              nullif(item->>'ends_at', '')::timestamptz, item->>'source', item->>'source_ref')
      on conflict (sea_area_id, stage, starts_at) do update
        set ends_at = excluded.ends_at, source = excluded.source, source_ref = excluded.source_ref;
    v_count = v_count + 1;
  end loop;
  return jsonb_build_object('persisted', true, 'received_count', v_count);
end;
$$;

-- 분석 입력에 작업 기록(창 시작 72시간 전부터)과 해역 적조 특보(창과 겹치는 것)를 더한다.
create or replace function private.analysis_input_core(p_cage_id bigint,p_from timestamptz,p_to timestamptz,p_limit integer)
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
    'stock_version',private.stock_version(c.id),'metrics',v_metrics,'metrics_truncated',v_truncated,'from',p_from,'to',p_to,
    'work_logs',coalesce((select jsonb_agg(jsonb_build_object('kind',w.kind,'performed_at',w.performed_at) order by w.performed_at,w.id)
      from private.work_log w where w.cage_id=c.id and w.performed_at>=p_from-interval '72 hours' and w.performed_at<p_to),'[]'::jsonb),
    'red_tide_advisories',coalesce((select jsonb_agg(jsonb_build_object('stage',a.stage,'starts_at',a.starts_at,'ends_at',a.ends_at,
      'source_ref',a.source_ref) order by a.starts_at,a.stage) from private.red_tide_advisory a
      where a.sea_area_id=f.sea_area_id and a.starts_at<p_to and (a.ends_at is null or a.ends_at>p_from)),'[]'::jsonb))
    into v from public.cage c join public.farm f on f.id=c.farm_id where c.id=p_cage_id;
  if v is null then raise exception 'Cage not found' using errcode='22023'; end if;
  return v;
end;
$$;

create function public.record_work_log(p_cage_id bigint, p_kind text, p_performed_at timestamptz, p_request_key uuid)
returns jsonb language sql security invoker set search_path = '' as $$
  select private.record_work_log(p_cage_id, p_kind, p_performed_at, p_request_key);
$$;
create function public.load_red_tide_advisories(p_rows jsonb) returns jsonb
language sql security invoker set search_path = '' as $$ select private.load_red_tide_advisories(p_rows); $$;

revoke all on function private.work_log_receipt(private.work_log),
  private.record_work_log(bigint,text,timestamptz,uuid), private.load_red_tide_advisories(jsonb),
  public.record_work_log(bigint,text,timestamptz,uuid), public.load_red_tide_advisories(jsonb)
  from public, anon, authenticated;
grant execute on function private.record_work_log(bigint,text,timestamptz,uuid),
  public.record_work_log(bigint,text,timestamptz,uuid) to authenticated;
grant execute on function private.load_red_tide_advisories(jsonb), public.load_red_tide_advisories(jsonb) to service_role;

comment on function public.record_work_log(bigint,text,timestamptz,uuid) is
  '소유자의 망갈이·선별 작업 기록 (#45, 원인 규칙 R6 입력). request_key로 재전송 안전';
comment on function public.load_red_tide_advisories(jsonb) is
  '운영자 전용 적조 특보 적재 (#45, 원인 규칙 R4 입력). 같은 해역·단계·시작은 해제 시각만 갱신';
commit;
