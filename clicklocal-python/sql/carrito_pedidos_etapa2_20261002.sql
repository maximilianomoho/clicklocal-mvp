-- ClickLocal - Carrito/Pedidos transversal, etapa 2
-- PREPARADA PARA REVISION. NO EJECUTAR SIN AUTORIZACION.

begin;

create table if not exists public.pedidos_configuracion (
    comercio_id uuid primary key references public.comercios(id) on delete cascade,
    acepta_retiro boolean not null default true,
    acepta_delivery boolean not null default false,
    acepta_efectivo boolean not null default true,
    acepta_transferencia boolean not null default true,
    pedido_minimo numeric(12,2),
    delivery_modo text not null default 'fijo'
        check (delivery_modo in ('fijo', 'distancia')),
    delivery_costo_fijo numeric(12,2) not null default 0,
    delivery_origen_direccion text,
    delivery_origen_latitud double precision,
    delivery_origen_longitud double precision,
    delivery_ciudad text,
    delivery_franjas jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (pedido_minimo is null or pedido_minimo >= 0),
    check (delivery_costo_fijo >= 0),
    check (jsonb_typeof(delivery_franjas) = 'array'),
    check (acepta_retiro or acepta_delivery),
    check (acepta_efectivo or acepta_transferencia),
    check (delivery_origen_latitud is null or delivery_origen_latitud between -90 and 90),
    check (delivery_origen_longitud is null or delivery_origen_longitud between -180 and 180),
    check (
        not (acepta_delivery and delivery_modo = 'distancia')
        or (
            length(btrim(coalesce(delivery_origen_direccion, ''))) > 0
            and length(btrim(coalesce(delivery_ciudad, ''))) > 0
            and delivery_origen_latitud is not null
            and delivery_origen_longitud is not null
            and jsonb_array_length(delivery_franjas) > 0
        )
    )
);

create table if not exists public.pedido_contadores (
    comercio_id uuid primary key references public.comercios(id) on delete cascade,
    ultimo_numero bigint not null default 0,
    updated_at timestamptz not null default now()
);

create table if not exists public.pedidos (
    id uuid primary key default gen_random_uuid(),
    comercio_id uuid not null references public.comercios(id) on delete restrict,
    idempotency_key uuid not null,
    idempotency_fingerprint text not null
        check (idempotency_fingerprint ~ '^[0-9a-f]{64}$'),
    numero bigint not null,
    estado text not null default 'nuevo'
        check (estado in ('nuevo','aceptado','preparando','listo','entregado','cancelado')),
    nombre_cliente text not null,
    whatsapp_cliente text not null,
    whatsapp_normalizado text not null,
    tipo_entrega text not null check (tipo_entrega in ('retiro','delivery')),
    direccion text,
    referencia text,
    forma_pago text not null check (forma_pago in ('efectivo','transferencia')),
    paga_con numeric(12,2),
    subtotal numeric(12,2) not null check (subtotal >= 0),
    costo_envio numeric(12,2) not null default 0 check (costo_envio >= 0),
    total numeric(12,2) not null check (total >= 0),
    distancia_delivery_m integer,
    observaciones text,
    texto_pedido text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (comercio_id, numero),
    unique (comercio_id, idempotency_key),
    check (tipo_entrega <> 'delivery' or length(btrim(coalesce(direccion,''))) > 0),
    check (forma_pago <> 'efectivo' or paga_con is null or paga_con >= total),
    check (total = subtotal + costo_envio),
    check (tipo_entrega <> 'retiro' or costo_envio = 0),
    check (forma_pago <> 'transferencia' or paga_con is null),
    check (paga_con is null or paga_con >= 0),
    check (distancia_delivery_m is null or distancia_delivery_m >= 0),
    check (tipo_entrega <> 'retiro' or distancia_delivery_m is null)
);

create table if not exists public.pedido_items (
    id uuid primary key default gen_random_uuid(),
    pedido_id uuid not null references public.pedidos(id) on delete cascade,
    publicacion_id uuid references public.publicaciones(id) on delete set null,
    nombre_snapshot text not null,
    precio_unitario_snapshot numeric(12,2) not null check (precio_unitario_snapshot >= 0),
    cantidad integer not null check (cantidad between 1 and 99),
    subtotal numeric(12,2) not null check (
        subtotal >= 0 and subtotal = precio_unitario_snapshot * cantidad
    ),
    created_at timestamptz not null default now()
);

create index if not exists idx_pedidos_comercio_estado_fecha
    on public.pedidos (comercio_id, estado, created_at desc);
create index if not exists idx_pedidos_comercio_cliente
    on public.pedidos (comercio_id, whatsapp_normalizado, created_at desc);
create index if not exists idx_pedido_items_pedido on public.pedido_items (pedido_id);

create or replace function public.asignar_numero_pedido_clicklocal()
returns trigger language plpgsql security definer set search_path = public as $$
declare siguiente bigint;
begin
    insert into public.pedido_contadores (comercio_id, ultimo_numero)
    values (new.comercio_id, 1)
    on conflict (comercio_id) do update
      set ultimo_numero = public.pedido_contadores.ultimo_numero + 1,
          updated_at = now()
    returning ultimo_numero into siguiente;
    new.numero := siguiente;
    return new;
end;
$$;

revoke all privileges on function public.asignar_numero_pedido_clicklocal() from public;
revoke all privileges on function public.asignar_numero_pedido_clicklocal() from anon;
revoke all privileges on function public.asignar_numero_pedido_clicklocal() from authenticated;

drop trigger if exists trg_asignar_numero_pedido_clicklocal on public.pedidos;
create trigger trg_asignar_numero_pedido_clicklocal
before insert on public.pedidos for each row
execute function public.asignar_numero_pedido_clicklocal();

create or replace function public.crear_pedido_clicklocal(
    p_pedido jsonb,
    p_items jsonb
) returns public.pedidos
language plpgsql security definer set search_path = public as $$
declare
    creado public.pedidos;
    existente public.pedidos;
    v_comercio_id uuid;
    v_idempotency_key uuid;
    v_idempotency_fingerprint text;
begin
    if p_pedido is null or jsonb_typeof(p_pedido) <> 'object' then
        raise exception 'El pedido debe ser un objeto JSON válido.';
    end if;
    if p_items is null or jsonb_typeof(p_items) <> 'array'
       or jsonb_array_length(p_items) = 0 then
        raise exception 'El pedido debe contener al menos un item.';
    end if;

    v_comercio_id := (p_pedido->>'comercio_id')::uuid;
    v_idempotency_key := (p_pedido->>'idempotency_key')::uuid;
    v_idempotency_fingerprint := p_pedido->>'idempotency_fingerprint';
    if v_idempotency_fingerprint is null
       or v_idempotency_fingerprint !~ '^[0-9a-f]{64}$' then
        raise exception 'El fingerprint de idempotencia no es válido.';
    end if;

    -- Serializa reintentos concurrentes de la misma key. El UNIQUE de la tabla
    -- permanece como defensa final de integridad.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            v_comercio_id::text || ':' || v_idempotency_key::text,
            0
        )
    );

    select * into existente
    from public.pedidos
    where comercio_id = v_comercio_id
      and idempotency_key = v_idempotency_key;

    if found then
        if existente.idempotency_fingerprint is distinct from v_idempotency_fingerprint then
            raise exception 'La clave de idempotencia ya fue usada para otro pedido.';
        end if;
        return existente;
    end if;

    insert into public.pedidos (
        comercio_id, idempotency_key, idempotency_fingerprint,
        numero, estado, nombre_cliente, whatsapp_cliente,
        whatsapp_normalizado, tipo_entrega, direccion, referencia,
        forma_pago, paga_con, subtotal, costo_envio, total,
        distancia_delivery_m, observaciones, texto_pedido
    ) values (
        v_comercio_id, v_idempotency_key, v_idempotency_fingerprint,
        0, 'nuevo',
        p_pedido->>'nombre_cliente', p_pedido->>'whatsapp_cliente',
        p_pedido->>'whatsapp_normalizado', p_pedido->>'tipo_entrega',
        nullif(p_pedido->>'direccion',''), nullif(p_pedido->>'referencia',''),
        p_pedido->>'forma_pago', nullif(p_pedido->>'paga_con','')::numeric,
        (p_pedido->>'subtotal')::numeric, (p_pedido->>'costo_envio')::numeric,
        (p_pedido->>'total')::numeric,
        nullif(p_pedido->>'distancia_delivery_m','')::integer,
        nullif(p_pedido->>'observaciones',''), p_pedido->>'texto_pedido'
    ) returning * into creado;

    insert into public.pedido_items (
        pedido_id, publicacion_id, nombre_snapshot,
        precio_unitario_snapshot, cantidad, subtotal
    )
    select creado.id, (item->>'publicacion_id')::uuid,
           item->>'nombre_snapshot', (item->>'precio_unitario_snapshot')::numeric,
           (item->>'cantidad')::integer, (item->>'subtotal')::numeric
    from jsonb_array_elements(p_items) item;

    return creado;
end;
$$;

revoke all on function public.crear_pedido_clicklocal(jsonb, jsonb) from public;
revoke all on function public.crear_pedido_clicklocal(jsonb, jsonb) from anon;
revoke all on function public.crear_pedido_clicklocal(jsonb, jsonb) from authenticated;
grant execute on function public.crear_pedido_clicklocal(jsonb, jsonb) to service_role;

alter table public.pedidos_configuracion enable row level security;
alter table public.pedido_contadores enable row level security;
alter table public.pedidos enable row level security;
alter table public.pedido_items enable row level security;

revoke all privileges on table public.pedidos_configuracion from public, anon, authenticated;
revoke all privileges on table public.pedido_contadores from public, anon, authenticated;
revoke all privileges on table public.pedidos from public, anon, authenticated;
revoke all privileges on table public.pedido_items from public, anon, authenticated;

revoke all privileges on table public.pedidos_configuracion from service_role;
revoke all privileges on table public.pedido_contadores from service_role;
revoke all privileges on table public.pedidos from service_role;
revoke all privileges on table public.pedido_items from service_role;

grant select, insert, update on table public.pedidos_configuracion to service_role;
grant select, update on table public.pedidos to service_role;
grant select on table public.pedido_items to service_role;

-- El backend usa service_role. No se crean políticas públicas: el navegador
-- nunca escribe precios, estados ni pedidos directamente en Supabase.

commit;
