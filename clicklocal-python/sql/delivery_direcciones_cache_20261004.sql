-- ClickLocal: caché transversal de distancias de delivery.
-- Guarda resultados derivados; nunca precios ni información del cliente.
begin;

create table if not exists public.delivery_direcciones_cache (
    comercio_id uuid not null
        references public.comercios(id) on delete cascade,
    direccion_normalizada text not null,
    origen_clave text not null,
    distancia_m integer not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint delivery_direcciones_cache_pkey primary key (
        comercio_id,
        direccion_normalizada,
        origen_clave
    ),
    constraint delivery_direcciones_cache_direccion_check
        check (btrim(direccion_normalizada) <> ''),
    constraint delivery_direcciones_cache_origen_check
        check (btrim(origen_clave) <> ''),
    constraint delivery_direcciones_cache_distancia_check
        check (distancia_m >= 0)
);

alter table public.delivery_direcciones_cache enable row level security;

revoke all privileges on table public.delivery_direcciones_cache
    from public, anon, authenticated, service_role;

grant select, insert, update on table public.delivery_direcciones_cache
    to service_role;

commit;
