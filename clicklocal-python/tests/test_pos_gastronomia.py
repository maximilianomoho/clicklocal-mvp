from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from gastronomia import gastronomia_bp
from gastronomia.routes import (
    _cargar_catalogo_gastronomico,
    _categorias_catalogo,
    _limpiar_categoria_producto,
)
from gastronomia.services.pedidos import PedidoError


@pytest.fixture(autouse=True)
def pos_activo(monkeypatch):
    monkeypatch.setattr(
        "gastronomia.routes._pos_activo_gastronomia",
        lambda _comercio_id: True,
    )


class ConsultaCatalogoFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.columnas = ""

    def select(self, *args, **_kwargs):
        self.columnas = "".join(args)
        return self

    def eq(self, campo, valor):
        self.filtros.append(("eq", campo, valor))
        return self

    def in_(self, campo, valor):
        self.filtros.append(("in", campo, tuple(valor)))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.db.consultas.append(self)
        return SimpleNamespace(
            data=[dict(fila) for fila in self.db.datos[self.tabla]]
        )


class CatalogoFalso:
    def __init__(self):
        self.consultas = []
        self.datos = {
            "gastronomia_productos": [
                {
                    "id": "producto-1",
                    "nombre": "Hamburguesa",
                    "descripcion": "Simple",
                    "categoria": "Hamburguesas",
                    "precio": 1000,
                    "precio_promocional": 900,
                    "imagen_url": None,
                    "disponible": True,
                    "activo": True,
                    "orden": 1,
                },
                {
                    "id": "producto-2",
                    "nombre": "Papas",
                    "descripcion": "",
                    "categoria": None,
                    "precio": 500,
                    "precio_promocional": None,
                    "imagen_url": None,
                    "disponible": False,
                    "activo": True,
                    "orden": 2,
                },
            ],
            "gastronomia_grupos_opciones": [
                {
                    "id": "grupo-1",
                    "producto_id": "producto-1",
                    "nombre": "Salsas",
                    "minimo": 1,
                    "maximo": 2,
                    "activo": True,
                    "orden": 1,
                }
            ],
            "gastronomia_opciones": [
                {
                    "id": "opcion-1",
                    "grupo_id": "grupo-1",
                    "nombre": "Alioli",
                    "precio_extra": 100,
                    "disponible": True,
                    "activo": True,
                    "orden": 1,
                }
            ],
        }

    def table(self, tabla):
        return ConsultaCatalogoFalsa(self, tabla)


def crear_app_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(gastronomia_bp)

    @app.route("/login", endpoint="login")
    def login():
        return "login"

    @app.route("/panel", endpoint="panel")
    def panel():
        return "panel"

    return app


def test_helper_reutiliza_filtros_precios_grupos_y_opciones_publicos():
    db = CatalogoFalso()

    with patch("gastronomia.routes.supabase_admin", db):
        productos = _cargar_catalogo_gastronomico("comercio-1")

    assert len(productos) == 2
    assert "categoria" in db.consultas[0].columnas
    assert productos[0]["precio_venta"] == 900
    assert productos[0]["categoria"] == "Hamburguesas"
    assert productos[1]["precio_venta"] == 500
    assert productos[0]["grupos_opciones"][0]["opciones"][0]["id"] == "opcion-1"
    assert ("eq", "comercio_id", "comercio-1") in db.consultas[0].filtros
    assert ("eq", "activo", True) in db.consultas[0].filtros
    assert ("eq", "activo", True) in db.consultas[1].filtros
    assert ("eq", "activo", True) in db.consultas[2].filtros


def test_categorias_catalogo_ignora_vacias_y_agrupa_mayusculas():
    categorias = _categorias_catalogo([
        {"categoria": " Hamburguesas "},
        {"categoria": "hamburguesas"},
        {"categoria": "Bebidas"},
        {"categoria": None},
        {"categoria": "   "},
    ])

    assert categorias == ["Hamburguesas", "Bebidas"]


def test_categoria_producto_es_opcional_y_se_guarda_limpia():
    assert _limpiar_categoria_producto("  Hamburguesas  ") == "Hamburguesas"
    assert _limpiar_categoria_producto("") is None
    assert _limpiar_categoria_producto("   ") is None
    assert _limpiar_categoria_producto(None) is None


def test_pos_es_privado_y_solo_admite_get():
    app = crear_app_prueba()
    cliente = app.test_client()

    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value=None,
    ):
        respuesta = cliente.get("/gastronomia/panel/pos")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login")
    assert cliente.post("/gastronomia/panel/pos").status_code == 405


def test_pos_carga_catalogo_del_comercio_autenticado():
    app = crear_app_prueba()
    db = CatalogoFalso()

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-propio", "nombre_negocio": "Bar"},
        ),
        patch("gastronomia.routes.supabase_admin", db),
        patch(
            "gastronomia.routes.render_template",
            return_value="pos",
        ) as render,
    ):
        respuesta = app.test_client().get("/gastronomia/panel/pos")

    assert respuesta.status_code == 200
    assert render.call_args.args[0] == "gastronomia/pos.html"
    assert len(render.call_args.kwargs["productos"]) == 2
    assert render.call_args.kwargs["categorias"] == ["Hamburguesas"]
    assert ("eq", "comercio_id", "comercio-propio") in db.consultas[0].filtros


def test_pos_confirma_solo_ids_cantidades_y_opciones():
    raiz = Path(__file__).resolve().parents[1]
    javascript = (raiz / "static/gastronomia/pos.js").read_text(encoding="utf-8")
    plantilla = (raiz / "templates/gastronomia/pos.html").read_text(encoding="utf-8")

    assert "fetch(" in javascript
    assert "data-confirmar-venta" in plantilla
    assert "precioUnitario: precioUnitarioSeleccionado" in javascript
    assert "forma_pago: formaPago" in javascript
    assert "tipo_venta: tipoVentaEl.value" in javascript
    assert "precioUnitario: item.precioUnitario" not in javascript
    assert "XMLHttpRequest" not in javascript
    assert "WebSocket" not in javascript
    assert "data-agregar-producto" in plantilla
    assert "data-vaciar-carrito" in plantilla
    assert "data-filtro-categoria" in plantilla
    assert "data-categoria" in plantilla
    for forma_pago in ("efectivo", "transferencia", "qr", "debito", "credito"):
        assert f'value="{forma_pago}"' in plantilla
    assert 'value="rapida"' in plantilla
    assert 'value="preparacion"' in plantilla
    assert "telefono_cliente" not in plantilla


def test_confirmar_pos_requiere_autenticacion_y_carrito():
    app = crear_app_prueba()
    cliente = app.test_client()

    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value=None,
    ):
        respuesta = cliente.post("/gastronomia/panel/pos/confirmar", json={})
    assert respuesta.status_code == 401

    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value={"id": "comercio-propio"},
    ):
        respuesta = cliente.post(
            "/gastronomia/panel/pos/confirmar",
            json={"detalle": []},
        )
    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "El carrito está vacío."


def test_confirmar_pos_reutiliza_servicio_con_contrato_operativo():
    app = crear_app_prueba()
    detalle = [{
        "id": "producto-1",
        "cantidad": 3,
        "opciones": [{"id": "opcion-1"}],
        "precio": 1,
    }]

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-propio"},
        ),
        patch("gastronomia.routes.crear_pedido") as crear_mock,
    ):
        crear_mock.return_value = {
            "id": "pedido-1",
            "numero_pedido": 23,
            "total": 3300,
        }
        respuesta = app.test_client().post(
            "/gastronomia/panel/pos/confirmar",
            json={
                "detalle": detalle,
                "forma_pago": "qr",
                "tipo_venta": "rapida",
            },
        )

    assert respuesta.status_code == 200
    assert respuesta.get_json() == {
        "ok": True,
        "pedido_id": "pedido-1",
        "numero_pedido": 23,
        "total": 3300,
    }
    argumentos = crear_mock.call_args.kwargs
    assert argumentos["comercio_id"] == "comercio-propio"
    assert argumentos["items"] == detalle
    assert argumentos["origen"] == "pos"
    assert argumentos["modalidad"] == "mostrador"
    assert argumentos["forma_pago"] == "qr"
    assert argumentos["estado_inicial"] == "cerrado"
    assert argumentos["estado_pago_inicial"] == "pagado"
    assert argumentos["aplicar_condiciones_comerciales"] is False


def test_confirmar_pos_admite_pagos_y_mapea_tipos_de_venta():
    app = crear_app_prueba()
    casos = (
        ("efectivo", "rapida", "cerrado"),
        ("qr", "rapida", "cerrado"),
        ("debito", "rapida", "cerrado"),
        ("credito", "rapida", "cerrado"),
        ("transferencia", "preparacion", "pendiente"),
    )

    for forma_pago, tipo_venta, estado in casos:
        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-propio"},
            ),
            patch("gastronomia.routes.crear_pedido") as crear_mock,
        ):
            crear_mock.return_value = {"id": "pedido-1"}
            respuesta = app.test_client().post(
                "/gastronomia/panel/pos/confirmar",
                json={
                    "detalle": [{"id": "producto-1", "cantidad": 1}],
                    "forma_pago": forma_pago,
                    "tipo_venta": tipo_venta,
                },
            )

        assert respuesta.status_code == 200
        argumentos = crear_mock.call_args.kwargs
        assert argumentos["forma_pago"] == forma_pago
        assert argumentos["estado_inicial"] == estado
        assert argumentos["estado_pago_inicial"] == "pagado"


def test_confirmar_pos_rechaza_pago_o_tipo_de_venta_invalidos():
    app = crear_app_prueba()
    cliente = app.test_client()
    detalle = [{"id": "producto-1", "cantidad": 1}]

    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value={"id": "comercio-propio"},
    ):
        for forma_pago in (None, "cheque"):
            respuesta = cliente.post(
                "/gastronomia/panel/pos/confirmar",
                json={
                    "detalle": detalle,
                    "forma_pago": forma_pago,
                    "tipo_venta": "rapida",
                },
            )
            assert respuesta.status_code == 400
            assert "forma de pago" in respuesta.get_json()["error"]

        respuesta = cliente.post(
            "/gastronomia/panel/pos/confirmar",
            json={
                "detalle": detalle,
                "forma_pago": "efectivo",
                "tipo_venta": "diferida",
            },
        )
        assert respuesta.status_code == 400
        assert "tipo de venta" in respuesta.get_json()["error"]


def test_confirmar_pos_preserva_error_del_servicio():
    app = crear_app_prueba()
    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-propio"},
        ),
        patch(
            "gastronomia.routes.crear_pedido",
            side_effect=PedidoError("Producto ajeno.", 400),
        ),
    ):
        respuesta = app.test_client().post(
            "/gastronomia/panel/pos/confirmar",
            json={
                "detalle": [{"id": "otro", "cantidad": 1}],
                "forma_pago": "transferencia",
                "tipo_venta": "preparacion",
            },
        )

    assert respuesta.status_code == 400
    assert respuesta.get_json() == {"ok": False, "error": "Producto ajeno."}
