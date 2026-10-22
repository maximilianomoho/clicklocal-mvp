from types import SimpleNamespace

import app as app_module
import pytest

from pedidos_comercio import routes


COMERCIO_ID = "11111111-1111-4111-8111-111111111111"
OTRO_COMERCIO_ID = "22222222-2222-4222-8222-222222222222"
PUBLICACION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
IDEMPOTENCY_KEY = "33333333-3333-4333-8333-333333333333"


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.ids = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def in_(self, _campo, valores):
        self.ids = list(valores)
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, datos):
        self.db.actualizaciones.append((self.tabla, datos))
        return self

    def execute(self):
        self.db.consultas.append(self)
        if self.tabla == "comercios":
            return SimpleNamespace(data=[self.db.comercio])
        if self.tabla == "publicaciones":
            return SimpleNamespace(data=self.db.publicaciones)
        if self.tabla == "pedidos_configuracion":
            return SimpleNamespace(data=[self.db.configuracion])
        if self.tabla == "pedidos":
            return SimpleNamespace(data=[{"id": "pedido-1"}])
        raise AssertionError("Tabla inesperada: " + self.tabla)


class RpcFalsa:
    def __init__(self, db, parametros):
        self.db = db
        self.parametros = parametros

    def execute(self):
        self.db.rpc_parametros = self.parametros
        pedido = self.parametros["p_pedido"]
        clave = (pedido["comercio_id"], pedido["idempotency_key"])
        existente = self.db.pedidos_por_key.get(clave)
        if existente:
            if existente["fingerprint"] != pedido["idempotency_fingerprint"]:
                raise RuntimeError("La clave de idempotencia ya fue usada")
            return SimpleNamespace(data=existente["pedido"])
        creado = {"id": f"pedido-{self.db.rpc_creaciones + 1}", "numero": 123 + self.db.rpc_creaciones}
        self.db.rpc_creaciones += 1
        self.db.pedidos_por_key[clave] = {
            "fingerprint": pedido["idempotency_fingerprint"],
            "pedido": creado,
        }
        return SimpleNamespace(data=creado)


class SupabaseFalso:
    def __init__(self, publicaciones):
        self.comercio = {
            "id": COMERCIO_ID,
            "nombre_negocio": "Almacén de prueba",
            "whatsapp": "3434000000",
            "activo": True,
        }
        self.publicaciones = publicaciones
        self.consultas = []
        self.actualizaciones = []
        self.rpc_parametros = None
        self.rpc_creaciones = 0
        self.pedidos_por_key = {}
        self.configuracion = {
            "acepta_retiro": True,
            "acepta_delivery": False,
            "acepta_efectivo": True,
            "acepta_transferencia": True,
            "pedido_minimo": None,
            "delivery_modo": "fijo",
            "delivery_costo_fijo": 0,
        }

    def table(self, tabla):
        return ConsultaFalsa(self, tabla)

    def rpc(self, nombre, parametros):
        assert nombre == "crear_pedido_clicklocal"
        return RpcFalsa(self, parametros)


class ConsultaConfiguracionFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.datos_upsert = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lt(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def upsert(self, datos, on_conflict=None):
        assert self.tabla == "pedidos_configuracion"
        assert on_conflict == "comercio_id"
        self.datos_upsert = dict(datos)
        return self

    def execute(self):
        if self.datos_upsert is not None:
            self.db.configuracion = self.datos_upsert
            return SimpleNamespace(data=[self.db.configuracion])
        if self.tabla == "pedidos_configuracion":
            return SimpleNamespace(data=[self.db.configuracion])
        if self.tabla == "pedidos":
            return SimpleNamespace(data=[])
        raise AssertionError("Tabla inesperada: " + self.tabla)


class SupabaseConfiguracionFalso:
    def __init__(self):
        self.configuracion = {}

    def table(self, tabla):
        return ConsultaConfiguracionFalsa(self, tabla)


def publicacion(**cambios):
    datos = {
        "id": PUBLICACION_ID,
        "comercio_id": COMERCIO_ID,
        "nombre": "Coca-Cola 2,25 L",
        "precio": 3500,
        "activa": True,
        "eliminada": False,
        "imagenes": ["https://example.test/coca-1.jpg"],
        "imagen_principal": "https://example.test/coca-portada.jpg",
        "imagen_url": "https://example.test/coca-portada.jpg",
    }
    datos.update(cambios)
    return datos


def confirmar(monkeypatch, publicaciones, payload=None, modulo=True, configuracion=None, db=None):
    db = db or SupabaseFalso(publicaciones)
    if configuracion:
        db.configuracion.update(configuracion)
    monkeypatch.setattr(app_module, "supabase_admin", db)
    monkeypatch.setattr(app_module, "modulo_activo", lambda *_args: modulo)
    cuerpo = dict(payload or {
        "nombre": "Ana",
        "apellido": "Pérez",
        "whatsapp": "3436123456",
        "entrega": "retiro",
        "pago": "efectivo",
        "items": [{"id": PUBLICACION_ID, "cantidad": 2, "precio": 1}],
    })
    cuerpo.setdefault("apellido", "Pérez")
    cuerpo.setdefault("idempotency_key", IDEMPOTENCY_KEY)
    respuesta = app_module.app.test_client().post(
        f"/comercio/{COMERCIO_ID}/carrito/confirmar",
        json=cuerpo,
    )
    return respuesta, db


def test_backend_ignora_precio_cliente_y_calcula_desde_publicacion(monkeypatch):
    respuesta, db = confirmar(monkeypatch, [publicacion()])
    datos = respuesta.get_json()

    assert respuesta.status_code == 200
    assert datos["total"] == 7000.0
    assert datos["whatsapp_url"].startswith("https://wa.me/5493434000000")
    assert {consulta.tabla for consulta in db.consultas} == {
        "comercios",
        "publicaciones",
        "pedidos_configuracion",
        "pedidos",
    }
    assert db.rpc_parametros["p_pedido"]["total"] == 7000.0
    assert db.rpc_parametros["p_items"][0]["precio_unitario_snapshot"] == 3500.0
    assert db.rpc_parametros["p_pedido"]["idempotency_key"] == IDEMPOTENCY_KEY
    assert len(db.rpc_parametros["p_pedido"]["idempotency_fingerprint"]) == 64


def test_nombre_y_apellido_se_normalizan_y_concatenan(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        payload={"nombre": "  Ana   María ", "apellido": " Pérez   Gómez ",
            "whatsapp": "3436123456", "entrega": "retiro", "pago": "efectivo",
            "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )

    assert respuesta.status_code == 200
    assert db.rpc_parametros["p_pedido"]["nombre_cliente"] == "Ana María Pérez Gómez"


def test_rechaza_nombre_ausente(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        payload={"nombre": "", "apellido": "Pérez", "whatsapp": "3436123456",
            "entrega": "retiro", "pago": "efectivo",
            "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "Ingresá tu nombre."
    assert db.rpc_parametros is None


def test_rechaza_apellido_ausente(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        payload={"nombre": "Ana", "apellido": "", "whatsapp": "3436123456",
            "entrega": "retiro", "pago": "efectivo",
            "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "Ingresá tu apellido."
    assert db.rpc_parametros is None


def test_misma_key_y_mismo_pedido_reutiliza_resultado(monkeypatch):
    db = SupabaseFalso([publicacion()])
    primera, _ = confirmar(monkeypatch, db.publicaciones, db=db)
    segunda, _ = confirmar(monkeypatch, db.publicaciones, db=db)

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert primera.get_json()["pedido_id"] == segunda.get_json()["pedido_id"]
    assert db.rpc_creaciones == 1


def test_misma_key_y_fingerprint_diferente_se_rechaza(monkeypatch):
    db = SupabaseFalso([publicacion()])
    primera, _ = confirmar(monkeypatch, db.publicaciones, db=db)
    payload = {"nombre": "Ana", "whatsapp": "3436123456", "entrega": "retiro",
        "pago": "efectivo", "idempotency_key": IDEMPOTENCY_KEY,
        "items": [{"id": PUBLICACION_ID, "cantidad": 1}]}
    segunda, _ = confirmar(monkeypatch, db.publicaciones, payload=payload, db=db)

    assert primera.status_code == 200
    assert segunda.status_code == 500
    assert db.rpc_creaciones == 1


def test_key_nueva_crea_otro_pedido(monkeypatch):
    db = SupabaseFalso([publicacion()])
    primera, _ = confirmar(monkeypatch, db.publicaciones, db=db)
    payload = {"nombre": "Ana", "whatsapp": "3436123456", "entrega": "retiro",
        "pago": "efectivo", "idempotency_key": "44444444-4444-4444-8444-444444444444",
        "items": [{"id": PUBLICACION_ID, "cantidad": 2}]}
    segunda, _ = confirmar(monkeypatch, db.publicaciones, payload=payload, db=db)

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert primera.get_json()["pedido_id"] != segunda.get_json()["pedido_id"]
    assert db.rpc_creaciones == 2


def test_rechaza_key_ausente_o_invalida(monkeypatch):
    payload = {"nombre": "Ana", "whatsapp": "3436123456", "entrega": "retiro",
        "pago": "efectivo", "idempotency_key": "no-es-uuid",
        "items": [{"id": PUBLICACION_ID, "cantidad": 1}]}
    respuesta, db = confirmar(monkeypatch, [publicacion()], payload)

    assert respuesta.status_code == 400
    assert db.rpc_parametros is None


def test_rechaza_publicacion_de_otro_comercio(monkeypatch):
    respuesta, _db = confirmar(
        monkeypatch,
        [publicacion(comercio_id=OTRO_COMERCIO_ID)],
    )
    assert respuesta.status_code == 400
    assert "no pertenece" in respuesta.get_json()["error"]


def test_rechaza_publicacion_inactiva_o_eliminada(monkeypatch):
    respuesta, _db = confirmar(monkeypatch, [publicacion(activa=False)])
    assert respuesta.status_code == 400
    assert "no está disponible" in respuesta.get_json()["error"]

    respuesta, _db = confirmar(monkeypatch, [publicacion(eliminada=True)])
    assert respuesta.status_code == 400
    assert "no está disponible" in respuesta.get_json()["error"]


def test_rechaza_precio_invalido(monkeypatch):
    respuesta, _db = confirmar(monkeypatch, [publicacion(precio=None)])
    assert respuesta.status_code == 400
    assert "precio válido" in respuesta.get_json()["error"]


def test_rechaza_cantidad_cero(monkeypatch):
    payload = {
        "nombre": "Ana",
        "whatsapp": "3436123456",
        "entrega": "retiro",
        "pago": "transferencia",
        "items": [{"id": PUBLICACION_ID, "cantidad": 0}],
    }
    respuesta, _db = confirmar(monkeypatch, [publicacion()], payload)
    assert respuesta.status_code == 400
    assert "cantidad" in respuesta.get_json()["error"].lower()


def test_comercio_sin_modulo_no_puede_confirmar(monkeypatch):
    respuesta, db = confirmar(monkeypatch, [publicacion()], modulo=False)
    assert respuesta.status_code == 403
    assert db.consultas == []


def test_delivery_permanece_bloqueado_sin_configuracion(monkeypatch):
    payload = {
        "nombre": "Ana",
        "whatsapp": "3436123456",
        "entrega": "delivery",
        "pago": "efectivo",
        "items": [{"id": PUBLICACION_ID, "cantidad": 1}],
    }
    respuesta, _db = confirmar(monkeypatch, [publicacion()], payload)
    assert respuesta.status_code == 400
    assert "no ofrece delivery" in respuesta.get_json()["error"]


def test_pedido_minimo_se_valida_en_backend(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        configuracion={"pedido_minimo": 5000, "acepta_delivery": True,
            "delivery_modo": "fijo", "delivery_costo_fijo": 0},
        payload={"nombre": "Ana", "whatsapp": "3436123456", "entrega": "delivery",
            "direccion": "San Martín", "numero": "123", "pago": "efectivo",
            "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )
    assert respuesta.status_code == 400
    assert "pedido mínimo" in respuesta.get_json()["error"]
    assert db.rpc_parametros is None


def test_pedido_minimo_no_se_aplica_a_retiro(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        configuracion={"pedido_minimo": 5000},
        payload={"nombre": "Ana", "whatsapp": "3436123456", "entrega": "retiro",
            "pago": "efectivo", "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )

    assert respuesta.status_code == 200
    assert db.rpc_parametros["p_pedido"]["subtotal"] == 3500.0


def test_medio_de_pago_deshabilitado_se_rechaza(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        configuracion={"acepta_transferencia": False},
        payload={"nombre": "Ana", "whatsapp": "3436123456", "entrega": "retiro", "pago": "transferencia", "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )
    assert respuesta.status_code == 400
    assert "no acepta transferencia" in respuesta.get_json()["error"]
    assert db.rpc_parametros is None


def test_delivery_sin_direccion_se_rechaza(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        configuracion={"acepta_delivery": True, "delivery_modo": "fijo", "delivery_costo_fijo": 1500},
        payload={"nombre": "Ana", "whatsapp": "3436123456", "entrega": "delivery", "pago": "efectivo", "items": [{"id": PUBLICACION_ID, "cantidad": 1}]},
    )
    assert respuesta.status_code == 400
    assert "dirección" in respuesta.get_json()["error"]
    assert db.rpc_parametros is None


def test_delivery_fijo_lo_calcula_el_backend_y_persiste_antes_de_whatsapp(monkeypatch):
    respuesta, db = confirmar(
        monkeypatch,
        [publicacion()],
        configuracion={"acepta_delivery": True, "delivery_modo": "fijo", "delivery_costo_fijo": 1500},
        payload={"nombre": "Ana", "whatsapp": "3436123456", "entrega": "delivery", "direccion": "San Martín", "numero": "123", "pago": "efectivo", "items": [{"id": PUBLICACION_ID, "cantidad": 1}], "costo_envio": 1},
    )
    assert respuesta.status_code == 200
    assert respuesta.get_json()["total"] == 5000.0
    assert db.rpc_parametros["p_pedido"]["costo_envio"] == 1500.0


def test_templates_del_carrito_compilan():
    app_module.app.jinja_env.get_template("perfil.html")
    app_module.app.jinja_env.get_template("detalle.html")
    app_module.app.jinja_env.get_template("pedidos_comercio/imprimir.html")


@pytest.mark.parametrize(
    ("forma_pago", "paga_con", "total", "cambio"),
    [
        ("efectivo", 10400, 10400, 0.0),
        ("efectivo", 20000, 10400, 9600.0),
        ("efectivo", None, 10400, None),
        ("efectivo", 9000, 10400, None),
        ("transferencia", 20000, 10400, None),
    ],
)
def test_cambio_para_presentacion_es_defensivo(forma_pago, paga_con, total, cambio):
    pedido = {"forma_pago": forma_pago, "paga_con": paga_con, "total": total}
    assert routes._agregar_cambio_presentacion(pedido)["cambio"] == cambio


class ConsultaImpresionFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []

    def select(self, *_args):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        self.db.consultas.append((self.tabla, list(self.filtros)))
        if self.tabla == "pedidos":
            coincide = (
                ("id", self.db.pedido["id"]) in self.filtros
                and ("comercio_id", self.db.pedido["comercio_id"]) in self.filtros
            )
            return SimpleNamespace(data=[dict(self.db.pedido)] if coincide else [])
        if self.tabla == "pedido_items":
            assert ("pedido_id", self.db.pedido["id"]) in self.filtros
            return SimpleNamespace(data=[dict(item) for item in self.db.items])
        raise AssertionError("Tabla inesperada: " + self.tabla)


class SupabaseImpresionFalso:
    def __init__(self):
        self.consultas = []
        self.pedido = {
            "id": "pedido-impresion",
            "comercio_id": COMERCIO_ID,
            "numero": 2,
            "created_at": "2026-10-05T20:43:00+00:00",
            "nombre_cliente": "<script>alert(1)</script>",
            "whatsapp_cliente": "3434000000",
            "tipo_entrega": "delivery",
            "direccion": "Pellegrini 140",
            "forma_pago": "efectivo",
            "paga_con": 20000,
            "subtotal": 8900,
            "costo_envio": 1500,
            "total": 10400,
            "estado": "nuevo",
        }
        self.items = [{
            "pedido_id": "pedido-impresion",
            "cantidad": 1,
            "nombre_snapshot": "Vino Altura",
            "subtotal": 8900,
        }]

    def table(self, tabla):
        return ConsultaImpresionFalsa(self, tabla)


def preparar_impresion(monkeypatch, comercio_id=COMERCIO_ID, modulo=True):
    db = SupabaseImpresionFalso()
    monkeypatch.setattr(routes, "supabase_admin", db)
    monkeypatch.setattr(
        routes,
        "_comercio_panel",
        lambda: {"id": comercio_id, "nombre_negocio": "Almacén de prueba"},
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: modulo)
    return db


def test_comercio_propio_imprime_ticket_completo_y_escapado(monkeypatch):
    db = preparar_impresion(monkeypatch)
    respuesta = app_module.app.test_client().get(
        "/panel/pedidos/pedido-impresion/imprimir"
    )
    html = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Vino Altura" in html
    assert "Subtotal" in html and "Envío" in html and "TOTAL" in html
    assert "Paga con" in html and "$ 20.000" in html
    assert "Cambio" in html and "$ 9.600" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert db.consultas[0] == (
        "pedidos",
        [("id", "pedido-impresion"), ("comercio_id", COMERCIO_ID)],
    )


def test_pedido_ajeno_devuelve_404_sin_cargar_items(monkeypatch):
    db = preparar_impresion(monkeypatch, comercio_id=OTRO_COMERCIO_ID)
    respuesta = app_module.app.test_client().get(
        "/panel/pedidos/pedido-impresion/imprimir"
    )

    assert respuesta.status_code == 404
    assert [tabla for tabla, _filtros in db.consultas] == ["pedidos"]


def test_impresion_sin_modulo_devuelve_403_sin_consultar(monkeypatch):
    db = preparar_impresion(monkeypatch, modulo=False)
    respuesta = app_module.app.test_client().get(
        "/panel/pedidos/pedido-impresion/imprimir"
    )

    assert respuesta.status_code == 403
    assert db.consultas == []


def test_modo_gestor_mantiene_aislamiento_de_impresion(monkeypatch):
    db = preparar_impresion(monkeypatch, comercio_id=OTRO_COMERCIO_ID)
    with app_module.app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["admin_logueado"] = True
            sesion["gestor_comercio_id"] = OTRO_COMERCIO_ID
        respuesta = cliente.get("/panel/pedidos/pedido-impresion/imprimir")

    assert respuesta.status_code == 404
    assert ("comercio_id", OTRO_COMERCIO_ID) in db.consultas[0][1]


def test_transferencia_y_paga_con_nulo_no_muestran_detalle_en_ticket(monkeypatch):
    db = preparar_impresion(monkeypatch)
    db.pedido["forma_pago"] = "transferencia"
    db.pedido["paga_con"] = None
    respuesta = app_module.app.test_client().get(
        "/panel/pedidos/pedido-impresion/imprimir"
    )
    html = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Paga con" not in html
    assert "Cambio" not in html


def datos_configuracion_franjas(franjas):
    limites = [str(limite) for limite, _precio in franjas]
    precios = [str(precio) for _limite, precio in franjas]
    faltantes = 5 - len(franjas)
    return {
        "acepta_retiro": "1",
        "acepta_delivery": "1",
        "acepta_efectivo": "1",
        "acepta_transferencia": "1",
        "delivery_costo_fijo": "0",
        "delivery_origen_direccion": "San Martín 123",
        "delivery_ciudad": "Paraná",
        "franja_km": limites + ([""] * faltantes),
        "franja_precio": precios + ([""] * faltantes),
    }


def preparar_panel_configuracion(monkeypatch, configuracion=None):
    db = SupabaseConfiguracionFalso()
    db.configuracion = dict(configuracion or {})
    comercio = {
        "id": COMERCIO_ID,
        "nombre_negocio": "Almacén de prueba",
        "direccion": "San Martín 123",
        "ciudad": "Paraná",
        "plan": "gratis",
    }
    monkeypatch.setattr(routes, "supabase_admin", db)
    monkeypatch.setattr(routes, "_comercio_panel", lambda: comercio)
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(
        routes,
        "geocodificar_direccion_nominatim",
        lambda *_args: (-31.731234, -60.523456),
    )
    return db


def publicar_configuracion(monkeypatch, datos, configuracion=None):
    db = preparar_panel_configuracion(monkeypatch, configuracion)
    respuesta = app_module.app.test_client().post(
        "/panel/pedidos",
        data=datos,
        follow_redirects=True,
    )
    return respuesta, db


def guardar_configuracion_franjas(monkeypatch, franjas):
    return publicar_configuracion(
        monkeypatch,
        datos_configuracion_franjas(franjas),
    )


def test_configuracion_fija_sin_franjas_es_valida(monkeypatch):
    datos = datos_configuracion_franjas([])
    datos["usar_costo_fijo"] = "1"
    datos["delivery_costo_fijo"] = "1800"

    respuesta, db = publicar_configuracion(monkeypatch, datos)

    assert respuesta.status_code == 200
    assert "Configuración guardada." in respuesta.get_data(as_text=True)
    assert db.configuracion["delivery_modo"] == "fijo"
    assert db.configuracion["delivery_costo_fijo"] == 1800.0
    assert db.configuracion["delivery_franjas"] == []


def test_configuracion_fija_con_costo_cero_es_valida(monkeypatch):
    datos = datos_configuracion_franjas([])
    datos["usar_costo_fijo"] = "1"
    datos["delivery_costo_fijo"] = "0"

    _respuesta, db = publicar_configuracion(monkeypatch, datos)

    assert db.configuracion["delivery_modo"] == "fijo"
    assert db.configuracion["delivery_costo_fijo"] == 0.0


def test_configuracion_rechaza_checkbox_costo_fijo_manipulado(monkeypatch):
    datos = datos_configuracion_franjas([])
    datos["usar_costo_fijo"] = "alterado"

    respuesta, db = publicar_configuracion(monkeypatch, datos)
    html = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "La opción de costo fijo no es válida." in html
    assert db.configuracion == {}


def test_configuracion_guarda_una_franja_y_omite_las_vacias(monkeypatch):
    respuesta, db = guardar_configuracion_franjas(monkeypatch, [(1, 1500)])

    assert respuesta.status_code == 200
    assert db.configuracion["delivery_franjas"] == [
        {"hasta_km": 1.0, "precio": 1500.0}
    ]
    assert db.configuracion["delivery_modo"] == "distancia"


def test_configuracion_distancia_requiere_al_menos_una_franja(monkeypatch):
    respuesta, db = guardar_configuracion_franjas(monkeypatch, [])

    assert "Configurá entre 1 y 5 franjas" in respuesta.get_data(as_text=True)
    assert db.configuracion == {}


def test_configuracion_guarda_varias_franjas_y_omite_las_vacias(monkeypatch):
    franjas = [(1, 1500), (2, 2500), (3, 3500)]
    _respuesta, db = guardar_configuracion_franjas(monkeypatch, franjas)

    assert db.configuracion["delivery_franjas"] == [
        {"hasta_km": 1.0, "precio": 1500.0},
        {"hasta_km": 2.0, "precio": 2500.0},
        {"hasta_km": 3.0, "precio": 3500.0},
    ]


def test_configuracion_guarda_cinco_franjas_completas(monkeypatch):
    franjas = [(1, 1000), (2, 2000), (3, 3000), (4, 4000), (5, 5000)]
    _respuesta, db = guardar_configuracion_franjas(monkeypatch, franjas)

    assert len(db.configuracion["delivery_franjas"]) == 5
    assert db.configuracion["delivery_franjas"][-1] == {
        "hasta_km": 5.0,
        "precio": 5000.0,
    }


def test_fila_de_franja_completamente_vacia_se_ignora():
    limites, precios = routes._filtrar_franjas_formulario(
        ["1", "", "3", "", ""],
        ["1500", "", "3500", "", ""],
    )

    assert limites == ["1", "3"]
    assert precios == ["1500", "3500"]


@pytest.mark.parametrize(
    ("limite", "precio"),
    [("2", ""), ("", "2500")],
    ids=["km-sin-precio", "precio-sin-km"],
)
def test_fila_de_franja_parcial_se_rechaza(limite, precio):
    with pytest.raises(
        ValueError,
        match="Completá el límite y el precio de cada franja",
    ):
        routes._filtrar_franjas_formulario([limite], [precio])


def test_get_posterior_renderiza_las_franjas_guardadas(monkeypatch):
    respuesta, _db = guardar_configuracion_franjas(
        monkeypatch,
        [(1, 1500), (2, 2500), (3, 3500)],
    )
    html = respuesta.get_data(as_text=True)

    assert 'name="usar_costo_fijo" value="1" checked' not in html
    assert 'value="1.0"' in html
    assert 'value="1500.0"' in html
    assert 'value="2.0"' in html
    assert 'value="2500.0"' in html
    assert 'value="3.0"' in html
    assert 'value="3500.0"' in html


def test_get_con_config_fija_mantiene_fijo_y_un_solo_control(monkeypatch):
    datos = datos_configuracion_franjas([])
    datos["usar_costo_fijo"] = "1"
    respuesta, _db = publicar_configuracion(monkeypatch, datos)
    html = respuesta.get_data(as_text=True)

    assert 'name="delivery_modo"' not in html
    assert html.count('name="usar_costo_fijo"') == 1
    assert 'name="usar_costo_fijo" value="1" checked' in html


def test_delivery_desactivado_conserva_configuracion_y_no_geocodifica(monkeypatch):
    anterior = {
        "delivery_modo": "distancia",
        "delivery_costo_fijo": 1800,
        "delivery_franjas": [{"hasta_km": 3, "precio": 3500}],
    }
    datos = datos_configuracion_franjas([(1, 1000)])
    datos.pop("acepta_delivery")
    db = preparar_panel_configuracion(monkeypatch, anterior)
    monkeypatch.setattr(
        routes,
        "geocodificar_direccion_nominatim",
        lambda *_args: pytest.fail("No debe geocodificar"),
    )

    respuesta = app_module.app.test_client().post(
        "/panel/pedidos", data=datos, follow_redirects=True
    )

    assert respuesta.status_code == 200
    assert db.configuracion["acepta_delivery"] is False
    assert db.configuracion["delivery_costo_fijo"] == 1800.0
    assert db.configuracion["delivery_franjas"] == anterior["delivery_franjas"]


def test_modo_fijo_no_geocodifica_y_conserva_franjas(monkeypatch):
    anterior = {
        "delivery_modo": "distancia",
        "delivery_costo_fijo": 900,
        "delivery_franjas": [
            {"hasta_km": 1, "precio": 1500},
            {"hasta_km": 2, "precio": 2500},
        ],
    }
    datos = datos_configuracion_franjas([(1, 1500), (2, 2500)])
    datos["usar_costo_fijo"] = "1"
    datos["delivery_costo_fijo"] = "2000"
    db = preparar_panel_configuracion(monkeypatch, anterior)
    monkeypatch.setattr(
        routes,
        "geocodificar_direccion_nominatim",
        lambda *_args: pytest.fail("No debe geocodificar"),
    )

    respuesta = app_module.app.test_client().post(
        "/panel/pedidos", data=datos, follow_redirects=True
    )

    assert respuesta.status_code == 200
    assert db.configuracion["delivery_modo"] == "fijo"
    assert db.configuracion["delivery_costo_fijo"] == 2000.0
    assert db.configuracion["delivery_franjas"] == anterior["delivery_franjas"]


def test_modo_distancia_geocodifica_y_conserva_costo_fijo(monkeypatch):
    anterior = {"delivery_modo": "fijo", "delivery_costo_fijo": 1800}
    datos = datos_configuracion_franjas([(1, 1500), (2, 2500)])

    _respuesta, db = publicar_configuracion(monkeypatch, datos, anterior)

    assert db.configuracion["delivery_modo"] == "distancia"
    assert db.configuracion["delivery_costo_fijo"] == 1800.0
    assert db.configuracion["delivery_origen_latitud"] == -31.731234
    assert db.configuracion["delivery_origen_longitud"] == -60.523456
    assert len(db.configuracion["delivery_franjas"]) == 2


@pytest.mark.parametrize(
    ("campo", "mensaje"),
    [
        ("delivery_origen_direccion", "dirección de salida válida"),
        ("delivery_ciudad", "ciudad válida"),
    ],
)
def test_modo_distancia_requiere_origen_y_ciudad(monkeypatch, campo, mensaje):
    datos = datos_configuracion_franjas([(1, 1500)])
    datos[campo] = ""

    respuesta, db = publicar_configuracion(monkeypatch, datos)

    assert mensaje in respuesta.get_data(as_text=True)
    assert db.configuracion == {}


def test_cotizacion_por_distancia_valida_no_usa_costo_fijo_cero(monkeypatch):
    configuracion = {
        "acepta_delivery": True,
        "delivery_modo": "distancia",
        "delivery_costo_fijo": 0,
        "delivery_origen_direccion": "San Martín 123, Paraná",
        "delivery_ciudad": "Paraná",
        "delivery_origen_latitud": -31.731234,
        "delivery_origen_longitud": -60.523456,
        "delivery_franjas": [
            {"hasta_km": 1, "precio": 1500},
            {"hasta_km": 2, "precio": 2500},
            {"hasta_km": 3, "precio": 3500},
        ],
    }
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(routes, "_configuracion", lambda *_args: configuracion)
    monkeypatch.setattr(routes, "buscar_distancia_cache", lambda *_args: 800)

    respuesta = app_module.app.test_client().post(
        f"/comercio/{COMERCIO_ID}/carrito/cotizar-envio",
        json={
            "whatsapp": "3436123456",
            "direccion": "Perú",
            "numero": "50",
        },
    )

    assert respuesta.status_code == 200
    assert respuesta.get_json()["costo_envio"] == 1500.0
