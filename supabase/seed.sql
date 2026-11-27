-- Local/demo seed only. A reserved Auth user with NO credentials owns the demo farm.
-- It cannot sign in. Do not reuse this UUID as a production owner.
begin;
insert into auth.users(id, aud, role, email, raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values ('00000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated',
  'demo-owner@example.invalid', '{"provider":"email","providers":["email"]}', '{}', now(), now())
on conflict (id) do nothing;

insert into public.sea_area(id, name, geom, nifs_station_id) overriding system value
values (1, '시연 해역 (합성)', '{"type":"Polygon","coordinates":[[[128.40,34.70],[128.41,34.70],[128.41,34.71],[128.40,34.71],[128.40,34.70]]]}', null)
on conflict (id) do nothing;
insert into public.farm(id, sea_area_id, owner_user_id, name, location) overriding system value
values (1, 1, '00000000-0000-4000-8000-000000000001', '시연 어가 (합성)', '{"type":"Point","coordinates":[128.405,34.705]}')
on conflict (id) do nothing;
insert into public.cage(id, farm_id, is_sentinel, depth_m, net_depth_m) overriding system value
values (3, 1, true, 20, 15) on conflict (id) do nothing;
select setval(pg_get_serial_sequence('public.sea_area','id'), (select max(id) from public.sea_area));
select setval(pg_get_serial_sequence('public.farm','id'), (select max(id) from public.farm));
select setval(pg_get_serial_sequence('public.cage','id'), (select max(id) from public.cage));
commit;
