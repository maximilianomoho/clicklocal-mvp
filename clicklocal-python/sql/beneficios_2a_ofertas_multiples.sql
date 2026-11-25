-- CLICKLOCAL 2.0 - BENEFICIOS - OFERTAS MULTIPLES Y GENERALES
-- Migración incremental sobre beneficios_1a.
-- No modifica filas de beneficios, series, códigos ni eventos.
-- No ejecutar sin autorización específica.
begin;

-- Evita que otra transacción inserte o modifique objetivos mientras se
-- reemplazan la restricción, los índices y la función de pertenencia.
lock table public.beneficios in access exclusive mode;

do $preflight$
declare
  v_marca_funcion text;
begin
  if to_regclass('public.beneficios') is null then
    raise exception
      'Precondicion incumplida: falta public.beneficios';
  end if;

  if to_regclass('public.comercios') is null then
    raise exception
      'Precondicion incumplida: falta public.comercios';
  end if;

  if to_regclass('public.publicaciones') is null then
    raise exception
      'Precondicion incumplida: falta public.publicaciones';
  end if;

  if to_regclass('public.gastronomia_productos') is null then
    raise exception
      'Precondicion incumplida: falta public.gastronomia_productos';
  end if;

  if not exists (
    select 1
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'beneficios'
       and column_name = 'comercio_id'
       and data_type = 'uuid'
       and is_nullable = 'NO'
  ) then
    raise exception
      'Precondicion incumplida: beneficios.comercio_id';
  end if;

  if not exists (
    select 1
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'beneficios'
       and column_name = 'tipo_objetivo'
       and data_type = 'text'
       and is_nullable = 'NO'
  ) then
    raise exception
      'Precondicion incumplida: beneficios.tipo_objetivo';
  end if;

  if not exists (
    select 1
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'beneficios'
       and column_name = 'publicacion_id'
       and data_type = 'uuid'
       and is_nullable = 'YES'
  ) then
    raise exception
      'Precondicion incumplida: beneficios.publicacion_id';
  end if;

  if not exists (
    select 1
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'beneficios'
       and column_name = 'gastronomia_producto_id'
       and data_type = 'uuid'
       and is_nullable = 'YES'
  ) then
    raise exception
      'Precondicion incumplida: beneficios.gastronomia_producto_id';
  end if;

  if not exists (
    select 1
      from pg_constraint
     where conrelid = 'public.beneficios'::regclass
       and conname = 'beneficios_objetivo_check'
       and contype = 'c'
  ) then
    raise exception
      'Precondicion incumplida: falta beneficios_objetivo_check';
  end if;

  if not exists (
    select 1
      from pg_constraint
     where conrelid = 'public.beneficios'::regclass
       and conname = 'beneficios_comercio_id_fkey'
       and contype = 'f'
  ) then
    raise exception
      'Precondicion incumplida: falta beneficios_comercio_id_fkey';
  end if;

  if to_regclass(
    'public.beneficios_publicacion_activa_unique'
  ) is null then
    raise exception
      'Precondicion incumplida: falta '
      'beneficios_publicacion_activa_unique';
  end if;

  if to_regclass(
    'public.beneficios_producto_activo_unique'
  ) is null then
    raise exception
      'Precondicion incumplida: falta '
      'beneficios_producto_activo_unique';
  end if;

  if to_regclass(
    'public.beneficios_serie_edicion_unique'
  ) is null then
    raise exception
      'Precondicion incumplida: falta '
      'beneficios_serie_edicion_unique';
  end if;

  if to_regclass(
    'public.beneficios_comercio_activo_idx'
  ) is null then
    raise exception
      'Precondicion incumplida: falta '
      'beneficios_comercio_activo_idx';
  end if;

  if to_regclass(
    'public.beneficios_publicacion_activa_idx'
  ) is not null
    or to_regclass(
      'public.beneficios_producto_activo_idx'
    ) is not null
    or to_regclass(
      'public.beneficios_comercio_general_activo_idx'
    ) is not null
  then
    raise exception
      'Precondicion incumplida: existen objetos de Beneficios 2A';
  end if;

  if to_regprocedure(
    'public.beneficios_validar_objetivo_comercio()'
  ) is null then
    raise exception
      'Precondicion incumplida: falta '
      'beneficios_validar_objetivo_comercio()';
  end if;

  select obj_description(
    to_regprocedure(
      'public.beneficios_validar_objetivo_comercio()'
    ),
    'pg_proc'
  )
  into v_marca_funcion;

  if v_marca_funcion is distinct from
    'clicklocal:beneficios_1a:v2'
  then
    raise exception
      'Precondicion incumplida: marca inesperada de la funcion';
  end if;

  if not exists (
    select 1
      from pg_trigger
     where tgrelid = 'public.beneficios'::regclass
       and tgname =
         'beneficios_validar_objetivo_comercio_trigger'
       and not tgisinternal
  ) then
    raise exception
      'Precondicion incumplida: falta el trigger de pertenencia';
  end if;

  if exists (
    select 1
      from public.beneficios
     where tipo_objetivo not in (
       'publicacion',
       'gastronomia_producto'
     )
  ) then
    raise exception
      'Precondicion incumplida: tipo_objetivo desconocido';
  end if;
end
$preflight$;

-- Estos son los dos únicos objetos que actualmente impiden varias
-- ofertas activas sobre una misma publicación o producto.
drop index public.beneficios_publicacion_activa_unique;
drop index public.beneficios_producto_activo_unique;

-- Se conserva el nombre de la restricción para que las funciones,
-- diagnósticos y futuras migraciones tengan una referencia estable.
alter table public.beneficios
  drop constraint beneficios_objetivo_check;

alter table public.beneficios
  add constraint beneficios_objetivo_check check (
    (
      tipo_objetivo = 'comercio'
      and publicacion_id is null
      and gastronomia_producto_id is null
    )
    or (
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

-- Los nuevos índices no son únicos: aceleran las mismas consultas sin
-- limitar la cantidad de ofertas activas.
create index beneficios_publicacion_activa_idx
  on public.beneficios(
    publicacion_id,
    vigente_desde,
    vigente_hasta
  )
  where activo and publicacion_id is not null;

create index beneficios_producto_activo_idx
  on public.beneficios(
    gastronomia_producto_id,
    vigente_desde,
    vigente_hasta
  )
  where activo and gastronomia_producto_id is not null;

create index beneficios_comercio_general_activo_idx
  on public.beneficios(
    comercio_id,
    vigente_desde,
    vigente_hasta
  )
  where activo and tipo_objetivo = 'comercio';

-- El trigger existente permanece asociado a esta función. CREATE OR
-- REPLACE conserva su OID, propietario y permisos.
create or replace function
public.beneficios_validar_objetivo_comercio()
returns trigger
language plpgsql
set search_path = public, pg_catalog
as $f$
declare
  v_comercio_id uuid;
begin
  if new.tipo_objetivo = 'comercio' then
    select id
      into v_comercio_id
      from public.comercios
     where id = new.comercio_id;
  elsif new.tipo_objetivo = 'publicacion' then
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
is 'clicklocal:beneficios_2a:v1';

commit;
