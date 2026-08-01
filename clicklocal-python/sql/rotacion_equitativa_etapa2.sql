-- ============================================================
-- CLICKLOCAL
-- Rotación equitativa por exposición real — Etapa 2
--
-- Este archivo debe ejecutarse manualmente en Supabase.
-- Es idempotente: puede ejecutarse más de una vez.
-- ============================================================

create table if not exists public.exposicion_comercios (
    comercio_id uuid not null
        references public.comercios(id)
        on delete cascade,

    contexto text not null,

    ultima_exposicion_at timestamptz,

    exposiciones_ponderadas numeric(14,4)
        not null default 0,

    exposiciones_altas integer
        not null default 0,

    exposiciones_medias integer
        not null default 0,

    exposiciones_bajas integer
        not null default 0,

    total_exposiciones integer
        not null default 0,

    created_at timestamptz
        not null default now(),

    updated_at timestamptz
        not null default now(),

    primary key (comercio_id, contexto),

    constraint exposicion_comercios_contexto_valido
        check (
            contexto like 'macro:%'
            or contexto like 'categoria:%'
        )
);

create index if not exists
    idx_exposicion_comercios_contexto_ultima
on public.exposicion_comercios (
    contexto,
    ultima_exposicion_at asc nulls first
);

create index if not exists
    idx_exposicion_comercios_contexto_ponderadas
on public.exposicion_comercios (
    contexto,
    exposiciones_ponderadas asc
);

create or replace function
public.registrar_exposiciones_comercios(
    p_contexto text,
    p_items jsonb
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    item jsonb;
    v_comercio_id uuid;
    v_peso numeric;
    v_posicion integer;
    v_actualizadas integer := 0;
begin
    if p_contexto is null
       or not (
           p_contexto like 'macro:%'
           or p_contexto like 'categoria:%'
       )
    then
        raise exception 'Contexto de exposición inválido';
    end if;

    if p_items is null
       or jsonb_typeof(p_items) <> 'array'
    then
        return 0;
    end if;

    for item in
        select value
        from jsonb_array_elements(p_items)
    loop
        begin
            v_comercio_id :=
                nullif(item ->> 'comercio_id', '')::uuid;

            v_peso := greatest(
                0.10,
                least(
                    1.00,
                    coalesce(
                        (item ->> 'peso')::numeric,
                        0.30
                    )
                )
            );

            v_posicion := greatest(
                1,
                coalesce(
                    (item ->> 'posicion')::integer,
                    999
                )
            );

        exception
            when others then
                continue;
        end;

        if v_comercio_id is null then
            continue;
        end if;

        if not exists (
            select 1
            from public.comercios
            where id = v_comercio_id
              and activo is not false
        ) then
            continue;
        end if;

        insert into public.exposicion_comercios (
            comercio_id,
            contexto,
            ultima_exposicion_at,
            exposiciones_ponderadas,
            exposiciones_altas,
            exposiciones_medias,
            exposiciones_bajas,
            total_exposiciones,
            updated_at
        )
        values (
            v_comercio_id,
            p_contexto,
            now(),
            v_peso,
            case when v_posicion <= 4 then 1 else 0 end,
            case
                when v_posicion between 5 and 8
                then 1 else 0
            end,
            case when v_posicion >= 9 then 1 else 0 end,
            1,
            now()
        )
        on conflict (comercio_id, contexto)
        do update set
            ultima_exposicion_at = now(),

            exposiciones_ponderadas =
                public.exposicion_comercios
                .exposiciones_ponderadas
                + excluded.exposiciones_ponderadas,

            exposiciones_altas =
                public.exposicion_comercios
                .exposiciones_altas
                + excluded.exposiciones_altas,

            exposiciones_medias =
                public.exposicion_comercios
                .exposiciones_medias
                + excluded.exposiciones_medias,

            exposiciones_bajas =
                public.exposicion_comercios
                .exposiciones_bajas
                + excluded.exposiciones_bajas,

            total_exposiciones =
                public.exposicion_comercios
                .total_exposiciones
                + 1,

            updated_at = now();

        v_actualizadas := v_actualizadas + 1;
    end loop;

    return v_actualizadas;
end;
$$;

revoke all
on function public.registrar_exposiciones_comercios(
    text,
    jsonb
)
from public;

grant execute
on function public.registrar_exposiciones_comercios(
    text,
    jsonb
)
to service_role;

alter table public.exposicion_comercios
enable row level security;

-- No se habilitan políticas públicas.
-- Flask accede exclusivamente con service_role.
