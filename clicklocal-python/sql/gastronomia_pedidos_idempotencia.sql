-- ============================================================
-- CLICKLOCAL GASTRONOMIA V1
-- IDEMPOTENCIA DE PEDIDOS PUBLICOS
-- ============================================================
--
-- Los campos admiten NULL para conservar compatibilidad con
-- pedidos historicos y pedidos creados desde el POS.
-- ============================================================

begin;

alter table public.gastronomia_pedidos
    add column if not exists idempotency_key uuid;

alter table public.gastronomia_pedidos
    add column if not exists idempotency_fingerprint text;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.gastronomia_pedidos'::regclass
          and conname = 'gastronomia_pedidos_comercio_idempotency_unique'
    ) then
        alter table public.gastronomia_pedidos
            add constraint gastronomia_pedidos_comercio_idempotency_unique
            unique (comercio_id, idempotency_key);
    end if;
end;
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.gastronomia_pedidos'::regclass
          and conname = 'gastronomia_pedidos_idempotency_campos_check'
    ) then
        alter table public.gastronomia_pedidos
            add constraint gastronomia_pedidos_idempotency_campos_check
            check (
                (idempotency_key is null and idempotency_fingerprint is null)
                or
                (idempotency_key is not null and idempotency_fingerprint is not null)
            );
    end if;
end;
$$;

commit;
