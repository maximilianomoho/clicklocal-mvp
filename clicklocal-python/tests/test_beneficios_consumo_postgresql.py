from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from beneficios.errors import CodigoInvalidoError
from beneficios.repository import RepositorioBeneficiosPostgresql
from beneficios.services import ServicioBeneficios
from beneficios.types import (
    Beneficio,
    MetodoUtilizacion,
    ObjetivoBeneficio,
    SolicitudCodigo,
    TipoDescuento,
    TipoObjetivo,
)


CONEXION = {
    "dbname": "clicklocal_beneficios_ui_test",
    "user": "debian",
    "host": (
        "/home/debian/PROYECTO-LIMPIO/"
        "clicklocal-postgresql-pruebas/socket"
    ),
    "port": 55432,
}
SECRETO_FICTICIO = (
    b"secreto-ficticio-consumo-beneficios-bloque-a"
)
TITULOS_HISTORICOS = (
    "20% OFF",
    "PRUEBA CONCURRENCIA",
)


def conectar():
    return psycopg.connect(**CONEXION)


def _estado_historico():
    with conectar() as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            with historicos as (
              select id
                from public.beneficios
               where titulo = any(%s)
            )
            select jsonb_build_object(
              'beneficios',
              coalesce(
                (
                  select jsonb_agg(to_jsonb(b) order by b.id)
                    from public.beneficios b
                   where b.id in (select id from historicos)
                ),
                '[]'::jsonb
              ),
              'codigos',
              coalesce(
                (
                  select jsonb_agg(to_jsonb(c) order by c.id)
                    from public.beneficios_codigos c
                   where c.beneficio_id in (
                     select id from historicos
                   )
                ),
                '[]'::jsonb
              ),
              'eventos',
              coalesce(
                (
                  select jsonb_agg(to_jsonb(e) order by e.id)
                    from public.beneficios_eventos e
                   where e.beneficio_id in (
                     select id from historicos
                   )
                ),
                '[]'::jsonb
              )
            )
            """,
            (list(TITULOS_HISTORICOS),),
        )
        return cursor.fetchone()[0]


def _limpiar_propios(
    *,
    beneficio_id,
    publicacion_id,
    comercio_id,
):
    with conectar() as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            delete from public.beneficios_eventos
             where beneficio_id = %s
            """,
            (beneficio_id,),
        )
        cursor.execute(
            """
            delete from public.beneficios_codigos
             where beneficio_id = %s
            """,
            (beneficio_id,),
        )
        cursor.execute(
            "delete from public.beneficios where id = %s",
            (beneficio_id,),
        )
        cursor.execute(
            "delete from public.publicaciones where id = %s",
            (publicacion_id,),
        )
        cursor.execute(
            "delete from public.comercios where id = %s",
            (comercio_id,),
        )


def _cantidad_residuos(
    *,
    beneficio_id,
    publicacion_id,
    comercio_id,
):
    with conectar() as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select
              (select count(*) from public.beneficios
                where id = %s)
              + (select count(*) from public.publicaciones
                  where id = %s)
              + (select count(*) from public.comercios
                  where id = %s)
              + (select count(*) from public.beneficios_codigos
                  where beneficio_id = %s)
              + (select count(*) from public.beneficios_eventos
                  where beneficio_id = %s)
            """,
            (
                beneficio_id,
                publicacion_id,
                comercio_id,
                beneficio_id,
                beneficio_id,
            ),
        )
        return cursor.fetchone()[0]


def _preparar_codigo_ficticio(
    *,
    comercio_id,
    publicacion_id,
    beneficio_id,
    consumidor_id,
    idempotency_key,
):
    ahora = datetime.now(timezone.utc)
    repositorio = RepositorioBeneficiosPostgresql(
        conectar,
        secreto_codigos=SECRETO_FICTICIO,
    )
    servicio = ServicioBeneficios(repositorio)

    with conectar() as conexion, conexion.cursor() as cursor:
        cursor.execute(
            "insert into public.comercios(id) values (%s)",
            (comercio_id,),
        )
        cursor.execute(
            """
            insert into public.publicaciones(id, comercio_id)
            values (%s, %s)
            """,
            (publicacion_id, comercio_id),
        )

    beneficio = Beneficio(
        id=beneficio_id,
        comercio_id=comercio_id,
        titulo=f"Beneficio ficticio {beneficio_id}",
        objetivo=ObjetivoBeneficio(
            TipoObjetivo.PUBLICACION,
            publicacion_id,
        ),
        tipo_descuento=TipoDescuento.PORCENTAJE,
        valor_descuento=Decimal("10"),
        vigente_desde=ahora - timedelta(minutes=1),
        vigente_hasta=ahora + timedelta(days=1),
        metodos=frozenset({
            MetodoUtilizacion.WHATSAPP,
        }),
    )
    servicio.crear_para_publicacion(beneficio)
    codigo = servicio.generar_codigo(
        SolicitudCodigo(
            beneficio_id=beneficio_id,
            consumidor_id=consumidor_id,
            idempotency_key=idempotency_key,
        )
    )
    return servicio, codigo.codigo


def _cantidad_eventos_utilizacion(beneficio_id):
    with conectar() as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select count(*)
              from public.beneficios_eventos
             where beneficio_id = %s
               and tipo_evento = 'activacion_whatsapp'
               and canal = 'whatsapp'
            """,
            (beneficio_id,),
        )
        return cursor.fetchone()[0]


def _ejecutar_con_limpieza(caso):
    comercio_id = str(uuid4())
    publicacion_id = str(uuid4())
    beneficio_id = str(uuid4())
    consumidor_id = str(uuid4())
    idempotency_key = str(uuid4())
    historicos_antes = _estado_historico()

    try:
        caso(
            comercio_id=comercio_id,
            publicacion_id=publicacion_id,
            beneficio_id=beneficio_id,
            consumidor_id=consumidor_id,
            idempotency_key=idempotency_key,
        )
    finally:
        try:
            _limpiar_propios(
                beneficio_id=beneficio_id,
                publicacion_id=publicacion_id,
                comercio_id=comercio_id,
            )
        finally:
            assert _cantidad_residuos(
                beneficio_id=beneficio_id,
                publicacion_id=publicacion_id,
                comercio_id=comercio_id,
            ) == 0
            assert _estado_historico() == historicos_antes


def test_dos_consumos_consecutivos_aceptan_exactamente_el_primero():
    def caso(**ids):
        servicio, codigo = _preparar_codigo_ficticio(**ids)

        validado = servicio.validar_codigo_comercio(
            codigo,
            ids["comercio_id"],
        )
        assert validado.utilizado_en is None

        consumido = servicio.consumir_codigo_comercio(
            codigo,
            ids["comercio_id"],
        )
        assert consumido.utilizado_en is not None

        with pytest.raises(CodigoInvalidoError) as segundo_error:
            servicio.consumir_codigo_comercio(
                codigo,
                ids["comercio_id"],
            )

        assert segundo_error.value.mensaje_publico == (
            "El código no existe, venció o ya fue utilizado."
        )
        assert _cantidad_eventos_utilizacion(
            ids["beneficio_id"]
        ) == 1

    _ejecutar_con_limpieza(caso)


def test_dos_consumos_concurrentes_permiten_un_solo_consumo():
    def caso(**ids):
        _, codigo = _preparar_codigo_ficticio(**ids)
        barrera = Barrier(2)

        def consumir():
            repositorio = RepositorioBeneficiosPostgresql(
                conectar,
                secreto_codigos=SECRETO_FICTICIO,
            )
            barrera.wait(timeout=10)
            try:
                resultado = repositorio.consumir_codigo(
                    codigo,
                    ids["comercio_id"],
                )
                return ("exito", resultado)
            except CodigoInvalidoError as error:
                return ("rechazado", error)

        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = tuple(
                ejecutor.map(lambda _: consumir(), range(2))
            )

        exitos = [
            resultado
            for estado, resultado in resultados
            if estado == "exito"
        ]
        rechazos = [
            resultado
            for estado, resultado in resultados
            if estado == "rechazado"
        ]

        assert len(exitos) == 1
        assert exitos[0].utilizado_en is not None
        assert len(rechazos) == 1
        assert rechazos[0].mensaje_publico == (
            "El código no existe, venció o ya fue utilizado."
        )
        assert _cantidad_eventos_utilizacion(
            ids["beneficio_id"]
        ) == 1

    _ejecutar_con_limpieza(caso)
