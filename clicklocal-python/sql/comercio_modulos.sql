-- ============================================================
-- CLICKLOCAL
-- MODULOS TRANSVERSALES POR COMERCIO
-- ============================================================
--
-- Objetivo:
-- - Permitir activar herramientas reutilizables para cualquier comercio.
-- - Ejemplos: turnos, carrito, presupuestos.
-- - NO representa bloques/verticales como Gastronomía.
-- ============================================================

begin;


create table if not exists public.comercio_modulos (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete cascade,

    modulo text not null,

    activo boolean not null default true,

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now(),

    constraint comercio_modulos_comercio_modulo_unique
        unique (comercio_id, modulo),

    constraint comercio_modulos_modulo_no_vacio
        check (btrim(modulo) <> '')
);


create index if not exists
    idx_comercio_modulos_comercio_activo
on public.comercio_modulos (
    comercio_id,
    activo
);


alter table public.comercio_modulos
    enable row level security;

-- Sin políticas públicas.
-- La gestión se hará desde Flask mediante supabase_admin.


commit;
