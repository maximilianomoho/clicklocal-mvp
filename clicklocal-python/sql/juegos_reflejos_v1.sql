-- ============================================================
-- CLICKLOCAL JUEGOS — REFLEJOS V1
-- ============================================================
-- Extiende el intento común con el instante server-side a partir
-- del cual puede completarse y registra Reflejos en el catálogo.
-- Preparada para ejecución manual en Supabase. NO ejecutada.
-- ============================================================

begin;

alter table public.jugadores
    add column if not exists intento_ready_at timestamptz;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'jugadores_intento_ready_coherente'
          and conrelid = 'public.jugadores'::regclass
    ) then
        alter table public.jugadores
            add constraint jugadores_intento_ready_coherente
            check (
                intento_ready_at is null
                or (
                    intento_token_hash is not null
                    and intento_juego_id is not null
                    and intento_expires_at is not null
                    and intento_ready_at < intento_expires_at
                )
            );
    end if;
end
$$;

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
    'reflejos',
    'Reflejos',
    'lower',
    'ms',
    80,
    3000,
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

commit;
