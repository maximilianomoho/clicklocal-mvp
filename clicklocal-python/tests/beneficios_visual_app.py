"""Circuito integral local de Beneficios contra PostgreSQL aislado.

No importa ``app.py`` ni Supabase. Los modos que escriben datos sólo se
ejecutan al invocar explícitamente ``--automatico`` o ``--visual``.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import re
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from flask import Flask, Response

from beneficios.config import ConfiguracionBeneficios
from beneficios.errors import BeneficioNoVigenteError, CodigoInvalidoError
from beneficios.identity import CLAVE_CONSUMIDOR, CLAVE_IDEMPOTENCIAS
from beneficios.repository import RepositorioBeneficiosPostgresql
from beneficios.routes import crear_blueprint_publico_beneficios
from beneficios.services import ServicioBeneficios
from beneficios.types import (
    Beneficio,
    MetodoUtilizacion,
    ObjetivoBeneficio,
    SolicitudCodigo,
    TipoDescuento,
    TipoObjetivo,
)


WHATSAPP_FICTICIO = "5493430000000"
CSRF_FICTICIO = "csrf-beneficios-integral-aislado"
TABLAS_HISTORICAS = (
    "comercios",
    "publicaciones",
    "beneficios",
    "beneficios_codigos",
    "beneficios_eventos",
)
TIPOS_CONSUMO = {
    "activacion_whatsapp",
    "validacion_qr",
    "codigo_consumido_carrito",
}
IDENTIDAD_CLUSTER_ESPERADA = (
    "debian",
    "clicklocal_beneficios_ui_test",
    "/home/debian/PROYECTO-LIMPIO/clicklocal-postgresql-pruebas/data",
    "55432",
    "",
    True,
)


@dataclass(frozen=True, slots=True)
class IdentificadoresFicticios:
    comercio_id: str
    publicacion_id: str
    beneficio_id: str
    beneficio_editado_id: str

    @classmethod
    def nuevos(cls):
        return cls(*(str(uuid4()) for _ in range(4)))


@dataclass(slots=True)
class PresentacionFicticia:
    usar_whatsapp: bool = True


@dataclass(slots=True)
class EstadoCreacionFicticia:
    comercio_y_publicacion_confirmados: bool = False
    beneficios_confirmados: list[str] = field(default_factory=list)


def configuracion_aislada():
    """Valida destino cerrado y obtiene el secreto sólo del entorno."""
    return ConfiguracionBeneficios.desde_entorno(os.environ)


def conectar(configuracion):
    return psycopg.connect(**configuracion.parametros_conexion())


@contextmanager
def _exigir_error(tipo_error):
    try:
        yield
    except tipo_error:
        return
    raise AssertionError(f"Se esperaba {tipo_error.__name__}")


def validar_identidad_cluster(configuracion, conectar_fn=conectar):
    """Confirma el clúster aislado real antes de habilitar escrituras."""
    with conectar_fn(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select current_user,
                   current_database(),
                   current_setting('data_directory'),
                   current_setting('port'),
                   current_setting('listen_addresses'),
                   inet_server_addr() is null
            """
        )
        identidad = tuple(cursor.fetchone())
    if identidad != IDENTIDAD_CLUSTER_ESPERADA:
        raise RuntimeError(
            "La identidad de PostgreSQL no corresponde al clúster aislado"
        )


def crear_servicio(configuracion):
    return ServicioBeneficios(RepositorioBeneficiosPostgresql(
        lambda: conectar(configuracion),
        secreto_codigos=configuracion.secreto_codigos,
    ))


def crear_aplicacion(servicio, ids, presentacion):
    aplicacion = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static",
    )
    aplicacion.secret_key = "sesion-ficticia-beneficios-integral-aislada"

    def construir_whatsapp(comercio_id, mensaje):
        if comercio_id != ids.comercio_id or not presentacion.usar_whatsapp:
            return ""
        from urllib.parse import quote
        return f"https://wa.me/{WHATSAPP_FICTICIO}?text={quote(mensaje)}"

    aplicacion.register_blueprint(crear_blueprint_publico_beneficios(
        lambda: servicio,
        lambda: CSRF_FICTICIO,
        construir_whatsapp,
        lambda comercio_id: comercio_id == ids.comercio_id,
    ))

    @aplicacion.get("/")
    def inicio():
        return Response(_pagina_visual(ids.beneficio_id), mimetype="text/html")

    return aplicacion


def _pagina_visual(beneficio_id):
    endpoint = f"/beneficios/{beneficio_id}/codigo"
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beneficios 2.0 — prueba aislada</title></head>
<body><main data-beneficio data-endpoint="{endpoint}" data-csrf="{CSRF_FICTICIO}">
<h1>Oferta ficticia integral</h1>
<p>20% OFF — PostgreSQL aislado</p>
<button class="beneficios-20-publico__obtener">Obtener código</button>
<p class="beneficios-20-publico__estado"></p>
<section class="beneficios-20-publico__cupon" hidden>
<strong class="beneficios-20-publico__codigo"></strong>
<p class="beneficios-20-publico__vencimiento"></p>
<a class="beneficios-20-publico__enviar" hidden>Enviar por WhatsApp ficticio</a>
<p class="beneficios-20-publico__presencial">También podés mostrar este código en el comercio.</p>
<p>Alternativa presencial: mostrá el código directamente en el comercio ficticio.</p>
</section></main><script src="/static/beneficios/publico.js"></script></body></html>"""


def instantanea_historica(configuracion):
    """Representación estable de todas las filas que deben preservarse."""
    resultado = {}
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        for tabla in TABLAS_HISTORICAS:
            cursor.execute(
                f"select coalesce(jsonb_agg(to_jsonb(t) order by t.id), '[]'::jsonb)::text "
                f"from public.{tabla} t"
            )
            resultado[tabla] = cursor.fetchone()[0]
    return resultado


def instantanea_ventas(configuracion):
    """Captura cualquier tabla pública cuyo nombre represente ventas."""
    resultado = {}
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select tablename
              from pg_catalog.pg_tables
             where schemaname = 'public'
               and lower(tablename) like '%%venta%%'
             order by tablename
            """
        )
        for (tabla,) in cursor.fetchall():
            cursor.execute(sql.SQL(
                "select coalesce(jsonb_agg(fila order by fila::text), "
                "'[]'::jsonb)::text from (select to_jsonb(t) fila "
                "from public.{}) datos"
            ).format(sql.Identifier(tabla)))
            resultado[tabla] = cursor.fetchone()[0]
    return resultado


def preparar_ficticios(configuracion, servicio, ids, estado_creacion):
    ahora = datetime.now(timezone.utc)
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            "insert into public.comercios(id) values (%s)",
            (ids.comercio_id,),
        )
        cursor.execute(
            "insert into public.publicaciones(id, comercio_id) values (%s, %s)",
            (ids.publicacion_id, ids.comercio_id),
        )
    estado_creacion.comercio_y_publicacion_confirmados = True

    servicio.crear_para_publicacion(Beneficio(
        id=ids.beneficio_id,
        comercio_id=ids.comercio_id,
        titulo=f"Beneficio ficticio integral {ids.beneficio_id}",
        objetivo=ObjetivoBeneficio(
            TipoObjetivo.PUBLICACION,
            ids.publicacion_id,
        ),
        tipo_descuento=TipoDescuento.PORCENTAJE,
        valor_descuento=Decimal("20"),
        vigente_desde=ahora - timedelta(minutes=1),
        vigente_hasta=ahora + timedelta(days=1),
        metodos=frozenset({MetodoUtilizacion.WHATSAPP}),
    ))
    estado_creacion.beneficios_confirmados.append(ids.beneficio_id)


def limpiar_ficticios(configuracion, ids, estado_creacion):
    """Borra sólo las filas alcanzables desde los UUID de esta ejecución."""
    if not estado_creacion.comercio_y_publicacion_confirmados:
        return
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select id
              from public.beneficios
             where comercio_id = %s
             order by numero_edicion desc, id
            """,
            (ids.comercio_id,),
        )
        beneficios_creados = [str(fila[0]) for fila in cursor.fetchall()]
        if beneficios_creados:
            cursor.execute(
                "delete from public.beneficios_eventos "
                "where beneficio_id = any(%s::uuid[])",
                (beneficios_creados,),
            )
            cursor.execute(
                "delete from public.beneficios_codigos "
                "where beneficio_id = any(%s::uuid[])",
                (beneficios_creados,),
            )
            for beneficio_id in beneficios_creados:
                cursor.execute(
                    "delete from public.beneficios "
                    "where id = %s and comercio_id = %s",
                    (beneficio_id, ids.comercio_id),
                )
        cursor.execute(
            "delete from public.publicaciones where id = %s",
            (ids.publicacion_id,),
        )
        cursor.execute(
            "delete from public.comercios where id = %s",
            (ids.comercio_id,),
        )


def cantidad_residuos(configuracion, ids, estado_creacion):
    if not estado_creacion.comercio_y_publicacion_confirmados:
        return 0
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            with propios as (
              select id from public.beneficios where comercio_id = %s
            )
            select
              (select count(*) from public.comercios where id = %s)
              + (select count(*) from public.publicaciones where id = %s)
              + (select count(*) from propios)
              + (select count(*) from public.beneficios_codigos
                   where beneficio_id in (select id from propios))
              + (select count(*) from public.beneficios_eventos
                   where beneficio_id in (select id from propios))
            """,
            (ids.comercio_id, ids.comercio_id, ids.publicacion_id),
        )
        return cursor.fetchone()[0]


def verificar_persistencia(
    configuracion,
    beneficio_id,
    codigo,
    vence_en,
    *,
    consumido=False,
):
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select count(*), min(vence_en), bool_or(utilizado_en is not null)
              from public.beneficios_codigos
             where beneficio_id = %s
            """,
            (beneficio_id,),
        )
        cantidad, vence_persistido, hubo_consumo = cursor.fetchone()
        assert cantidad == 1
        assert vence_persistido.isoformat() == vence_en
        assert hubo_consumo is consumido

        cursor.execute(
            """
            select tipo_evento, count(*)
              from public.beneficios_eventos
             where beneficio_id = %s
             group by tipo_evento
            """,
            (beneficio_id,),
        )
        eventos = dict(cursor.fetchall())
        assert eventos.get("codigo_emitido") == 1
        if consumido:
            assert eventos.get("activacion_whatsapp") == 1
        else:
            assert TIPOS_CONSUMO.isdisjoint(eventos)

    assert re.fullmatch(r"[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}", codigo)


def verificar_edicion_historica(configuracion, ids):
    with conectar(configuracion) as conexion, conexion.cursor() as cursor:
        cursor.execute(
            """
            select id, serie_id, numero_edicion, edicion_anterior_id, activo
              from public.beneficios
             where id = any(%s::uuid[])
             order by numero_edicion
            """,
            ([ids.beneficio_id, ids.beneficio_editado_id],),
        )
        filas = cursor.fetchall()
        assert filas == [
            (
                UUID(ids.beneficio_id),
                UUID(ids.beneficio_id),
                1,
                None,
                False,
            ),
            (
                UUID(ids.beneficio_editado_id),
                UUID(ids.beneficio_id),
                2,
                UUID(ids.beneficio_id),
                True,
            ),
        ]
        cursor.execute(
            "select count(*) from public.beneficios_codigos "
            "where beneficio_id = %s",
            (ids.beneficio_id,),
        )
        assert cursor.fetchone()[0] == 1


def ejecutar_automatico():
    configuracion = configuracion_aislada()
    validar_identidad_cluster(configuracion)
    ids = IdentificadoresFicticios.nuevos()
    estado_creacion = EstadoCreacionFicticia()
    servicio = crear_servicio(configuracion)
    presentacion = PresentacionFicticia()
    aplicacion = crear_aplicacion(servicio, ids, presentacion)
    historicos_antes = instantanea_historica(configuracion)
    ventas_antes = instantanea_ventas(configuracion)

    print(f"comercio_ficticio={ids.comercio_id}")
    print(f"publicacion_ficticia={ids.publicacion_id}")
    print(f"beneficio_ficticio={ids.beneficio_id}")
    print(f"beneficio_editado_ficticio={ids.beneficio_editado_id}")

    try:
        preparar_ficticios(configuracion, servicio, ids, estado_creacion)
        cliente = aplicacion.test_client()
        cabeceras = {"X-CSRF-Token": CSRF_FICTICIO}

        primera = cliente.post(
            f"/beneficios/{ids.beneficio_id}/codigo",
            headers=cabeceras,
        )
        assert primera.status_code == 200
        datos_primera = primera.get_json()
        assert datos_primera["whatsapp_url"].startswith(
            f"https://wa.me/{WHATSAPP_FICTICIO}?text="
        )
        assert datos_primera["permite_presentar"] is True
        assert datos_primera["vence_en"]

        with cliente.session_transaction() as sesion:
            consumidor = sesion.get(CLAVE_CONSUMIDOR)
            idempotencias = dict(sesion.get(CLAVE_IDEMPOTENCIAS) or {})
        assert consumidor
        assert ids.beneficio_id in idempotencias

        presentacion.usar_whatsapp = False
        segunda = cliente.post(
            f"/beneficios/{ids.beneficio_id}/codigo",
            headers=cabeceras,
        )
        assert segunda.status_code == 200
        datos_segunda = segunda.get_json()
        assert datos_segunda["codigo"] == datos_primera["codigo"]
        assert datos_segunda["whatsapp_url"] is None
        assert datos_segunda["permite_presentar"] is True

        with cliente.session_transaction() as sesion:
            assert sesion.get(CLAVE_CONSUMIDOR) == consumidor
            assert dict(sesion.get(CLAVE_IDEMPOTENCIAS) or {}) == idempotencias

        verificar_persistencia(
            configuracion,
            ids.beneficio_id,
            datos_primera["codigo"],
            datos_primera["vence_en"],
        )

        beneficio_anterior = servicio.obtener_detalle(ids.beneficio_id)
        beneficio_editado = servicio.editar_del_comercio(
            ids.beneficio_id,
            ids.comercio_id,
            replace(
                beneficio_anterior,
                id=ids.beneficio_editado_id,
                titulo=f"Beneficio ficticio editado {ids.beneficio_editado_id}",
                valor_descuento=Decimal("25"),
            ),
        )
        estado_creacion.beneficios_confirmados.append(
            ids.beneficio_editado_id
        )
        assert beneficio_editado.serie_id == ids.beneficio_id
        assert beneficio_editado.numero_edicion == 2
        verificar_edicion_historica(configuracion, ids)

        presentacion.usar_whatsapp = True
        tercera = cliente.post(
            f"/beneficios/{ids.beneficio_editado_id}/codigo",
            headers=cabeceras,
        )
        assert tercera.status_code == 200
        datos_tercera = tercera.get_json()
        cuarta = cliente.post(
            f"/beneficios/{ids.beneficio_editado_id}/codigo",
            headers=cabeceras,
        )
        assert cuarta.status_code == 200
        assert cuarta.get_json()["codigo"] == datos_tercera["codigo"]

        validado = servicio.validar_codigo_comercio(
            datos_tercera["codigo"],
            ids.comercio_id,
        )
        assert validado.utilizado_en is None
        verificar_persistencia(
            configuracion,
            ids.beneficio_editado_id,
            datos_tercera["codigo"],
            datos_tercera["vence_en"],
        )

        consumido = servicio.consumir_codigo_comercio(
            datos_tercera["codigo"],
            ids.comercio_id,
        )
        assert consumido.utilizado_en is not None
        with _exigir_error(CodigoInvalidoError):
            servicio.consumir_codigo_comercio(
                datos_tercera["codigo"],
                ids.comercio_id,
            )
        verificar_persistencia(
            configuracion,
            ids.beneficio_editado_id,
            datos_tercera["codigo"],
            datos_tercera["vence_en"],
            consumido=True,
        )
        assert instantanea_ventas(configuracion) == ventas_antes

        desactivado = servicio.desactivar_del_comercio(
            ids.beneficio_editado_id,
            ids.comercio_id,
        )
        assert desactivado.activo is False
        quinta = cliente.post(
            f"/beneficios/{ids.beneficio_editado_id}/codigo",
            headers=cabeceras,
        )
        assert quinta.status_code == 409
        with _exigir_error(BeneficioNoVigenteError):
            servicio.generar_codigo(SolicitudCodigo(
                beneficio_id=ids.beneficio_editado_id,
                consumidor_id=str(uuid4()),
                idempotency_key=str(uuid4()),
            ))
        print("resultado=exitoso")
    finally:
        limpiar_ficticios(configuracion, ids, estado_creacion)
        assert cantidad_residuos(
            configuracion,
            ids,
            estado_creacion,
        ) == 0
        assert instantanea_historica(configuracion) == historicos_antes
        assert instantanea_ventas(configuracion) == ventas_antes


def ejecutar_visual():
    configuracion = configuracion_aislada()
    validar_identidad_cluster(configuracion)
    ids = IdentificadoresFicticios.nuevos()
    estado_creacion = EstadoCreacionFicticia()
    servicio = crear_servicio(configuracion)
    presentacion = PresentacionFicticia()
    aplicacion = crear_aplicacion(servicio, ids, presentacion)
    historicos_antes = instantanea_historica(configuracion)

    print(f"comercio_ficticio={ids.comercio_id}")
    print(f"publicacion_ficticia={ids.publicacion_id}")
    print(f"beneficio_ficticio={ids.beneficio_id}")
    try:
        preparar_ficticios(configuracion, servicio, ids, estado_creacion)
        aplicacion.run(host="127.0.0.1", port=5057, debug=False)
    finally:
        limpiar_ficticios(configuracion, ids, estado_creacion)
        assert cantidad_residuos(
            configuracion,
            ids,
            estado_creacion,
        ) == 0
        assert instantanea_historica(configuracion) == historicos_antes


def main():
    parser = argparse.ArgumentParser()
    modos = parser.add_mutually_exclusive_group(required=True)
    modos.add_argument("--automatico", action="store_true")
    modos.add_argument("--visual", action="store_true")
    argumentos = parser.parse_args()
    if argumentos.automatico:
        ejecutar_automatico()
    else:
        ejecutar_visual()


if __name__ == "__main__":
    main()
