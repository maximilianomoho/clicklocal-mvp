from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
import pytest

from gastronomia import gastronomia_bp
from gastronomia.routes import (
    _parsear_precio_producto,
    _precio_producto_input,
)


class ConsultaEdicionFalsa:
    def __init__(self, db):
        self.db = db
        self.datos_actualizados = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, datos):
        self.datos_actualizados = datos
        self.db.actualizacion = datos
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.datos_actualizados is None:
            return SimpleNamespace(data=[{
                "id": "producto-1",
                "imagen_url": "https://example.test/producto.jpg",
                "precio": 10000,
                "activo": True,
            }])
        return SimpleNamespace(data=[])


class SupabaseEdicionFalso:
    def __init__(self):
        self.actualizacion = None

    def table(self, tabla):
        assert tabla == "gastronomia_productos"
        return ConsultaEdicionFalsa(self)


class ConsultaComercioFalsa:
    def __init__(self, db):
        self.db = db

    def update(self, datos):
        self.db.actualizacion = datos
        return self

    def eq(self, campo, valor):
        self.db.filtro = (campo, valor)
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class SupabaseComercioFalso:
    def __init__(self):
        self.tabla = None
        self.actualizacion = None
        self.filtro = None

    def table(self, tabla):
        self.tabla = tabla
        return ConsultaComercioFalsa(self)


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


def test_precio_entero_se_muestra_sin_decimal_innecesario():
    assert _precio_producto_input(5500) == "5500"
    assert _precio_producto_input(5500.0) == "5500"
    assert _precio_producto_input(12000) == "12000"
    assert _precio_producto_input(12000.0) == "12000"
    assert _precio_producto_input(3500.5) == "3500.5"


def test_precio_decimal_del_formulario_no_se_multiplica():
    assert _parsear_precio_producto("5500") == 5500
    assert _parsear_precio_producto("5500.0") == 5500
    assert _parsear_precio_producto("5500.00") == 5500
    assert isinstance(_parsear_precio_producto("5500.0"), int)

    with pytest.raises(ValueError):
        _parsear_precio_producto("")


def test_editar_categoria_o_descripcion_mantiene_precio_5500():
    for categoria, descripcion in (
        ("Bebidas", "Descripción original"),
        ("Hamburguesas", "Descripción editada"),
    ):
        db = SupabaseEdicionFalso()
        app = crear_app_prueba()

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-1"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/producto/producto-1/editar",
                data={
                    "nombre": "Producto",
                    "precio": "5500.0",
                    "categoria": categoria,
                    "descripcion": descripcion,
                    "imagen_url": "https://example.test/producto.jpg",
                },
            )

        assert respuesta.status_code == 302
        assert db.actualizacion["precio"] == 5500
        assert db.actualizacion["categoria"] == categoria
        assert db.actualizacion["descripcion"] == descripcion


def test_formatos_de_miles_ya_admitidos_se_conservan():
    assert _parsear_precio_producto("$ 12.000") == 12000
    assert _parsear_precio_producto("3.500,50") == 3500.5


def test_crear_y_editar_comparten_el_parser_seguro():
    raiz = Path(__file__).resolve().parents[1]
    rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")

    assert rutas.count("precio = _parsear_precio_producto(precio_raw)") == 2


def test_formulario_de_edicion_usa_el_valor_limpio():
    raiz = Path(__file__).resolve().parents[1]
    plantilla = (
        raiz / "templates/gastronomia/panel.html"
    ).read_text(encoding="utf-8")

    assert 'data-precio="{{ producto.precio_input }}"' in plantilla
    assert 'value="{{ producto.precio_promocional_input }}"' in plantilla
    assert 'value="{{ configuracion.pedido_minimo_input }}"' in plantilla
    assert 'value="{{ configuracion.costo_envio_input }}"' in plantilla
    assert 'name="delivery_origen_latitud"' not in plantilla
    assert 'name="delivery_origen_longitud"' not in plantilla
    assert "Guardar ubicación de salida del delivery" in plantilla
    assert 'value="{{ configuracion.descuento_efectivo_pct_input }}"' in plantilla
    assert 'value="{{ configuracion.descuento_transferencia_pct_input }}"' in plantilla
    assert "configuracion.pedido_minimo|int" not in plantilla


def test_datos_comercio_se_precargan_antes_de_delivery():
    raiz = Path(__file__).resolve().parents[1]
    plantilla = (
        raiz / "templates/gastronomia/panel.html"
    ).read_text(encoding="utf-8")

    posicion_datos = plantilla.index("Datos del comercio")
    posicion_delivery = plantilla.index("Entrega y tiempos")

    assert posicion_datos < posicion_delivery
    bloque_datos = plantilla[posicion_datos:posicion_delivery]
    assert 'name="nombre_negocio"' in bloque_datos
    assert "comercio.nombre_negocio" in bloque_datos
    assert 'name="descripcion"' in bloque_datos
    assert "comercio.descripcion or ''" in bloque_datos
    assert 'name="direccion"' in plantilla
    assert "comercio.direccion or comercio.direccion_mostrar" in plantilla
    assert 'name="ciudad"' in plantilla
    assert "comercio.ciudad or ''" in plantilla
    assert 'name="whatsapp"' in plantilla
    assert "comercio.whatsapp or ''" in plantilla
    assert 'name="logo"' in bloque_datos
    assert "comercio.logo_url" in bloque_datos
    assert 'name="email"' not in bloque_datos
    assert 'name="categoria"' not in bloque_datos


def test_guardar_datos_comercio_normaliza_y_actualiza_sesion():
    db = SupabaseComercioFalso()
    app = crear_app_prueba()

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["user_id"] = "admin-user"
            sesion["admin_logueado"] = True
            sesion["gestor_comercio_id"] = "comercio-1"
            sesion["comercio"] = {"id": "comercio-1"}

        respuesta = cliente.post(
            "/gastronomia/panel/datos-comercio",
            data={
                "nombre_negocio": " La Esquina ",
                "descripcion": " Bar y pizzería ",
                "direccion": " Nogoyá 150 ",
                "ciudad": " Paraná ",
                "whatsapp": "0343 415-0049",
            },
        )

        with cliente.session_transaction() as sesion:
            comercio_sesion = sesion["comercio"]
            user_id_sesion = sesion["user_id"]
            gestor_comercio_id = sesion["gestor_comercio_id"]

    assert respuesta.status_code == 302
    assert respuesta.headers["Location"].endswith(
        "?datos_comercio_ok=1#datos-comercio"
    )
    assert db.tabla == "comercios"
    assert db.filtro == ("id", "comercio-1")
    assert db.actualizacion == {
        "nombre_negocio": "La Esquina",
        "descripcion": "Bar y pizzería",
        "direccion": "Nogoyá 150",
        "direccion_mostrar": "Nogoyá 150",
        "ciudad": "Paraná",
        "whatsapp": "5493434150049",
    }
    assert comercio_sesion == {
        "id": "comercio-1",
        **db.actualizacion,
    }
    assert user_id_sesion == "admin-user"
    assert gestor_comercio_id == "comercio-1"


def test_guardar_datos_comercio_no_toca_configuracion_delivery():
    db = SupabaseComercioFalso()
    app = crear_app_prueba()

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        respuesta = app.test_client().post(
            "/gastronomia/panel/datos-comercio",
            data={
                "nombre_negocio": "La Esquina",
                "descripcion": "Rotisería",
                "direccion": "Nogoyá 150",
                "ciudad": "Paraná",
                "whatsapp": "3434150049",
            },
        )

    assert respuesta.status_code == 302
    assert db.tabla == "comercios"
    assert not any(
        clave.startswith("delivery_")
        for clave in db.actualizacion
    )


def test_promocion_usa_parser_seguro_sin_eliminar_punto_decimal():
    raiz = Path(__file__).resolve().parents[1]
    rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
    bloque = rutas.split("def guardar_promocion_producto", 1)[1].split(
        "def quitar_promocion_producto", 1
    )[0]

    assert "precio_promocional = _parsear_precio_producto(precio_raw)" in bloque
    assert '.replace(".", "")' not in bloque


@pytest.mark.parametrize(
    ("valor_formulario", "valor_esperado"),
    (("5500.0", 5500), ("3500.5", 3500.5)),
)
def test_guardar_promocion_sin_editar_importe_lo_conserva(
    valor_formulario,
    valor_esperado,
):
    db = SupabaseEdicionFalso()
    app = crear_app_prueba()

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        respuesta = app.test_client().post(
            "/gastronomia/panel/producto/producto-1/promocion",
            data={"precio_promocional": valor_formulario},
        )

    assert respuesta.status_code == 302
    assert db.actualizacion["precio_promocional"] == valor_esperado
