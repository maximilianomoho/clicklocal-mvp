from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from gastronomia import gastronomia_bp
from gastronomia.routes import (
    LIMITE_PRODUCTOS_ACTIVOS_GASTRONOMIA,
    _limite_productos_gastronomia_alcanzado,
)


class ConsultaToggleFalsa:
    def __init__(self, db):
        self.db = db
        self.operacion = "select"
        self.filtros = []
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.operacion = "update"
        self.payload = payload
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operacion == "update":
            self.db.actualizacion = self.payload
            return SimpleNamespace(data=[])
        if ("id", "producto-1") in self.filtros:
            return SimpleNamespace(data=[{"id": "producto-1", "activo": False}])
        if ("activo", True) in self.filtros:
            return SimpleNamespace(data=[
                {"id": f"producto-{indice}"}
                for indice in range(self.db.cantidad_activos)
            ])
        return SimpleNamespace(data=[])


class DBToggleFalsa:
    def __init__(self, cantidad_activos):
        self.cantidad_activos = cantidad_activos
        self.actualizacion = None

    def table(self, tabla):
        assert tabla == "gastronomia_productos"
        return ConsultaToggleFalsa(self)


def crear_app_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(gastronomia_bp)

    @app.route("/login", endpoint="login")
    def login():
        return "login"

    return app


def _reactivar_con(cantidad_activos):
    db = DBToggleFalsa(cantidad_activos)
    app = crear_app_prueba()
    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        respuesta = app.test_client().post(
            "/gastronomia/panel/producto/producto-1/toggle-activo"
        )
    return respuesta, db


def test_base_permite_reactivar_producto_numero_30():
    assert not _limite_productos_gastronomia_alcanzado(29)
    respuesta, db = _reactivar_con(29)
    assert respuesta.status_code == 302
    assert "limite_productos" not in respuesta.location
    assert db.actualizacion == {"activo": True}


def test_base_rechaza_superar_30_productos_activos():
    assert _limite_productos_gastronomia_alcanzado(30)
    respuesta, db = _reactivar_con(30)
    assert respuesta.status_code == 302
    assert "limite_productos=1" in respuesta.location
    assert db.actualizacion is None


def test_modelo_catalogo_gastronomico_no_conserva_premium():
    raiz = Path(__file__).resolve().parents[1]
    rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
    panel = (raiz / "templates/gastronomia/panel.html").read_text(encoding="utf-8")

    assert LIMITE_PRODUCTOS_ACTIVOS_GASTRONOMIA == 30
    for texto in (
        "Gastronomía Premium",
        "Tu plan actual: Gratis",
        "Pasar a Gastronomía Premium",
        "premium-gastronomia-modal",
        "abrir-premium-modal",
        "_es_premium_gastronomia",
        "premium_bloqueado",
        "limite_productos_gratis",
    ):
        assert texto not in rutas
        assert texto not in panel


def test_base_muestra_extras_promociones_destacados_y_rendimiento():
    panel = Path("templates/gastronomia/panel.html").read_text(encoding="utf-8")
    for texto in (
        "gastronomia.extras_producto",
        "gastronomia.destacar_producto",
        "gastronomia.guardar_promocion_producto",
        "Rendimiento por producto",
        "metricas_por_producto.get",
    ):
        assert texto in panel
    assert "Opciones y extras 🔒" not in panel
    assert "Promociones 🔒" not in panel
    assert "Rendimiento 🔒" not in panel


def test_backend_no_bloquea_funciones_base_por_plan_viejo():
    rutas = Path("gastronomia/routes.py").read_text(encoding="utf-8")
    for nombre in (
        "destacar_producto",
        "quitar_destacado_producto",
        "guardar_promocion_producto",
        "quitar_promocion_producto",
        "extras_producto",
        "crear_grupo_extra",
        "crear_opcion_extra",
        "eliminar_grupo_extra",
        "eliminar_opcion_extra",
    ):
        bloque = rutas.split(f"def {nombre}", 1)[1].split("\n\ndef ", 1)[0]
        assert "premium" not in bloque.lower()


def test_metricas_base_filtran_origen_clicklocal_y_pos_conserva_amplitud():
    rutas = Path("gastronomia/routes.py").read_text(encoding="utf-8")
    bloque = rutas.split("consulta_metricas = (", 1)[1].split(
        "pedidos_metricas_res =", 1
    )[0]
    assert '"estado,estado_pago,origen,"' in bloque
    assert "if not pos_activo:" in bloque
    assert '"origen",\n                    "clicklocal"' in bloque


def test_template_catalogo_compila():
    from app import app

    app.jinja_env.get_template("gastronomia/panel.html")
    app.jinja_env.get_template("gastronomia/productos.html")
