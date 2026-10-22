begin;

CREATE OR REPLACE FUNCTION public.asignar_caja_a_pedido_pagado()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
declare
    v_fecha_comercial date :=
        pg_catalog.timezone(
            'America/Argentina/Cordoba',
            pg_catalog.statement_timestamp()
        )::date;
begin
    if new.estado_pago = 'pagado'
       and new.caja_id is null
    then
        if tg_op = 'INSERT'
           or (
               tg_op = 'UPDATE'
               and old.estado_pago is distinct from 'pagado'
           )
        then
            if exists (
                select 1
                from public.comercio_modulos as modulo
                where modulo.comercio_id = new.comercio_id
                  and modulo.modulo = 'pos'
                  and modulo.activo is true
                  and modulo.aviso_dias_antes is not null
                  and modulo.aviso_dias_antes >= 0
                  and modulo.gracia_dias is not null
                  and modulo.gracia_dias >= 0
                  and (
                      modulo.fecha_activacion is null
                      or modulo.fecha_vencimiento is null
                      or modulo.fecha_vencimiento >= modulo.fecha_activacion
                  )
                  and (
                      modulo.fecha_activacion is null
                      or v_fecha_comercial >= modulo.fecha_activacion
                  )
                  and (
                      modulo.fecha_vencimiento is null
                      or v_fecha_comercial <= modulo.fecha_vencimiento
                      or (
                          modulo.gracia_dias > 0
                          and v_fecha_comercial > modulo.fecha_vencimiento
                          and (
                              v_fecha_comercial - modulo.fecha_vencimiento
                          ) <= modulo.gracia_dias
                      )
                  )
            ) then
                new.caja_id :=
                    public.obtener_o_abrir_gastronomia_caja(
                        new.comercio_id,
                        coalesce(new.pagado_at, pg_catalog.now())
                    );
            end if;
        end if;
    end if;

    return new;
end;
$function$;

commit;
