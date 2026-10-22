import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from gastronomia.services.pedidos import PedidoError, buscar_pedido_idempotente


COMERCIO_A = "11111111-1111-1111-1111-111111111111"
COMERCIO_B = "22222222-2222-2222-2222-222222222222"
TOKEN = "token-secreto-de-prueba"
PUBLICACION_ID = "44444444-4444-4444-8444-444444444444"
CSRF = "csrf-preview-prueba"


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.operacion = "select"
        self.payload = None

    def select(self, *_args): return self
    def limit(self, *_args): return self
    def order(self, *_args, **_kwargs): return self
    def range(self, *_args): return self
    def in_(self, campo, valores):
        self.filtros.append((campo, "in", {str(v) for v in valores}))
        return self
    def eq(self, campo, valor):
        self.filtros.append((campo, "eq", valor))
        return self
    def update(self, payload):
        self.operacion = "update"
        self.payload = dict(payload)
        return self

    def execute(self):
        filas = [dict(fila) for fila in self.db.datos.get(self.tabla, [])]
        for campo, operador, valor in self.filtros:
            if operador == "eq":
                filas = [fila for fila in filas if fila.get(campo) == valor]
            else:
                filas = [fila for fila in filas if str(fila.get(campo)) in valor]
        if self.operacion == "update":
            for original in self.db.datos.get(self.tabla, []):
                if any(original.get(c) != v for c, op, v in self.filtros if op == "eq"):
                    continue
                original.update(self.payload)
            self.db.actualizaciones.append((self.tabla, self.payload))
        return SimpleNamespace(data=filas)


class SupabaseFalso:
    def __init__(self, *, activo=False, token=TOKEN, vence=None, comercio_id=COMERCIO_A):
        vence = vence or datetime.now(timezone.utc) + timedelta(days=1)
        self.actualizaciones = []
        self.datos = {
            "gastronomia_configuracion": [{
                "comercio_id": comercio_id,
                "activo": activo,
                "acepta_delivery": False,
                "acepta_retiro": True,
                "pedido_minimo": 0,
                "costo_envio": 0,
                "tiempo_estimado_min": 30,
                "preview_token_hash": hashlib.sha256(token.encode()).hexdigest() if token else None,
                "preview_token_created_at": datetime.now(timezone.utc).isoformat() if token else None,
                "preview_token_expires_at": vence.isoformat() if token else None,
            }],
            "comercios": [{
                "id": comercio_id,
                "nombre_negocio": "Granola",
                "categoria": "Gastronomía",
                "activo": True,
                "descripcion": "Cocina natural",
                "whatsapp": "3434000000",
            }],
            "gastronomia_productos": [{
                "id": "producto-1", "comercio_id": comercio_id,
                "nombre": "Bowl", "descripcion": "Con frutas", "precio": 5000,
                "precio_promocional": None, "imagen_url": "https://img.test/bowl.jpg",
                "disponible": True, "activo": True, "orden": 1,
            }],
            "gastronomia_grupos_opciones": [{
                "id": "grupo-1", "producto_id": "producto-1",
                "nombre": "Tamaño", "minimo": 0, "maximo": 1,
                "activo": True, "orden": 1,
            }],
            "gastronomia_opciones": [{
                "id": "opcion-1", "grupo_id": "grupo-1", "nombre": "Grande",
                "precio_extra": 1000, "disponible": True, "activo": True, "orden": 1,
            }],
            "publicaciones": [{
                "id": PUBLICACION_ID, "comercio_id": comercio_id,
                "nombre": "Publicación heredada Granola", "descripcion": "Bowl",
                "precio": 5000, "activa": True, "eliminada": False,
                "imagenes": [], "created_at": "2026-10-01T12:00:00+00:00",
                "orden_grilla_at": "2026-10-01T12:00:00+00:00",
            }],
            "listas_buscables": [{
                "id": "lista-1", "comercio_id": comercio_id,
                "producto_categoria": "Granola", "atributos_texto": "Bowl",
                "activa": True, "created_at": "2026-10-01T12:00:00+00:00",
            }],
        }

    def table(self, tabla):
        return ConsultaFalsa(self, tabla)


def cliente_admin():
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["csrf_preview_admin"] = CSRF
    return cliente


def test_preview_valida_muestra_catalogo_sin_carrito_y_no_se_cachea():
    db = SupabaseFalso()
    with patch("gastronomia.routes.supabase_admin", db):
        cliente = app_module.app.test_client()
        acceso = cliente.get(f"/gastronomia/preview/{COMERCIO_A}")
        respuesta = cliente.post(
            f"/gastronomia/preview/{COMERCIO_A}/validar",
            json={"token": TOKEN},
        )
    acceso_html = acceso.get_data(as_text=True)
    html = respuesta.get_data(as_text=True)
    assert acceso.status_code == 200
    assert TOKEN not in acceso_html and "Granola" not in acceso_html
    assert "http://" not in acceso_html and "https://" not in acceso_html
    assert respuesta.status_code == 200
    assert "Bowl" in html and "Grande" in html and "$ 5.000" in html
    assert "Tu pedido" not in html and "Agregar al pedido" not in html
    assert respuesta.headers["Cache-Control"].startswith("no-store")
    assert respuesta.headers["X-Robots-Tag"].startswith("noindex")
    assert respuesta.headers["Referrer-Policy"] == "no-referrer"
    assert TOKEN not in html
    assert all(
        "<token>" not in regla.rule
        for regla in app_module.app.url_map.iter_rules()
        if "preview" in regla.rule
    )


def test_preview_rechaza_token_invalido_vencido_revocado_publicado_y_otro_comercio():
    casos = [
        (SupabaseFalso(), "incorrecto", COMERCIO_A),
        (SupabaseFalso(vence=datetime.now(timezone.utc) - timedelta(seconds=1)), TOKEN, COMERCIO_A),
        (SupabaseFalso(token=""), TOKEN, COMERCIO_A),
        (SupabaseFalso(activo=True), TOKEN, COMERCIO_A),
        (SupabaseFalso(), TOKEN, COMERCIO_B),
    ]
    for db, token, comercio_id in casos:
        with patch("gastronomia.routes.supabase_admin", db):
            respuesta = app_module.app.test_client().post(
                f"/gastronomia/preview/{comercio_id}/validar",
                json={"token": token},
            )
        assert respuesta.status_code == 404


def test_admin_genera_solo_hash_renovable_y_revoca():
    db = SupabaseFalso(token="")
    with patch("app.supabase_admin", db):
        respuesta = cliente_admin().post(
            f"/admin/gastronomia/{COMERCIO_A}/preview/generar",
            data={"csrf_token": CSRF},
        )
    assert respuesta.status_code == 200
    actualizacion = db.actualizaciones[-1][1]
    assert len(actualizacion["preview_token_hash"]) == 64
    assert "token" not in " ".join(str(v) for v in actualizacion.values())
    assert "/gastronomia/preview/" in respuesta.get_data(as_text=True)
    assert "#" in respuesta.get_data(as_text=True)
    hash_anterior = actualizacion["preview_token_hash"]

    with patch("app.supabase_admin", db):
        renovada = cliente_admin().post(
            f"/admin/gastronomia/{COMERCIO_A}/preview/generar",
            data={"csrf_token": CSRF},
        )
    assert renovada.status_code == 200
    assert db.actualizaciones[-1][1]["preview_token_hash"] != hash_anterior

    with patch("app.supabase_admin", db):
        respuesta = cliente_admin().post(
            f"/admin/gastronomia/{COMERCIO_A}/preview/revocar",
            data={"csrf_token": CSRF},
        )
    assert respuesta.status_code == 302
    assert db.actualizaciones[-1][1] == {
        "preview_token_hash": None,
        "preview_token_created_at": None,
        "preview_token_expires_at": None,
    }


def test_admin_preview_rechaza_post_sin_csrf():
    db = SupabaseFalso(token="")
    with patch("app.supabase_admin", db):
        generar = cliente_admin().post(
            f"/admin/gastronomia/{COMERCIO_A}/preview/generar"
        )
        revocar = cliente_admin().post(
            f"/admin/gastronomia/{COMERCIO_A}/preview/revocar"
        )
    assert generar.status_code == 400
    assert revocar.status_code == 400
    assert db.actualizaciones == []


def test_publicar_revoca_preview_y_pedido_directo_rechaza_borrador():
    db = SupabaseFalso()
    with patch("app.supabase_admin", db):
        respuesta = cliente_admin().post(
            f"/admin/gastronomia/{COMERCIO_A}/toggle"
        )
    assert respuesta.status_code == 302
    assert db.actualizaciones[-1][1] == {
        "activo": True,
        "preview_token_hash": None,
        "preview_token_created_at": None,
        "preview_token_expires_at": None,
    }

    db = SupabaseFalso()
    with patch(
        "gastronomia.routes.buscar_pedido_idempotente",
        side_effect=PedidoError("El comercio no está recibiendo pedidos.", 403),
    ):
        respuesta = app_module.app.test_client().post(
            f"/gastronomia/comercio/{COMERCIO_A}/pedido", json={
                "idempotency_key": "33333333-3333-4333-8333-333333333333",
                "nombre": "Ana", "apellido": "Prueba",
                "whatsapp": "3436123456", "modalidad": "retiro",
                "forma_pago": "efectivo",
                "detalle": [{"id": "producto-1", "cantidad": 1}],
            }
        )
    assert respuesta.status_code == 403
    assert respuesta.get_json()["ok"] is False

    try:
        buscar_pedido_idempotente(
            COMERCIO_A,
            "33333333-3333-4333-8333-333333333333",
            "huella",
            cliente_supabase=db,
        )
        assert False, "Un borrador no debe consultar ni reproducir pedidos"
    except PedidoError as error:
        assert error.status_code == 403


def test_menu_publico_no_abre_borrador_y_delivery_no_cotiza():
    db = SupabaseFalso()
    with patch("gastronomia.routes.supabase_admin", db):
        menu = app_module.app.test_client().get(
            f"/gastronomia/comercio/{COMERCIO_A}"
        )
        delivery = app_module.app.test_client().post(
            f"/gastronomia/comercio/{COMERCIO_A}/delivery/cotizar",
            json={"direccion": "San Martín 100", "telefono_cliente": "3436123456"},
        )
    assert menu.status_code == 302
    assert delivery.status_code == 404


def test_borrador_no_aparece_en_galeria_ni_perfil_general_y_publicado_sigue_operativo():
    borrador = SupabaseFalso()
    with patch("gastronomia.routes.supabase_admin", borrador):
        galeria = app_module.app.test_client().get("/gastronomia/")
    assert galeria.status_code == 200
    assert "Granola" not in galeria.get_data(as_text=True)

    with patch("app.supabase_admin", borrador):
        perfil = app_module.app.test_client().get(f"/comercio/{COMERCIO_A}")
    assert perfil.status_code == 302

    with patch("app.supabase_admin", borrador), patch(
        "app.modulo_activo", return_value=True
    ):
        cliente = app_module.app.test_client()
        portada = cliente.get("/")
        busqueda = cliente.get("/?q=Granola")
        recientes = cliente.get("/api/publicaciones-recientes")
        detalle = cliente.get(f"/detalle/{PUBLICACION_ID}")
        imagen_publicacion = cliente.get(
            f"/detalle/{PUBLICACION_ID}/imagen-compartir.jpg"
        )
        imagen_comercio = cliente.get(
            f"/comercio/{COMERCIO_A}/imagen-compartir.png"
        )
        whatsapp = cliente.get(f"/analytics/whatsapp/{COMERCIO_A}")
        carrito = cliente.post(
            f"/comercio/{COMERCIO_A}/carrito/confirmar",
            json={},
        )
    assert "Publicación heredada Granola" not in portada.get_data(as_text=True)
    assert "Publicación heredada Granola" not in busqueda.get_data(as_text=True)
    assert "Producto relacionado" not in busqueda.get_data(as_text=True)
    assert recientes.get_json()["items"] == []
    assert detalle.status_code == 302
    assert imagen_publicacion.status_code == 404
    assert imagen_comercio.status_code == 404
    assert whatsapp.status_code == 302
    assert whatsapp.location.endswith(("/", "/index.html"))
    assert carrito.status_code == 404

    publicado = SupabaseFalso(activo=True)
    with patch("gastronomia.routes.supabase_admin", publicado):
        menu = app_module.app.test_client().get(
            f"/gastronomia/comercio/{COMERCIO_A}"
        )
    assert menu.status_code == 200
    assert "Bowl" in menu.get_data(as_text=True)
    assert "Tu pedido" in menu.get_data(as_text=True)

    with patch("app.supabase_admin", publicado):
        recientes_publicado = app_module.app.test_client().get(
            "/api/publicaciones-recientes"
        )
    assert recientes_publicado.get_json()["items"][0]["comercio"] == "Granola"


def test_panel_sigue_admitiendo_edicion_con_configuracion_en_borrador():
    db = SupabaseFalso()
    comercio = db.datos["comercios"][0]
    comercio.update({"user_id": "propietaria-1", "terminos_aceptados_at": None})
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["user_id"] = "propietaria-1"
        sesion["comercio"] = comercio
    with patch("gastronomia.routes.supabase_admin", db):
        respuesta = cliente.get("/gastronomia/panel/productos")
    assert respuesta.status_code == 200
