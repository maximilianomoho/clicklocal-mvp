-- ClickLocal - Rollback de Carrito/Pedidos transversal, etapa 2
--
-- USAR UNICAMENTE si la migracion fue aplicada pero el modulo todavia no
-- comenzo a recibir pedidos reales que deban conservarse.
-- Este rollback no modifica comercios, publicaciones ni otros modulos.

begin;

-- Defensa contra borrado accidental de pedidos reales. Si la tabla existe y
-- contiene al menos una fila, toda la transaccion se aborta sin eliminar nada.
do $$
declare
    existen_pedidos boolean;
begin
    if to_regclass('public.pedidos') is not null then
        execute 'select exists (select 1 from public.pedidos)'
        into existen_pedidos;

        if existen_pedidos then
            raise exception
                'Rollback cancelado: public.pedidos contiene datos que deben preservarse.';
        end if;
    end if;
end;
$$;

-- El trigger depende de la tabla pedidos y de la funcion de numeracion.
-- La guarda permite que el rollback tambien sea seguro si la tabla no existe.
do $$
begin
    if to_regclass('public.pedidos') is not null then
        execute 'drop trigger if exists trg_asignar_numero_pedido_clicklocal on public.pedidos';
    end if;
end;
$$;

-- Funciones exclusivas del carrito comun. La RPC se elimina antes que la tabla
-- pedidos porque su tipo de retorno depende de public.pedidos.
drop function if exists public.crear_pedido_clicklocal(jsonb, jsonb);
drop function if exists public.asignar_numero_pedido_clicklocal();

-- Indices explicitos creados por la migracion. Los indices implicitos de PK y
-- UNIQUE se eliminan automaticamente junto con sus tablas.
drop index if exists public.idx_pedido_items_pedido;
drop index if exists public.idx_pedidos_comercio_cliente;
drop index if exists public.idx_pedidos_comercio_estado_fecha;

-- Tablas exclusivas del modulo, en orden inverso a sus dependencias.
-- No se usa CASCADE: cualquier dependencia inesperada debe bloquear el rollback.
drop table if exists public.pedido_items;
drop table if exists public.pedidos;
drop table if exists public.pedido_contadores;
drop table if exists public.pedidos_configuracion;

commit;
