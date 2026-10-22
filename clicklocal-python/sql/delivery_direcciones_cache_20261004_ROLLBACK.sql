-- ClickLocal: rollback exclusivo de la caché transversal de delivery.
begin;

do $$
begin
    if to_regclass('public.delivery_direcciones_cache') is not null then
        execute 'revoke all privileges on table public.delivery_direcciones_cache from public, anon, authenticated, service_role';
    end if;
end
$$;

drop table if exists public.delivery_direcciones_cache;

commit;
