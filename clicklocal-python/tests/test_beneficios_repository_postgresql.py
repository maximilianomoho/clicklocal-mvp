from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import psycopg

from beneficios.repository import (
    RepositorioBeneficiosPostgresql,
    _beneficio_desde_fila,
)
from beneficios.services import ServicioBeneficios
from beneficios.types import (
    Beneficio, ConsultaBeneficios, MetodoUtilizacion, ObjetivoBeneficio, TemaEtiqueta,
    TipoDescuento, TipoObjetivo,
)


CONEXION = {
    "dbname": "clicklocal_beneficios_ui_test", "user": "debian",
    "host": "/home/debian/PROYECTO-LIMPIO/clicklocal-postgresql-pruebas/socket",
    "port": 55432,
}


def conectar():
    return psycopg.connect(**CONEXION)


def test_persistencia_real_guarda_solo_whatsapp_y_verifica_pertenencia():
    comercio_id, publicacion_id, beneficio_id = str(uuid4()), str(uuid4()), str(uuid4())
    ahora = datetime.now(timezone.utc)
    with conectar() as conexion, conexion.cursor() as cursor:
        cursor.execute("insert into public.comercios(id) values (%s)", (comercio_id,))
        cursor.execute("insert into public.publicaciones(id, comercio_id) values (%s, %s)", (publicacion_id, comercio_id))
    try:
        beneficio = Beneficio(
            id=beneficio_id, comercio_id=comercio_id, titulo="15% OFF",
            objetivo=ObjetivoBeneficio(TipoObjetivo.PUBLICACION, publicacion_id),
            tipo_descuento=TipoDescuento.PORCENTAJE,
            valor_descuento=Decimal("15"), vigente_desde=ahora,
            vigente_hasta=ahora + timedelta(days=7), dias_semana=frozenset({3, 5}),
            hora_desde=time(22), hora_hasta=time(2), tema_etiqueta=TemaEtiqueta.CLARA,
            metodos=frozenset({MetodoUtilizacion.WHATSAPP}),
        )
        creado = ServicioBeneficios(RepositorioBeneficiosPostgresql(conectar)).crear_para_publicacion(beneficio)
        assert creado.id == beneficio_id
        with conectar() as conexion, conexion.cursor() as cursor:
            cursor.execute("select dias_semana_mask, hora_desde, hora_hasta, permite_whatsapp, permite_qr, permite_carrito from public.beneficios where id=%s", (beneficio_id,))
            assert cursor.fetchone() == ((1 << 3) | (1 << 5), time(22), time(2), True, False, False)
            cursor.execute("select count(*) from public.beneficios_codigos where beneficio_id=%s", (beneficio_id,))
            assert cursor.fetchone()[0] == 0
    finally:
        with conectar() as conexion, conexion.cursor() as cursor:
            cursor.execute("delete from public.beneficios where id=%s", (beneficio_id,))
            cursor.execute("delete from public.publicaciones where id=%s", (publicacion_id,))
            cursor.execute("delete from public.comercios where id=%s", (comercio_id,))


def fila_beneficio_2a(**cambios):
    fila = {
        "id": "10000000-0000-4000-8000-000000000001",
        "serie_id": "10000000-0000-4000-8000-000000000001",
        "numero_edicion": 1,
        "comercio_id": "20000000-0000-4000-8000-000000000001",
        "titulo": "Oferta general",
        "detalle_promocion": None,
        "tipo_objetivo": "comercio",
        "publicacion_id": None,
        "gastronomia_producto_id": None,
        "tipo_descuento": "porcentaje",
        "valor_descuento": Decimal("10"),
        "vigente_desde": datetime(2026, 11, 20, tzinfo=timezone.utc),
        "vigente_hasta": datetime(2026, 11, 30, tzinfo=timezone.utc),
        "activo": True,
        "dias_semana_mask": 127,
        "hora_desde": None,
        "hora_hasta": None,
        "zona_horaria": "America/Argentina/Cordoba",
        "cupo_maximo": None,
        "maximo_unidades_codigo": 1,
        "mostrar_codigos_restantes": False,
        "tema_etiqueta": "clara",
        "permite_whatsapp": True,
        "permite_qr": True,
        "permite_carrito": False,
    }
    fila.update(cambios)
    return fila


class CursorUnitario:
    def __init__(self, filas):
        self.filas = filas
        self.consulta = None
        self.parametros = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, consulta, parametros):
        self.consulta = consulta
        self.parametros = parametros

    def fetchall(self):
        return self.filas


class ConexionUnitaria:
    def __init__(self, cursor):
        self.cursor_unitario = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self, **kwargs):
        return self.cursor_unitario


class CursorSecuencia(CursorUnitario):
    def __init__(self, resultados):
        super().__init__([])
        self.resultados = list(resultados)
        self.consultas = []

    def execute(self, consulta, parametros):
        self.consultas.append((consulta, parametros))

    def fetchone(self):
        return self.resultados.pop(0)


def test_unitario_deserializa_oferta_general_con_id_del_comercio():
    beneficio = _beneficio_desde_fila(fila_beneficio_2a())

    assert beneficio.objetivo == ObjetivoBeneficio(
        TipoObjetivo.COMERCIO,
        beneficio.comercio_id,
    )


def test_unitario_consulta_comercio_y_conserva_ofertas_multiples():
    primera = fila_beneficio_2a()
    segunda = fila_beneficio_2a(
        id="10000000-0000-4000-8000-000000000002",
        titulo="Segunda oferta general",
    )
    cursor = CursorUnitario([primera, segunda])
    repositorio = RepositorioBeneficiosPostgresql(
        lambda: ConexionUnitaria(cursor)
    )
    instante = datetime(2026, 11, 21, tzinfo=timezone.utc)

    resultado = repositorio.buscar_vigentes(
        ConsultaBeneficios(
            (
                ObjetivoBeneficio(
                    TipoObjetivo.COMERCIO,
                    primera["comercio_id"],
                ),
            ),
            instante,
        )
    )

    assert tuple(item.id for item in resultado) == (
        primera["id"],
        segunda["id"],
    )
    assert "b.tipo_objetivo = 'comercio'" in cursor.consulta
    assert cursor.parametros[2] == [primera["comercio_id"]]


def test_unitario_lista_solo_ultima_revision_por_serie_y_comercio():
    fila = fila_beneficio_2a(numero_edicion=2)
    cursor = CursorUnitario([fila])
    repositorio = RepositorioBeneficiosPostgresql(
        lambda: ConexionUnitaria(cursor)
    )
    resultado = repositorio.listar_por_comercio(fila["comercio_id"])
    assert resultado[0].numero_edicion == 2
    assert "distinct on (b.serie_id)" in cursor.consulta
    assert "b.comercio_id = %s" in cursor.consulta
    assert cursor.parametros == (fila["comercio_id"],)


def test_unitario_edicion_desactiva_anterior_e_inserta_revision_historica():
    anterior = fila_beneficio_2a()
    nueva_fila = fila_beneficio_2a(
        id="10000000-0000-4000-8000-000000000002",
        numero_edicion=2,
        valor_descuento=Decimal("20"),
        detalle_promocion="Promo exclusiva en salón",
    )
    cursor = CursorSecuencia([anterior, nueva_fila])
    repositorio = RepositorioBeneficiosPostgresql(
        lambda: ConexionUnitaria(cursor)
    )
    nueva = replace(
        _beneficio_desde_fila(anterior),
        id=nueva_fila["id"],
        valor_descuento=Decimal("20"),
        detalle_promocion="Promo exclusiva en salón",
    )
    editada = repositorio.editar_beneficio(anterior["id"], nueva)
    assert editada.id == nueva_fila["id"]
    assert editada.numero_edicion == 2
    assert editada.detalle_promocion == "Promo exclusiva en salón"
    assert "for update" in cursor.consultas[0][0].lower()
    assert "set activo = false" in cursor.consultas[1][0].lower()
    assert "edicion_anterior_id" in cursor.consultas[2][0]
    assert anterior["id"] in cursor.consultas[2][1]
    assert all("delete" not in consulta.lower() for consulta, _ in cursor.consultas)


def test_unitario_edicion_ajena_no_actualiza_ni_inserta():
    cursor = CursorSecuencia([None])
    repositorio = RepositorioBeneficiosPostgresql(
        lambda: ConexionUnitaria(cursor)
    )
    beneficio = _beneficio_desde_fila(fila_beneficio_2a())
    assert repositorio.editar_beneficio("ajeno", beneficio) is None
    assert len(cursor.consultas) == 1


def test_unitario_desactivar_limita_por_id_comercio_y_activo():
    fila = fila_beneficio_2a(activo=False)
    cursor = CursorSecuencia([fila])
    repositorio = RepositorioBeneficiosPostgresql(
        lambda: ConexionUnitaria(cursor)
    )
    resultado = repositorio.desactivar_beneficio(
        fila["id"], fila["comercio_id"]
    )
    assert resultado.activo is False
    consulta, parametros = cursor.consultas[0]
    assert "comercio_id = %s and activo" in consulta
    assert parametros == (fila["id"], fila["comercio_id"])
