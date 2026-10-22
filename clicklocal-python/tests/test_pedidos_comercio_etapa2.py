from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from pedidos_comercio.routes import ESTADOS, TRANSICIONES
from pedidos_comercio import routes as routes_module


COMERCIO_ID = "11111111-1111-4111-8111-111111111111"


class ConsultaPedidosFalsa:
    def __init__(self, db):
        self.db = db
        self.filtros = []
        self.es_update = False

    def select(self, *_args):
        return self

    def update(self, _cambio):
        self.es_update = True
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

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        self.db.consultas.append(self)
        if self.es_update:
            return SimpleNamespace(data=[{"id": "pedido-1", "estado": "aceptado"}])
        return SimpleNamespace(data=[{
            "id": "pedido-1",
            "estado": "nuevo",
            "created_at": "2026-10-03T12:00:00+00:00",
        }])


class SupabasePedidosFalso:
    def __init__(self):
        self.consultas = []

    def table(self, tabla):
        assert tabla == "pedidos"
        return ConsultaPedidosFalsa(self)


def preparar_endpoint(monkeypatch, modulo_activo):
    db = SupabasePedidosFalso()
    monkeypatch.setattr(routes_module, "supabase_admin", db)
    monkeypatch.setattr(routes_module, "_comercio_panel", lambda: {"id": COMERCIO_ID})
    monkeypatch.setattr(routes_module, "modulo_activo", lambda *_args: modulo_activo)
    return Flask(__name__), db


def test_vocabulario_y_transiciones_de_estados():
    assert ESTADOS == ("nuevo", "aceptado", "preparando", "listo", "entregado", "cancelado")
    assert TRANSICIONES["nuevo"] == {"aceptado", "cancelado"}
    assert "entregado" not in TRANSICIONES["nuevo"]
    assert TRANSICIONES["entregado"] == set()


def test_polling_es_liviano_y_alerta_requiere_activacion():
    panel = Path("static/pedidos/panel.js").read_text()
    alerta = Path("static/pedidos/alertas.js").read_text()
    assert "15000" in panel
    assert "data-activar-sonido" in alerta
    assert "addEventListener(\"click\"" in alerta


def test_alerta_tiene_patron_volumen_y_repeticion_controlada():
    panel = Path("static/pedidos/panel.js").read_text()
    alerta = Path("static/pedidos/alertas.js").read_text()
    template = Path("templates/pedidos_comercio/panel.html").read_text()

    assert "PATRON_ALERTA" in alerta
    patron_general = alerta.split(
        "const PATRON_ALERTA = [", 1
    )[1].split("];", 1)[0]
    patron_base = alerta.split(
        "const PATRON_ALERTA_BASE = [", 1
    )[1].split("];", 1)[0]
    assert patron_general.count("frecuencia:") == 4
    assert patron_base.count("frecuencia:") == 7
    assert "GANANCIA_MAXIMA = 0.7" in alerta
    assert "INTERVALO_REPETICION = 45000" in alerta
    assert "temporizadorRepeticion !== null" in alerta
    assert "clearTimeout(temporizadorRepeticion)" in alerta
    assert "nuevos === 0" in alerta
    assert "document.hidden" in alerta
    assert "localStorage.setItem(CLAVE_VOLUMEN" in alerta
    assert 'type="range"' in template
    assert "data-volumen-alerta" in template
    assert "Paga con" in template and "Cambio" in template
    assert "pedidos_comercio.imprimir_pedido" in template
    assert "setTimeout(function(){window.location.reload();},700)" not in panel
    assert "data-aviso-pedido-nuevo" in panel
    assert "actualizarPendientes" in panel


def test_migracion_persiste_snapshots_y_no_productos_gastronomicos():
    sql = Path("sql/carrito_pedidos_etapa2_20261002.sql").read_text()
    assert "nombre_snapshot" in sql
    assert "precio_unitario_snapshot" in sql
    assert "public.publicaciones" in sql
    assert "gastronomia_productos" not in sql
    assert "crear_pedido_clicklocal" in sql


def test_migracion_es_transaccional_e_idempotente_por_checkout():
    sql = Path("sql/carrito_pedidos_etapa2_20261002.sql").read_text().lower()
    assert sql.strip().startswith("-- clicklocal")
    assert "begin;" in sql
    assert sql.strip().endswith("commit;")
    assert "idempotency_key uuid not null" in sql
    assert "unique (comercio_id, idempotency_key)" in sql
    assert "idempotency_fingerprint" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "if found then" in sql
    assert "is distinct from v_idempotency_fingerprint" in sql


def test_rpc_rechaza_items_nulos_no_array_o_vacios():
    sql = Path("sql/carrito_pedidos_etapa2_20261002.sql").read_text().lower()
    assert "p_items is null" in sql
    assert "jsonb_typeof(p_items) <> 'array'" in sql
    assert "jsonb_array_length(p_items) = 0" in sql
    assert "debe contener al menos un item" in sql


def test_constraints_basicos_de_pedidos_e_items():
    sql = Path("sql/carrito_pedidos_etapa2_20261002.sql").read_text().lower()
    assert "check (total = subtotal + costo_envio)" in sql
    assert "tipo_entrega <> 'retiro' or costo_envio = 0" in sql
    assert "forma_pago <> 'transferencia' or paga_con is null" in sql
    assert "paga_con is null or paga_con >= 0" in sql
    assert "distancia_delivery_m is null or distancia_delivery_m >= 0" in sql
    assert "tipo_entrega <> 'retiro' or distancia_delivery_m is null" in sql
    assert "subtotal = precio_unitario_snapshot * cantidad" in sql


def test_constraints_basicos_de_configuracion():
    sql = Path("sql/carrito_pedidos_etapa2_20261002.sql").read_text().lower()
    assert "check (acepta_retiro or acepta_delivery)" in sql
    assert "check (acepta_efectivo or acepta_transferencia)" in sql
    assert "delivery_origen_latitud between -90 and 90" in sql
    assert "delivery_origen_longitud between -180 and 180" in sql
    assert "acepta_delivery and delivery_modo = 'distancia'" in sql
    assert "delivery_origen_direccion" in sql
    assert "delivery_ciudad" in sql
    assert "jsonb_array_length(delivery_franjas) > 0" in sql


def test_rls_y_revocaciones_explicitas():
    sql = Path("sql/carrito_pedidos_etapa2_20261002.sql").read_text().lower()
    tablas = ("pedidos_configuracion", "pedido_contadores", "pedidos", "pedido_items")
    for tabla in tablas:
        assert f"alter table public.{tabla} enable row level security" in sql
        assert f"revoke all privileges on table public.{tabla} from public, anon, authenticated" in sql
    assert "revoke all privileges on function public.asignar_numero_pedido_clicklocal() from public" in sql
    assert "grant execute on function public.crear_pedido_clicklocal(jsonb, jsonb) to service_role" in sql
    for tabla in tablas:
        assert f"revoke all privileges on table public.{tabla} from service_role" in sql
    assert "grant select, insert, update on table public.pedidos_configuracion to service_role" in sql
    assert "grant select, update on table public.pedidos to service_role" in sql
    assert "grant select on table public.pedido_items to service_role" in sql
    assert "grant select on table public.pedido_contadores to service_role" not in sql
    assert "grant select, insert, update, delete" not in sql


def test_frontend_conserva_key_hasta_confirmacion_exitosa():
    javascript = Path("static/carrito/carrito.js").read_text()
    assert '"clicklocal_carrito_intento_" + config.comercioId' in javascript
    assert "sessionStorage.getItem(claveIntento)" in javascript
    assert "crypto.randomUUID()" in javascript
    assert "idempotency_key: obtenerIdempotencyKey()" in javascript
    assert "limpiarIdempotencyKey();" in javascript


def test_polling_sin_modulo_devuelve_403_y_no_consulta(monkeypatch):
    app, db = preparar_endpoint(monkeypatch, modulo_activo=False)
    with app.test_request_context("/panel/pedidos/actualizacion"):
        respuesta, estado = routes_module.actualizacion_pedidos()

    assert estado == 403
    assert respuesta.get_json() == {
        "ok": False,
        "error": "Este comercio no tiene el carrito habilitado.",
    }
    assert db.consultas == []


def test_polling_con_modulo_funciona_y_filtra_comercio(monkeypatch):
    app, db = preparar_endpoint(monkeypatch, modulo_activo=True)
    with app.test_request_context("/panel/pedidos/actualizacion"):
        respuesta = routes_module.actualizacion_pedidos()

    assert respuesta.get_json()["ok"] is True
    assert db.consultas[0].filtros[0] == ("comercio_id", COMERCIO_ID)
    assert db.consultas[0].filtros[1][0:2] == ("created_at", ">=")
    assert db.consultas[0].filtros[2][0:2] == ("created_at", "<")


def test_cambio_estado_sin_modulo_devuelve_403_y_no_consulta(monkeypatch):
    app, db = preparar_endpoint(monkeypatch, modulo_activo=False)
    with app.test_request_context(
        "/panel/pedidos/pedido-1/estado", method="POST", json={"estado": "aceptado"}
    ):
        respuesta, estado = routes_module.cambiar_estado("pedido-1")

    assert estado == 403
    assert respuesta.get_json() == {
        "ok": False,
        "error": "Este comercio no tiene el carrito habilitado.",
    }
    assert db.consultas == []


def test_cambio_estado_con_modulo_funciona_y_filtra_comercio(monkeypatch):
    app, db = preparar_endpoint(monkeypatch, modulo_activo=True)
    with app.test_request_context(
        "/panel/pedidos/pedido-1/estado", method="POST", json={"estado": "aceptado"}
    ):
        respuesta = routes_module.cambiar_estado("pedido-1")

    assert respuesta.get_json() == {"ok": True, "estado": "aceptado"}
    assert ("comercio_id", COMERCIO_ID) in db.consultas[0].filtros
    assert ("comercio_id", COMERCIO_ID) in db.consultas[1].filtros


@pytest.mark.parametrize(
    ("periodo", "inicio", "fin"),
    [
        ("hoy", "2026-10-06T03:00:00+00:00", "2026-10-07T03:00:00+00:00"),
        ("ayer", "2026-10-05T03:00:00+00:00", "2026-10-06T03:00:00+00:00"),
        ("7d", "2026-09-30T03:00:00+00:00", "2026-10-07T03:00:00+00:00"),
        ("30d", "2026-09-07T03:00:00+00:00", "2026-10-07T03:00:00+00:00"),
    ],
)
def test_periodos_respetan_dia_local_argentino(periodo, inicio, fin):
    ahora = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
    desde, hasta, normalizado = routes_module._limites_periodo(periodo, ahora)
    assert normalizado == periodo
    assert desde.isoformat() == inicio
    assert hasta.isoformat() == fin


def test_consulta_periodo_filtra_comercio_fecha_y_limite(monkeypatch):
    _app, db = preparar_endpoint(monkeypatch, modulo_activo=True)
    desde, hasta, _ = routes_module._limites_periodo(
        "hoy", datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    )
    routes_module._consulta_pedidos_periodo(COMERCIO_ID, desde, hasta, 50, db)
    consulta = db.consultas[0]
    assert consulta.filtros[0] == ("comercio_id", COMERCIO_ID)
    assert consulta.filtros[1] == ("created_at", ">=", desde.isoformat())
    assert consulta.filtros[2] == ("created_at", "<", hasta.isoformat())


def _ejecutar_historial(monkeypatch, query="", modulo=True, comercio_id=COMERCIO_ID):
    pedidos = [
        {"id": "a", "numero": 152, "nombre_cliente": "Juan Pérez", "whatsapp_cliente": "343 555-1234", "whatsapp_normalizado": "5493435551234"},
        {"id": "b", "numero": 153, "nombre_cliente": "Ana Gómez", "whatsapp_cliente": "343 999-0000", "whatsapp_normalizado": "5493439990000"},
    ]
    capturado = {}
    monkeypatch.setattr(routes_module, "_comercio_panel", lambda: {"id": comercio_id})
    monkeypatch.setattr(routes_module, "modulo_activo", lambda *_args: modulo)
    monkeypatch.setattr(routes_module, "_consulta_pedidos_periodo", lambda cid, *_args: [dict(p) for p in pedidos] if cid == comercio_id else [])
    monkeypatch.setattr(routes_module, "_adjuntar_resumen_items", lambda filas: filas)
    monkeypatch.setattr(routes_module, "render_template", lambda plantilla, **datos: capturado.update(plantilla=plantilla, **datos) or "ok")
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/historial?" + query):
        respuesta = routes_module.historial_pedidos()
    return respuesta, capturado


def test_historial_sin_modulo_no_consulta(monkeypatch):
    monkeypatch.setattr(routes_module, "_comercio_panel", lambda: {"id": COMERCIO_ID})
    monkeypatch.setattr(routes_module, "modulo_activo", lambda *_args: False)
    monkeypatch.setattr(routes_module, "_consulta_pedidos_periodo", lambda *_args: pytest.fail("No debe consultar"))
    app = Flask(__name__)
    with app.test_request_context("/panel/pedidos/historial"), pytest.raises(Exception) as error:
        routes_module.historial_pedidos()
    assert getattr(error.value, "code", None) == 403


@pytest.mark.parametrize(("query", "pedido_id"), [("q=152", "a"), ("q=juan", "a"), ("q=5551234", "a")])
def test_historial_busca_numero_nombre_y_whatsapp(monkeypatch, query, pedido_id):
    _respuesta, datos = _ejecutar_historial(monkeypatch, query)
    assert [pedido["id"] for pedido in datos["pedidos"]] == [pedido_id]


def test_historial_modo_gestor_usa_solo_comercio_administrado(monkeypatch):
    comercio_gestor = "22222222-2222-4222-8222-222222222222"
    _respuesta, datos = _ejecutar_historial(monkeypatch, "periodo=30d", comercio_id=comercio_gestor)
    assert len(datos["pedidos"]) == 2
    assert datos["periodo"] == "30d"


def test_muchos_items_se_resumen_sin_expandirse():
    class Items:
        def table(self, tabla):
            assert tabla == "pedido_items"
            return self
        def select(self, *_args): return self
        def in_(self, campo, valores):
            assert campo == "pedido_id" and valores == ["grande"]
            return self
        def execute(self):
            return SimpleNamespace(data=[{"pedido_id": "grande", "cantidad": 2}] * 20)
    pedido = {"id": "grande", "created_at": "2026-10-06T12:00:00+00:00", "forma_pago": "transferencia"}
    routes_module._adjuntar_resumen_items([pedido], db=Items())
    assert pedido["cantidad_items"] == 40
    assert "items" not in pedido


def test_operativa_conserva_acciones_y_navegacion():
    html = Path("templates/pedidos_comercio/panel.html").read_text()
    for estado in ("aceptado", "preparando", "listo", "entregado", "cancelado"):
        assert f'data-estado="{estado}"' in html
    assert "pedidos_comercio.historial_pedidos" in html
