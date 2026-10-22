from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask

from pedidos_comercio import routes


COMERCIO_A = "11111111-1111-4111-8111-111111111111"
COMERCIO_B = "22222222-2222-4222-8222-222222222222"


def datos_metricas():
    pedidos = [
        {"id": "p4", "nombre_cliente": "Ana", "whatsapp_cliente": "3439990000", "whatsapp_normalizado": "5493439990000", "created_at": "2026-10-06T13:00:00+00:00", "total": 400, "estado": "entregado"},
        {"id": "p3", "nombre_cliente": "Cancelado", "whatsapp_cliente": "3430000000", "whatsapp_normalizado": "5493430000000", "created_at": "2026-10-06T12:00:00+00:00", "total": 500, "estado": "cancelado"},
        {"id": "p2", "nombre_cliente": "Juan Pérez", "whatsapp_cliente": "3436123456", "whatsapp_normalizado": "5493436123456", "created_at": "2026-10-06T11:00:00+00:00", "total": 200, "estado": "entregado"},
        {"id": "p1", "nombre_cliente": "Juan", "whatsapp_cliente": "03436123456", "whatsapp_normalizado": "5493436123456", "created_at": "2026-10-06T10:00:00+00:00", "total": 100, "estado": "aceptado"},
    ]
    items = [
        {"pedido_id": "p1", "publicacion_id": "coca", "nombre_snapshot": "Coca-Cola", "cantidad": 2, "subtotal": 100},
        {"pedido_id": "p2", "publicacion_id": "coca", "nombre_snapshot": "Coca-Cola 2,25 L", "cantidad": 3, "subtotal": 180},
        {"pedido_id": "p2", "publicacion_id": "yerba", "nombre_snapshot": "Yerba", "cantidad": 1, "subtotal": 100},
        {"pedido_id": "p3", "publicacion_id": "coca", "nombre_snapshot": "Coca-Cola", "cantidad": 99, "subtotal": 999},
        {"pedido_id": "p4", "publicacion_id": "leche", "nombre_snapshot": "Leche", "cantidad": 10, "subtotal": 400},
    ]
    return pedidos, items


def test_resumen_excluye_cancelados_y_agrupa_clientes_como_crm():
    pedidos, items = datos_metricas()
    metricas = routes._calcular_metricas(pedidos, items, COMERCIO_A)
    assert metricas["ventas"] == 700
    assert metricas["pedidos"] == 3
    assert metricas["ticket_promedio"] == pytest.approx(700 / 3)
    assert metricas["clientes"] == 2
    assert [cliente["nombre"] for cliente in metricas["top_clientes"]] == ["Ana", "Juan Pérez"]
    assert metricas["top_clientes"][1]["pedidos_compra"] == 2


def test_cero_pedidos_da_ticket_promedio_cero():
    metricas = routes._calcular_metricas([], [], COMERCIO_A)
    assert metricas["ventas"] == 0
    assert metricas["pedidos"] == 0
    assert metricas["ticket_promedio"] == 0
    assert metricas["clientes"] == 0


def test_rankings_productos_suman_snapshots_y_excluyen_cancelados():
    pedidos, items = datos_metricas()
    metricas = routes._calcular_metricas(pedidos, items, COMERCIO_A)
    assert [(p["nombre"], p["unidades"]) for p in metricas["productos_unidades"]] == [
        ("Leche", 10), ("Coca-Cola 2,25 L", 5), ("Yerba", 1)
    ]
    assert [(p["nombre"], p["facturacion"]) for p in metricas["productos_facturacion"]] == [
        ("Leche", 400.0), ("Coca-Cola 2,25 L", 280.0), ("Yerba", 100.0)
    ]


def test_rankings_quedan_limitados_a_diez():
    pedidos = [{"id": "p", "nombre_cliente": "Cliente", "whatsapp_cliente": "3436123456", "whatsapp_normalizado": "5493436123456", "created_at": "2026-10-06T10:00:00+00:00", "total": 100, "estado": "entregado"}]
    items = [{"pedido_id": "p", "publicacion_id": str(i), "nombre_snapshot": f"Producto {i}", "cantidad": i, "subtotal": i} for i in range(1, 13)]
    metricas = routes._calcular_metricas(pedidos, items, COMERCIO_A)
    assert len(metricas["productos_unidades"]) == 10
    assert len(metricas["productos_facturacion"]) == 10
    assert metricas["productos_unidades"][0]["unidades"] == 12
    assert metricas["productos_facturacion"][0]["facturacion"] == 12


@pytest.mark.parametrize(
    ("periodo", "inicio", "fin"),
    [
        ("hoy", "2026-10-06T03:00:00+00:00", "2026-10-07T03:00:00+00:00"),
        ("7d", "2026-09-30T03:00:00+00:00", "2026-10-07T03:00:00+00:00"),
        ("30d", "2026-09-07T03:00:00+00:00", "2026-10-07T03:00:00+00:00"),
    ],
)
def test_periodos_metricas_respetan_zona_argentina(periodo, inicio, fin):
    desde, hasta, normalizado = routes._limites_periodo(
        periodo, datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    )
    assert normalizado == periodo
    assert desde.isoformat() == inicio
    assert hasta.isoformat() == fin


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
    def select(self, campos):
        self.campos = campos
        return self
    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self
    def gte(self, campo, valor):
        self.filtros.append((campo, ">=", valor))
        return self
    def lt(self, campo, valor):
        self.filtros.append((campo, "<", valor))
        return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, valor):
        self.limite = valor
        return self
    def in_(self, campo, valores):
        self.filtros.append((campo, tuple(valores)))
        return self
    def execute(self):
        self.db.consultas.append(self)
        if self.tabla == "pedidos":
            return SimpleNamespace(data=self.db.pedidos)
        return SimpleNamespace(data=self.db.items)


class DbFalsa:
    def __init__(self):
        self.pedidos, self.items = datos_metricas()
        self.consultas = []
    def table(self, tabla):
        assert tabla in {"pedidos", "pedido_items"}
        return ConsultaFalsa(self, tabla)


def test_consultas_metricas_filtran_comercio_fecha_y_solo_items_validos():
    db = DbFalsa()
    desde, hasta, _ = routes._limites_periodo("hoy", datetime(2026, 10, 6, 15, tzinfo=timezone.utc))
    routes._consulta_datos_metricas(COMERCIO_A, desde, hasta, db)
    consulta_pedidos, consulta_items = db.consultas
    assert consulta_pedidos.filtros[0] == ("comercio_id", COMERCIO_A)
    assert consulta_pedidos.filtros[1] == ("created_at", ">=", desde.isoformat())
    assert consulta_pedidos.filtros[2] == ("created_at", "<", hasta.isoformat())
    assert consulta_pedidos.limite == routes.METRICAS_MAX_PEDIDOS
    assert consulta_items.filtros == [("pedido_id", ("p4", "p2", "p1"))]


def test_comercio_sin_modulo_no_accede_a_metricas(monkeypatch):
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": COMERCIO_A})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: False)
    monkeypatch.setattr(routes, "_consulta_datos_metricas", lambda *_args: pytest.fail("No debe consultar"))
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/metricas"), pytest.raises(Exception) as error:
        routes.metricas_pedidos()
    assert getattr(error.value, "code", None) == 403


def test_modo_gestor_y_comercio_aislados(monkeypatch):
    consultados = []
    capturado = {}
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": COMERCIO_B})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(routes, "_consulta_datos_metricas", lambda cid, *_args: consultados.append(cid) or ([], []))
    monkeypatch.setattr(routes, "render_template", lambda plantilla, **datos: capturado.update(plantilla=plantilla, **datos) or "ok")
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/metricas?comercio_id=" + COMERCIO_A):
        respuesta = routes.metricas_pedidos()
    assert respuesta == "ok"
    assert consultados == [COMERCIO_B]
    assert capturado["comercio"]["id"] == COMERCIO_B
