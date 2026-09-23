from types import SimpleNamespace

from flask import Flask

import modulos
from contenido import contenido_bp
from contenido.services.fuentes import (
    obtener_imagenes_publicacion,
    obtener_publicacion_comercio,
    obtener_publicaciones_comercio,
)


COMERCIO_A = "11111111-1111-1111-1111-111111111111"
COMERCIO_B = "22222222-2222-2222-2222-222222222222"
PUBLICACION_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PUBLICACION_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def publicacion(publicacion_id=PUBLICACION_A, comercio_id=COMERCIO_A):
    return {
        "id": publicacion_id,
        "comercio_id": comercio_id,
        "nombre": "Producto de prueba",
        "descripcion": "Descripción breve",
        "precio": 3500,
        "imagenes": [
            "https://imagenes.example/portada.jpg",
            "https://imagenes.example/segunda.jpg",
        ],
        "imagen_principal": "https://imagenes.example/portada.jpg",
        "imagen_url": "https://imagenes.example/portada.jpg",
        "activa": True,
        "eliminada": False,
        "created_at": "2026-09-23T12:00:00+00:00",
        "imagenes_disponibles": [
            "https://imagenes.example/portada.jpg",
            "https://imagenes.example/segunda.jpg",
        ],
        "imagen_mostrar": "https://imagenes.example/portada.jpg",
    }


class ConsultaPublicacionesFalsa:
    def __init__(self, filas):
        self.filas = [dict(fila) for fila in filas]
        self.filtros = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        filas = self.filas
        for campo, valor in self.filtros:
            filas = [fila for fila in filas if fila.get(campo) == valor]
        return SimpleNamespace(data=filas)


class SupabasePublicacionesFalso:
    def __init__(self, filas):
        self.filas = filas
        self.consultas = []

    def table(self, nombre):
        assert nombre == "publicaciones"
        consulta = ConsultaPublicacionesFalsa(self.filas)
        self.consultas.append(consulta)
        return consulta


def crear_app_prueba():
    app = Flask(__name__)
    app.secret_key = "test-contenido"
    app.register_blueprint(contenido_bp)

    @app.route("/login", endpoint="login")
    def login():
        return "login"

    @app.route("/panel", endpoint="panel")
    def panel():
        return "panel"

    return app


def test_contenido_aparece_en_catalogo():
    contenido = modulos.CATALOGO_MODULOS["contenido"]
    assert contenido["slug"] == "contenido"
    assert contenido["nombre"] == "ClickLocal Contenido"
    assert contenido["endpoint_operativo"] == "contenido.panel_contenido"
    assert "categoria_requerida" not in contenido


def test_comercio_sin_modulo_no_accede(monkeypatch):
    monkeypatch.setattr(modulos, "modulo_activo", lambda *_args: False)
    app = crear_app_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {"id": "comercio-sin-contenido"}
        respuesta = cliente.get("/contenido/")

    assert respuesta.status_code == 403


def test_comercio_con_modulo_activo_accede(monkeypatch):
    monkeypatch.setattr(modulos, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(
        "contenido.routes.obtener_publicaciones_comercio",
        lambda comercio_id: [],
    )
    app = crear_app_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {
                "id": "comercio-con-contenido",
                "nombre_negocio": "Comercio de prueba",
            }
        respuesta = cliente.get("/contenido/")

    assert respuesta.status_code == 200
    assert b"ClickLocal Contenido" in respuesta.data
    assert b"Crear para redes" in respuesta.data


def test_listado_de_publicaciones_esta_aislado_por_comercio():
    db = SupabasePublicacionesFalso([
        publicacion(PUBLICACION_A, COMERCIO_A),
        publicacion(PUBLICACION_B, COMERCIO_B),
    ])

    publicaciones = obtener_publicaciones_comercio(COMERCIO_A, cliente=db)

    assert [publicacion["id"] for publicacion in publicaciones] == [
        PUBLICACION_A
    ]
    assert ("comercio_id", COMERCIO_A) in db.consultas[0].filtros
    assert ("eliminada", False) in db.consultas[0].filtros


def test_no_recupera_publicacion_de_otro_comercio():
    db = SupabasePublicacionesFalso([
        publicacion(PUBLICACION_B, COMERCIO_B),
    ])

    resultado = obtener_publicacion_comercio(
        PUBLICACION_B,
        COMERCIO_A,
        cliente=db,
    )

    assert resultado is None
    assert db.consultas[0].filtros == [
        ("id", PUBLICACION_B),
        ("comercio_id", COMERCIO_A),
        ("eliminada", False),
    ]


def test_imagenes_seleccionables_son_solo_las_de_la_publicacion():
    fila = publicacion()
    fila["imagenes"] = [
        "https://imagenes.example/portada.jpg",
        "https://imagenes.example/segunda.jpg",
        "https://imagenes.example/segunda.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,ajena",
    ]

    assert obtener_imagenes_publicacion(fila) == [
        "https://imagenes.example/portada.jpg",
        "https://imagenes.example/segunda.jpg",
    ]


def test_acceso_a_publicacion_propia_renderiza_tres_formatos(monkeypatch):
    monkeypatch.setattr(modulos, "modulo_activo", lambda *_args: True)
    llamadas = []

    def obtener(publicacion_id, comercio_id):
        llamadas.append((publicacion_id, comercio_id))
        return publicacion(publicacion_id, comercio_id)

    monkeypatch.setattr(
        "contenido.routes.obtener_publicacion_comercio",
        obtener,
    )
    app = crear_app_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {
                "id": COMERCIO_A,
                "nombre_negocio": "Comercio A",
            }
        respuesta = cliente.get(f"/contenido/publicacion/{PUBLICACION_A}")

    assert respuesta.status_code == 200
    assert llamadas == [(PUBLICACION_A, COMERCIO_A)]
    assert b'value="post"' in respuesta.data
    assert b'value="historia"' in respuesta.data
    assert b'value="estado"' in respuesta.data
    assert b"contenido-preview--post" in respuesta.data
    assert b"Texto para compartir" in respuesta.data
    assert b"Comercio A" in respuesta.data


def test_ruta_bloquea_publicacion_ajena(monkeypatch):
    monkeypatch.setattr(modulos, "modulo_activo", lambda *_args: True)
    llamadas = []

    def obtener(publicacion_id, comercio_id):
        llamadas.append((publicacion_id, comercio_id))
        return None

    monkeypatch.setattr(
        "contenido.routes.obtener_publicacion_comercio",
        obtener,
    )
    app = crear_app_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {"id": COMERCIO_A}
        respuesta = cliente.get(f"/contenido/publicacion/{PUBLICACION_B}")

    assert respuesta.status_code == 404
    assert llamadas == [(PUBLICACION_B, COMERCIO_A)]


def test_ruta_rechaza_uuid_manipulado_sin_consultar(monkeypatch):
    monkeypatch.setattr(modulos, "modulo_activo", lambda *_args: True)
    llamadas = []
    monkeypatch.setattr(
        "contenido.routes.obtener_publicacion_comercio",
        lambda *args: llamadas.append(args),
    )
    app = crear_app_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {"id": COMERCIO_A}
        respuesta = cliente.get("/contenido/publicacion/no-es-un-uuid")

    assert respuesta.status_code == 404
    assert llamadas == []


def test_detalle_requiere_modulo_contenido_activo(monkeypatch):
    monkeypatch.setattr(modulos, "modulo_activo", lambda *_args: False)
    app = crear_app_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {"id": COMERCIO_A}
        respuesta = cliente.get(f"/contenido/publicacion/{PUBLICACION_A}")

    assert respuesta.status_code == 403


def test_sin_comercio_en_sesion_redirige_al_login():
    app = crear_app_prueba()
    respuesta = app.test_client().get(f"/contenido/publicacion/{PUBLICACION_A}")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login")
