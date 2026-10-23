-- Reversión segura del estado operativo de tiendas gastronómicas.
--
-- Eliminar publicacion_autorizada_at destruye el registro de qué tiendas ya
-- fueron autorizadas. Por eso esta reversión se detiene si existe al menos una
-- autorización. En ese caso, exportar y verificar esos valores antes de
-- preparar una reversión destructiva específica.

begin;

do $rollback$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'gastronomia_configuracion'
          and column_name = 'publicacion_autorizada_at'
    ) then
        if exists (
            select 1
            from public.gastronomia_configuracion
            where publicacion_autorizada_at is not null
        ) then
            raise exception
                'Reversión detenida: publicacion_autorizada_at contiene autorizaciones';
        end if;
    end if;
end
$rollback$;

alter table public.gastronomia_configuracion
    drop column if exists publicacion_autorizada_at;

commit;
