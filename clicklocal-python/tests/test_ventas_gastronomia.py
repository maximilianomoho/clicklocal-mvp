from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from gastronomia import gastronomia_bp
from gastronomia.routes import (
    _etiqueta_operacion_venta,
    _resumir_ventas,
)


@pytest.fixture(autouse=True)
def pos_activo(monkeypatch):
    monkeypatch.setattr(
        "gastronomia.routes._pos_activo_gastronomia",
        lambda _comercio_id: True,
    )


class ConsultaVentasFalsa:
    def __init__(self, db):
        self.db = db
        self.ventas = db.ventas
        self.filtros = []
        self.orden = None
        self.cambios = None

    def select(self, *args, **kwargs):
        self.columnas = "".join(args)
        return self

    def eq(self, campo, valor):
        self.filtros.append(("eq", campo, valor))
        return self

    def neq(self, campo, valor):
        self.filtros.append(("neq", campo, valor))
        return self

    def gte(self, campo, valor):
        self.filtros.append(("gte", campo, valor))
        return self

    def lt(self, campo, valor):
        self.filtros.append(("lt", campo, valor))
        return self

    def order(self, campo, desc=False):
        self.orden = (campo, desc)
        return self

    def limit(self, *_args):
        return self

    def update(self, cambios):
        self.cambios = cambios
        return self

    def execute(self):
        ventas = self.ventas
        for operador, campo, valor in self.filtros:
            if operador == "eq":
                ventas = [venta for venta in ventas if venta.get(campo) == valor]
            elif operador == "neq":
                ventas = [venta for venta in ventas if venta.get(campo) != valor]
        if self.cambios is not None:
            for venta in ventas:
                venta.update(self.cambios)
            self.db.actualizacion = dict(self.cambios)
        return SimpleNamespace(data=[dict(venta) for venta in ventas])


class DBVentasFalsa:
    def __init__(self, ventas):
        self.ventas = ventas
        self.consulta = ConsultaVentasFalsa(self)
        self.tabla = None
        self.actualizacion = None

    def table(self, tabla):
        self.tabla = tabla
        return self.consulta


class RPCCajaFalsa:
    def __init__(self, data="caja-1", error=None):
        self.data = data
        self.error = error
        self.llamada = None

    def rpc(self, nombre, parametros):
        self.llamada = (nombre, parametros)
        return self

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


def crear_app_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(gastronomia_bp)

    @app.route("/login", endpoint="login")
    def login():
        return "login"

    return app


def test_resumen_incluye_ventas_validas_y_excluye_el_resto():
    operaciones = [
        {"id": "rapida", "estado": "cerrado", "estado_pago": "pagado", "total": 1000, "forma_pago": "efectivo"},
        {"id": "preparacion", "estado": "pendiente", "estado_pago": "pagado", "total": 2000, "forma_pago": "transferencia"},
        {"id": "online", "estado": "marchando", "estado_pago": "pagado", "total": 3000, "forma_pago": "qr"},
        {"id": "sin-pago", "estado": "preparado", "estado_pago": "pendiente", "total": 9000, "forma_pago": "debito"},
        {"id": "cancelada", "estado": "cancelado", "estado_pago": "pagado", "total": 8000, "forma_pago": "credito"},
        {"id": "debito", "estado": "cerrado", "estado_pago": "pagado", "total": 4000, "forma_pago": "debito"},
    ]

    resumen = _resumir_ventas(operaciones)

    assert [venta["id"] for venta in resumen["ventas"]] == [
        "rapida", "preparacion", "online", "debito"
    ]
    assert resumen["total_vendido"] == 10000
    assert resumen["cantidad_ventas"] == 4
    assert resumen["ticket_promedio"] == 2500
    assert resumen["totales_forma_pago"] == {
        "efectivo": 1000,
        "transferencia": 2000,
        "qr": 3000,
        "debito": 4000,
        "credito": 0,
    }


def test_etiquetas_solo_usan_distinciones_disponibles():
    assert _etiqueta_operacion_venta({"origen": "pos", "estado": "pendiente"}) == "Preparación"
    assert _etiqueta_operacion_venta({"origen": "pos", "estado": "cerrado"}) == "Venta POS"
    assert _etiqueta_operacion_venta({"origen": "clicklocal", "estado": "cerrado"}) == "Pedido online"


def test_ventas_es_privada_y_solo_admite_get():
    app = crear_app_prueba()
    with patch("gastronomia.routes._comercio_panel_gastronomia", return_value=None):
        respuesta = app.test_client().get("/gastronomia/panel/ventas")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login")
    assert app.test_client().post("/gastronomia/panel/ventas").status_code == 405


def test_ventas_caja_abierta_calcula_metricas_solo_con_sus_operaciones():
    app = crear_app_prueba()
    caja = {
        "id": "caja-7", "numero": 7,
        "abierto_at": "2026-09-15T22:04:00+00:00", "cerrado_at": None,
    }
    operaciones = [
        {"id": "venta-2", "numero_pedido": 2, "created_at": "2026-09-16T04:30:00+00:00", "total": 2000, "forma_pago": "qr", "estado": "pendiente", "estado_pago": "pagado", "origen": "pos", "tipo_entrega": "mostrador"},
        {"id": "venta-1", "numero_pedido": 1, "created_at": "2026-09-15T23:30:00+00:00", "total": 1000, "forma_pago": "efectivo", "estado": "cerrado", "estado_pago": "pagado", "origen": "clicklocal", "tipo_entrega": "retiro"},
        {"id": "anulada", "numero_pedido": 3, "created_at": "2026-09-16T01:00:00+00:00", "total": 9000, "forma_pago": "credito", "estado": "cancelado", "estado_pago": "pagado", "origen": "pos", "tipo_entrega": "mostrador", "motivo_cancelacion": "error"},
    ]
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes._consultar_caja_abierta", return_value=caja),
        patch("gastronomia.routes._consultar_ventas_caja", return_value=operaciones) as consultar,
        patch("gastronomia.routes._consultar_ultima_caja") as ultima,
        patch("gastronomia.routes.render_template", return_value="ventas") as render,
    ):
        respuesta = app.test_client().get("/gastronomia/panel/ventas")

    assert respuesta.status_code == 200
    consultar.assert_called_once_with("comercio-1", "caja-7")
    ultima.assert_not_called()
    contexto = render.call_args.kwargs
    assert contexto["caja_abierta"]["numero"] == 7
    assert contexto["caja_abierta"]["abierto_at_mostrar"] == "15/09/2026 19:04"
    assert contexto["total_vendido"] == 3000
    assert contexto["cantidad_ventas"] == 2
    assert contexto["totales_forma_pago"]["credito"] == 0
    assert [venta["id"] for venta in contexto["operaciones"]] == ["venta-2", "venta-1", "anulada"]


def test_ventas_sin_cajas_anuncia_apertura_automatica_de_caja_uno():
    app = crear_app_prueba()
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes._consultar_caja_abierta", return_value=None),
        patch("gastronomia.routes._consultar_ultima_caja", return_value=None),
        patch("gastronomia.routes.render_template", return_value="ventas") as render,
    ):
        respuesta = app.test_client().get("/gastronomia/panel/ventas")

    assert respuesta.status_code == 200
    contexto = render.call_args.kwargs
    assert contexto["caja_abierta"] is None
    assert contexto["ultima_caja"] is None
    assert contexto["proximo_numero"] == 1
    from pathlib import Path
    plantilla = (Path(__file__).resolve().parents[1] / "templates/gastronomia/ventas.html").read_text(encoding="utf-8")
    assert "La primera venta abrirá automáticamente la Caja #1." in plantilla


def test_ventas_tras_cierre_anuncia_siguiente_numero_sin_abrir_caja():
    app = crear_app_prueba()
    ultima = {"id": "caja-1", "numero": 1, "abierto_at": "2026-09-15T15:00:00+00:00", "cerrado_at": "2026-09-16T06:10:00+00:00"}
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes._consultar_caja_abierta", return_value=None),
        patch("gastronomia.routes._consultar_ultima_caja", return_value=ultima),
        patch("gastronomia.routes.render_template", return_value="ventas") as render,
    ):
        respuesta = app.test_client().get("/gastronomia/panel/ventas")

    assert respuesta.status_code == 200
    contexto = render.call_args.kwargs
    assert contexto["ultima_caja"]["cerrado_at_mostrar"] == "16/09/2026 03:10"
    assert contexto["proximo_numero"] == 2
    assert contexto["operaciones"] == []


def test_cerrar_caja_llama_exclusivamente_rpc_del_backend():
    app = crear_app_prueba()
    db = RPCCajaFalsa(data="caja-1")
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes.supabase_admin", db),
        patch("gastronomia.routes._resumir_ventas") as resumir,
    ):
        respuesta = app.test_client().post("/gastronomia/panel/ventas/cerrar-caja")

    assert respuesta.status_code == 200
    assert respuesta.get_json()["mensaje"] == "Caja cerrada correctamente."
    assert db.llamada == ("cerrar_gastronomia_caja", {"p_comercio_id": "comercio-1"})
    resumir.assert_not_called()
    assert app.test_client().post("/gastronomia/panel/ventas/cerrar-dia").status_code == 404


def test_cerrar_caja_sin_caja_abierta_devuelve_error_claro():
    app = crear_app_prueba()
    db = RPCCajaFalsa(error=RuntimeError("No hay una caja abierta."))
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        respuesta = app.test_client().post("/gastronomia/panel/ventas/cerrar-caja")

    assert respuesta.status_code == 409
    assert respuesta.get_json() == {"ok": False, "error": "No hay una caja abierta."}


def test_cada_venta_ofrece_detalle_completo_de_solo_lectura():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    plantilla = (raiz / "templates/gastronomia/ventas.html").read_text(
        encoding="utf-8"
    )
    modal = (raiz / "templates/gastronomia/_pedido_modal.html").read_text(
        encoding="utf-8"
    )
    javascript = (raiz / "static/gastronomia/pedido_detalle.js").read_text(
        encoding="utf-8"
    )

    assert "Ver detalle" in plantilla
    assert 'data-pedido="{{ venta|tojson|forceescape }}"' in plantilla
    assert '{% include "gastronomia/_pedido_modal.html" %}' in plantilla
    assert "pedido_detalle.js" in plantilla
    assert "pedidoModalProductos" in modal
    assert "pedidoModalTotales" in modal
    assert "JSON.parse(botonDetalle.dataset.pedido)" in javascript
    assert "pedido.detalle_items" in javascript
    assert "pedido.total" in javascript
    for accion in ("data-accion-estado", "data-estado-pago", "data-accion-entrega"):
        assert accion not in modal
        assert accion not in javascript


def venta_pagada(estado="cerrado", origen="pos"):
    return {
        "id": "venta-1", "numero_pedido": 31, "comercio_id": "comercio-1",
        "created_at": "2026-09-14T15:30:00+00:00",
        "total": 5000, "forma_pago": "efectivo", "estado": estado,
        "estado_pago": "pagado", "origen": origen,
        "tipo_entrega": "mostrador", "detalle": [],
        "cancelado_at": None, "motivo_cancelacion": None,
    }


def test_anular_venta_guarda_trazabilidad_sin_delete():
    for origen in ("pos", "clicklocal"):
        db = DBVentasFalsa([venta_pagada(origen=origen)])
        app = crear_app_prueba()
        with (
            patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
            patch("gastronomia.routes.supabase_admin", db),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/ventas/venta-1/anular",
                json={"confirmado": True, "motivo": "  cobro incorrecto  "},
            )
        assert respuesta.status_code == 200
        assert db.actualizacion["estado"] == "cancelado"
        assert db.actualizacion["motivo_cancelacion"] == "cobro incorrecto"
        assert db.actualizacion["cancelado_at"]
        for campo in ("total", "forma_pago", "detalle", "pagado_at", "entregado_at", "created_at"):
            assert campo not in db.actualizacion


def test_anulacion_requiere_confirmacion_motivo_y_no_se_repite():
    app = crear_app_prueba()
    for payload, estado, error in (
        ({"motivo": "error"}, "cerrado", "confirmación"),
        ({"confirmado": True, "motivo": "   "}, "cerrado", "motivo"),
        ({"confirmado": True, "motivo": "otra vez"}, "cancelado", "ya está anulada"),
    ):
        db = DBVentasFalsa([venta_pagada(estado=estado)])
        with (
            patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}),
            patch("gastronomia.routes.supabase_admin", db),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/ventas/venta-1/anular", json=payload,
            )
        assert respuesta.status_code == 400
        assert error in respuesta.get_json()["error"]
        assert db.actualizacion is None


def test_anulada_no_suma_pero_sigue_visible_en_ventas_e_historial():
    operaciones = [venta_pagada(), venta_pagada(estado="cancelado")]
    operaciones[1]["id"] = "anulada"
    operaciones[1]["motivo_cancelacion"] = "error de carga"
    resumen = _resumir_ventas(operaciones)
    assert resumen["cantidad_ventas"] == 1
    assert resumen["total_vendido"] == 5000

    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    ventas_html = (raiz / "templates/gastronomia/ventas.html").read_text(encoding="utf-8")
    historial_html = (raiz / "templates/gastronomia/pedidos_historial.html").read_text(encoding="utf-8")
    rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
    endpoint = rutas.split("def anular_venta_gastronomia", 1)[1].split(
        "@gastronomia_bp.route", 1
    )[0]
    assert "ANULADA" in ventas_html
    assert "motivo_cancelacion" in ventas_html
    assert "motivo_cancelacion" in historial_html
    assert ".delete(" not in endpoint
