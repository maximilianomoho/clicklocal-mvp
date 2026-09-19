from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from gastronomia import gastronomia_bp


@pytest.fixture(autouse=True)
def pos_activo(monkeypatch):
    monkeypatch.setattr(
        "gastronomia.routes._pos_activo_gastronomia",
        lambda _comercio_id: True,
    )


CAJA = {
    "id": "caja-2",
    "comercio_id": "comercio-1",
    "numero": 2,
    "abierto_at": "2026-09-15T22:04:00+00:00",
    "cerrado_at": "2026-09-16T06:10:00+00:00",
    "total_vendido": 15000,
    "cantidad_ventas": 5,
    "ticket_promedio": 3000,
    "efectivo": 1000,
    "transferencia": 2000,
    "qr": 3000,
    "debito": 4000,
    "credito": 5000,
}

VENTAS = [
    {
        "id": "venta-1", "numero_pedido": 41, "comercio_id": "comercio-1", "caja_id": "caja-2",
        "created_at": "2026-09-14T15:00:00+00:00",
        "estado": "cerrado", "estado_pago": "pagado",
        "origen": "pos", "forma_pago": "efectivo", "total": 1000,
        "detalle": [],
    },
    {
        "id": "anulada", "numero_pedido": 42, "comercio_id": "comercio-1", "caja_id": "caja-2",
        "created_at": "2026-09-15T01:00:00+00:00",
        "estado": "cancelado", "estado_pago": "pagado",
        "origen": "clicklocal", "forma_pago": "qr", "total": 9000,
        "motivo_cancelacion": "error", "detalle": [],
    },
]


class ConsultaHistorialFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.ordenes = []

    def select(self, columnas, **_kwargs):
        self.columnas = columnas
        return self

    def eq(self, campo, valor):
        self.filtros.append(("eq", campo, valor))
        return self

    def gte(self, campo, valor):
        self.filtros.append(("gte", campo, valor))
        return self

    def lt(self, campo, valor):
        self.filtros.append(("lt", campo, valor))
        return self

    def order(self, campo, desc=False):
        self.ordenes.append((campo, desc))
        return self

    def limit(self, _cantidad):
        return self

    def execute(self):
        self.db.consultas.append(self)
        filas = self.db.cajas if self.tabla == "gastronomia_cajas" else self.db.ventas
        for operador, campo, valor in self.filtros:
            if operador == "eq":
                filas = [fila for fila in filas if fila.get(campo) == valor]
        return SimpleNamespace(data=[dict(fila) for fila in filas])


class DBHistorialFalsa:
    def __init__(self, cajas=None, ventas=None):
        self.cajas = cajas or []
        self.ventas = ventas or []
        self.consultas = []

    def table(self, tabla):
        return ConsultaHistorialFalsa(self, tabla)


def crear_app_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(gastronomia_bp)

    @app.route("/login", endpoint="login")
    def login():
        return "login"

    return app


def test_lista_cajas_solo_muestra_cerradas_y_usa_fotos_guardadas():
    app = crear_app_prueba()
    abierta = dict(CAJA, id="caja-3", numero=3, cerrado_at=None)
    anterior = dict(
        CAJA,
        id="caja-1",
        numero=1,
        abierto_at="2026-09-15T10:00:00+00:00",
        cerrado_at="2026-09-15T15:00:00+00:00",
    )
    db = DBHistorialFalsa(cajas=[abierta, CAJA, anterior])
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes.supabase_admin", db),
        patch("gastronomia.routes._resumir_ventas") as resumir,
        patch("gastronomia.routes.render_template", return_value="cajas") as render,
    ):
        respuesta = app.test_client().get("/gastronomia/panel/pedidos/historial/cajas")

    assert respuesta.status_code == 200
    consulta = db.consultas[0]
    assert consulta.tabla == "gastronomia_cajas"
    assert consulta.ordenes == [("numero", True)]
    assert ("eq", "comercio_id", "comercio-1") in consulta.filtros
    cajas = render.call_args.kwargs["cajas"]
    assert [caja["numero"] for caja in cajas] == [2, 1]
    assert cajas[0]["total_vendido"] == 15000
    assert cajas[0]["cantidad_ventas"] == 5
    assert cajas[0]["abierto_at_mostrar"] == "15/09/2026 19:04"
    assert cajas[0]["cerrado_at_mostrar"] == "16/09/2026 03:10"
    resumir.assert_not_called()


def test_detalle_usa_foto_y_consulta_operaciones_solo_por_caja_id():
    app = crear_app_prueba()
    db = DBHistorialFalsa(cajas=[CAJA], ventas=VENTAS)
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes.supabase_admin", db),
        patch("gastronomia.routes._resumir_ventas") as resumir,
        patch("gastronomia.routes.render_template", return_value="detalle") as render,
    ):
        respuesta = app.test_client().get("/gastronomia/panel/pedidos/historial/cajas/caja-2")

    assert respuesta.status_code == 200
    contexto = render.call_args.kwargs
    assert contexto["caja_seleccionada"]["total_vendido"] == 15000
    assert [venta["id"] for venta in contexto["operaciones"]] == ["venta-1", "anulada"]
    assert contexto["operaciones"][1]["estado"] == "cancelado"
    consulta_ventas = db.consultas[1]
    assert consulta_ventas.tabla == "gastronomia_pedidos"
    assert ("eq", "caja_id", "caja-2") in consulta_ventas.filtros
    assert not any(filtro[0] in {"gte", "lt"} for filtro in consulta_ventas.filtros)
    assert ("eq", "estado_pago", "pagado") in consulta_ventas.filtros
    resumir.assert_not_called()


def test_historiales_quedan_en_su_seccion_y_reutilizan_modal():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    pedidos = (raiz / "templates/gastronomia/pedidos_historial.html").read_text(encoding="utf-8")
    cajas = (raiz / "templates/gastronomia/cierres_historial.html").read_text(encoding="utf-8")
    assert "pedidos_vista = 'historial'" in pedidos
    assert '<h2>Historial de pedidos</h2>' in pedidos
    assert "ventas_vista = 'historial'" in cajas
    assert '<h2>Historial de cajas</h2>' in cajas
    assert "gastro-subnav" not in cajas
    assert "Historial de cajas" not in pedidos
    assert "Cierres diarios" not in pedidos
    assert "ANULADA" in cajas and "motivo_cancelacion" in cajas
    assert 'class="cierre-detalle js-ver-detalle"' in cajas
    assert '{% include "gastronomia/_pedido_modal.html" %}' in cajas
    assert "pedido_detalle.js" in cajas
    assert "_resumir_ventas" not in cajas
