-- Gastronomía Base: contrato inicial para modalidades de gestión.
--
-- Los comercios actuales y nuevos comienzan en Solo WhatsApp.
-- Los snapshots históricos de pedidos se conservan sin reclasificar.

begin;

alter table public.gastronomia_configuracion
    add column if not exists modalidad_pedidos text;

alter table public.gastronomia_pedidos
    add column if not exists modalidad_gestion text;

alter table public.gastronomia_configuracion
    alter column modalidad_pedidos set default 'solo_whatsapp';

update public.gastronomia_configuracion
set modalidad_pedidos = 'solo_whatsapp'
where modalidad_pedidos is null;

do $migration$
begin
    if not exists (
        select 1
        from pg_catalog.pg_constraint
        where conrelid = 'public.gastronomia_configuracion'::regclass
          and conname = 'gastronomia_configuracion_modalidad_pedidos_check'
    ) then
        alter table public.gastronomia_configuracion
            add constraint gastronomia_configuracion_modalidad_pedidos_check
            check (
                modalidad_pedidos is null
                or modalidad_pedidos in ('solo_whatsapp', 'bandeja')
            ) not valid;
    end if;
end
$migration$;

do $migration$
begin
    if not exists (
        select 1
        from pg_catalog.pg_constraint
        where conrelid = 'public.gastronomia_pedidos'::regclass
          and conname = 'gastronomia_pedidos_modalidad_gestion_check'
    ) then
        alter table public.gastronomia_pedidos
            add constraint gastronomia_pedidos_modalidad_gestion_check
            check (
                modalidad_gestion is null
                or modalidad_gestion in ('solo_whatsapp', 'bandeja')
            ) not valid;
    end if;
end
$migration$;

alter table public.gastronomia_configuracion
    validate constraint gastronomia_configuracion_modalidad_pedidos_check;

alter table public.gastronomia_pedidos
    validate constraint gastronomia_pedidos_modalidad_gestion_check;

commit;
