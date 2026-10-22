-- ClickLocal - rollback de Fidelizacion/Sellos para Carrito/Pedidos general.
-- PREPARADO PARA REVISION. NO EJECUTAR SIN AUTORIZACION EXPRESA.

begin;

-- Nunca elimina historia real silenciosamente.
do $$
declare
    v_existen_canjes boolean := false;
    v_existen_periodos boolean := false;
    v_existe_configuracion boolean := false;
begin
    if pg_catalog.to_regclass('public.pedidos_fidelizacion_canjes') is not null then
        execute 'select exists (select 1 from public.pedidos_fidelizacion_canjes)'
            into v_existen_canjes;
    end if;
    if v_existen_canjes then
        raise exception
            'Rollback cancelado: public.pedidos_fidelizacion_canjes contiene datos.';
    end if;

    if pg_catalog.to_regclass('public.pedidos_fidelizacion_periodos') is not null then
        execute 'select exists (select 1 from public.pedidos_fidelizacion_periodos)'
            into v_existen_periodos;
    end if;
    if v_existen_periodos then
        raise exception
            'Rollback cancelado: public.pedidos_fidelizacion_periodos contiene historia.';
    end if;

    if exists (
        select 1 from information_schema.columns
        where table_schema = 'public'
          and table_name = 'pedidos_configuracion'
          and column_name = 'fidelizacion_meta_sellos'
    ) and exists (
        select 1 from information_schema.columns
        where table_schema = 'public'
          and table_name = 'pedidos_configuracion'
          and column_name = 'fidelizacion_premio'
    ) then
        execute $consulta$
            select exists (
                select 1 from public.pedidos_configuracion
                where fidelizacion_meta_sellos is not null
                   or fidelizacion_premio is not null
            )
        $consulta$ into v_existe_configuracion;
    end if;
    if v_existe_configuracion then
        raise exception
            'Rollback cancelado: existe configuracion de fidelizacion que debe preservarse.';
    end if;
end
$$;

drop function if exists
    public.canjear_premio_fidelizacion_clicklocal(uuid, text, uuid);
drop function if exists
    public.configurar_fidelizacion_clicklocal(uuid, boolean, integer, text);
drop function if exists
    public.clave_cliente_pedido_clicklocal(uuid, text, text);
drop function if exists
    public.normalizar_whatsapp_clicklocal(text);

drop index if exists
    public.idx_pedidos_fidelizacion_canjes_comercio_cliente_fecha;
drop table if exists public.pedidos_fidelizacion_canjes;

drop index if exists
    public.idx_pedidos_fidelizacion_periodos_comercio_inicio;
drop index if exists
    public.idx_pedidos_fidelizacion_periodos_un_abierto;
drop table if exists public.pedidos_fidelizacion_periodos;

alter table public.pedidos_configuracion
    drop constraint if exists pedidos_configuracion_fidelizacion_premio_check,
    drop constraint if exists pedidos_configuracion_fidelizacion_meta_check,
    drop column if exists fidelizacion_premio,
    drop column if exists fidelizacion_meta_sellos;

commit;
