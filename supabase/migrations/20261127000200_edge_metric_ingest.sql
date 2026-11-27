-- Dedicated Supabase Auth edge accounts can insert only explicitly assigned cages.
-- app_metadata is administered server-side; no broad farmer/official write grant.
begin;
create function private.can_ingest_cage(cage_id bigint) returns boolean
language sql stable set search_path = '' as $$
  select auth.uid() is not null
    and coalesce(auth.jwt()->'app_metadata'->>'role' = 'edge', false)
    and coalesce(auth.jwt()->'app_metadata'->'ingest_cage_ids' @> jsonb_build_array(cage_id), false);
$$;
revoke all on function private.can_ingest_cage(bigint) from public, anon, authenticated;
grant execute on function private.can_ingest_cage(bigint) to authenticated, service_role;
grant insert(cage_id, ts, source, metric, value, confidence) on public.metric to authenticated;
create policy metric_edge_insert on public.metric for insert to authenticated
with check (private.can_ingest_cage(cage_id));
comment on policy metric_edge_insert on public.metric is
  'Edge app_metadata ingest_cage_ids allow-list. Batch is atomic; no UPDATE/DELETE or metric SELECT granted to edge accounts.';
commit;
