-- CLICKLOCAL 2.0 - BENEFICIOS - ROLLBACK PREPARADO
-- No ejecutar automáticamente. No usa CASCADE y se niega a perder datos.
begin;
do $r$ declare n text; o oid; marca text := 'clicklocal:beneficios_1a:v2'; begin
 foreach n in array array['beneficios','beneficios_codigos','beneficios_eventos','beneficios_qr_comercios'] loop
  o=to_regclass('public.'||n);
  if o is null then raise exception 'Rollback detenido: falta %',n; end if;
  if coalesce(obj_description(o,'pg_class'),'') not like marca||'%' then
   raise exception 'Rollback detenido: % no pertenece a esta migracion',n; end if;
 end loop;
 foreach n in array array[
  'beneficios_validar_objetivo_comercio()','beneficios_validar_edicion()',
  'beneficios_validar_codigo()','beneficios_validar_evento()',
  'beneficios_actualizar_updated_at()','beneficios_proteger_inmutabilidad()',
  'beneficios_validar_conflicto_gastronomico()',
  'beneficios_gastronomia_validar_conflicto()',
  'beneficios_emitir_codigo(uuid,uuid,bytea,text,integer)',
  'beneficios_consumir_codigo(bytea,text,uuid,bytea,text,uuid)',
  'beneficios_validar_codigo_carrito(bytea,uuid,uuid)'
 ] loop
  o=to_regprocedure('public.'||n);
  if o is null or coalesce(obj_description(o,'pg_proc'),'')<>marca then
   raise exception 'Rollback detenido: funcion ajena, ausente o alterada: %',n; end if;
 end loop;
 select t.oid into o from pg_trigger t join pg_class c on c.oid=t.tgrelid
  join pg_namespace s on s.oid=c.relnamespace
  where s.nspname='public' and c.relname='gastronomia_productos'
   and t.tgname='beneficios_gastronomia_conflicto_trigger' and not t.tgisinternal;
 if o is null or coalesce(obj_description(o,'pg_trigger'),'')<>marca then
  raise exception 'Rollback detenido: trigger gastronomico ajeno, ausente o alterado'; end if;
 if exists(select 1 from public.beneficios limit 1)
  or exists(select 1 from public.beneficios_codigos limit 1)
  or exists(select 1 from public.beneficios_eventos limit 1)
  or exists(select 1 from public.beneficios_qr_comercios limit 1) then
  raise exception 'Rollback detenido: existen datos de Beneficios o QR'; end if;
end $r$;
drop trigger beneficios_gastronomia_conflicto_trigger on public.gastronomia_productos;
drop trigger beneficios_inmutabilidad_trigger on public.beneficios;
drop trigger beneficios_actualizar_updated_at_trigger on public.beneficios;
drop trigger beneficios_conflicto_gastronomico_trigger on public.beneficios;
drop trigger beneficios_validar_edicion_trigger on public.beneficios;
drop trigger beneficios_validar_objetivo_comercio_trigger on public.beneficios;
drop trigger beneficios_validar_codigo_trigger on public.beneficios_codigos;
drop trigger beneficios_validar_evento_trigger on public.beneficios_eventos;
drop function public.beneficios_emitir_codigo(uuid,uuid,bytea,text,integer) restrict;
drop function public.beneficios_consumir_codigo(bytea,text,uuid,bytea,text,uuid) restrict;
drop function public.beneficios_validar_codigo_carrito(bytea,uuid,uuid) restrict;
drop function public.beneficios_proteger_inmutabilidad() restrict;
drop function public.beneficios_gastronomia_validar_conflicto() restrict;
drop function public.beneficios_validar_conflicto_gastronomico() restrict;
drop function public.beneficios_actualizar_updated_at() restrict;
drop function public.beneficios_validar_evento() restrict;
drop function public.beneficios_validar_codigo() restrict;
drop function public.beneficios_validar_edicion() restrict;
drop function public.beneficios_validar_objetivo_comercio() restrict;
drop table public.beneficios_eventos restrict;
drop table public.beneficios_codigos restrict;
drop table public.beneficios_qr_comercios restrict;
drop table public.beneficios restrict;
commit;
