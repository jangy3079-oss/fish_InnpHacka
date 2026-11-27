-- SDD 7: ten public domain tables. Apply with the Supabase migration owner.
begin;

create schema if not exists private;
revoke all on schema private from public;

create table public.sea_area (
  id bigint generated always as identity primary key,
  name text not null check (length(btrim(name)) > 0),
  geom jsonb not null check (coalesce((
    jsonb_typeof(geom) = 'object' and geom->>'type' = 'Polygon'
    and jsonb_typeof(geom->'coordinates') = 'array'
    and jsonb_array_length(geom->'coordinates') > 0
  ), false)),
  nifs_station_id text
);

create table public.farm (
  id bigint generated always as identity primary key,
  sea_area_id bigint not null references public.sea_area(id),
  owner_user_id uuid not null references auth.users(id),
  name text not null check (length(btrim(name)) > 0),
  location jsonb not null check (jsonb_typeof(location) = 'object'),
  created_at timestamptz not null default now()
);
create index farm_owner_idx on public.farm(owner_user_id);
create index farm_area_idx on public.farm(sea_area_id);

create table public.cage (
  id bigint generated always as identity primary key,
  farm_id bigint not null references public.farm(id),
  is_sentinel boolean not null default false,
  depth_m numeric not null check (depth_m > 0 and depth_m < 'Infinity'::numeric),
  net_depth_m numeric not null check (net_depth_m > 0 and net_depth_m <= depth_m)
);
create index cage_farm_idx on public.cage(farm_id);

create table public.stock (
  id bigint generated always as identity primary key,
  cage_id bigint not null references public.cage(id),
  size_group text not null check (size_group in ('fry', 'mid', 'market')),
  count bigint not null check (count >= 0),
  avg_weight_kg numeric not null check (avg_weight_kg > 0 and avg_weight_kg < 'Infinity'::numeric),
  insured boolean not null default false,
  updated_at timestamptz not null default now(),
  unique(cage_id, size_group)
);

create table public.sensor (
  id bigint generated always as identity primary key,
  cage_id bigint not null references public.cage(id),
  type text not null check (type in ('sonar', 'camera', 'env')),
  model text not null,
  status text not null default 'unknown' check (status in ('online', 'offline', 'unknown')),
  last_seen timestamptz
);
create index sensor_cage_idx on public.sensor(cage_id);

create function private.valid_metric_value(metric_name text, payload jsonb)
returns boolean language plpgsql immutable set search_path = '' as $$
declare item jsonb; total numeric := 0;
begin
  if metric_name = 'vertical_dist' then
    if jsonb_typeof(payload) is distinct from 'array' then return false; end if;
    if jsonb_array_length(payload) = 0 then return false; end if;
    for item in select value from jsonb_array_elements(payload) loop
      if jsonb_typeof(item) <> 'number' then return false; end if;
      if item::text::numeric < 0 or item::text::numeric > 1 then return false; end if;
      total := total + item::text::numeric;
    end loop;
    return abs(total - 1) <= 0.000001;
  end if;
  if jsonb_typeof(payload) is distinct from 'number' then return false; end if;
  if metric_name in ('quality', 'still_ratio') then
    return payload::text::numeric between 0 and 1;
  end if;
  return true;
end;
$$;

create table public.metric (
  cage_id bigint not null references public.cage(id),
  ts timestamptz not null check (isfinite(ts)),
  source text not null check (source in ('sonar', 'camera', 'env')),
  metric text not null check (metric in ('vertical_dist','activity','cohesion','feeding',
    'still_ratio','rubbing_rate','quality','temp_surface','temp_mid','do')),
  value jsonb not null check (private.valid_metric_value(metric, value)),
  confidence numeric not null check (confidence between 0 and 1),
  primary key(cage_id, ts, source, metric)
);
create index metric_cage_ts_idx on public.metric(cage_id, ts);

create table public.stress_score (
  cage_id bigint not null references public.cage(id),
  ts timestamptz not null check (isfinite(ts)),
  score numeric check (score between 0 and 100),
  level text check (level in ('정상', '주의', '경고', '위험')),
  baseline_status text not null check (baseline_status in ('ready', 'warming_up')),
  model_version text not null check (length(btrim(model_version)) > 0),
  evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(evidence) = 'object'),
  missing_sources text[] not null default '{}' check (missing_sources <@ array['sonar','camera','env']::text[]),
  camera_weight numeric not null default 0 check (camera_weight between 0 and 0.5),
  primary key(cage_id, ts),
  check (baseline_status <> 'ready' or (score is not null and level is not null))
);

create table public.cause_estimate (
  id bigint generated always as identity primary key,
  cage_id bigint not null references public.cage(id),
  ts timestamptz not null check (isfinite(ts)),
  cause text not null,
  probability numeric not null check (probability between 0 and 1),
  rule_id text not null check (rule_id ~ '^R[1-8]$'),
  evidence jsonb not null check (jsonb_typeof(evidence) = 'object')
);
create index cause_cage_ts_idx on public.cause_estimate(cage_id, ts);

create table public.recommendation (
  id bigint generated always as identity primary key,
  cause_estimate_id bigint not null references public.cause_estimate(id),
  options jsonb not null check (jsonb_typeof(options) = 'array'),
  deadline timestamptz check (isfinite(deadline)),
  chosen_option text,
  outcome jsonb check (jsonb_typeof(outcome) = 'object'),
  created_at timestamptz not null default now()
);
create index recommendation_cause_idx on public.recommendation(cause_estimate_id);

create table public.alert (
  id bigint generated always as identity primary key,
  recommendation_id bigint not null references public.recommendation(id),
  user_id uuid not null references auth.users(id),
  channel text not null check (channel in ('web', 'mock', 'messenger')),
  message text not null,
  sent_at timestamptz not null default now(),
  read_at timestamptz
);
create index alert_rec_idx on public.alert(recommendation_id);
create index alert_user_idx on public.alert(user_id);

create function private.touch_stock() returns trigger
language plpgsql set search_path = '' as $$
begin new.updated_at := now(); return new; end;
$$;
create trigger stock_updated_at before update on public.stock
for each row execute function private.touch_stock();

-- Authorization data is read from protected app_metadata, never user_metadata.
create function private.official_in_area(area_id bigint) returns boolean
language sql stable set search_path = '' as $$
  select auth.uid() is not null
    and coalesce(auth.jwt()->'app_metadata'->>'role' = 'official', false)
    and coalesce(auth.jwt()->'app_metadata'->'area_ids' @> jsonb_build_array(area_id), false);
$$;

create function private.owns_farm(farm_id bigint) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.farm f where f.id = farm_id and f.owner_user_id = auth.uid());
$$;

create function private.read_farm(farm_id bigint, financial boolean default false) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.farm f where f.id = farm_id and (
    f.owner_user_id = auth.uid() or (
      private.official_in_area(f.sea_area_id) and (
        not financial or coalesce(auth.jwt()->'app_metadata'->'financial_farm_ids' @> jsonb_build_array(f.id), false)
      )
    )
  ));
$$;

create function private.read_cage(cage_id bigint, financial boolean default false) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.cage c where c.id = cage_id and private.read_farm(c.farm_id, financial));
$$;

create function private.owns_cage(cage_id bigint) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.cage c where c.id = cage_id and private.owns_farm(c.farm_id));
$$;

create function private.read_recommendation(recommendation_id bigint) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.recommendation r join public.cause_estimate c on c.id = r.cause_estimate_id
    where r.id = recommendation_id and private.read_cage(c.cage_id, true));
$$;

alter table public.sea_area enable row level security;
alter table public.farm enable row level security;
alter table public.cage enable row level security;
alter table public.stock enable row level security;
alter table public.sensor enable row level security;
alter table public.metric enable row level security;
alter table public.stress_score enable row level security;
alter table public.cause_estimate enable row level security;
alter table public.recommendation enable row level security;
alter table public.alert enable row level security;

create policy sea_area_read on public.sea_area for select to authenticated using (
  private.official_in_area(id) or exists(select 1 from public.farm f where f.sea_area_id = sea_area.id and f.owner_user_id = auth.uid())
);
create policy farm_read on public.farm for select to authenticated using (private.read_farm(id));
create policy cage_read on public.cage for select to authenticated using (private.read_cage(id));
create policy stock_read on public.stock for select to authenticated using (private.read_cage(cage_id, true));
create policy stock_insert on public.stock for insert to authenticated with check (private.owns_cage(cage_id));
create policy stock_update on public.stock for update to authenticated
  using (private.owns_cage(cage_id)) with check (private.owns_cage(cage_id));
create policy sensor_read on public.sensor for select to authenticated using (private.read_cage(cage_id));
create policy metric_read on public.metric for select to authenticated using (private.read_cage(cage_id));
create policy stress_read on public.stress_score for select to authenticated using (private.read_cage(cage_id));
create policy cause_read on public.cause_estimate for select to authenticated using (private.read_cage(cage_id));
create policy recommendation_read on public.recommendation for select to authenticated using (
  exists(select 1 from public.cause_estimate c where c.id = cause_estimate_id and private.read_cage(c.cage_id, true))
);
create policy alert_read on public.alert for select to authenticated using (
  user_id = auth.uid() and private.read_recommendation(recommendation_id)
);

-- Supabase may apply broad default grants; explicitly narrow all new objects.
revoke all on public.sea_area, public.farm, public.cage, public.stock, public.sensor,
  public.metric, public.stress_score, public.cause_estimate, public.recommendation, public.alert
  from public, anon, authenticated;
grant select on public.sea_area, public.farm, public.cage, public.stock, public.sensor,
  public.metric, public.stress_score, public.cause_estimate, public.recommendation, public.alert to authenticated;
grant insert(cage_id, size_group, count, avg_weight_kg, insured),
  update(cage_id, size_group, count, avg_weight_kg, insured) on public.stock to authenticated;
revoke all on sequence public.sea_area_id_seq, public.farm_id_seq, public.cage_id_seq,
  public.stock_id_seq, public.sensor_id_seq, public.cause_estimate_id_seq,
  public.recommendation_id_seq, public.alert_id_seq from public, anon, authenticated;
grant usage on sequence public.stock_id_seq to authenticated;
grant all on public.sea_area, public.farm, public.cage, public.stock, public.sensor,
  public.metric, public.stress_score, public.cause_estimate, public.recommendation, public.alert to service_role;
grant usage, select on sequence public.sea_area_id_seq, public.farm_id_seq, public.cage_id_seq,
  public.stock_id_seq, public.sensor_id_seq, public.cause_estimate_id_seq,
  public.recommendation_id_seq, public.alert_id_seq to service_role;

revoke all on function private.valid_metric_value(text, jsonb), private.touch_stock(),
  private.official_in_area(bigint), private.owns_farm(bigint), private.read_farm(bigint, boolean),
  private.read_cage(bigint, boolean), private.owns_cage(bigint), private.read_recommendation(bigint)
  from public, anon, authenticated;
grant usage on schema private to authenticated, service_role;
grant execute on function private.valid_metric_value(text, jsonb), private.official_in_area(bigint),
  private.owns_farm(bigint), private.read_farm(bigint, boolean), private.read_cage(bigint, boolean),
  private.owns_cage(bigint), private.read_recommendation(bigint) to authenticated, service_role;

comment on column public.sea_area.geom is 'GeoJSON Polygon in WGS84 longitude/latitude; no spatial queries in this migration.';
comment on column public.stress_score.evidence is 'Extensible evidence, including sensor-group scores/last seen used by issues #59-61.';
comment on table public.recommendation is 'Financial data: owner or explicitly authorized official only. Writes use trusted backend.';

commit;
