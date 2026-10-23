-- Estado operativo de tiendas gastronómicas.
-- Sólo las configuraciones publicadas al ejecutar esta migración se marcan
-- como previamente autorizadas. Los borradores permanecen sin autorización.

begin;

alter table public.gastronomia_configuracion
    add column if not exists publicacion_autorizada_at timestamptz;

update public.gastronomia_configuracion
set publicacion_autorizada_at = now()
where activo = true
  and publicacion_autorizada_at is null;

commit;
