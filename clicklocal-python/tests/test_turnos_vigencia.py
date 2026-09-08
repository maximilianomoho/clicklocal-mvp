from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from flask import Flask, session

import modulos
import turnos.routes as routes
from turnos import turnos_bp
from whatsapp import construir_url_whatsapp


class SupabaseVacio:
    def __init__(self, datos=None):
        self.datos = [] if datos is None else datos

    def table(self, *args, **kwargs):
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.datos)


class SupabasePorTabla:
    def __init__(self, datos_por_tabla):
        self.datos_por_tabla = datos_por_tabla
        self.tabla = ""

    def table(self, nombre):
        self.tabla = nombre
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.datos_por_tabla.get(self.tabla, []))


def aplicacion_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(turnos_bp)

    @app.route("/login")
    def login():
        return "login"

    @app.route("/comercio/<comercio_id>", endpoint="perfil_comercio")
    def perfil_comercio(comercio_id):
        return comercio_id

    return app


def vigencia(**cambios):
    datos = {
        "existe": True,
        "habilitado_manual": True,
        "estado_vigencia": "activo",
        "fecha_activacion": date(2026, 9, 1),
        "fecha_vencimiento": date(2026, 10, 1),
        "aviso_dias_antes": 5,
        "gracia_dias": 5,
        "inicio_aviso": date(2026, 9, 26),
        "dias_restantes": 21,
        "fecha_fin_gracia": date(2026, 10, 6),
        "dias_gracia_restantes": None,
        "acceso_operativo": True,
        "acceso_publico": True,
        "motivo_bloqueo": None,
    }
    datos.update(cambios)
    return datos


def ejecutar_agenda(monkeypatch, estado, whatsapp=""):
    contexto = {}

    def render_falso(nombre, **datos):
        contexto.update(datos)
        contexto["template"] = nombre
        return contexto

    monkeypatch.setattr(routes, "supabase_admin", SupabaseVacio())
    monkeypatch.setattr(
        routes,
        "evaluar_vigencia_modulo",
        lambda comercio_id, slug: estado,
    )
    monkeypatch.setattr(routes, "render_template", render_falso)
    monkeypatch.setattr(routes, "CLICKLOCAL_WHATSAPP", whatsapp)

    app = aplicacion_prueba()
    with app.test_request_context("/turnos/agenda"):
        session["comercio"] = {
            "id": "comercio-1",
            "nombre_negocio": "La casa del sombrero",
        }
        return routes.agenda_turnos()


def test_get_agenda_permite_vencido_en_modo_limitado(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(
            estado_vigencia="vencido",
            acceso_operativo=False,
            acceso_publico=False,
            motivo_bloqueo="vigencia_vencida",
        ),
    )
    assert contexto["template"] == "turnos/agenda.html"
    assert contexto["modo_limitado"] is True
    assert contexto["vigencia_modulo"]["estado_vigencia"] == "vencido"


def test_get_agenda_permite_suspendido_en_modo_limitado(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(
            habilitado_manual=False,
            acceso_operativo=False,
            acceso_publico=False,
            motivo_bloqueo="suspension_manual",
        ),
    )
    assert contexto["modo_limitado"] is True
    assert contexto["vigencia_modulo"]["habilitado_manual"] is False


def test_get_agenda_cierra_si_no_existe_relacion(monkeypatch):
    app = aplicacion_prueba()
    monkeypatch.setattr(
        routes,
        "evaluar_vigencia_modulo",
        lambda comercio_id, slug: {"existe": False},
    )
    with app.test_request_context("/turnos/agenda"):
        session["comercio"] = {"id": "comercio-1"}
        respuesta, estado = routes.agenda_turnos()
    assert estado == 403
    assert "no activo" in respuesta


def test_get_agenda_sin_sesion_conserva_destino_login():
    app = aplicacion_prueba()
    cliente = app.test_client()
    respuesta = cliente.get("/turnos/agenda")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login?next=/turnos/agenda")


def test_qr_privado_usa_exactamente_url_publica(monkeypatch):
    capturado = {}

    class ImagenQrFalsa:
        def save(self, archivo, format):
            assert format == "PNG"
            archivo.write(b"png-de-prueba")

    def crear_qr_falso(url):
        capturado["url"] = url
        return ImagenQrFalsa()

    app = aplicacion_prueba()
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    monkeypatch.setattr(routes.qrcode, "make", crear_qr_falso)

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {
                "id": "11111111-1111-1111-1111-111111111111"
            }
        respuesta = cliente.get("/turnos/agenda/qr.png")

    assert respuesta.status_code == 200
    assert respuesta.mimetype == "image/png"
    assert capturado["url"] == (
        "http://localhost/turnos/comercio/"
        "11111111-1111-1111-1111-111111111111"
    )


def test_qr_sin_sesion_no_se_genera():
    app = aplicacion_prueba()
    respuesta = app.test_client().get("/turnos/agenda/qr.png")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login")


def test_manifest_conserva_inicio_general_y_agrega_shortcut_turnos():
    raiz = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (raiz / "static" / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "ClickLocal"
    assert manifest["short_name"] == "ClickLocal"
    assert manifest["start_url"] == "/index.html"
    assert manifest["scope"] == "/"
    assert any(
        acceso.get("name") == "ClickLocal Turnos"
        and acceso.get("short_name") == "Turnos"
        and acceso.get("url") == "/turnos/agenda"
        for acceso in manifest["shortcuts"]
    )


def test_agenda_carga_pwa_y_reutiliza_url_publica_para_compartir():
    raiz = Path(__file__).resolve().parents[1]
    agenda = (
        raiz / "turnos" / "templates" / "turnos" / "agenda.html"
    ).read_text(encoding="utf-8")

    assert 'rel="manifest" href="/static/manifest.json"' in agenda
    assert 'src="/static/pwa.js" data-install-ui="false"' in agenda
    assert "const TURNERA_PUBLICA_URL = {{ turnera_publica_url" in agenda
    assert "const enlace = TURNERA_PUBLICA_URL;" in agenda


def test_mutacion_privada_bloqueada_vencido(monkeypatch):
    app = aplicacion_prueba()
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: False)
    with app.test_request_context(
        "/turnos/agenda/profesionales/nuevo",
        method="POST",
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta, estado = routes.crear_profesional()
    assert estado == 403
    assert "no activo" in respuesta


def test_acceso_publico_bloqueado_vencido(monkeypatch):
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabaseVacio([{
            "id": "11111111-1111-1111-1111-111111111111",
            "nombre_negocio": "Comercio",
            "activo": True,
        }]),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: False)
    respuesta, estado = routes.turnera_publica(
        "11111111-1111-1111-1111-111111111111"
    )
    assert respuesta == ""
    assert estado == 404


def test_turnera_publica_abre_sin_login(monkeypatch):
    comercio_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabasePorTabla({
            "comercios": [{
                "id": comercio_id,
                "nombre_negocio": "Comercio",
                "activo": True,
            }],
        }),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: True)

    app = aplicacion_prueba()
    respuesta = app.test_client().get(f"/turnos/comercio/{comercio_id}")

    assert respuesta.status_code == 200
    assert b"Nuevo turno" in respuesta.data
    assert b"Iniciar sesi" not in respuesta.data


def test_reserva_publica_sigue_sin_requerir_login(monkeypatch):
    comercio_id = "11111111-1111-1111-1111-111111111111"
    servicio_id = "22222222-2222-2222-2222-222222222222"
    profesional_id = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabasePorTabla({
            "comercios": [{"id": comercio_id, "whatsapp": ""}],
        }),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: True)
    monkeypatch.setattr(
        routes,
        "_crear_reserva_validada",
        lambda *args: ({"ok": True}, 201),
    )

    app = aplicacion_prueba()
    respuesta = app.test_client().post(
        f"/turnos/comercio/{comercio_id}/reservas",
        data={
            "servicio_id": servicio_id,
            "profesional_id": profesional_id,
        },
    )

    assert respuesta.status_code == 201
    assert respuesta.get_json() == {"ok": True}


def test_cta_configurado_incluye_comercio_y_modulo(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(estado_vigencia="por_vencer"),
        whatsapp="3434150049",
    )
    url = contexto["whatsapp_clicklocal_url"]
    assert url.startswith("https://wa.me/5493434150049?")
    mensaje = parse_qs(urlparse(url).query)["text"][0]
    assert mensaje == (
        "Hola ClickLocal, soy La casa del sombrero. "
        "Quiero renovar el módulo Turnos."
    )


def test_sin_whatsapp_no_genera_url(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(estado_vigencia="en_gracia"),
        whatsapp="",
    )
    assert contexto["whatsapp_clicklocal_url"] is None
    assert construir_url_whatsapp("", "mensaje") == ""
