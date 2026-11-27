-- Dedicated edge grants are separate from farmer/official access.
set role authenticated;
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000004","user_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
select pg_temp.expect_error($q$insert into public.metric values(3,'2026-09-01T00:00:00Z','env','do','6.5',1)$q$,
  '42501', 'edge grant in user metadata is ignored');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
insert into public.metric values
 (3,'2026-09-01T00:00:00Z','env','do','6.5',1),
 (3,'2026-09-01T00:00:00Z','env','temp_mid','21.5',1);
select pg_temp.check_ok((select count(*)=0 from public.metric), 'edge cannot read farm metrics');
select pg_temp.expect_error($q$insert into public.metric values(4,'2026-09-01T00:00:00Z','env','do','6.5',1)$q$,
  '42501', 'edge cannot ingest unassigned cage');
select pg_temp.expect_error($q$insert into public.metric values
  (3,'2026-09-02T00:00:00Z','env','do','6.5',1),
  (4,'2026-09-02T00:00:00Z','env','do','6.5',1)$q$,
  '42501', 'mixed authorized and unauthorized batch rejected');
select pg_temp.expect_error($q$insert into public.metric values
  (3,'2026-09-03T00:00:00Z','env','do','6.5',1),
  (3,'2026-09-01T00:00:00Z','env','do','6.5',1)$q$,
  '23505', 'existing-key conflict rejects batch');
select pg_temp.expect_error($q$insert into public.metric values
  (3,'2026-09-04T00:00:00Z','env','do','6.5',1),
  (3,'2026-09-04T00:00:00Z','camera','quality','2',1)$q$,
  '23514', 'invalid value rejects batch');
select pg_temp.expect_error($q$update public.metric set value='0' where cage_id=3$q$,
  '42501', 'edge cannot overwrite observations');
select pg_temp.expect_error($q$delete from public.metric where cage_id=3$q$,
  '42501', 'edge cannot delete observations');
set request.jwt.claims = '{"sub":"00000000-0000-4000-8000-000000000004","app_metadata":{"role":"edge","ingest_cage_ids":["3"]}}';
select pg_temp.expect_error($q$insert into public.metric values(3,'2026-09-05T00:00:00Z','env','do','6.5',1)$q$,
  '42501', 'edge grants require numeric DB IDs');
set request.jwt.claims = '{"app_metadata":{"role":"edge","ingest_cage_ids":[3]}}';
select pg_temp.expect_error($q$insert into public.metric values(3,'2026-09-06T00:00:00Z','env','do','6.5',1)$q$,
  '42501', 'edge grant without identity fails closed');
reset role;
select pg_temp.check_ok((select count(*)=2 from public.metric where ts='2026-09-01T00:00:00Z'), 'edge batch committed');
select pg_temp.check_ok((select count(*)=0 from public.metric where ts between '2026-09-02' and '2026-09-06'), 'failed batches leave no partial rows');
