-- CLICKLOCAL 2.0 - BENEFICIOS - ROLLBACK OFERTAS MULTIPLES
-- No elimina datos. Se detiene si volver al modelo anterior perdería soporte.
-- No ejecutar sin autorización específica.
begin;

do $preflight$
begin
  if exists (
    select 1
      from public.beneficios
     where tipo_objetivo = 'comercio'
  ) then
    raise exception
      'Rollback detenido: existen ofertas generales de comercio';
  end if;

  if exists (
    select publicacion_id
      from public.beneficios
     where activo
       and publicacion_id is not null
     group by publicacion_id
    having count(*) > 1
  ) then
    raise exception
      'Rollback detenido: existen varias ofertas activas por publicacion';
  end if;

  if exists (
    select gastronomia_producto_id
      from public.beneficios
     where activo
       and gastronomia_producto_id is not null
     group by gastronomia_producto_id
    having count(*) > 1
  ) then
    raise exception
      'Rollback detenido: existen varias ofertas activas por producto';
  end if;
end $preflight$;

drop index public.beneficios_comercio_general_activo_idx;
drop index public.beneficios_producto_activo_idx;
drop index public.beneficios_publicacion_activa_idx;

alter table public.beneficios
  drop constraint beneficios_objetivo_check;

alter table public.beneficios
  add constraint beneficios_objetivo_check check (
    (
      tipo_objetivo = 'publicacion'
      and publicacion_id is not null
      and gastronomia_producto_id is null
    )
    or (
      tipo_objetivo = 'gastronomia_producto'
      and publicacion_id is null
      and gastronomia_producto_id is not null
    )
  );

create unique index beneficios_publicacion_activa_unique
  on public.beneficios(publicacion_id)
  where activo and publicacion_id is not null;

create unique index beneficios_producto_activo_unique
  on public.beneficios(gastronomia_producto_id)
  where activo and gastronomia_producto_id is not null;

create or replace function
public.beneficios_validar_objetivo_comercio()
returns trigger
language plpgsql
set search_path = public, pg_catalog
as $f$
declare
  v_comercio_id uuid;
begin
  if new.tipo_objetivo = 'publicacion' then
    select comercio_id
      into v_comercio_id
      from public.publicaciones
     where id = new.publicacion_id;
  elsif new.tipo_objetivo = 'gastronomia_producto' then
    select comercio_id
      into v_comercio_id
      from public.gastronomia_productos
     where id = new.gastronomia_producto_id;
  end if;

  if v_comercio_id is null then
    raise exception 'Objetivo inexistente';
  end if;

  if v_comercio_id <> new.comercio_id then
    raise exception 'Objetivo de otro comercio';
  end if;

  return new;
end
$f$;

comment on function
public.beneficios_validar_objetivo_comercio()
is 'clicklocal:beneficios_1a:v2';

comment on table public.beneficios
is 'clicklocal:beneficios_1a:v2';

commit;
