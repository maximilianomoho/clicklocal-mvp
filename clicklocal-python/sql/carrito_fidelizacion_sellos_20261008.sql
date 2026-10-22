-- ClickLocal - Fidelizacion/Sellos para Carrito/Pedidos general.
-- PREPARADA PARA REVISION. NO EJECUTAR SIN AUTORIZACION EXPRESA.
-- No contiene backfill: los sellos se derivan de public.pedidos.

begin;

alter table public.pedidos_configuracion
    add column if not exists fidelizacion_meta_sellos integer,
    add column if not exists fidelizacion_premio text;

do $$
begin
    if not exists (
        select 1
        from pg_catalog.pg_constraint
        where conrelid = 'public.pedidos_configuracion'::regclass
          and conname = 'pedidos_configuracion_fidelizacion_meta_check'
    ) then
        alter table public.pedidos_configuracion
            add constraint pedidos_configuracion_fidelizacion_meta_check
            check (
                fidelizacion_meta_sellos is null
                or fidelizacion_meta_sellos >= 1
            );
    end if;

    if not exists (
        select 1
        from pg_catalog.pg_constraint
        where conrelid = 'public.pedidos_configuracion'::regclass
          and conname = 'pedidos_configuracion_fidelizacion_premio_check'
    ) then
        alter table public.pedidos_configuracion
            add constraint pedidos_configuracion_fidelizacion_premio_check
            check (
                fidelizacion_premio is null
                or btrim(fidelizacion_premio) <> ''
            );
    end if;

end
$$;

-- La existencia de un periodo abierto es la unica fuente de verdad del estado
-- activo. No se duplica ese estado en pedidos_configuracion.
create table if not exists public.pedidos_fidelizacion_periodos (
    id uuid primary key default gen_random_uuid(),
    comercio_id uuid not null
        references public.comercios(id) on delete restrict,
    inicio timestamptz not null,
    fin timestamptz,
    created_at timestamptz not null default now(),
    constraint pedidos_fidelizacion_periodos_limites_check
        check (fin is null or fin >= inicio)
);

-- Defensa final contra dos activaciones abiertas simultaneamente.
create unique index if not exists
    idx_pedidos_fidelizacion_periodos_un_abierto
    on public.pedidos_fidelizacion_periodos (comercio_id)
    where fin is null;

-- Sirve para auditar cronologicamente los periodos de un comercio y para
-- acotar la busqueda temporal de pedidos elegibles.
create index if not exists idx_pedidos_fidelizacion_periodos_comercio_inicio
    on public.pedidos_fidelizacion_periodos (comercio_id, inicio desc);

create table if not exists public.pedidos_fidelizacion_canjes (
    id uuid primary key default gen_random_uuid(),
    comercio_id uuid not null
        references public.comercios(id) on delete restrict,
    cliente_clave text not null,
    idempotency_key uuid not null,
    canjeado_at timestamptz not null default now(),
    sellos_consumidos integer not null,
    meta_snapshot integer not null,
    premio_snapshot text not null,
    saldo_restante_snapshot bigint not null,
    constraint pedidos_fidelizacion_canjes_cliente_check
        check (btrim(cliente_clave) <> ''),
    constraint pedidos_fidelizacion_canjes_sellos_check
        check (sellos_consumidos >= 1),
    constraint pedidos_fidelizacion_canjes_meta_check
        check (meta_snapshot >= 1),
    constraint pedidos_fidelizacion_canjes_consumo_meta_check
        check (sellos_consumidos = meta_snapshot),
    constraint pedidos_fidelizacion_canjes_premio_check
        check (btrim(premio_snapshot) <> ''),
    constraint pedidos_fidelizacion_canjes_saldo_check
        check (saldo_restante_snapshot >= 0),
    constraint pedidos_fidelizacion_canjes_idempotency_unique
        unique (comercio_id, idempotency_key)
);

create index if not exists idx_pedidos_fidelizacion_canjes_comercio_cliente_fecha
    on public.pedidos_fidelizacion_canjes
        (comercio_id, cliente_clave, canjeado_at desc);

-- Replica exactamente whatsapp.limpiar_numero_whatsapp del backend para el
-- fallback de pedidos antiguos sin whatsapp_normalizado.
create or replace function public.normalizar_whatsapp_clicklocal(p_whatsapp text)
returns text
language plpgsql
immutable
strict
set search_path = pg_catalog
as $$
declare
    v_numero text;
    v_resto text;
begin
    v_numero := pg_catalog.regexp_replace(p_whatsapp, '[^0-9]', '', 'g');
    if v_numero like '00%' then
        v_numero := pg_catalog.substr(v_numero, 3);
    end if;
    v_numero := pg_catalog.regexp_replace(v_numero, '^0+', '');
    if v_numero = '' then
        return '';
    end if;
    if v_numero like '549%' then
        return v_numero;
    end if;
    if v_numero like '54%' then
        v_resto := pg_catalog.substr(v_numero, 3);
        if v_resto like '9%' then
            return v_numero;
        end if;
        return '549' || v_resto;
    end if;
    return '549' || v_numero;
end;
$$;

create or replace function public.clave_cliente_pedido_clicklocal(
    p_pedido_id uuid,
    p_whatsapp_normalizado text,
    p_whatsapp_cliente text
)
returns text
language sql
immutable
set search_path = pg_catalog
as $$
    select case
        when pg_catalog.btrim(coalesce(p_whatsapp_normalizado, '')) <> ''
            then pg_catalog.btrim(p_whatsapp_normalizado)
        when public.normalizar_whatsapp_clicklocal(coalesce(p_whatsapp_cliente, '')) <> ''
            then public.normalizar_whatsapp_clicklocal(coalesce(p_whatsapp_cliente, ''))
        else 'pedido:' || p_pedido_id::text
    end
$$;

create or replace function public.configurar_fidelizacion_clicklocal(
    p_comercio_id uuid,
    p_activa boolean,
    p_meta_sellos integer,
    p_premio text
)
returns table (
    activa boolean,
    meta_sellos integer,
    premio text,
    periodo_id uuid,
    periodo_inicio timestamptz,
    periodo_fin timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_config public.pedidos_configuracion%rowtype;
    v_periodo public.pedidos_fidelizacion_periodos%rowtype;
    v_premio text := pg_catalog.btrim(coalesce(p_premio, ''));
    v_ahora timestamptz;
begin
    if p_comercio_id is null or p_activa is null then
        raise exception 'Los datos de fidelizacion no son validos.';
    end if;
    if p_meta_sellos is null or p_meta_sellos < 1 or v_premio = '' then
        raise exception 'La meta y el premio de fidelizacion no son validos.';
    end if;

    -- Esta fila es el lock estable por comercio. Serializa configuraciones,
    -- activaciones, desactivaciones y canjes del mismo comercio.
    select * into v_config
    from public.pedidos_configuracion
    where comercio_id = p_comercio_id
    for update;

    if not found then
        raise exception 'El comercio no tiene configuracion de pedidos.';
    end if;

    -- Se toma despues del lock: representa el orden efectivo de transiciones,
    -- incluso cuando esta llamada debio esperar otra operacion concurrente.
    v_ahora := pg_catalog.clock_timestamp();

    update public.pedidos_configuracion
    set fidelizacion_meta_sellos = p_meta_sellos,
        fidelizacion_premio = v_premio,
        updated_at = v_ahora
    where comercio_id = p_comercio_id;

    select * into v_periodo
    from public.pedidos_fidelizacion_periodos
    where comercio_id = p_comercio_id
      and fin is null
    for update;

    if p_activa and not found then
        insert into public.pedidos_fidelizacion_periodos (
            comercio_id, inicio
        ) values (
            p_comercio_id, v_ahora
        )
        returning * into v_periodo;
    elsif not p_activa and found then
        update public.pedidos_fidelizacion_periodos
        set fin = v_ahora
        where id = v_periodo.id
        returning * into v_periodo;
    end if;

    return query select
        p_activa,
        p_meta_sellos,
        v_premio,
        v_periodo.id,
        v_periodo.inicio,
        v_periodo.fin;
end;
$$;

create or replace function public.canjear_premio_fidelizacion_clicklocal(
    p_comercio_id uuid,
    p_cliente_clave text,
    p_idempotency_key uuid
)
returns table (
    ok boolean,
    canje_id uuid,
    sellos_consumidos integer,
    saldo_restante bigint,
    premio text
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_cliente_clave text := pg_catalog.btrim(coalesce(p_cliente_clave, ''));
    v_config public.pedidos_configuracion%rowtype;
    v_existente public.pedidos_fidelizacion_canjes%rowtype;
    v_sellos_brutos bigint;
    v_sellos_consumidos bigint;
    v_saldo bigint;
    v_canje public.pedidos_fidelizacion_canjes%rowtype;
begin
    if p_comercio_id is null or v_cliente_clave = '' or p_idempotency_key is null then
        raise exception 'Los datos del canje no son validos.';
    end if;

    -- Serializa todo canje del mismo comercio/cliente, incluso si cada request
    -- trae una idempotency_key diferente.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_comercio_id::text || ':' || v_cliente_clave,
            0
        )
    );

    select * into v_existente
    from public.pedidos_fidelizacion_canjes
    where comercio_id = p_comercio_id
      and idempotency_key = p_idempotency_key;

    if found then
        if v_existente.cliente_clave is distinct from v_cliente_clave then
            raise exception 'La clave de idempotencia ya fue usada para otro cliente.';
        end if;
        return query select
            true,
            v_existente.id,
            v_existente.sellos_consumidos,
            v_existente.saldo_restante_snapshot,
            v_existente.premio_snapshot;
        return;
    end if;

    -- El bloqueo estabiliza meta, premio y periodos durante el canje. La RPC de
    -- configuracion usa la misma fila, por lo que una transicion debe esperar.
    select * into v_config
    from public.pedidos_configuracion
    where comercio_id = p_comercio_id
    for update;

    if not found then
        raise exception 'El comercio no tiene configuracion de pedidos.';
    end if;
    if v_config.fidelizacion_meta_sellos is null
       or v_config.fidelizacion_meta_sellos < 1
       or v_config.fidelizacion_premio is null
       or pg_catalog.btrim(v_config.fidelizacion_premio) = '' then
        raise exception 'La configuracion de fidelizacion no es valida.';
    end if;
    if not exists (
        select 1
        from public.pedidos_fidelizacion_periodos periodo
        where periodo.comercio_id = p_comercio_id
          and periodo.fin is null
    ) then
        raise exception 'El programa de fidelizacion no esta activo.';
    end if;

    select pg_catalog.count(*) into v_sellos_brutos
    from public.pedidos p
    where p.comercio_id = p_comercio_id
      and p.estado = 'entregado'
      and public.clave_cliente_pedido_clicklocal(
            p.id, p.whatsapp_normalizado, p.whatsapp_cliente
          ) = v_cliente_clave
      and exists (
          select 1
          from public.pedidos_fidelizacion_periodos periodo
          where periodo.comercio_id = p.comercio_id
            and periodo.inicio <= p.created_at
            and (periodo.fin is null or p.created_at < periodo.fin)
      );

    select coalesce(pg_catalog.sum(c.sellos_consumidos), 0)
      into v_sellos_consumidos
    from public.pedidos_fidelizacion_canjes c
    where c.comercio_id = p_comercio_id
      and c.cliente_clave = v_cliente_clave;

    v_saldo := v_sellos_brutos - v_sellos_consumidos;
    if v_saldo < v_config.fidelizacion_meta_sellos then
        raise exception 'El cliente no tiene sellos suficientes.';
    end if;

    v_saldo := v_saldo - v_config.fidelizacion_meta_sellos;

    insert into public.pedidos_fidelizacion_canjes (
        comercio_id,
        cliente_clave,
        idempotency_key,
        sellos_consumidos,
        meta_snapshot,
        premio_snapshot,
        saldo_restante_snapshot
    ) values (
        p_comercio_id,
        v_cliente_clave,
        p_idempotency_key,
        v_config.fidelizacion_meta_sellos,
        v_config.fidelizacion_meta_sellos,
        v_config.fidelizacion_premio,
        v_saldo
    )
    returning * into v_canje;

    return query select
        true,
        v_canje.id,
        v_canje.sellos_consumidos,
        v_canje.saldo_restante_snapshot,
        v_canje.premio_snapshot;
end;
$$;

alter table public.pedidos_fidelizacion_canjes enable row level security;
alter table public.pedidos_fidelizacion_periodos enable row level security;

revoke all privileges on table public.pedidos_fidelizacion_canjes
    from public, anon, authenticated, service_role;
revoke all privileges on table public.pedidos_fidelizacion_periodos
    from public, anon, authenticated, service_role;

revoke all on function public.normalizar_whatsapp_clicklocal(text)
    from public, anon, authenticated, service_role;
revoke all on function public.clave_cliente_pedido_clicklocal(uuid, text, text)
    from public, anon, authenticated, service_role;
revoke all on function public.configurar_fidelizacion_clicklocal(uuid, boolean, integer, text)
    from public, anon, authenticated, service_role;
revoke all on function public.canjear_premio_fidelizacion_clicklocal(uuid, text, uuid)
    from public, anon, authenticated, service_role;

grant execute on function public.configurar_fidelizacion_clicklocal(uuid, boolean, integer, text)
    to service_role;
grant execute on function public.canjear_premio_fidelizacion_clicklocal(uuid, text, uuid)
    to service_role;

-- Conserva el ACL vigente de pedidos_configuracion (service_role ya posee
-- SELECT/INSERT/UPDATE). No se concede acceso directo a la tabla de canjes.

commit;
