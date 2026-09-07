-- CLICKLOCAL GASTRONOMIA V1 - BLOQUE 1
-- Modelo canonico de estados operativos y de pago.
-- Preparada para ejecucion manual posterior. NO fue ejecutada.

begin;

alter table public.gastronomia_pedidos
    add column if not exists estado_pago text;

alter table public.gastronomia_pedidos
    add column if not exists pagado_at timestamptz;

alter table public.gastronomia_pedidos
    add column if not exists cerrado_at timestamptz;

-- La constraint legacy no admite "pendiente": debe retirarse antes del backfill.
alter table public.gastronomia_pedidos
    drop constraint if exists gastronomia_pedidos_estado_check;

update public.gastronomia_pedidos
set estado = 'pendiente'
where estado = 'recibido';

update public.gastronomia_pedidos
set estado_pago = 'pendiente'
where estado_pago is null;

alter table public.gastronomia_pedidos
    alter column estado_pago set default 'pendiente';

alter table public.gastronomia_pedidos
    alter column estado_pago set not null;

alter table public.gastronomia_pedidos
    add constraint gastronomia_pedidos_estado_check
    check (estado in (
        'pendiente',
        'marchando',
        'preparado',
        'cerrado',
        'cancelado'
    ));

alter table public.gastronomia_pedidos
    drop constraint if exists gastronomia_pedidos_estado_pago_check;

alter table public.gastronomia_pedidos
    add constraint gastronomia_pedidos_estado_pago_check
    check (estado_pago in ('pendiente', 'pagado'));

alter table public.gastronomia_pedidos
    drop constraint if exists gastronomia_pedidos_origen_check;

alter table public.gastronomia_pedidos
    add constraint gastronomia_pedidos_origen_check
    check (origen in (
        'clicklocal',
        'pos',
        'whatsapp',
        'telefono',
        'qr_mesa'
    ));

alter table public.gastronomia_pedidos
    drop constraint if exists gastronomia_pedidos_tipo_entrega_check;

alter table public.gastronomia_pedidos
    add constraint gastronomia_pedidos_tipo_entrega_check
    check (tipo_entrega in (
        'delivery',
        'retiro',
        'mesa',
        'mostrador'
    ));

create index if not exists
    idx_gastronomia_pedidos_comercio_estado_pago_fecha
on public.gastronomia_pedidos (
    comercio_id,
    estado_pago,
    created_at desc
);

create or replace function
public.actualizar_updated_at_gastronomia_pedidos()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists
    trg_actualizar_updated_at_gastronomia_pedidos
on public.gastronomia_pedidos;

create trigger trg_actualizar_updated_at_gastronomia_pedidos
before update
on public.gastronomia_pedidos
for each row
execute function public.actualizar_updated_at_gastronomia_pedidos();

commit;
