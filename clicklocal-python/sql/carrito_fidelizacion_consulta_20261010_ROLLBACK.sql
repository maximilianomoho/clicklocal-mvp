-- ClickLocal - Rollback de consulta de Fidelizacion/Sellos.
-- NO EJECUTAR SIN AUTORIZACION EXPRESA.

begin;

drop function if exists
    public.consultar_fidelizacion_clicklocal(uuid, text[]);

commit;
