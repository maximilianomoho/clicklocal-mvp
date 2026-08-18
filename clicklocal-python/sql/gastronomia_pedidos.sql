-- ============================================================
-- CLICKLOCAL GASTRONOMIA
-- ESTRUCTURA V1 DE PEDIDOS
-- ============================================================
--
-- Objetivo:
-- - Registrar cada pedido antes de enviarlo por WhatsApp.
-- - Número correlativo independiente por comercio.
-- - Guardar datos mínimos para métricas futuras.
-- - Mantener detalle flexible en JSONB.
--
-- IMPORTANTE:
-- Este archivo crea estructura.
-- No elimina datos existentes.
-- ============================================================

begin;


-- ============================================================
-- 1. CONTADOR DE PEDIDOS POR COMERCIO
-- ============================================================

create table if not exists public.gastronomia_pedido_contadores (
    comercio_id uuid primary key
        references public.comercios(id)
        on delete cascade,

    ultimo_numero bigint not null default 0,

    updated_at timestamptz not null default now()
);


-- ============================================================
-- 2. TABLA PRINCIPAL DE PEDIDOS
-- ============================================================

create table if not exists public.gastronomia_pedidos (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete restrict,

    numero_pedido bigint not null,

    -- Datos del cliente
    cliente_nombre text not null,
    cliente_apellido text not null,

    whatsapp text not null,
    whatsapp_normalizado text not null,

    -- Pedido
    total numeric(12,2) not null
        check (total >= 0),

    modalidad text not null
        check (modalidad in ('Delivery', 'Retiro')),

    direccion_entrega text,

    forma_pago text not null
        check (forma_pago in ('Efectivo', 'Transferencia')),

    paga_con numeric(12,2),

    aclaracion_general text,

    -- Productos, cantidades, extras y aclaraciones
    detalle jsonb not null default '[]'::jsonb,

    -- Texto exacto que termina viendo comercio/WhatsApp
    texto_pedido text not null,

    created_at timestamptz not null default now(),

    constraint gastronomia_pedidos_numero_por_comercio_unique
        unique (comercio_id, numero_pedido),

    constraint gastronomia_pedidos_delivery_direccion_check
        check (
            modalidad <> 'Delivery'
            or (
                direccion_entrega is not null
                and btrim(direccion_entrega) <> ''
            )
        ),

    constraint gastronomia_pedidos_efectivo_paga_con_check
        check (
            forma_pago <> 'Efectivo'
            or (
                paga_con is not null
                and paga_con >= total
            )
        )
);


-- ============================================================
-- 3. INDICES PARA METRICAS
-- ============================================================

create index if not exists
    idx_gastronomia_pedidos_comercio_fecha
on public.gastronomia_pedidos (
    comercio_id,
    created_at desc
);


create index if not exists
    idx_gastronomia_pedidos_comercio_cliente
on public.gastronomia_pedidos (
    comercio_id,
    whatsapp_normalizado
);


create index if not exists
    idx_gastronomia_pedidos_whatsapp_normalizado
on public.gastronomia_pedidos (
    whatsapp_normalizado
);


-- ============================================================
-- 4. FUNCION PARA NUMERO CORRELATIVO
-- ============================================================

create or replace function
public.asignar_numero_pedido_gastronomia()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin

    if new.numero_pedido is null or new.numero_pedido <= 0 then

        insert into public.gastronomia_pedido_contadores (
            comercio_id,
            ultimo_numero,
            updated_at
        )
        values (
            new.comercio_id,
            1,
            now()
        )

        on conflict (comercio_id)
        do update
        set
            ultimo_numero =
                public.gastronomia_pedido_contadores.ultimo_numero + 1,

            updated_at = now()

        returning ultimo_numero
        into new.numero_pedido;

    end if;

    return new;

end;
$$;


-- ============================================================
-- 5. TRIGGER AUTOMATICO
-- ============================================================

drop trigger if exists
    trg_asignar_numero_pedido_gastronomia
on public.gastronomia_pedidos;


create trigger trg_asignar_numero_pedido_gastronomia

before insert
on public.gastronomia_pedidos

for each row

execute function
    public.asignar_numero_pedido_gastronomia();


-- ============================================================
-- 6. SEGURIDAD
-- ============================================================

alter table public.gastronomia_pedidos
    enable row level security;

alter table public.gastronomia_pedido_contadores
    enable row level security;

-- No creamos políticas públicas.
-- El registro V1 se hará exclusivamente desde Flask
-- utilizando supabase_admin.


commit;
