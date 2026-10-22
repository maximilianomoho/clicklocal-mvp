from types import SimpleNamespace

import pytest
from flask import Flask

from pedidos_comercio import routes


COMERCIO_A = "11111111-1111-4111-8111-111111111111"
COMERCIO_B = "22222222-2222-4222-8222-222222222222"


def pedidos_cliente():
    return [
        {"id": "p3", "numero": 3, "nombre_cliente": "Juan Pérez", "whatsapp_cliente": "343 612-3456", "whatsapp_normalizado": "5493436123456", "created_at": "2026-10-06T12:00:00+00:00", "total": 3000, "estado": "cancelado", "tipo_entrega": "delivery"},
        {"id": "p2", "numero": 2, "nombre_cliente": "Juan Pérez", "whatsapp_cliente": "3436123456", "whatsapp_normalizado": "5493436123456", "created_at": "2026-10-05T12:00:00+00:00", "total": 2000, "estado": "entregado", "tipo_entrega": "retiro"},
        {"id": "p1", "numero": 1, "nombre_cliente": "Juan", "whatsapp_cliente": "0343 6123456", "whatsapp_normalizado": "5493436123456", "created_at": "2026-10-01T12:00:00+00:00", "total": 1000, "estado": "entregado", "tipo_entrega": "retiro"},
    ]


def test_mismo_whatsapp_agrupa_nombres_y_calcula_resumen_sin_cancelados():
    clientes = routes._agrupar_clientes(pedidos_cliente(), COMERCIO_A)
    assert len(clientes) == 1
    cliente = clientes[0]
    assert cliente["nombre"] == "Juan Pérez"
    assert cliente["pedidos_compra"] == 2
    assert cliente["total_comprado"] == 3000
    assert cliente["ticket_promedio"] == 1500
    assert cliente["primera_compra"] == "01/10/2026 09:00"
    assert cliente["ultima_compra"] == "05/10/2026 09:00"
    assert [pedido["id"] for pedido in cliente["pedidos"]] == ["p3", "p2", "p1"]


def test_whatsapp_antiguo_sin_normalizado_usa_normalizador_actual():
    antiguo = pedidos_cliente()[2]
    antiguo["whatsapp_normalizado"] = ""
    cliente = routes._agrupar_clientes([antiguo], COMERCIO_A)[0]
    assert cliente["clave"] == "5493436123456"
    assert cliente["whatsapp"] == "0343 6123456"


def test_identificador_no_expone_whatsapp_y_depende_del_comercio():
    clave = "5493436123456"
    identificador_a = routes._identificador_cliente(COMERCIO_A, clave)
    identificador_b = routes._identificador_cliente(COMERCIO_B, clave)
    assert len(identificador_a) == 64
    assert clave not in identificador_a
    assert identificador_a != identificador_b


class ConsultaClientesFalsa:
    def __init__(self):
        self.filtros = []
        self.limite = None
    def select(self, campos):
        assert "whatsapp_normalizado" in campos
        return self
    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self
    def order(self, campo, desc=False):
        assert campo == "created_at" and desc is True
        return self
    def limit(self, valor):
        self.limite = valor
        return self
    def execute(self):
        return SimpleNamespace(data=[])


class DbClientesFalsa:
    def __init__(self):
        self.consulta = ConsultaClientesFalsa()
    def table(self, tabla):
        assert tabla == "pedidos"
        return self.consulta


def test_consulta_clientes_filtra_comercio_y_tiene_limite():
    db = DbClientesFalsa()
    routes._consulta_pedidos_clientes(COMERCIO_A, db)
    assert db.consulta.filtros == [("comercio_id", COMERCIO_A)]
    assert db.consulta.limite == routes.CLIENTES_MAX_PEDIDOS


def ejecutar_lista(monkeypatch, query="", modulo=True, comercio_id=COMERCIO_A):
    capturado = {}
    consultados = []
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": comercio_id})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: modulo)
    monkeypatch.setattr(routes, "_consulta_pedidos_clientes", lambda cid: consultados.append(cid) or pedidos_cliente())
    monkeypatch.setattr(routes, "_consultar_fidelizacion", lambda *_args: {
        "activa": False, "meta_sellos": None, "premio": None, "clientes": []
    })
    monkeypatch.setattr(routes, "render_template", lambda plantilla, **datos: capturado.update(plantilla=plantilla, **datos) or "ok")
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/clientes?" + query):
        respuesta = routes.clientes_pedidos()
    return respuesta, capturado, consultados


def test_comercio_sin_modulo_no_abre_clientes(monkeypatch):
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": COMERCIO_A})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: False)
    monkeypatch.setattr(routes, "_consulta_pedidos_clientes", lambda *_args: pytest.fail("No debe consultar"))
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/clientes"), pytest.raises(Exception) as error:
        routes.clientes_pedidos()
    assert getattr(error.value, "code", None) == 403


@pytest.mark.parametrize("query", ["q=juan", "q=6123456"])
def test_busqueda_clientes_por_nombre_y_whatsapp(monkeypatch, query):
    _respuesta, datos, consultados = ejecutar_lista(monkeypatch, query)
    assert len(datos["clientes"]) == 1
    assert consultados == [COMERCIO_A]


def test_busqueda_sin_coincidencias(monkeypatch):
    _respuesta, datos, _consultados = ejecutar_lista(monkeypatch, "q=ana")
    assert datos["clientes"] == []


def test_modo_gestor_consulta_solo_comercio_seleccionado(monkeypatch):
    _respuesta, _datos, consultados = ejecutar_lista(monkeypatch, comercio_id=COMERCIO_B)
    assert consultados == [COMERCIO_B]


def test_ficha_no_permite_usar_identificador_de_otro_comercio(monkeypatch):
    identificador_a = routes._identificador_cliente(COMERCIO_A, "5493436123456")
    pedidos_b = [dict(pedidos_cliente()[0], whatsapp_normalizado="5493439990000")]
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": COMERCIO_B})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(routes, "_consulta_pedidos_clientes", lambda cid: pedidos_b if cid == COMERCIO_B else pytest.fail("Comercio incorrecto"))
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/clientes/" + identificador_a), pytest.raises(Exception) as error:
        routes.cliente_pedidos(identificador_a)
    assert getattr(error.value, "code", None) == 404


def test_ficha_incluye_cancelado_en_historial(monkeypatch):
    identificador = routes._identificador_cliente(COMERCIO_A, "5493436123456")
    capturado = {}
    monkeypatch.setattr(routes, "_comercio_panel", lambda: {"id": COMERCIO_A})
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(routes, "_consulta_pedidos_clientes", lambda cid: pedidos_cliente() if cid == COMERCIO_A else [])
    monkeypatch.setattr(routes, "_consultar_fidelizacion", lambda *_args: {
        "activa": False, "meta_sellos": None, "premio": None, "clientes": []
    })
    monkeypatch.setattr(routes, "render_template", lambda plantilla, **datos: capturado.update(plantilla=plantilla, **datos) or "ok")
    app = Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/panel/pedidos/clientes/" + identificador):
        respuesta = routes.cliente_pedidos(identificador)
    assert respuesta == "ok"
    assert [p["estado"] for p in capturado["cliente"]["pedidos"]] == ["cancelado", "entregado", "entregado"]
    assert capturado["cliente"]["pedidos_compra"] == 2


def test_navegacion_general_incluye_clientes():
    for ruta in ("templates/pedidos_comercio/panel.html", "templates/pedidos_comercio/historial.html"):
        assert "pedidos_comercio.clientes_pedidos" in open(ruta, encoding="utf-8").read()
