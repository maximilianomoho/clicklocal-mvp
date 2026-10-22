from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, session

from gastronomia import gastronomia_bp
from modulos import CATALOGO_MODULOS, pos_activo_para_comercio


class ConsultaPedidos:
    def __init__(self, db, filas):
        self.db = db
        self.filas = [dict(fila) for fila in filas]
        self.filtros = []
        self.rango = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append(("eq", campo, valor))
        return self

    def in_(self, campo, valores):
        self.filtros.append(("in", campo, tuple(valores)))
        return self

    def gte(self, *_args):
        return self

    def lt(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def range(self, desde, hasta):
        self.rango = (desde, hasta)
        return self

    def execute(self):
        filas = self.filas
        for operador, campo, valor in self.filtros:
            if operador == "eq" and campo in {"comercio_id", "origen"}:
                filas = [fila for fila in filas if fila.get(campo) == valor]
        total = len(filas)
        if self.rango:
            filas = filas[self.rango[0]:self.rango[1] + 1]
        return SimpleNamespace(data=filas, count=total)


class DBPedidos:
    def __init__(self, filas):
        self.consultas = []
        self.filas = filas

    def table(self, tabla):
        assert tabla == "gastronomia_pedidos"
        consulta = ConsultaPedidos(self, self.filas)
        self.consultas.append(consulta)
        return consulta


class ConsultaConfiguracion:
    def __init__(self, db):
        self.db = db
        self.filtros = []
        self.cambios = None

    def select(self, *_args, **_kwargs): return self
    def eq(self, campo, valor): self.filtros.append((campo, valor)); return self
    def limit(self, *_args): return self
    def update(self, cambios): self.cambios = dict(cambios); return self

    def execute(self):
        filas = [
            fila for fila in self.db.filas
            if all(fila.get(campo) == valor for campo, valor in self.filtros)
        ]
        if self.cambios is not None:
            for fila in filas:
                fila.update(self.cambios)
        return SimpleNamespace(data=[dict(fila) for fila in filas])


class DBConfiguracion:
    def __init__(self):
        self.filas = [
            {
                "comercio_id": "comercio-1",
                "modalidad_pedidos": "solo_whatsapp",
                "delivery_franjas": [],
                "delivery_origen_direccion": "",
                "delivery_origen_latitud": None,
                "delivery_origen_longitud": None,
            },
            {
                "comercio_id": "comercio-2",
                "modalidad_pedidos": "solo_whatsapp",
            },
        ]

    def table(self, tabla):
        assert tabla == "gastronomia_configuracion"
        return ConsultaConfiguracion(self)


def crear_app():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(gastronomia_bp)

    @app.route("/login", endpoint="login")
    def login():
        return "login"

    return app


def pedido(pedido_id, origen, estado="pendiente", estado_pago="pendiente"):
    return {
        "id": pedido_id,
        "comercio_id": "comercio-1",
        "numero_pedido": 1,
        "created_at": "2026-09-18T15:00:00+00:00",
        "estado": estado,
        "estado_pago": estado_pago,
        "origen": origen,
        "total": 1000,
        "forma_pago": "efectivo",
        "detalle": [],
    }


def test_catalogo_pos_reutiliza_comercio_modulos():
    assert CATALOGO_MODULOS["pos"]["slug"] == "pos"
    with patch("modulos.modulo_activo", return_value=True) as modulo_activo:
        assert pos_activo_para_comercio("comercio-1") is True
    modulo_activo.assert_called_once_with("comercio-1", "pos")


def test_sin_pos_pedidos_filtra_clicklocal_y_ventas_redirige_sin_consultar():
    app = crear_app()
    db = DBPedidos([
        pedido("online", "clicklocal", estado_pago="pagado"),
        pedido("local", "pos", estado_pago="pagado"),
        pedido("legacy", None, estado_pago="pagado"),
    ])

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=False),
        patch(
            "gastronomia.routes._modalidad_pedidos_comercio",
            return_value="bandeja",
        ),
        patch("gastronomia.routes.supabase_admin", db),
        patch("gastronomia.routes.render_template", return_value="ok") as render,
    ):
        assert app.test_client().get("/gastronomia/panel/pedidos").status_code == 200
        pedidos_contexto = render.call_args.kwargs
        assert pedidos_contexto["pos_activo"] is False
        assert [p["id"] for p in pedidos_contexto["pedidos_recibidos"]] == ["online"]
        assert pedidos_contexto["paginacion_recibidos"]["total"] == 1

        with patch("gastronomia.routes._consultar_ventas_clicklocal") as consultar_ventas:
            respuesta_ventas = app.test_client().get("/gastronomia/panel/ventas")
        assert respuesta_ventas.status_code == 302
        assert respuesta_ventas.location.endswith("/gastronomia/panel/pedidos")
        consultar_ventas.assert_not_called()

    assert all(
        ("eq", "origen", "clicklocal") in consulta.filtros
        for consulta in db.consultas
    )


def test_sin_pos_bloquea_rutas_operativas_en_servidor():
    app = crear_app()
    comercio = {"id": "comercio-1"}
    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value=comercio),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=False),
    ):
        cliente = app.test_client()
        for ruta in (
            "/gastronomia/panel/pos",
            "/gastronomia/panel/pedidos/historial/cajas",
            "/gastronomia/panel/pedidos/historial/cajas/caja-1",
        ):
            respuesta = cliente.get(ruta)
            assert respuesta.status_code == 302
            assert "/gastronomia/panel/ventas?pos_requerido=1" in respuesta.location

        for ruta in (
            "/gastronomia/panel/pos/confirmar",
            "/gastronomia/panel/ventas/cerrar-caja",
            "/gastronomia/panel/ventas/pedido-1/anular",
        ):
            respuesta = cliente.post(ruta, json={})
            assert respuesta.status_code == 403
            assert "POS activo" in respuesta.get_json()["error"]


def test_plantilla_base_es_lectura_y_pos_conserva_herramientas():
    raiz = Path(__file__).resolve().parents[1]
    plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
        encoding="utf-8"
    )
    bloque_pos, bloque_base = plantilla.split(
        '{% else %}\n<section\n  class="pedidos-recibidos"', 1
    )
    assert "{% for estado in estados_kanban %}" in bloque_pos
    assert "data-accion-estado" in bloque_pos
    assert "data-accion-entrega" in bloque_pos
    assert "{% for pedido in pedidos_recibidos %}" in bloque_base
    assert "pedido.detalle_items" in bloque_base
    assert "data-accion-estado" not in bloque_base
    assert "data-accion-entrega" not in bloque_base
    assert "data-confirmar-cobro" not in bloque_base
    assert "data-activar-sonido" in bloque_base
    assert "js-ver-comanda" not in bloque_base
    assert "js-ver-ticket" not in bloque_base
    assert "data-imprimir-pedido" in bloque_base
    assert "data-compartir-pedido" in bloque_base
    assert "pedido_acciones_base.js" in bloque_base


def test_configuracion_base_guarda_modalidad_y_aísla_comercio():
    app = crear_app()
    db = DBConfiguracion()
    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=False),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        respuesta = app.test_client().post(
            "/gastronomia/panel/configuracion",
            data={
                "modalidad_pedidos": "bandeja",
                "acepta_retiro": "on",
                "pedido_minimo": "0",
                "costo_envio": "0",
                "tiempo_estimado_min": "30",
                "descuento_efectivo_pct": "0",
                "descuento_transferencia_pct": "0",
                "delivery_modalidad": "fijo",
            },
        )

    assert respuesta.status_code == 302
    assert db.filas[0]["modalidad_pedidos"] == "bandeja"
    assert db.filas[1]["modalidad_pedidos"] == "solo_whatsapp"


def test_pos_no_escribe_modalidad_base_desde_configuracion():
    app = crear_app()
    db = DBConfiguracion()
    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=True),
        patch("gastronomia.routes.supabase_admin", db),
    ):
        respuesta = app.test_client().post(
            "/gastronomia/panel/configuracion",
            data={
                "modalidad_pedidos": "bandeja",
                "acepta_retiro": "on",
                "pedido_minimo": "0",
                "costo_envio": "0",
                "tiempo_estimado_min": "30",
                "descuento_efectivo_pct": "0",
                "descuento_transferencia_pct": "0",
                "delivery_modalidad": "fijo",
            },
        )

    assert respuesta.status_code == 302
    assert db.filas[0]["modalidad_pedidos"] == "solo_whatsapp"


def test_modo_gestor_conserva_usuario_y_respeta_pos_del_comercio():
    app = crear_app()
    db = DBPedidos([pedido("online", "clicklocal")])
    cliente = app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = "comercio-1"
        sesion["user_id"] = "admin-user"
        sesion["comercio"] = {"id": "comercio-1"}

    with (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=False) as activo,
        patch(
            "gastronomia.routes._modalidad_pedidos_comercio",
            return_value="bandeja",
        ),
        patch("gastronomia.routes.supabase_admin", db),
        patch("gastronomia.routes.render_template", return_value="pedidos"),
    ):
        assert cliente.get("/gastronomia/panel/pedidos").status_code == 200

    activo.assert_called_with("comercio-1")
    with cliente.session_transaction() as sesion:
        assert sesion["user_id"] == "admin-user"
        assert sesion["gestor_comercio_id"] == "comercio-1"


def test_navegacion_no_duplica_pedidos_ventas_y_oculta_pos_condicionalmente():
    raiz = Path(__file__).resolve().parents[1]
    nav = (raiz / "templates/gastronomia/_panel_nav.html").read_text(
        encoding="utf-8"
    )
    assert nav.count(">Punto de venta</a>") == 1
    assert nav.count(">Historial de cajas</a>") == 1
    assert "{% if pos_activo %}" in nav
    assert "Pedidos POS" not in nav
    assert "Ventas POS" not in nav


def test_checkout_publico_conserva_origen_clicklocal_sin_consultar_pos():
    raiz = Path(__file__).resolve().parents[1]
    servicio = (raiz / "gastronomia/services/pedidos.py").read_text(
        encoding="utf-8"
    )
    rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
    registrar = rutas.split("def registrar_pedido", 1)[1].split(
        "@gastronomia_bp.route", 1
    )[0]
    assert 'origen="clicklocal"' in servicio
    assert "_pos_activo_gastronomia" not in registrar


def test_pantalla_base_es_lectura_simple_y_pos_conserva_operacion():
    raiz = Path(__file__).resolve().parents[1]
    plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
        encoding="utf-8"
    )
    separador = '{% else %}\n<section\n  class="pedidos-recibidos"'
    pos, base = plantilla.split(separador, 1)

    assert "Pedidos recibidos" in plantilla
    assert "pedidos_recibidos" in base
    assert "data-confirmar-cobro" not in base
    assert "data-accion-estado" not in base
    assert "data-accion-entrega" not in base
    assert "data-activar-sonido" in base
    assert "data-accion-estado" in pos
    assert "data-activar-sonido" in pos
