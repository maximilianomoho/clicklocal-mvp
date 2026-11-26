-- CLICKLOCAL 2.0 - BENEFICIOS - ESQUEMA DEFINITIVO PREPARADO
-- No ejecutar sin autorización. No almacena códigos ni tokens QR en claro.
begin;

do $preflight$
declare r record; n text;
begin
  for r in select * from (values
    ('comercios','id','uuid'), ('publicaciones','id','uuid'),
    ('publicaciones','comercio_id','uuid'),
    ('gastronomia_productos','id','uuid'),
    ('gastronomia_productos','comercio_id','uuid'),
    ('gastronomia_productos','precio_promocional','numeric'),
    ('gastronomia_productos','promocion_desde','date'),
    ('gastronomia_productos','promocion_hasta','date')
  ) v(tabla,columna,tipo) loop
    if not exists (select 1 from pg_attribute a join pg_class c on c.oid=a.attrelid
      join pg_namespace s on s.oid=c.relnamespace where s.nspname='public'
      and c.relname=r.tabla and c.relkind in ('r','p') and a.attname=r.columna
      and a.attnum>0 and not a.attisdropped
      and a.atttypid=to_regtype(r.tipo)) then
      raise exception 'Precondicion incumplida: %.% debe ser %',r.tabla,r.columna,r.tipo;
    end if;
  end loop;
  if to_regprocedure('gen_random_uuid()') is null then raise exception 'Falta gen_random_uuid()'; end if;
  if not exists(select 1 from pg_roles where rolname='anon')
    or not exists(select 1 from pg_roles where rolname='authenticated')
    or not exists(select 1 from pg_roles where rolname='service_role') then
    raise exception 'Faltan roles Supabase';
  end if;
  foreach n in array array[
    'beneficios','beneficios_codigos','beneficios_eventos','beneficios_qr_comercios',
    'beneficios_serie_edicion_unique','beneficios_publicacion_activa_unique',
    'beneficios_producto_activo_unique','beneficios_comercio_activo_idx',
    'beneficios_vigencia_idx','beneficios_codigos_hash_unique',
    'beneficios_codigos_idempotencia_unique','beneficios_codigos_cupo_idx',
    'beneficios_eventos_emision_unique','beneficios_eventos_consumo_unique',
    'beneficios_eventos_carrito_unique','beneficios_eventos_analitica_idx',
    'beneficios_qr_token_hash_unique'
  ] loop if to_regclass('public.'||n) is not null then raise exception 'Colision de objeto: %',n; end if; end loop;
  foreach n in array array[
    'beneficios_validar_objetivo_comercio()','beneficios_validar_edicion()',
    'beneficios_validar_codigo()','beneficios_validar_evento()',
    'beneficios_actualizar_updated_at()','beneficios_proteger_inmutabilidad()',
    'beneficios_validar_conflicto_gastronomico()',
    'beneficios_gastronomia_validar_conflicto()',
    'beneficios_emitir_codigo(uuid,uuid,bytea,text,integer)',
    'beneficios_consumir_codigo(bytea,text,uuid,bytea,text,uuid)',
    'beneficios_validar_codigo_carrito(bytea,uuid,uuid)'
  ] loop if to_regprocedure('public.'||n) is not null then raise exception 'Colision de funcion: %',n; end if; end loop;
  foreach n in array array[
    'beneficios_validar_objetivo_comercio_trigger','beneficios_validar_edicion_trigger',
    'beneficios_inmutabilidad_trigger','beneficios_conflicto_gastronomico_trigger',
    'beneficios_actualizar_updated_at_trigger','beneficios_validar_codigo_trigger',
    'beneficios_validar_evento_trigger','beneficios_gastronomia_conflicto_trigger'
  ] loop if exists(select 1 from pg_trigger where tgname=n and not tgisinternal) then
    raise exception 'Colision de trigger: %',n; end if; end loop;
end $preflight$;

create table public.beneficios (
 id uuid primary key default gen_random_uuid(), serie_id uuid not null default gen_random_uuid(),
 numero_edicion integer not null default 1,
 edicion_anterior_id uuid references public.beneficios(id) on delete restrict,
 comercio_id uuid not null references public.comercios(id) on delete restrict,
 titulo text not null, tipo_objetivo text not null,
 publicacion_id uuid references public.publicaciones(id) on delete restrict,
 gastronomia_producto_id uuid references public.gastronomia_productos(id) on delete restrict,
 tipo_descuento text not null, valor_descuento numeric,
 vigente_desde timestamptz not null, vigente_hasta timestamptz not null,
 dias_semana_mask smallint not null default 127, hora_desde time, hora_hasta time,
 zona_horaria text not null default 'America/Argentina/Cordoba', cupo_maximo integer,
 maximo_unidades_codigo integer not null default 1,
 mostrar_codigos_restantes boolean not null default false,
 tema_etiqueta text not null default 'clara', permite_whatsapp boolean not null default true,
 permite_qr boolean not null default true, permite_carrito boolean not null default true,
 activo boolean not null default true, created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(),
 constraint beneficios_titulo_check check(btrim(titulo)<>''),
 constraint beneficios_edicion_check check(numero_edicion>=1 and
   ((numero_edicion=1 and edicion_anterior_id is null) or (numero_edicion>1 and edicion_anterior_id is not null))),
 constraint beneficios_objetivo_check check(
   (tipo_objetivo='publicacion' and publicacion_id is not null and gastronomia_producto_id is null) or
   (tipo_objetivo='gastronomia_producto' and publicacion_id is null and gastronomia_producto_id is not null)),
 constraint beneficios_tipo_check check(tipo_descuento in
   ('porcentaje','monto_fijo','precio_especial','dos_por_uno','tres_por_dos','segunda_unidad_50')),
 constraint beneficios_valor_check check(
   (tipo_descuento='porcentaje' and valor_descuento>0 and valor_descuento<=100) or
   (tipo_descuento in ('monto_fijo','precio_especial') and valor_descuento>=0) or
   (tipo_descuento in ('dos_por_uno','tres_por_dos','segunda_unidad_50') and valor_descuento is null)),
 constraint beneficios_vigencia_check check(vigente_hasta>vigente_desde),
 constraint beneficios_dias_check check(dias_semana_mask between 1 and 127),
 constraint beneficios_horario_check check((hora_desde is null and hora_hasta is null) or
   (hora_desde is not null and hora_hasta is not null and hora_desde<>hora_hasta)),
 constraint beneficios_zona_check check(btrim(zona_horaria)<>''),
 constraint beneficios_cupo_check check(cupo_maximo is null or cupo_maximo>=1),
 constraint beneficios_unidades_check check(maximo_unidades_codigo>=1),
 constraint beneficios_tema_check check(tema_etiqueta in ('clara','oscura')),
 constraint beneficios_metodos_check check(permite_whatsapp or permite_qr or permite_carrito)
);

create table public.beneficios_codigos (
 id uuid primary key default gen_random_uuid(),
 beneficio_id uuid not null references public.beneficios(id) on delete restrict,
 comercio_id uuid not null references public.comercios(id) on delete restrict,
 codigo_hash bytea not null, consumidor_id text, idempotency_key uuid not null,
 cantidad_unidades integer not null, emitido_en timestamptz not null,
 vence_en timestamptz not null, utilizado_en timestamptz, canal_utilizacion text,
 referencia_tipo text, referencia_id uuid, created_at timestamptz not null default now(),
 constraint beneficios_codigos_hash_check check(octet_length(codigo_hash)=32),
 constraint beneficios_codigos_consumidor_check check(consumidor_id is null or btrim(consumidor_id)<>''),
 constraint beneficios_codigos_unidades_check check(cantidad_unidades>=1),
 constraint beneficios_codigos_vencimiento_check check(vence_en>emitido_en and vence_en<=emitido_en+interval '24 hours'),
 constraint beneficios_codigos_consumo_check check(
   (utilizado_en is null and canal_utilizacion is null) or
   (utilizado_en is not null and canal_utilizacion in ('whatsapp','qr','carrito')
    and utilizado_en>=emitido_en and utilizado_en<vence_en)),
 constraint beneficios_codigos_referencia_check check(
   (referencia_tipo is null and referencia_id is null) or
   (btrim(referencia_tipo)<>'' and referencia_id is not null))
);

create table public.beneficios_eventos (
 id uuid primary key default gen_random_uuid(),
 beneficio_id uuid not null references public.beneficios(id) on delete restrict,
 codigo_id uuid references public.beneficios_codigos(id) on delete restrict,
 comercio_id uuid not null references public.comercios(id) on delete restrict,
 tipo_evento text not null, canal text, referencia_tipo text, referencia_id uuid,
 ocurrido_en timestamptz not null, created_at timestamptz not null default now(),
 constraint beneficios_eventos_tipo_check check(tipo_evento in
  ('visualizacion','codigo_emitido','activacion_whatsapp','validacion_qr',
   'codigo_ingresado_carrito','codigo_consumido_carrito')),
 constraint beneficios_eventos_codigo_check check((tipo_evento='visualizacion' and codigo_id is null) or
   (tipo_evento<>'visualizacion' and codigo_id is not null)),
 constraint beneficios_eventos_canal_check check(
   (tipo_evento in ('visualizacion','codigo_emitido') and canal is null) or
   (tipo_evento='activacion_whatsapp' and canal='whatsapp') or
   (tipo_evento='validacion_qr' and canal='qr') or
   (tipo_evento in ('codigo_ingresado_carrito','codigo_consumido_carrito') and canal='carrito')),
 constraint beneficios_eventos_referencia_check check(
   (referencia_tipo is null and referencia_id is null) or
   (btrim(referencia_tipo)<>'' and referencia_id is not null))
);

create table public.beneficios_qr_comercios (
 comercio_id uuid primary key references public.comercios(id) on delete restrict,
 token_hash bytea not null, created_at timestamptz not null default now(), rotated_at timestamptz,
 constraint beneficios_qr_hash_check check(octet_length(token_hash)=32),
 constraint beneficios_qr_rotacion_check check(rotated_at is null or rotated_at>=created_at)
);

create function public.beneficios_validar_objetivo_comercio() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$
declare v uuid; begin
 if new.tipo_objetivo='publicacion' then select comercio_id into v from public.publicaciones where id=new.publicacion_id;
 elsif new.tipo_objetivo='gastronomia_producto' then select comercio_id into v from public.gastronomia_productos where id=new.gastronomia_producto_id; end if;
 if v is null then raise exception 'Objetivo inexistente'; end if;
 if v<>new.comercio_id then raise exception 'Objetivo de otro comercio'; end if; return new;
end $f$;

create function public.beneficios_validar_edicion() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$
declare a public.beneficios; begin
 if not exists(select 1 from pg_timezone_names where name=new.zona_horaria) then raise exception 'Zona horaria invalida'; end if;
 if new.edicion_anterior_id is not null then select * into a from public.beneficios where id=new.edicion_anterior_id;
  if not found or a.serie_id<>new.serie_id or a.numero_edicion+1<>new.numero_edicion
   or a.comercio_id<>new.comercio_id or a.tipo_objetivo<>new.tipo_objetivo
   or a.publicacion_id is distinct from new.publicacion_id
   or a.gastronomia_producto_id is distinct from new.gastronomia_producto_id then raise exception 'Cadena de ediciones invalida'; end if;
 end if; return new;
end $f$;

create function public.beneficios_validar_codigo() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$
declare b public.beneficios; begin select * into b from public.beneficios where id=new.beneficio_id;
 if not found or b.comercio_id<>new.comercio_id then raise exception 'Codigo y beneficio no coinciden'; end if;
 if new.emitido_en<b.vigente_desde or new.emitido_en>=b.vigente_hasta or new.vence_en>b.vigente_hasta then raise exception 'Vigencia de codigo invalida'; end if;
 if new.cantidad_unidades>b.maximo_unidades_codigo then raise exception 'Demasiadas unidades'; end if; return new;
end $f$;

create function public.beneficios_validar_evento() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$
declare bc uuid; cb uuid; cc uuid; cu timestamptz; cm text; begin select comercio_id into bc from public.beneficios where id=new.beneficio_id;
 if bc is null or bc<>new.comercio_id then raise exception 'Evento de otro comercio'; end if;
 if new.codigo_id is not null then select beneficio_id,comercio_id,utilizado_en,canal_utilizacion into cb,cc,cu,cm from public.beneficios_codigos where id=new.codigo_id;
  if cb is null or cb<>new.beneficio_id or cc<>new.comercio_id then raise exception 'Codigo no coincide con evento'; end if;
  if new.tipo_evento in ('activacion_whatsapp','validacion_qr','codigo_consumido_carrito')
   and (cu is null or cm is distinct from new.canal) then raise exception 'Evento no coincide con el consumo'; end if;
 end if; return new;
end $f$;

create function public.beneficios_actualizar_updated_at() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$ begin new.updated_at=clock_timestamp(); return new; end $f$;

create function public.beneficios_proteger_inmutabilidad() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$ begin
 if exists(select 1 from public.beneficios_codigos x where x.beneficio_id=old.id) and
  (new.serie_id,new.numero_edicion,new.edicion_anterior_id,
   new.comercio_id,new.tipo_objetivo,new.publicacion_id,new.gastronomia_producto_id,
   new.tipo_descuento,new.valor_descuento,new.vigente_desde,new.vigente_hasta,
   new.dias_semana_mask,new.hora_desde,new.hora_hasta,new.zona_horaria,new.cupo_maximo,
   new.maximo_unidades_codigo,new.permite_whatsapp,new.permite_qr,new.permite_carrito)
  is distinct from
  (old.serie_id,old.numero_edicion,old.edicion_anterior_id,
   old.comercio_id,old.tipo_objetivo,old.publicacion_id,old.gastronomia_producto_id,
   old.tipo_descuento,old.valor_descuento,old.vigente_desde,old.vigente_hasta,
   old.dias_semana_mask,old.hora_desde,old.hora_hasta,old.zona_horaria,old.cupo_maximo,
   old.maximo_unidades_codigo,old.permite_whatsapp,old.permite_qr,old.permite_carrito)
 then raise exception 'Edicion con codigos inmutable'; end if; return new;
end $f$;

create function public.beneficios_validar_conflicto_gastronomico() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$
declare p record; d date; h date; begin
 if new.tipo_objetivo<>'gastronomia_producto' or not new.activo then return new; end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
  'beneficios:gastronomia_producto:'||new.gastronomia_producto_id::text,0));
 select precio_promocional,promocion_desde,promocion_hasta into p from public.gastronomia_productos where id=new.gastronomia_producto_id;
 if p.precio_promocional is null then return new; end if;
 d=(new.vigente_desde at time zone new.zona_horaria)::date;
 h=((new.vigente_hasta at time zone new.zona_horaria)-interval '1 microsecond')::date;
 if d<=coalesce(p.promocion_hasta,'infinity'::date) and coalesce(p.promocion_desde,'-infinity'::date)<=h then raise exception 'Conflicto con precio_promocional'; end if; return new;
end $f$;

create function public.beneficios_gastronomia_validar_conflicto() returns trigger
language plpgsql set search_path=public,pg_catalog as $f$ begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
  'beneficios:gastronomia_producto:'||new.id::text,0));
 if new.precio_promocional is not null and exists(select 1 from public.beneficios b
  where b.gastronomia_producto_id=new.id and b.activo
  and (b.vigente_desde at time zone b.zona_horaria)::date<=coalesce(new.promocion_hasta,'infinity'::date)
  and coalesce(new.promocion_desde,'-infinity'::date)<=((b.vigente_hasta at time zone b.zona_horaria)-interval '1 microsecond')::date)
 then raise exception 'Conflicto con Beneficios'; end if; return new;
end $f$;

-- Emisión atómica: el bloqueo serializa el conteo y la reserva del cupo.
create function public.beneficios_emitir_codigo(
 p_beneficio_id uuid,p_idempotency_key uuid,p_codigo_hash bytea,
 p_consumidor_id text,p_cantidad_unidades integer)
returns table(codigo_id uuid,emitido_en timestamptz,vence_en timestamptz)
language plpgsql security definer set search_path=public,pg_catalog as $f$
declare b public.beneficios; c public.beneficios_codigos; ahora timestamptz=clock_timestamp(); ocupados bigint;
begin select b0.* into b from public.beneficios b0 where b0.id=p_beneficio_id for update; if not found then raise exception 'Beneficio inexistente'; end if;
 select x.* into c from public.beneficios_codigos x
 where x.beneficio_id=b.id and x.idempotency_key=p_idempotency_key;
 if found then if c.codigo_hash<>p_codigo_hash or c.cantidad_unidades<>p_cantidad_unidades
  or c.consumidor_id is distinct from nullif(btrim(p_consumidor_id),'') then raise exception 'Idempotencia incompatible'; end if;
  return query select c.id,c.emitido_en,c.vence_en; return; end if;
 if not b.activo or ahora<b.vigente_desde or ahora>=b.vigente_hasta then raise exception 'Beneficio no disponible'; end if;
 if p_idempotency_key is null or p_codigo_hash is null or octet_length(p_codigo_hash)<>32
  or p_cantidad_unidades is null or p_cantidad_unidades<1
  or p_cantidad_unidades>b.maximo_unidades_codigo then raise exception 'Codigo, idempotencia o unidades invalidos'; end if;
 if b.cupo_maximo is not null then select count(*) into ocupados from public.beneficios_codigos x
  where x.beneficio_id=b.id and (x.utilizado_en is not null or x.vence_en>ahora);
  if ocupados>=b.cupo_maximo then raise exception 'Cupo agotado'; end if; end if;
 insert into public.beneficios_codigos(beneficio_id,comercio_id,codigo_hash,consumidor_id,idempotency_key,cantidad_unidades,emitido_en,vence_en)
 values(b.id,b.comercio_id,p_codigo_hash,nullif(btrim(p_consumidor_id),''),p_idempotency_key,p_cantidad_unidades,ahora,least(ahora+interval '24 hours',b.vigente_hasta)) returning * into c;
 insert into public.beneficios_eventos(beneficio_id,codigo_id,comercio_id,tipo_evento,ocurrido_en)
 values(b.id,c.id,b.comercio_id,'codigo_emitido',ahora);
 return query select c.id,c.emitido_en,c.vence_en;
end $f$;

-- Ingreso en carrito: valida y registra, pero no consume.
create function public.beneficios_validar_codigo_carrito(
 p_codigo_hash bytea,p_comercio_id uuid,p_carrito_id uuid)
returns table(codigo_id uuid,beneficio_id uuid,cantidad_unidades integer)
language plpgsql security definer set search_path=public,pg_catalog as $f$
declare c public.beneficios_codigos; b public.beneficios; ahora timestamptz=clock_timestamp(); l timestamp; dia int;
begin if p_codigo_hash is null or octet_length(p_codigo_hash)<>32
 or p_comercio_id is null or p_carrito_id is null then raise exception 'Solicitud de carrito invalida'; end if;
 select x.* into c from public.beneficios_codigos x where x.codigo_hash=p_codigo_hash for update;
 if not found or c.comercio_id<>p_comercio_id or c.utilizado_en is not null or c.vence_en<=ahora then raise exception 'Codigo no disponible'; end if;
 select b0.* into b from public.beneficios b0 where b0.id=c.beneficio_id;
 if not b.permite_carrito or ahora<b.vigente_desde or ahora>=b.vigente_hasta then raise exception 'Carrito no habilitado'; end if;
 l=ahora at time zone b.zona_horaria; if b.hora_desde>b.hora_hasta and l::time<=b.hora_hasta then l=l-interval '1 day'; end if;
 dia=extract(isodow from l)::int-1;
 if (b.dias_semana_mask & (1<<dia))=0 or (b.hora_desde<b.hora_hasta and not (l::time between b.hora_desde and b.hora_hasta))
  or (b.hora_desde>b.hora_hasta and not(l::time>=b.hora_desde or l::time<=b.hora_hasta)) then raise exception 'Fuera de dias u horarios'; end if;
 insert into public.beneficios_eventos(beneficio_id,codigo_id,comercio_id,tipo_evento,canal,referencia_tipo,referencia_id,ocurrido_en)
 values(b.id,c.id,b.comercio_id,'codigo_ingresado_carrito','carrito','carrito',p_carrito_id,ahora)
 on conflict (codigo_id,referencia_id) where tipo_evento='codigo_ingresado_carrito' do nothing;
 return query select c.id,b.id,c.cantidad_unidades;
end $f$;

-- Consumo único. El carrito deberá invocarla en la transacción que crea el pedido.
create function public.beneficios_consumir_codigo(
 p_codigo_hash bytea,p_canal text,p_comercio_id uuid,p_qr_token_hash bytea,
 p_referencia_tipo text,p_referencia_id uuid)
returns table(codigo_id uuid,beneficio_id uuid,utilizado_en timestamptz)
language plpgsql security definer set search_path=public,pg_catalog as $f$
declare c public.beneficios_codigos; b public.beneficios; ahora timestamptz=clock_timestamp(); l timestamp; dia int; e text;
begin if p_canal not in ('whatsapp','qr','carrito') then raise exception 'Canal invalido'; end if;
 if p_codigo_hash is null or octet_length(p_codigo_hash)<>32 or p_comercio_id is null then raise exception 'Solicitud de consumo invalida'; end if;
 select x.* into c from public.beneficios_codigos x where x.codigo_hash=p_codigo_hash for update;
 if not found or c.comercio_id<>p_comercio_id or c.utilizado_en is not null or c.vence_en<=ahora then raise exception 'Codigo no disponible'; end if;
 select b0.* into b from public.beneficios b0 where b0.id=c.beneficio_id;
 if ahora<b.vigente_desde or ahora>=b.vigente_hasta or (p_canal='whatsapp' and not b.permite_whatsapp)
  or (p_canal='qr' and not b.permite_qr) or (p_canal='carrito' and not b.permite_carrito) then raise exception 'Canal no habilitado'; end if;
 if p_canal='qr' and (p_qr_token_hash is null or octet_length(p_qr_token_hash)<>32
  or not exists(select 1 from public.beneficios_qr_comercios q where q.comercio_id=b.comercio_id and q.token_hash=p_qr_token_hash)) then raise exception 'QR invalido'; end if;
 if p_canal='carrito' and (nullif(btrim(p_referencia_tipo),'') is null or p_referencia_id is null) then raise exception 'Falta referencia de pedido'; end if;
 l=ahora at time zone b.zona_horaria; if b.hora_desde>b.hora_hasta and l::time<=b.hora_hasta then l=l-interval '1 day'; end if;
 dia=extract(isodow from l)::int-1;
 if (b.dias_semana_mask & (1<<dia))=0 or (b.hora_desde<b.hora_hasta and not (l::time between b.hora_desde and b.hora_hasta))
  or (b.hora_desde>b.hora_hasta and not(l::time>=b.hora_desde or l::time<=b.hora_hasta)) then raise exception 'Fuera de dias u horarios'; end if;
 update public.beneficios_codigos x set utilizado_en=ahora,canal_utilizacion=p_canal,
  referencia_tipo=p_referencia_tipo,referencia_id=p_referencia_id where x.id=c.id;
 e=case p_canal when 'whatsapp' then 'activacion_whatsapp' when 'qr' then 'validacion_qr' else 'codigo_consumido_carrito' end;
 insert into public.beneficios_eventos(beneficio_id,codigo_id,comercio_id,tipo_evento,canal,referencia_tipo,referencia_id,ocurrido_en)
 values(b.id,c.id,b.comercio_id,e,p_canal,p_referencia_tipo,p_referencia_id,ahora); return query select c.id,b.id,ahora;
end $f$;

create trigger beneficios_validar_objetivo_comercio_trigger before insert or update of comercio_id,tipo_objetivo,publicacion_id,gastronomia_producto_id on public.beneficios for each row execute function public.beneficios_validar_objetivo_comercio();
create trigger beneficios_validar_edicion_trigger before insert or update of serie_id,numero_edicion,edicion_anterior_id,comercio_id,tipo_objetivo,publicacion_id,gastronomia_producto_id,zona_horaria on public.beneficios for each row execute function public.beneficios_validar_edicion();
create trigger beneficios_inmutabilidad_trigger before update on public.beneficios for each row execute function public.beneficios_proteger_inmutabilidad();
create trigger beneficios_conflicto_gastronomico_trigger before insert or update of gastronomia_producto_id,activo,vigente_desde,vigente_hasta,zona_horaria on public.beneficios for each row execute function public.beneficios_validar_conflicto_gastronomico();
create trigger beneficios_actualizar_updated_at_trigger before update on public.beneficios for each row execute function public.beneficios_actualizar_updated_at();
create trigger beneficios_validar_codigo_trigger before insert or update on public.beneficios_codigos for each row execute function public.beneficios_validar_codigo();
create trigger beneficios_validar_evento_trigger before insert or update on public.beneficios_eventos for each row execute function public.beneficios_validar_evento();
create trigger beneficios_gastronomia_conflicto_trigger before insert or update of precio_promocional,promocion_desde,promocion_hasta on public.gastronomia_productos for each row execute function public.beneficios_gastronomia_validar_conflicto();

create unique index beneficios_serie_edicion_unique on public.beneficios(serie_id,numero_edicion);
create unique index beneficios_publicacion_activa_unique on public.beneficios(publicacion_id) where activo and publicacion_id is not null;
create unique index beneficios_producto_activo_unique on public.beneficios(gastronomia_producto_id) where activo and gastronomia_producto_id is not null;
create index beneficios_comercio_activo_idx on public.beneficios(comercio_id,activo);
create index beneficios_vigencia_idx on public.beneficios(vigente_desde,vigente_hasta);
create unique index beneficios_codigos_hash_unique on public.beneficios_codigos(codigo_hash);
create unique index beneficios_codigos_idempotencia_unique on public.beneficios_codigos(beneficio_id,idempotency_key);
create index beneficios_codigos_cupo_idx on public.beneficios_codigos(beneficio_id,vence_en,utilizado_en);
create unique index beneficios_eventos_emision_unique on public.beneficios_eventos(codigo_id) where tipo_evento='codigo_emitido';
create unique index beneficios_eventos_consumo_unique on public.beneficios_eventos(codigo_id) where tipo_evento in ('activacion_whatsapp','validacion_qr','codigo_consumido_carrito');
create unique index beneficios_eventos_carrito_unique on public.beneficios_eventos(codigo_id,referencia_id) where tipo_evento='codigo_ingresado_carrito';
create index beneficios_eventos_analitica_idx on public.beneficios_eventos(beneficio_id,tipo_evento,ocurrido_en desc);
create unique index beneficios_qr_token_hash_unique on public.beneficios_qr_comercios(token_hash);

alter table public.beneficios enable row level security; alter table public.beneficios_codigos enable row level security;
alter table public.beneficios_eventos enable row level security; alter table public.beneficios_qr_comercios enable row level security;
revoke all on table public.beneficios,public.beneficios_codigos,public.beneficios_eventos,public.beneficios_qr_comercios from public,anon,authenticated;
grant select,insert,update on table public.beneficios,public.beneficios_qr_comercios to service_role;
grant select on table public.beneficios_codigos to service_role;
grant select,insert on table public.beneficios_eventos to service_role;
revoke all on function public.beneficios_validar_objetivo_comercio(),public.beneficios_validar_edicion(),public.beneficios_validar_codigo(),public.beneficios_validar_evento(),public.beneficios_actualizar_updated_at(),public.beneficios_proteger_inmutabilidad(),public.beneficios_validar_conflicto_gastronomico(),public.beneficios_gastronomia_validar_conflicto() from public,anon,authenticated;
revoke all on function public.beneficios_emitir_codigo(uuid,uuid,bytea,text,integer),public.beneficios_consumir_codigo(bytea,text,uuid,bytea,text,uuid),public.beneficios_validar_codigo_carrito(bytea,uuid,uuid) from public,anon,authenticated;
grant execute on function public.beneficios_emitir_codigo(uuid,uuid,bytea,text,integer),public.beneficios_consumir_codigo(bytea,text,uuid,bytea,text,uuid),public.beneficios_validar_codigo_carrito(bytea,uuid,uuid) to service_role;
comment on table public.beneficios_eventos is 'clicklocal:beneficios_1a:v2; Interacciones; ningun evento acredita una venta.';
comment on column public.beneficios_codigos.codigo_hash is 'SHA-256 binario; no se persiste el codigo en claro.';
comment on column public.beneficios_qr_comercios.token_hash is 'SHA-256 binario del token aleatorio del QR fijo.';
comment on table public.beneficios is 'clicklocal:beneficios_1a:v2';
comment on table public.beneficios_codigos is 'clicklocal:beneficios_1a:v2';
comment on table public.beneficios_qr_comercios is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_validar_objetivo_comercio() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_validar_edicion() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_validar_codigo() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_validar_evento() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_actualizar_updated_at() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_proteger_inmutabilidad() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_validar_conflicto_gastronomico() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_gastronomia_validar_conflicto() is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_emitir_codigo(uuid,uuid,bytea,text,integer) is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_consumir_codigo(bytea,text,uuid,bytea,text,uuid) is 'clicklocal:beneficios_1a:v2';
comment on function public.beneficios_validar_codigo_carrito(bytea,uuid,uuid) is 'clicklocal:beneficios_1a:v2';
comment on trigger beneficios_gastronomia_conflicto_trigger on public.gastronomia_productos is 'clicklocal:beneficios_1a:v2';
commit;
