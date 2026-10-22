from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from gastronomia import gastronomia_bp


class ConsultaBase:
    def __init__(self, db):
        self.db = db
        self.filtros = []
        self.cambios = None
        self.operaciones = []
        self.count = None

    def select(self, *_args, **_kwargs):
        self.operaciones.append(("select",))
        return self

    def eq(self, campo, valor):
        self.filtros.append(("eq", campo, valor))
        return self

    def is_(self, campo, valor):
        self.filtros.append(("is", campo, valor))
        return self

    def neq(self, campo, valor):
        self.operaciones.append(("neq", campo, valor))
        self.filtros.append(("neq", campo, valor))
        return self

    def or_(self, expresion):
        self.operaciones.append(("or", expresion))
        return self

    def in_(self, campo, valores):
        self.operaciones.append(("in", campo, tuple(valores)))
        return self

    def gte(self, *_args): return self
    def lt(self, *_args): return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, *_args): return self
    def range(self, *_args): return self

    def update(self, cambios):
        self.cambios = dict(cambios)
        return self

    def _coincide(self):
        for operador, campo, valor in self.filtros:
            actual = self.db.pedido.get(campo)
            if operador == "eq" and actual != valor:
                return False
            if operador == "is" and valor == "null" and actual is not None:
                return False
            if operador == "neq" and actual == valor:
                return False
        return True

    def execute(self):
        self.db.consultas.append(self)
        if not self._coincide():
            return SimpleNamespace(data=[], count=0)
        if self.cambios is not None:
            self.db.pedido.update(self.cambios)
        return SimpleNamespace(data=[dict(self.db.pedido)], count=1)


class DBBase:
    def __init__(self, **cambios):
        self.pedido = {
            "id": "pedido-1",
            "comercio_id": "comercio-1",
            "estado": "pendiente",
            "estado_pago": "pendiente",
            "pagado_at": None,
            "confirmado_at": None,
            "entregado_at": None,
            "origen": "clicklocal",
            "modalidad_gestion": "bandeja",
            "detalle": [],
        }
        self.pedido.update(cambios)
        self.consultas = []

    def table(self, tabla):
        assert tabla == "gastronomia_pedidos"
        return ConsultaBase(self)


def crear_app():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(gastronomia_bp)

    @app.route("/login", endpoint="login")
    def login(): return "login"

    return app


def contexto_base(db):
    return (
        patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value={"id": "comercio-1"},
        ),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=False),
        patch("gastronomia.routes.supabase_admin", db),
    )


def test_pantalla_base_no_clasifica_ni_expone_acciones_operativas():
    plantilla = (
        Path(__file__).resolve().parents[1]
        / "templates/gastronomia/pedidos.html"
    ).read_text(encoding="utf-8")
    bloque_base = plantilla.split('class="pedidos-recibidos"', 1)[1]
    assert "pedidos_recibidos" in bloque_base
    assert "data-confirmar-cobro" not in bloque_base
    assert "data-accion-estado" not in bloque_base
    assert "data-accion-entrega" not in bloque_base


def test_confirmacion_base_esta_deshabilitada_y_no_cambia_estado():
    db = DBBase()
    app = crear_app()
    comercio, pos, supabase = contexto_base(db)
    with comercio, pos, supabase:
        cliente = app.test_client()
        primera = cliente.post(
            "/gastronomia/panel/pedidos/pedido-1/confirmacion",
            json={},
        )
        confirmado_at = db.pedido["confirmado_at"]
        segunda = cliente.post(
            "/gastronomia/panel/pedidos/pedido-1/confirmacion",
            json={},
        )
    assert primera.status_code == segunda.status_code == 403
    assert confirmado_at is None
    assert db.pedido["confirmado_at"] is None
    assert db.pedido["estado"] == "pendiente"


def test_pago_y_entrega_base_estan_deshabilitados():
    db = DBBase()
    app = crear_app()
    comercio, pos, supabase = contexto_base(db)
    with comercio, pos, supabase:
        cliente = app.test_client()
        pago = cliente.post(
            "/gastronomia/panel/pedidos/pedido-1/pago",
            json={"estado_pago": "pagado"},
        )
        entrega = cliente.post(
            "/gastronomia/panel/pedidos/pedido-1/entrega",
            json={},
        )
    assert pago.status_code == 403
    assert entrega.status_code == 403
    assert db.pedido["estado"] == "pendiente"
    assert db.pedido["estado_pago"] == "pendiente"
    assert db.pedido["entregado_at"] is None


def test_entrega_base_repetida_no_escribe_fecha():
    db = DBBase()
    app = crear_app()
    comercio, pos, supabase = contexto_base(db)
    with comercio, pos, supabase:
        cliente = app.test_client()
        primera = cliente.post(
            "/gastronomia/panel/pedidos/pedido-1/entrega",
            json={},
        )
        entregado_at = db.pedido["entregado_at"]
        segunda = cliente.post(
            "/gastronomia/panel/pedidos/pedido-1/entrega",
            json={},
        )
    assert primera.status_code == segunda.status_code == 403
    assert entregado_at is None
    assert db.pedido["entregado_at"] is None


def test_base_rechaza_reversion_y_pedidos_de_otro_comercio_u_origen():
    app = crear_app()
    for db in (
        DBBase(estado_pago="pagado"),
        DBBase(comercio_id="comercio-ajeno"),
        DBBase(origen="pos"),
    ):
        comercio, pos, supabase = contexto_base(db)
        with comercio, pos, supabase:
            respuesta = app.test_client().post(
                "/gastronomia/panel/pedidos/pedido-1/pago",
                json={"estado_pago": "pendiente"},
            )
        assert respuesta.status_code == 403


def test_historial_base_filtra_finalizados_antes_de_paginar():
    db = DBBase()
    app = crear_app()
    comercio, pos, supabase = contexto_base(db)
    with comercio, pos, supabase, patch(
        "gastronomia.routes.render_template",
        return_value="ok",
    ):
        respuesta = app.test_client().get(
            "/gastronomia/panel/pedidos/historial?vista=finalizados"
        )
    assert respuesta.status_code == 200
    consulta = db.consultas[-1]
    assert (
        "or",
        "estado.eq.cancelado,and(estado_pago.eq.pagado,entregado_at.not.is.null)",
    ) in consulta.operaciones


def test_migraciones_locales_preparadas_sin_backfill():
    raiz = Path(__file__).resolve().parents[1]
    columna = (
        raiz / "sql/gastronomia_base_confirmado_at_20261015.sql"
    ).read_text(encoding="utf-8").lower()
    caja = (
        raiz / "sql/gastronomia_asignacion_caja_pos_vigente_20261015.sql"
    ).read_text(encoding="utf-8").lower()
    assert "add column if not exists confirmado_at timestamptz" in columna
    assert "update public.gastronomia_pedidos" not in columna
    assert "from public.comercio_modulos" in caja
    assert "modulo.modulo = 'pos'" in caja
    assert "modulo.gracia_dias" in caja
