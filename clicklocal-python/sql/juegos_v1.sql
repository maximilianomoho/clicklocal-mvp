-- ============================================================
-- CLICKLOCAL JUEGOS
-- ESTRUCTURA BASE V1
-- ============================================================
--
-- Crea el catálogo de juegos y el motor común para jugadores
-- anónimos, partidas y desafíos asincrónicos.
--
-- Migración preparada para ejecución manual en Supabase.
-- No fue ejecutada por este cambio.
-- ============================================================

begin;


-- ============================================================
-- 1. CATALOGO DE JUEGOS
-- ============================================================

create table if not exists public.juegos (
    id uuid primary key default gen_random_uuid(),
    slug text not null unique,
    nombre text not null,
    activo boolean not null default true,
    ranking_direction text not null,
    score_unit text not null,
    score_min numeric,
    score_max numeric,
    config jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint juegos_slug_formato
        check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
    constraint juegos_nombre_no_vacio
        check (btrim(nombre) <> ''),
    constraint juegos_ranking_direction_valida
        check (ranking_direction in ('higher', 'lower')),
    constraint juegos_score_unit_no_vacia
        check (btrim(score_unit) <> ''),
    constraint juegos_score_rango_valido
        check (
            score_min is null
            or score_max is null
            or score_max >= score_min
        ),
    constraint juegos_config_objeto
        check (jsonb_typeof(config) = 'object')
);


-- ============================================================
-- 2. JUGADORES ANONIMOS
-- ============================================================

create table if not exists public.jugadores (
    id uuid primary key default gen_random_uuid(),
    browser_token_hash text not null unique,
    alias text,
    alias_normalizado text,
    activo boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),

    constraint jugadores_token_hash_formato
        check (browser_token_hash ~ '^[0-9a-f]{64}$'),
    constraint jugadores_alias_longitud
        check (
            alias is null
            or char_length(btrim(alias)) between 3 and 24
        ),
    constraint jugadores_alias_coherente
        check (
            (alias is null and alias_normalizado is null)
            or (
                alias is not null
                and alias_normalizado is not null
                and btrim(alias_normalizado) <> ''
            )
        )
);

create unique index if not exists jugadores_alias_normalizado_uidx
    on public.jugadores (alias_normalizado)
    where alias_normalizado is not null;


-- ============================================================
-- 3. PARTIDAS
-- ============================================================

create table if not exists public.partidas (
    id uuid primary key default gen_random_uuid(),
    jugador_id uuid not null
        references public.jugadores(id)
        on delete restrict,
    juego_id uuid not null
        references public.juegos(id)
        on delete restrict,
    score numeric not null,
    metadata jsonb not null default '{}'::jsonb,
    valida boolean not null default true,
    invalid_reason text,
    created_at timestamptz not null default now(),

    constraint partidas_metadata_objeto
        check (jsonb_typeof(metadata) = 'object'),
    constraint partidas_invalidez_coherente
        check (
            (valida = true and invalid_reason is null)
            or valida = false
        )
);

create index if not exists partidas_juego_created_idx
    on public.partidas (juego_id, created_at desc);

create index if not exists partidas_juego_score_created_idx
    on public.partidas (juego_id, score, created_at);

create index if not exists partidas_jugador_juego_created_idx
    on public.partidas (jugador_id, juego_id, created_at desc);

create index if not exists partidas_validas_juego_created_idx
    on public.partidas (juego_id, created_at desc)
    where valida = true;


-- ============================================================
-- 4. DESAFIOS ASINCRONICOS
-- ============================================================

create table if not exists public.desafios (
    id uuid primary key default gen_random_uuid(),
    token_hash text not null unique,
    juego_id uuid not null
        references public.juegos(id)
        on delete restrict,
    jugador_creador_id uuid not null
        references public.jugadores(id)
        on delete restrict,
    partida_origen_id uuid not null unique
        references public.partidas(id)
        on delete restrict,
    jugador_respuesta_id uuid
        references public.jugadores(id)
        on delete restrict,
    partida_respuesta_id uuid unique
        references public.partidas(id)
        on delete restrict,
    estado text not null default 'abierto',
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    expires_at timestamptz,
    updated_at timestamptz not null default now(),

    constraint desafios_token_hash_formato
        check (token_hash ~ '^[0-9a-f]{64}$'),
    constraint desafios_estado_valido
        check (estado in ('abierto', 'completado', 'cancelado', 'expirado')),
    constraint desafios_respuesta_completa
        check (
            (jugador_respuesta_id is null and partida_respuesta_id is null)
            or
            (jugador_respuesta_id is not null and partida_respuesta_id is not null)
        ),
    constraint desafios_jugadores_distintos
        check (
            jugador_respuesta_id is null
            or jugador_respuesta_id <> jugador_creador_id
        ),
    constraint desafios_estado_coherente
        check (
            (
                estado = 'completado'
                and jugador_respuesta_id is not null
                and partida_respuesta_id is not null
                and completed_at is not null
            )
            or
            (
                estado <> 'completado'
                and jugador_respuesta_id is null
                and partida_respuesta_id is null
                and completed_at is null
            )
        ),
    constraint desafios_vencimiento_valido
        check (expires_at is null or expires_at > created_at)
);

create index if not exists desafios_juego_estado_created_idx
    on public.desafios (juego_id, estado, created_at desc);

create index if not exists desafios_creador_estado_idx
    on public.desafios (jugador_creador_id, estado, created_at desc);

create index if not exists desafios_abiertos_expiracion_idx
    on public.desafios (expires_at)
    where estado = 'abierto';


-- ============================================================
-- 5. CATALOGO INICIAL
-- ============================================================

insert into public.juegos (
    slug,
    nombre,
    ranking_direction,
    score_unit,
    score_min,
    score_max,
    activo
)
values (
    'circulo',
    'Círculo Perfecto',
    'higher',
    'percent',
    0,
    100,
    true
)
on conflict (slug) do update set
    nombre = excluded.nombre,
    ranking_direction = excluded.ranking_direction,
    score_unit = excluded.score_unit,
    score_min = excluded.score_min,
    score_max = excluded.score_max,
    activo = excluded.activo,
    updated_at = now();


-- ============================================================
-- 6. SEGURIDAD
-- ============================================================

alter table public.juegos enable row level security;
alter table public.jugadores enable row level security;
alter table public.partidas enable row level security;
alter table public.desafios enable row level security;

-- Sin políticas públicas. Flask operará exclusivamente mediante
-- supabase_admin y la service role configurada en el backend.
revoke all on table public.juegos from anon, authenticated;
revoke all on table public.jugadores from anon, authenticated;
revoke all on table public.partidas from anon, authenticated;
revoke all on table public.desafios from anon, authenticated;


commit;
