-- ClickLocal V1: dispositivos Web Push exclusivos de Administración.
-- Migración preparada para ejecutar manualmente en Supabase. NO fue ejecutada.

create table if not exists public.admin_push_suscripciones (
    id uuid primary key default gen_random_uuid(),
    admin_user text not null,
    endpoint text not null unique,
    p256dh text not null,
    auth text not null,
    activo boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint admin_push_endpoint_https check (endpoint like 'https://%'),
    constraint admin_push_endpoint_longitud check (char_length(endpoint) <= 2048),
    constraint admin_push_p256dh_no_vacio check (char_length(p256dh) >= 20),
    constraint admin_push_auth_no_vacio check (char_length(auth) >= 8)
);

create index if not exists admin_push_suscripciones_activas_idx
    on public.admin_push_suscripciones (activo)
    where activo = true;

alter table public.admin_push_suscripciones enable row level security;

-- No se crean políticas para anon/authenticated: la tabla queda accesible
-- únicamente desde el backend mediante la service role de Supabase.
revoke all on table public.admin_push_suscripciones from anon, authenticated;
