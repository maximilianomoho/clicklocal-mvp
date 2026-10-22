-- Reversión de la etapa estructural de modalidades de Gastronomía Base.
-- No borra ni modifica ninguna otra columna o tabla.

begin;

alter table public.gastronomia_pedidos
    drop constraint if exists gastronomia_pedidos_modalidad_gestion_check;

alter table public.gastronomia_pedidos
    drop column if exists modalidad_gestion;

alter table public.gastronomia_configuracion
    drop constraint if exists gastronomia_configuracion_modalidad_pedidos_check;

alter table public.gastronomia_configuracion
    drop column if exists modalidad_pedidos;

commit;
