-- ============================================================
-- CLICKLOCAL JUEGOS
-- INTENTOS PERSISTENTES V1
-- ============================================================
--
-- Agrega a jugadores un único intento activo, compartido por todos
-- los procesos Gunicorn. El token real nunca se guarda: solamente su
-- hash SHA-256 hexadecimal.
--
-- Migración preparada para ejecución manual en Supabase.
-- No fue ejecutada por este cambio.
-- ============================================================

begin;


alter table public.jugadores
    add column if not exists intento_token_hash text,
    add column if not exists intento_juego_id uuid,
    add column if not exists intento_expires_at timestamptz;


do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'jugadores_intento_juego_fk'
          and conrelid = 'public.jugadores'::regclass
    ) then
        alter table public.jugadores
            add constraint jugadores_intento_juego_fk
            foreign key (intento_juego_id)
            references public.juegos(id)
            on delete restrict;
    end if;
end
$$;


do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'jugadores_intento_token_hash_formato'
          and conrelid = 'public.jugadores'::regclass
    ) then
        alter table public.jugadores
            add constraint jugadores_intento_token_hash_formato
            check (
                intento_token_hash is null
                or intento_token_hash ~ '^[0-9a-f]{64}$'
            );
    end if;
end
$$;


do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'jugadores_intento_coherente'
          and conrelid = 'public.jugadores'::regclass
    ) then
        alter table public.jugadores
            add constraint jugadores_intento_coherente
            check (
                (
                    intento_token_hash is null
                    and intento_juego_id is null
                    and intento_expires_at is null
                )
                or
                (
                    intento_token_hash is not null
                    and intento_juego_id is not null
                    and intento_expires_at is not null
                )
            );
    end if;
end
$$;


create index if not exists jugadores_intento_activo_idx
    on public.jugadores (
        intento_juego_id,
        intento_expires_at
    )
    where intento_token_hash is not null;


commit;
