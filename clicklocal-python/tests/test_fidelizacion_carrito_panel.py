from types import SimpleNamespace

import pytest
from flask import Flask, session

from pedidos_comercio import pedidos_comercio_bp
from pedidos_comercio import routes


COMERCIO_A = "11111111-1111-4111-8111-111111111111"
COMERCIO_B = "22222222-2222-4222-8222-222222222222"
CLAVE = "5493436123456"
TOKEN = "33333333-3333-4333-8333-333333333333"


def app_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(pedidos_comercio_bp)
    return app


def pedido(cliente_clave=CLAVE):
    return {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "numero": 1,
        "nombre_cliente": "Ana Pérez",
        "whatsapp_cliente": "3436123456",
        "whatsapp_normalizado": cliente_clave,
        "created_at": "2026-10-10T12:00:00+00:00",
        "total": 1000,
        "estado": "entregado",
        "tipo_entrega": "retiro",
    }


def fidelizacion(saldo=3, disponible=False):
    return {
        "activa": True,
        "meta_sellos": 5,
        "premio": "20% de descuento",
        "periodo_abierto": {"id": "periodo", "inicio": "2026-10-01T00:00:00+00:00"},
        "clientes": [{
            "cliente_clave": CLAVE,
            "sellos_brutos": saldo,
            "sellos_consumidos": 0,
            "saldo": saldo,
            "premio_disponible": disponible,
            "canjes": [],
        }],
    }


class RpcFalsa:
    def __init__(self, db, nombre, parametros):
        self.db = db
        self.nombre = nombre
        self.parametros = parametros

    def execute(self):
        self.db.llamadas.append((self.nombre, self.parametros))
        if self.nombre == "consultar_fidelizacion_clicklocal":
            return SimpleNamespace(data=self.db.lectura)
        if self.nombre == "configurar_fidelizacion_clicklocal":
            return SimpleNamespace(data=[{"activa": self.parametros["p_activa"]}])
        if self.nombre == "canjear_premio_fidelizacion_clicklocal":
            if self.db.error_canje:
                raise RuntimeError("fallo controlado")
            return SimpleNamespace(data=[{"ok": True, "canje_id": "canje"}])
        raise AssertionError(self.nombre)


class DbFalsa:
    def __init__(self, lectura=None, error_canje=False):
        self.lectura = lectura or fidelizacion()
        self.error_canje = error_canje
        self.llamadas = []

    def rpc(self, nombre, parametros):
        return RpcFalsa(self, nombre, parametros)


def preparar(monkeypatch, db=None, comercio_id=COMERCIO_A):
    db = db or DbFalsa()
    monkeypatch.setattr(routes, "supabase_admin", db)
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": comercio_id, "plan": "gratis"})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(routes, "_consulta_pedidos_clientes", lambda cid: [pedido()] if cid == comercio_id else [])
    return db


def test_lectura_rpc_y_adjunto_de_sellos():
    db = DbFalsa(fidelizacion(saldo=5, disponible=True))
    resultado = routes._consultar_fidelizacion(COMERCIO_A, [CLAVE], db)
    clientes = routes._agrupar_clientes([pedido()], COMERCIO_A)
    routes._adjuntar_fidelizacion(clientes, resultado)
    assert db.llamadas == [("consultar_fidelizacion_clicklocal", {
        "p_comercio_id": COMERCIO_A,
        "p_cliente_claves": [CLAVE],
    })]
    assert clientes[0]["fidelizacion"]["saldo"] == 5
    assert clientes[0]["fidelizacion"]["premio_disponible"] is True


def test_lista_consulta_una_vez_solo_claves_de_la_pagina(monkeypatch):
    db = preparar(monkeypatch)
    capturado = {}
    monkeypatch.setattr(routes, "render_template", lambda plantilla, **datos: capturado.update(datos) or "ok")
    app = app_prueba()
    with app.test_request_context("/panel/pedidos/clientes"):
        assert routes.clientes_pedidos() == "ok"
    assert db.llamadas == [("consultar_fidelizacion_clicklocal", {
        "p_comercio_id": COMERCIO_A,
        "p_cliente_claves": [CLAVE],
    })]
    assert capturado["clientes"][0]["fidelizacion"]["saldo"] == 3


@pytest.mark.parametrize(("activa", "esperada"), [("1", True), (None, False)])
def test_guardado_configura_comercio_contextual(monkeypatch, activa, esperada):
    db = preparar(monkeypatch, comercio_id=COMERCIO_B)
    app = app_prueba()
    datos = {"fidelizacion_meta_sellos": "5", "fidelizacion_premio": "Premio"}
    if activa:
        datos["fidelizacion_activa"] = activa
    with app.test_request_context("/panel/pedidos/fidelizacion", method="POST", data=datos):
        respuesta = routes.guardar_fidelizacion()
    assert respuesta.status_code == 302
    nombre, parametros = db.llamadas[0]
    assert nombre == "configurar_fidelizacion_clicklocal"
    assert parametros == {
        "p_comercio_id": COMERCIO_B,
        "p_activa": esperada,
        "p_meta_sellos": 5,
        "p_premio": "Premio",
    }


@pytest.mark.parametrize("campo,valor", [
    ("fidelizacion_meta_sellos", "0"),
    ("fidelizacion_meta_sellos", "1.5"),
    ("fidelizacion_premio", "   "),
])
def test_configuracion_invalida_no_llama_rpc(monkeypatch, campo, valor):
    db = preparar(monkeypatch)
    datos = {"fidelizacion_meta_sellos": "5", "fidelizacion_premio": "Premio"}
    datos[campo] = valor
    app = app_prueba()
    with app.test_request_context("/panel/pedidos/fidelizacion", method="POST", data=datos):
        respuesta = routes.guardar_fidelizacion()
    assert respuesta.status_code == 302
    assert db.llamadas == []


def test_ficha_consulta_solo_clave_resuelta_y_crea_token(monkeypatch):
    db = preparar(monkeypatch)
    capturado = {}
    monkeypatch.setattr(routes, "render_template", lambda plantilla, **datos: capturado.update(datos) or "ok")
    identificador = routes._identificador_cliente(COMERCIO_A, CLAVE)
    app = app_prueba()
    with app.test_request_context("/panel/pedidos/clientes/" + identificador):
        assert routes.cliente_pedidos(identificador) == "ok"
        assert session["fidelizacion_canjes_pendientes"][identificador] == capturado["token_canje"]
    assert db.llamadas[0][1]["p_cliente_claves"] == [CLAVE]


def test_canje_usa_clave_resuelta_y_token_de_sesion(monkeypatch):
    db = preparar(monkeypatch)
    identificador = routes._identificador_cliente(COMERCIO_A, CLAVE)
    app = app_prueba()
    with app.test_request_context(
        "/panel/pedidos/clientes/" + identificador + "/canjear-premio",
        method="POST",
        data={"idempotency_key": TOKEN, "cliente_clave": "clave-atacante"},
    ):
        session["fidelizacion_canjes_pendientes"] = {identificador: TOKEN}
        respuesta = routes.canjear_premio_fidelizacion(identificador)
        assert "fidelizacion_canjes_pendientes" not in session
    assert respuesta.status_code == 302
    assert db.llamadas == [("canjear_premio_fidelizacion_clicklocal", {
        "p_comercio_id": COMERCIO_A,
        "p_cliente_clave": CLAVE,
        "p_idempotency_key": TOKEN,
    })]


def test_error_de_canje_conserva_token_para_retry(monkeypatch):
    db = preparar(monkeypatch, DbFalsa(error_canje=True))
    identificador = routes._identificador_cliente(COMERCIO_A, CLAVE)
    app = app_prueba()
    with app.test_request_context("/canjear", method="POST", data={"idempotency_key": TOKEN}):
        session["fidelizacion_canjes_pendientes"] = {identificador: TOKEN}
        respuesta = routes.canjear_premio_fidelizacion(identificador)
        assert session["fidelizacion_canjes_pendientes"][identificador] == TOKEN
    assert respuesta.status_code == 302
    assert db.llamadas[0][1]["p_idempotency_key"] == TOKEN


def test_token_invalido_rechaza_sin_llamar_rpc(monkeypatch):
    db = preparar(monkeypatch)
    identificador = routes._identificador_cliente(COMERCIO_A, CLAVE)
    app = app_prueba()
    with app.test_request_context("/canjear", method="POST", data={"idempotency_key": TOKEN}):
        session["fidelizacion_canjes_pendientes"] = {
            identificador: "44444444-4444-4444-8444-444444444444"
        }
        respuesta = routes.canjear_premio_fidelizacion(identificador)
    assert respuesta.status_code == 302
    assert db.llamadas == []


def test_exito_elimina_token_y_siguiente_get_genera_otro(monkeypatch):
    preparar(monkeypatch)
    identificador = routes._identificador_cliente(COMERCIO_A, CLAVE)
    app = app_prueba()
    with app.test_request_context("/canjear", method="POST", data={"idempotency_key": TOKEN}):
        session["fidelizacion_canjes_pendientes"] = {identificador: TOKEN}
        routes.canjear_premio_fidelizacion(identificador)
        nuevo_token = routes._token_canje_fidelizacion(identificador)
        assert nuevo_token != TOKEN


def test_tokens_son_independientes_por_cliente():
    app = app_prueba()
    identificador_a = routes._identificador_cliente(COMERCIO_A, CLAVE)
    identificador_b = routes._identificador_cliente(COMERCIO_A, "5493436999999")
    with app.test_request_context("/panel/pedidos/clientes"):
        token_a = routes._token_canje_fidelizacion(identificador_a)
        token_b = routes._token_canje_fidelizacion(identificador_b)
        assert token_a != token_b
        assert session["fidelizacion_canjes_pendientes"] == {
            identificador_a: token_a,
            identificador_b: token_b,
        }


def test_templates_muestran_configuracion_progreso_historial_y_formulario():
    panel = (routes.current_app.root_path if False else "templates/pedidos_comercio/panel.html")
    with open(panel, encoding="utf-8") as archivo:
        panel_html = archivo.read()
    with open("templates/pedidos_comercio/cliente.html", encoding="utf-8") as archivo:
        cliente_html = archivo.read()
    assert "Guardar fidelización" in panel_html
    assert "fidelizacion_meta_sellos" in panel_html
    assert "Programa de sellos" in cliente_html
    assert "Marcar premio como canjeado" in cliente_html
    assert "Historial de premios" in cliente_html
    assert 'name="idempotency_key"' in cliente_html


def test_backend_no_inserta_directamente_canjes():
    codigo = open("pedidos_comercio/routes.py", encoding="utf-8").read()
    assert '.table("pedidos_fidelizacion_canjes")' not in codigo
