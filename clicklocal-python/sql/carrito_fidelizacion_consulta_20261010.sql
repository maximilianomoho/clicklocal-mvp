-- ClickLocal - Consulta de Fidelizacion/Sellos para Carrito/Pedidos general.
-- PREPARADA PARA REVISION. NO EJECUTAR SIN AUTORIZACION EXPRESA.

begin;

create or replace function public.consultar_fidelizacion_clicklocal(
    p_comercio_id uuid,
    p_cliente_claves text[]
)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog
as $$
declare
    v_config public.pedidos_configuracion%rowtype;
    v_periodo public.pedidos_fidelizacion_periodos%rowtype;
    v_cliente_claves text[];
    v_activa boolean;
    v_clientes jsonb;
begin
    if p_comercio_id is null then
        raise exception 'El comercio no es valido.';
    end if;
    if p_cliente_claves is null then
        raise exception 'Las claves de clientes no pueden ser nulas.';
    end if;
    if pg_catalog.cardinality(p_cliente_claves) > 50 then
        raise exception 'No se pueden consultar mas de 50 clientes.';
    end if;
    if pg_catalog.array_position(p_cliente_claves, null) is not null then
        raise exception 'Las claves de clientes no pueden contener valores nulos.';
    end if;
    if exists (
        select 1
        from pg_catalog.unnest(p_cliente_claves) as clave(cliente_clave)
        where pg_catalog.btrim(clave.cliente_clave) = ''
    ) then
        raise exception 'Las claves de clientes no pueden estar vacias.';
    end if;
    if (
        select pg_catalog.count(*)
        from pg_catalog.unnest(p_cliente_claves) as clave(cliente_clave)
    ) <> (
        select pg_catalog.count(distinct pg_catalog.btrim(clave.cliente_clave))
        from pg_catalog.unnest(p_cliente_claves) as clave(cliente_clave)
    ) then
        raise exception 'Las claves de clientes no pueden estar duplicadas.';
    end if;

    select * into v_config
    from public.pedidos_configuracion
    where comercio_id = p_comercio_id;

    if not found then
        raise exception 'El comercio no tiene configuracion de pedidos.';
    end if;

    select * into v_periodo
    from public.pedidos_fidelizacion_periodos
    where comercio_id = p_comercio_id
      and fin is null;
    v_activa := found;

    select coalesce(
        pg_catalog.array_agg(
            pg_catalog.btrim(clave.cliente_clave)
            order by clave.ordinalidad
        ),
        array[]::text[]
    ) into v_cliente_claves
    from pg_catalog.unnest(p_cliente_claves)
        with ordinality as clave(cliente_clave, ordinalidad);

    with claves as (
        select clave.cliente_clave, clave.ordinalidad
        from pg_catalog.unnest(v_cliente_claves)
            with ordinality as clave(cliente_clave, ordinalidad)
    ),
    sellos_brutos as (
        select
            clave.cliente_clave,
            pg_catalog.count(pedido.id)::bigint as cantidad
        from claves clave
        join public.pedidos pedido
          on pedido.comercio_id = p_comercio_id
         and pedido.estado = 'entregado'
         and public.clave_cliente_pedido_clicklocal(
                pedido.id,
                pedido.whatsapp_normalizado,
                pedido.whatsapp_cliente
             ) = clave.cliente_clave
         and exists (
                select 1
                from public.pedidos_fidelizacion_periodos periodo
                where periodo.comercio_id = pedido.comercio_id
                  and periodo.inicio <= pedido.created_at
                  and (
                        periodo.fin is null
                        or pedido.created_at < periodo.fin
                  )
             )
        group by clave.cliente_clave
    ),
    sellos_consumidos as (
        select
            canje.cliente_clave,
            coalesce(pg_catalog.sum(canje.sellos_consumidos), 0)::bigint
                as cantidad
        from public.pedidos_fidelizacion_canjes canje
        where canje.comercio_id = p_comercio_id
          and canje.cliente_clave = any(v_cliente_claves)
        group by canje.cliente_clave
    )
    select coalesce(
        pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'cliente_clave', clave.cliente_clave,
                'sellos_brutos', coalesce(brutos.cantidad, 0),
                'sellos_consumidos', coalesce(consumidos.cantidad, 0),
                'saldo',
                    coalesce(brutos.cantidad, 0)
                    - coalesce(consumidos.cantidad, 0),
                'premio_disponible',
                    v_activa
                    and v_config.fidelizacion_meta_sellos is not null
                    and (
                        coalesce(brutos.cantidad, 0)
                        - coalesce(consumidos.cantidad, 0)
                    ) >= v_config.fidelizacion_meta_sellos,
                'canjes', coalesce((
                    select pg_catalog.jsonb_agg(
                        pg_catalog.jsonb_build_object(
                            'id', canje.id,
                            'canjeado_at', canje.canjeado_at,
                            'sellos_consumidos', canje.sellos_consumidos,
                            'meta_snapshot', canje.meta_snapshot,
                            'premio_snapshot', canje.premio_snapshot,
                            'saldo_restante_snapshot',
                                canje.saldo_restante_snapshot
                        ) order by canje.canjeado_at desc, canje.id desc
                    )
                    from public.pedidos_fidelizacion_canjes canje
                    where canje.comercio_id = p_comercio_id
                      and canje.cliente_clave = clave.cliente_clave
                ), '[]'::jsonb)
            ) order by clave.ordinalidad
        ),
        '[]'::jsonb
    ) into v_clientes
    from claves clave
    left join sellos_brutos brutos
      on brutos.cliente_clave = clave.cliente_clave
    left join sellos_consumidos consumidos
      on consumidos.cliente_clave = clave.cliente_clave;

    return pg_catalog.jsonb_build_object(
        'activa', v_activa,
        'meta_sellos', v_config.fidelizacion_meta_sellos,
        'premio', v_config.fidelizacion_premio,
        'periodo_abierto', case
            when v_activa then pg_catalog.jsonb_build_object(
                'id', v_periodo.id,
                'inicio', v_periodo.inicio
            )
            else null
        end,
        'clientes', v_clientes
    );
end;
$$;

revoke all on function public.consultar_fidelizacion_clicklocal(uuid, text[])
    from public, anon, authenticated, service_role;

grant execute on function public.consultar_fidelizacion_clicklocal(uuid, text[])
    to service_role;

commit;
