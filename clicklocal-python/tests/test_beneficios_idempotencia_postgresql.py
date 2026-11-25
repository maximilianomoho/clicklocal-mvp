from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from uuid import uuid4

import psycopg
import pytest

from beneficios.errors import PersistenciaBeneficiosError
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
    b"secreto-ficticio-idempotencia-beneficios-bloque-a"
)


def conectar():
    return psycopg.connect(**CONEXION)


def test_reintento_pertenece_al_mismo_consumidor():
    comercio_id = str(uuid4())
    publicacion_id = str(uuid4())
    beneficio_id = str(uuid4())
    idempotency_key = str(uuid4())
    consumidor_id = str(uuid4())
    otro_consumidor_id = str(uuid4())
    ahora = datetime.now(timezone.utc)

    repositorio = RepositorioBeneficiosPostgresql(
        conectar,
        secreto_codigos=SECRETO_FICTICIO,
    )
    servicio = ServicioBeneficios(repositorio)

    try:
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
            titulo="Beneficio ficticio idempotente",
            objetivo=ObjetivoBeneficio(
                TipoObjetivo.PUBLICACION,
                publicacion_id,
            ),
            tipo_descuento=TipoDescuento.PORCENTAJE,
            valor_descuento=Decimal("15"),
            vigente_desde=ahora - timedelta(minutes=1),
            vigente_hasta=ahora + timedelta(days=1),
            metodos=frozenset({
                MetodoUtilizacion.WHATSAPP,
            }),
        )
        servicio.crear_para_publicacion(beneficio)

        solicitud = SolicitudCodigo(
            beneficio_id=beneficio_id,
            consumidor_id=consumidor_id,
            idempotency_key=idempotency_key,
        )
        primero = servicio.generar_codigo(solicitud)
        segundo = servicio.generar_codigo(solicitud)

        assert re.fullmatch(
            r"[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}",
            primero.codigo,
        )
        assert segundo.codigo == primero.codigo
        assert segundo.emitido_en == primero.emitido_en
        assert segundo.vence_en == primero.vence_en

        solicitud_ajena = SolicitudCodigo(
            beneficio_id=beneficio_id,
            consumidor_id=otro_consumidor_id,
            idempotency_key=idempotency_key,
        )
        with pytest.raises(PersistenciaBeneficiosError):
            servicio.generar_codigo(solicitud_ajena)

        with conectar() as conexion, conexion.cursor() as cursor:
            cursor.execute(
                """
                select count(*)
                  from public.beneficios_codigos
                 where beneficio_id = %s
                """,
                (beneficio_id,),
            )
            assert cursor.fetchone()[0] == 1

            cursor.execute(
                """
                select consumidor_id
                  from public.beneficios_codigos
                 where beneficio_id = %s
                """,
                (beneficio_id,),
            )
            assert cursor.fetchone()[0] == consumidor_id

            cursor.execute(
                """
                select count(*)
                  from public.beneficios_eventos
                 where beneficio_id = %s
                   and tipo_evento = 'codigo_emitido'
                """,
                (beneficio_id,),
            )
            assert cursor.fetchone()[0] == 1
    finally:
        with conectar() as conexion, conexion.cursor() as cursor:
            cursor.execute(
                "delete from public.beneficios_eventos where beneficio_id=%s",
                (beneficio_id,),
            )
            cursor.execute(
                "delete from public.beneficios_codigos where beneficio_id=%s",
                (beneficio_id,),
            )
            cursor.execute(
                "delete from public.beneficios where id=%s",
                (beneficio_id,),
            )
            cursor.execute(
                "delete from public.publicaciones where id=%s",
                (publicacion_id,),
            )
            cursor.execute(
                "delete from public.comercios where id=%s",
                (comercio_id,),
            )
