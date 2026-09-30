import base64
import json
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import patch

import pytest

import app as app_module


SUPABASE_ORIGIN_FICTICIO = "https://proyecto.supabase.co"
REDIRECT_ACTIVACION = "https://clicklocal.com.ar/activar-cuenta"
ACTION_LINK_FICTICIO = (
    f"{SUPABASE_ORIGIN_FICTICIO}/auth/v1/verify"
    "?token=TOKEN_FICTICIO&type=recovery"
    "&redirect_to=https%3A%2F%2Fclicklocal.com.ar%2Factivar-cuenta"
)


class ConsultaAltaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.operacion = "select"
        self.payload = None

    def __getattr__(self, _nombre):
        return lambda *_args, **_kwargs: self

    def insert(self, payload):
        self.operacion = "insert"
        self.payload = payload
        return self

    def delete(self):
        self.operacion = "delete"
        return self

    def execute(self):
        self.db.operaciones.append((self.tabla, self.operacion, self.payload))
        if self.tabla == "comercios" and self.operacion == "select":
            return SimpleNamespace(data=[])
        if self.tabla == "comercios" and self.operacion == "insert":
            return SimpleNamespace(data=[{"id": self.db.comercio_id, **self.payload}])
        return SimpleNamespace(data=[self.payload] if self.payload else [])


class SupabaseAltaFalso:
    comercio_id = "11111111-1111-1111-1111-111111111111"

    def __init__(self):
        self.operaciones = []
        self.usuario = SimpleNamespace(id="usuario-auth-1")
        self.auth = SimpleNamespace(admin=SimpleNamespace(
            create_user=self.crear_usuario,
            delete_user=lambda _id: None,
        ))

    def crear_usuario(self, atributos):
        assert atributos == {
            "email": "duena@example.com",
            "email_confirm": True,
        }
        assert "password" not in atributos
        return SimpleNamespace(user=self.usuario)

    def table(self, tabla):
        return ConsultaAltaFalsa(self, tabla)


def test_alta_asistida_crea_vertical_y_entra_en_modo_gestor():
    db = SupabaseAltaFalso()
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["user_id"] = "admin-no-debe-cambiar"

    with patch("app.supabase_admin", db):
        respuesta = cliente.post("/admin/gastronomia/nueva", data={
            "nombre_negocio": "Cocina de Ana",
            "email": "duena@example.com",
            "whatsapp": "343 400 0000",
            "direccion": "San Martín 100",
            "descripcion": "Comida casera",
        })

    assert respuesta.status_code == 302
    assert "/panel" in respuesta.location
    inserciones = {
        tabla: payload
        for tabla, operacion, payload in db.operaciones
        if operacion == "insert"
    }
    assert inserciones["comercios"]["categoria"] == "Gastronomía"
    assert inserciones["comercios"]["terminos_aceptados_at"] is None
    assert inserciones["comercios"]["terminos_version"] is None
    assert inserciones["gastronomia_configuracion"]["activo"] is True
    with cliente.session_transaction() as sesion:
        assert sesion["gestor_comercio_id"] == db.comercio_id
        assert sesion["user_id"] == "admin-no-debe-cambiar"


def test_estado_admin_deriva_de_aceptacion_de_terminos():
    comercios_raw = [
        {"id": "c-1", "nombre_negocio": "Pendiente", "terminos_aceptados_at": None},
        {"id": "c-2", "nombre_negocio": "Activo", "terminos_aceptados_at": "2026-09-18T12:00:00+00:00"},
    ]
    with app_module.app.test_request_context("/"):
        comercios = app_module._construir_comercios_admin(comercios_raw, [], [])
    por_id = {comercio["id"]: comercio for comercio in comercios}
    assert por_id["c-1"]["estado_acceso"] == "Acceso pendiente"
    assert por_id["c-1"]["propietario_activo"] is False
    assert por_id["c-2"]["estado_acceso"] == "Propietario activo"
    assert por_id["c-2"]["propietario_activo"] is True


class ConsultaAccesoFalsa:
    def __init__(self, comercio):
        self.comercio = comercio

    def __getattr__(self, _nombre):
        return lambda *_args, **_kwargs: self

    def execute(self):
        return SimpleNamespace(data=[self.comercio] if self.comercio else [])


class SupabaseAccesoFalso:
    def __init__(self, comercio):
        self.comercio = comercio

    def table(self, _tabla):
        return ConsultaAccesoFalsa(self.comercio)


@pytest.mark.parametrize("cambios", [
    {"categoria": "Servicios"},
    {"email": ""},
    {"user_id": None},
])
def test_dar_acceso_valida_gastronomia_email_y_user_id(cambios):
    comercio = {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": "usuario-1",
        "email": "duena@example.com",
        "categoria": "Gastronomía",
        **cambios,
    }
    llamadas = []
    auth = SimpleNamespace(auth=SimpleNamespace(
        reset_password_for_email=lambda *args: llamadas.append(args)
    ))
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
    with patch("app.supabase_admin", SupabaseAccesoFalso(comercio)), patch("app.supabase_auth", auth):
        respuesta = cliente.post(f"/admin/gastronomia/{comercio['id']}/dar-acceso")
    assert respuesta.status_code == 302
    assert "acceso_error=1" in respuesta.location
    assert llamadas == []


def test_dar_acceso_envia_recuperacion_con_redirect_https():
    comercio = {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": "usuario-1",
        "email": "duena@example.com",
        "categoria": "Gastronomía",
    }
    llamadas = []
    auth = SimpleNamespace(auth=SimpleNamespace(
        reset_password_for_email=lambda *args: llamadas.append(args)
    ))
    with app_module.app.test_request_context(
        f"/admin/gastronomia/{comercio['id']}/dar-acceso",
        method="POST",
        base_url="http://clicklocal.com.ar",
    ), patch("app.supabase_admin", SupabaseAccesoFalso(comercio)), patch(
        "app.supabase_auth", auth
    ):
        app_module.session["admin_logueado"] = True
        respuesta = app_module.admin_dar_acceso_gastronomia(comercio["id"])
    assert respuesta.status_code == 302
    assert llamadas == [("duena@example.com", {
        "redirect_to": "https://clicklocal.com.ar/activar-cuenta",
    })]


def test_redirect_recuperacion_local_conserva_http():
    with app_module.app.test_request_context(
        "/", base_url="http://127.0.0.1:5000"
    ):
        assert app_module._url_activar_cuenta_externa() == (
            "http://127.0.0.1:5000/activar-cuenta"
        )


class SupabaseWhatsAppFalso(SupabaseAccesoFalso):
    def __init__(self, comercio, resultado):
        super().__init__(comercio)
        self.llamadas = []
        self.resultado = resultado
        self.auth = SimpleNamespace(admin=SimpleNamespace(
            generate_link=self.generar_enlace,
        ))

    def generar_enlace(self, parametros):
        self.llamadas.append(parametros)
        return self.resultado


def _resultado_enlace(**cambios):
    propiedades = SimpleNamespace(
        action_link=ACTION_LINK_FICTICIO,
        verification_type="recovery",
        redirect_to=REDIRECT_ACTIVACION,
    )
    valores = {
        "user": SimpleNamespace(id="usuario-1"),
        "properties": propiedades,
        **cambios,
    }
    return SimpleNamespace(**valores)


def _comercio_whatsapp(**cambios):
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": "usuario-1",
        "email": "duena@example.com",
        "whatsapp": "343 400 0000",
        "categoria": "Gastronomía",
        **cambios,
    }


def test_acceso_whatsapp_requiere_admin():
    cliente = app_module.app.test_client()
    respuesta = cliente.post(
        "/admin/gastronomia/11111111-1111-1111-1111-111111111111/dar-acceso-whatsapp"
    )
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/admin/login")


@pytest.mark.parametrize(("comercio_id", "comercio"), [
    ("uuid-invalido", _comercio_whatsapp()),
    ("11111111-1111-1111-1111-111111111111", None),
    ("11111111-1111-1111-1111-111111111111", _comercio_whatsapp(user_id=None)),
    ("11111111-1111-1111-1111-111111111111", _comercio_whatsapp(email="")),
    ("11111111-1111-1111-1111-111111111111", _comercio_whatsapp(categoria="Servicios")),
    ("11111111-1111-1111-1111-111111111111", _comercio_whatsapp(whatsapp="")),
    ("11111111-1111-1111-1111-111111111111", _comercio_whatsapp(whatsapp="1")),
])
def test_acceso_whatsapp_rechaza_comercio_invalido(comercio_id, comercio):
    db = SupabaseWhatsAppFalso(comercio, _resultado_enlace())
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
    with patch("app.supabase_admin", db):
        respuesta = cliente.post(
            f"/admin/gastronomia/{comercio_id}/dar-acceso-whatsapp"
        )
    assert respuesta.status_code == 302
    assert "acceso_whatsapp_error=1" in respuesta.location
    assert db.llamadas == []


def test_acceso_whatsapp_genera_recovery_y_construye_wa_me():
    db = SupabaseWhatsAppFalso(_comercio_whatsapp(), _resultado_enlace())
    with app_module.app.test_request_context(
        "/admin/gastronomia/11111111-1111-1111-1111-111111111111/dar-acceso-whatsapp",
        method="POST",
        base_url="https://clicklocal.com.ar",
    ), patch("app.supabase_admin", db), patch(
        "app.SUPABASE_URL", SUPABASE_ORIGIN_FICTICIO
    ):
        app_module.session["admin_logueado"] = True
        respuesta = app_module.admin_dar_acceso_gastronomia_whatsapp(
            "11111111-1111-1111-1111-111111111111",
        )
        sesion_serializada = repr(dict(app_module.session))

    assert respuesta.status_code == 302
    assert db.llamadas == [{
        "type": "recovery",
        "email": "duena@example.com",
        "options": {
            "redirect_to": REDIRECT_ACTIVACION,
        },
    }]
    destino = urlparse(respuesta.location)
    assert destino.scheme == "https"
    assert destino.netloc == "wa.me"
    assert destino.path == "/5493434000000"
    mensaje = parse_qs(destino.query)["text"][0]
    assert "https://clicklocal.com.ar/acceso#v=1&link=" in mensaje
    assert "/auth/v1/verify" not in mensaje
    wrapper = next(
        parte for parte in mensaje.split() if parte.startswith(
            "https://clicklocal.com.ar/acceso#"
        )
    )
    payload = parse_qs(urlparse(wrapper).fragment)["link"][0]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    assert base64.urlsafe_b64decode(payload).decode() == ACTION_LINK_FICTICIO
    assert "contraseña" in mensaje
    assert "contraseña provisoria" not in mensaje
    assert "password" not in mensaje.lower()
    assert "TOKEN_FICTICIO" not in sesion_serializada


@pytest.mark.parametrize("resultado", [
    _resultado_enlace(user=SimpleNamespace(id="otro-usuario")),
    _resultado_enlace(properties=None),
    _resultado_enlace(properties=SimpleNamespace(
        action_link="", verification_type="recovery",
        redirect_to="http://localhost/activar-cuenta",
    )),
    _resultado_enlace(properties=SimpleNamespace(
        action_link="https://proyecto.supabase.co/auth/v1/verify?token=secreto",
        verification_type="magiclink",
        redirect_to="http://localhost/activar-cuenta",
    )),
    _resultado_enlace(properties=SimpleNamespace(
        action_link="https://proyecto.supabase.co/auth/v1/verify?token=secreto",
        verification_type="recovery",
        redirect_to="https://clicklocal.com.ar/",
    )),
])
def test_acceso_whatsapp_rechaza_respuesta_auth_insegura(resultado, capsys):
    db = SupabaseWhatsAppFalso(_comercio_whatsapp(), resultado)
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
    with patch("app.supabase_admin", db):
        respuesta = cliente.post(
            "/admin/gastronomia/11111111-1111-1111-1111-111111111111/dar-acceso-whatsapp",
        )
    assert respuesta.status_code == 302
    assert "acceso_whatsapp_error=1" in respuesta.location
    assert not respuesta.location.startswith("https://wa.me/")
    assert "token=secreto" not in capsys.readouterr().out


def _action_link_ficticio(parametros=None, **partes):
    query = parametros if parametros is not None else [
        ("token", "TOKEN_FICTICIO"),
        ("type", "recovery"),
        ("redirect_to", REDIRECT_ACTIVACION),
    ]
    esquema = partes.get("esquema", "https")
    autoridad = partes.get("autoridad", "proyecto.supabase.co")
    path = partes.get("path", "/auth/v1/verify")
    fragmento = partes.get("fragmento", "")
    enlace = f"{esquema}://{autoridad}{path}?{urlencode(query)}"
    return f"{enlace}#{fragmento}" if fragmento else enlace


def test_validador_action_link_acepta_recovery_estricto():
    with patch("app.SUPABASE_URL", SUPABASE_ORIGIN_FICTICIO):
        assert app_module._validar_action_link_recovery(
            _action_link_ficticio()
        ) == _action_link_ficticio()


@pytest.mark.parametrize("action_link", [
    _action_link_ficticio(esquema="http"),
    _action_link_ficticio(autoridad="otro.supabase.co"),
    _action_link_ficticio(autoridad="proyecto.supabase.co.evil.example"),
    _action_link_ficticio(autoridad="proyecto.supabase.co:444"),
    _action_link_ficticio(autoridad="usuario:clave@proyecto.supabase.co"),
    _action_link_ficticio(path="/auth/v1/otro"),
    _action_link_ficticio(fragmento="interno"),
    _action_link_ficticio(parametros=[
        ("type", "recovery"), ("redirect_to", REDIRECT_ACTIVACION),
    ]),
    _action_link_ficticio(parametros=[
        ("token", "uno"), ("token", "dos"), ("type", "recovery"),
        ("redirect_to", REDIRECT_ACTIVACION),
    ]),
    _action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("redirect_to", REDIRECT_ACTIVACION),
    ]),
    _action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("type", "magiclink"),
        ("redirect_to", REDIRECT_ACTIVACION),
    ]),
    _action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("type", "recovery"),
        ("type", "recovery"), ("redirect_to", REDIRECT_ACTIVACION),
    ]),
    _action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("type", "recovery"),
        ("redirect_to", "https://clicklocal.com.ar/otro"),
    ]),
    _action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("type", "recovery"),
        ("redirect_to", REDIRECT_ACTIVACION),
        ("redirect_to", REDIRECT_ACTIVACION),
    ]),
])
def test_validador_action_link_rechaza_destinos_inseguros(action_link):
    with patch("app.SUPABASE_URL", SUPABASE_ORIGIN_FICTICIO), pytest.raises(
        ValueError
    ):
        app_module._validar_action_link_recovery(action_link)


def test_acceso_intermedio_es_publico_neutro_y_no_cacheable():
    cliente = app_module.app.test_client()
    with patch("app.SUPABASE_URL", SUPABASE_ORIGIN_FICTICIO):
        respuesta = cliente.get("/acceso#TOKEN_QUE_NO_LLEGA_AL_SERVIDOR")
    html = respuesta.get_data(as_text=True)
    assert respuesta.status_code == 200
    assert "Tu acceso a ClickLocal está listo" in html
    assert "TOKEN_QUE_NO_LLEGA_AL_SERVIDOR" not in html
    assert ACTION_LINK_FICTICIO not in html
    assert respuesta.headers["Cache-Control"] == "no-store"
    assert respuesta.headers["Referrer-Policy"] == "no-referrer"
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"
    csp = respuesta.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp


def test_acceso_intermedio_rechaza_query_sensible_sin_reflejarla():
    cliente = app_module.app.test_client()
    with patch("app.SUPABASE_URL", SUPABASE_ORIGIN_FICTICIO):
        respuesta = cliente.get("/acceso?token=NO_REFLEJAR")
    assert respuesta.status_code == 400
    assert "NO_REFLEJAR" not in respuesta.get_data(as_text=True)


class ConsultaActivacionFalsa:
    def __init__(self, db):
        self.db = db
        self.operacion = "select"
        self.payload = None

    def select(self, *_args):
        self.operacion = "select"
        return self

    def update(self, payload):
        self.operacion = "update"
        self.payload = payload
        return self

    def __getattr__(self, _nombre):
        return lambda *_args, **_kwargs: self

    def execute(self):
        if self.operacion == "select":
            return SimpleNamespace(data=[dict(self.db.comercio)] if self.db.comercio else [])
        actualizado = {**self.db.comercio, **self.payload}
        self.db.actualizacion = self.payload
        return SimpleNamespace(data=[actualizado])


class SupabaseActivacionFalso:
    def __init__(self, comercio):
        self.comercio = comercio
        self.actualizacion = None

    def table(self, tabla):
        assert tabla == "comercios"
        return ConsultaActivacionFalsa(self)


def _auth_token(usuario):
    return SimpleNamespace(auth=SimpleNamespace(
        get_user=lambda _token: SimpleNamespace(user=usuario)
    ))


def test_completar_activacion_rechaza_token_invalido():
    cliente = app_module.app.test_client()
    with patch("app.supabase_auth", _auth_token(None)):
        respuesta = cliente.post("/activar-cuenta/completar", json={
            "access_token": "token-invalido", "acepta_terminos": True,
        })
    assert respuesta.status_code == 401


def test_completar_activacion_rechaza_comercio_inexistente():
    cliente = app_module.app.test_client()
    with patch("app.supabase_auth", _auth_token(SimpleNamespace(id="usuario-1"))), patch(
        "app.supabase_admin", SupabaseActivacionFalso(None)
    ):
        respuesta = cliente.post("/activar-cuenta/completar", json={
            "access_token": "token-valido", "acepta_terminos": True,
        })
    assert respuesta.status_code == 404


def test_completar_activacion_registra_terminos_y_crea_sesion():
    comercio = {
        "id": "comercio-1",
        "user_id": "usuario-1",
        "categoria": "Gastronomía",
        "terminos_aceptados_at": None,
        "terminos_version": None,
    }
    db = SupabaseActivacionFalso(comercio)
    cliente = app_module.app.test_client()
    with patch("app.supabase_auth", _auth_token(SimpleNamespace(id="usuario-1"))), patch(
        "app.supabase_admin", db
    ):
        respuesta = cliente.post("/activar-cuenta/completar", json={
            "access_token": "token-valido", "acepta_terminos": True,
        })
    assert respuesta.status_code == 200
    assert respuesta.get_json()["destino"] == "/gastronomia/panel"
    assert db.actualizacion["terminos_aceptados_at"]
    assert db.actualizacion["terminos_version"] == app_module.TERMINOS_VERSION
    with cliente.session_transaction() as sesion:
        assert sesion["user_id"] == "usuario-1"
        assert sesion["comercio"]["terminos_version"] == app_module.TERMINOS_VERSION
        assert sesion["publicaciones"] == []


def test_template_recovery_exige_evento_y_evitar_dobles_envios():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert 'evento === "PASSWORD_RECOVERY"' in contenido
    assert "onAuthStateChange" in contenido
    assert "recuperacionConfirmada" in contenido
    assert "procesando" in contenido
    assert "updateUser({ password })" in contenido


def test_recovery_configura_supabase_js_para_callback_implicito():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert "@supabase/supabase-js@2.116.0" in contenido
    assert "detectSessionInUrl: true" in contenido
    assert 'flowType: "implicit"' in contenido
    assert "persistSession: true" in contenido


def test_recovery_no_declara_vencido_a_los_1500_ms():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert "ESPERA_MAXIMA_AUTENTICACION_MS = 10000" in contenido
    assert "DEMORA_FALLBACK_SET_SESSION_MS = 1500" in contenido
    assert "setTimeout(async () =>" not in contenido
    assert "while (" in contenido
    assert "await recuperarSesionDetectada()" in contenido


def test_recovery_tardio_y_get_session_lento_permanecen_aceptados():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert "eventoRecuperacionRecibido = true" in contenido
    assert "habilitarRecuperacion(sesionEvento)" in contenido
    assert "await cliente.auth.getSession()" in contenido
    assert "if (recuperacionConfirmada) return" in contenido


def test_recovery_perdido_usa_sesion_solo_con_evidencia_de_recovery():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    bloque = contenido.split("async function recuperarSesionDetectada()", 1)[1].split(
        "async function fallbackTokensIniciales()", 1
    )[0]
    assert "!eraRedirectRecuperacion" in bloque
    assert "cliente.auth.getSession()" in bloque
    assert "habilitarRecuperacion(resultado.data.session)" in bloque


def test_recovery_hash_tiene_fallback_set_session_una_sola_vez():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert 'fragmentoInicial.get("access_token")' in contenido
    assert 'fragmentoInicial.get("refresh_token")' in contenido
    assert "fallbackSetSessionIntentado" in contenido
    assert "cliente.auth.setSession({" in contenido
    assert "access_token: accessTokenInicial" in contenido
    assert "refresh_token: refreshTokenInicial" in contenido


def test_recovery_diagnostica_errores_sin_exponer_descripcion():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert 'valorCallback("error")' in contenido
    assert 'valorCallback("error_code")' in contenido
    assert 'valorCallback("error_description")' in contenido
    assert 'codigo === "otp_expired"' in contenido
    assert "Este enlace ya no es válido." in contenido
    assert "estado.textContent = callbackErrorDescription" not in contenido


def test_recovery_reconoce_code_pkce_no_compatible():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert 'queryInicial.get("code")' in contenido
    assert "hayCodigoPkce && !eraRedirectRecuperacion" in contenido
    assert "tipo de acceso no compatible" in contenido


def test_recovery_no_habilita_una_sesion_ordinaria():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert 'callbackType === "recovery"' in contenido
    assert "if (!eraRedirectRecuperacion)" in contenido
    assert "No encontramos una recuperación de cuenta válida" in contenido


def test_recovery_no_imprime_ni_persiste_tokens_manualmente():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert "console.log" not in contenido
    assert "console.error" not in contenido
    assert "localStorage.setItem" not in contenido
    assert "sessionStorage.setItem" not in contenido
    assert "fetch(window.location" not in contenido


def test_recovery_email_y_whatsapp_conservan_el_mismo_destino():
    contenido_app = open("app.py", encoding="utf-8").read()
    bloque_email = contenido_app.split(
        "def admin_dar_acceso_gastronomia(comercio_id):", 1
    )[1].split("def admin_dar_acceso_gastronomia_whatsapp", 1)[0]
    bloque_whatsapp = contenido_app.split(
        "def admin_dar_acceso_gastronomia_whatsapp(comercio_id):", 1
    )[1].split("def activar_cuenta", 1)[0]
    assert "reset_password_for_email" in bloque_email
    assert "_url_activar_cuenta_externa()" in bloque_email
    assert '"type": "recovery"' in bloque_whatsapp
    assert "_url_activar_cuenta_externa()" in bloque_whatsapp


def test_recovery_doble_evento_y_doble_submit_tienen_guardas():
    contenido = open("templates/activar_cuenta.html", encoding="utf-8").read()
    assert (
        "recuperacionConfirmada || activacionCompletada || "
        "!sesionRecuperada?.access_token"
    ) in contenido
    assert "procesando || activacionCompletada" in contenido
    assert "procesando = true" in contenido


def _ejecutar_recovery_javascript(configuracion):
    with app_module.app.test_request_context("/activar-cuenta"):
        html = app_module.render_template(
            "activar_cuenta.html",
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon-test",
        )
    script = re.search(
        r'<script type="module">(.*?)</script>', html, re.DOTALL
    ).group(1)
    script = script.replace(
        'import { createClient } from '
        '"https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.116.0/+esm";',
        "const createClient = globalThis.createClientMock;",
    )
    script = script.replace(
        "ESPERA_MAXIMA_AUTENTICACION_MS = 10000",
        "ESPERA_MAXIMA_AUTENTICACION_MS = 120",
    ).replace(
        "INTERVALO_COMPROBACION_MS = 250",
        "INTERVALO_COMPROBACION_MS = 5",
    ).replace(
        "DEMORA_FALLBACK_SET_SESSION_MS = 1500",
        "DEMORA_FALLBACK_SET_SESSION_MS = 20",
    )
    config_json = json.dumps(configuracion)
    arnes = f"""
const config = {config_json};
const estadoPrueba = {{
  textContent: "Validando enlace…",
  classList: {{ add() {{}}, remove() {{}} }},
}};
const formularioPrueba = {{
  hidden: true,
  addEventListener() {{}},
  querySelector() {{ return {{ disabled: false }}; }},
}};
globalThis.window = {{
  location: {{ href: config.url, assign() {{}} }},
}};
globalThis.document = {{
  getElementById(id) {{
    return id === "estado" ? estadoPrueba : formularioPrueba;
  }},
}};
let setSessionCount = 0;
let opcionesCliente = null;
const sesionValida = {{ access_token: "SESION_TEST" }};
globalThis.createClientMock = (_url, _key, opciones) => {{
  opcionesCliente = opciones;
  return {{ auth: {{
    onAuthStateChange(callback) {{
      for (const demora of config.eventDelays || []) {{
        setTimeout(() => callback("PASSWORD_RECOVERY", sesionValida), demora);
      }}
      return {{ data: {{ subscription: {{ unsubscribe() {{}} }} }} }};
    }},
    async getSession() {{
      await new Promise((resolve) => setTimeout(resolve, config.getSessionDelay || 0));
      return {{
        error: null,
        data: {{ session: config.getSessionSuccess ? sesionValida : null }},
      }};
    }},
    async setSession() {{
      setSessionCount += 1;
      return {{
        error: config.setSessionSuccess ? null : {{ message: "fallo" }},
        data: {{ session: config.setSessionSuccess ? sesionValida : null }},
      }};
    }},
    async updateUser() {{ return {{ error: null }}; }},
  }} }};
}};
globalThis.fetch = async () => ({{ ok: true, json: async () => ({{ destino: "/" }}) }});
"""
    salida = """
await new Promise((resolve) => setTimeout(resolve, 180));
process.stdout.write(JSON.stringify({
  hidden: formularioPrueba.hidden,
  message: estadoPrueba.textContent,
  setSessionCount,
  options: opcionesCliente.auth,
}));
"""
    resultado = subprocess.run(
        ["node", "--input-type=module"],
        input=arnes + script + salida,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(resultado.stdout)


@pytest.mark.parametrize(
    ("configuracion", "formulario_visible", "texto_esperado", "set_session"),
    [
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta#type=recovery",
            "eventDelays": [0],
        }, True, "", 0),
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta#type=recovery",
            "eventDelays": [40],
        }, True, "", 0),
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta#type=recovery",
            "getSessionDelay": 40,
            "getSessionSuccess": True,
        }, True, "", 0),
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta#type=recovery",
            "getSessionSuccess": True,
        }, True, "", 0),
        ({
            "url": (
                "https://clicklocal.com.ar/activar-cuenta"
                "#type=recovery&access_token=TEST&refresh_token=TEST"
            ),
            "setSessionSuccess": True,
        }, True, "", 1),
        ({
            "url": (
                "https://clicklocal.com.ar/activar-cuenta"
                "?error=access_denied&error_code=otp_expired"
            ),
        }, False, "Este enlace ya no es válido.", 0),
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta?error=access_denied",
        }, False, "No pudimos validar este enlace", 0),
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta",
            "getSessionSuccess": True,
        }, False, "No encontramos una recuperación", 0),
        ({
            "url": "https://clicklocal.com.ar/activar-cuenta?code=TEST",
        }, False, "tipo de acceso no compatible", 0),
    ],
)
def test_maquina_recovery_en_javascript(
    configuracion, formulario_visible, texto_esperado, set_session
):
    resultado = _ejecutar_recovery_javascript(configuracion)
    assert resultado["hidden"] is (not formulario_visible)
    assert texto_esperado in resultado["message"]
    assert resultado["setSessionCount"] == set_session
    assert resultado["options"] == {
        "detectSessionInUrl": True,
        "flowType": "implicit",
        "persistSession": True,
    }


def _payload_base64url(texto):
    return base64.urlsafe_b64encode(texto.encode()).decode().rstrip("=")


def _ejecutar_acceso_javascript(fragmento, clicks=0):
    with app_module.app.test_request_context("/acceso"):
        html = app_module.render_template(
            "acceso.html",
            csp_nonce="nonce-test",
            supabase_origin=SUPABASE_ORIGIN_FICTICIO,
            activar_cuenta_url=REDIRECT_ACTIVACION,
        )
    script = re.search(
        r'<script nonce="nonce-test">(.*?)</script>', html, re.DOTALL
    ).group(1)
    config_json = json.dumps({"hash": fragmento, "clicks": clicks})
    arnes = f"""
const config = {config_json};
const traza = [];
const navegaciones = [];
let clickHandler = null;
const botonPrueba = {{
  disabled: true,
  addEventListener(tipo, callback) {{
    if (tipo === "click") clickHandler = callback;
  }},
}};
const estadoPrueba = {{ textContent: "Validando acceso…" }};
globalThis.window = {{
  location: {{
    hash: config.hash,
    pathname: "/acceso",
    assign(destino) {{ traza.push("assign"); navegaciones.push(destino); }},
  }},
  history: {{
    replaceState(_estado, _titulo, ruta) {{ traza.push("replace:" + ruta); }},
  }},
  atob(valor) {{ traza.push("decode"); return globalThis.atob(valor); }},
  btoa(valor) {{ return globalThis.btoa(valor); }},
}};
globalThis.document = {{
  addEventListener(tipo, callback) {{
    if (tipo === "DOMContentLoaded") callback();
  }},
  getElementById(id) {{ return id === "continuar" ? botonPrueba : estadoPrueba; }},
}};
"""
    salida = """
const habilitadoTrasValidar = !botonPrueba.disabled;
const navegacionesAntesClick = navegaciones.length;
for (let indice = 0; indice < config.clicks; indice += 1) clickHandler();
process.stdout.write(JSON.stringify({
  trace: traza,
  enabledAfterValidation: habilitadoTrasValidar,
  message: estadoPrueba.textContent,
  navigationsBeforeClick: navegacionesAntesClick,
  navigations: navegaciones,
  disabledAfterClick: botonPrueba.disabled,
}));
"""
    resultado = subprocess.run(
        ["node", "--input-type=module"],
        input=arnes + script + salida,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(resultado.stdout)


def test_acceso_javascript_limpia_antes_de_decodificar_y_exige_click():
    payload = _payload_base64url(ACTION_LINK_FICTICIO)
    resultado = _ejecutar_acceso_javascript(
        f"#v=1&link={payload}", clicks=2
    )
    assert resultado["trace"][0] == "replace:/acceso"
    assert resultado["trace"].index("replace:/acceso") < resultado["trace"].index(
        "decode"
    )
    assert resultado["enabledAfterValidation"] is True
    assert resultado["navigationsBeforeClick"] == 0
    assert resultado["navigations"] == [ACTION_LINK_FICTICIO]
    assert resultado["disabledAfterClick"] is True


@pytest.mark.parametrize("fragmento", [
    "",
    "#v=2&link=AAAA",
    "#v=1&link=***",
    "#v=1&link=" + ("A" * 12001),
    "#v=1&link=" + _payload_base64url(_action_link_ficticio(esquema="http")),
    "#v=1&link=" + _payload_base64url(
        _action_link_ficticio(autoridad="otro.supabase.co")
    ),
    "#v=1&link=" + _payload_base64url(
        _action_link_ficticio(path="/auth/v1/otro")
    ),
    "#v=1&link=" + _payload_base64url(_action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("type", "magiclink"),
        ("redirect_to", REDIRECT_ACTIVACION),
    ])),
    "#v=1&link=" + _payload_base64url(_action_link_ficticio(parametros=[
        ("token", "TOKEN_FICTICIO"), ("type", "recovery"),
        ("redirect_to", "https://clicklocal.com.ar/otro"),
    ])),
])
def test_acceso_javascript_rechaza_wrappers_invalidos(fragmento):
    resultado = _ejecutar_acceso_javascript(fragmento, clicks=1)
    assert resultado["trace"][0] == "replace:/acceso"
    assert resultado["enabledAfterValidation"] is False
    assert resultado["navigations"] == []
    assert "Este enlace de acceso no es válido" in resultado["message"]


def test_acceso_template_no_filtra_credenciales_ni_navega_automaticamente():
    contenido = open("templates/acceso.html", encoding="utf-8").read()
    assert "window.location.hash.slice(1)" in contenido
    assert "window.history.replaceState" in contenido
    assert '<button id="continuar" type="button" disabled>' in contenido
    assert '<a href=' not in contenido
    assert "window.location.assign(destino)" in contenido
    assert 'boton.addEventListener("click"' in contenido
    assert "setTimeout" not in contenido
    assert ".click()" not in contenido
    assert "localStorage" not in contenido
    assert "sessionStorage" not in contenido
    assert "fetch(" not in contenido
    assert "console.log" not in contenido
    assert "console.error" not in contenido
    assert "innerHTML" not in contenido


def test_email_no_usa_puente_y_activar_cuenta_permanece_separado():
    contenido = open("app.py", encoding="utf-8").read()
    bloque_email = contenido.split(
        "def admin_dar_acceso_gastronomia(comercio_id):", 1
    )[1].split("def admin_dar_acceso_gastronomia_whatsapp", 1)[0]
    bloque_whatsapp = contenido.split(
        "def admin_dar_acceso_gastronomia_whatsapp(comercio_id):", 1
    )[1].split("def acceso_intermedio", 1)[0]
    assert "reset_password_for_email" in bloque_email
    assert "/acceso" not in bloque_email
    assert "generate_link" in bloque_whatsapp
    assert "_url_puente_acceso_whatsapp(action_link)" in bloque_whatsapp
    assert 'f"{action_link}' not in bloque_whatsapp
    assert '@app.route("/activar-cuenta", methods=["GET"])' in contenido
    assert '@app.post("/activar-cuenta/completar")' in contenido


def test_templates_nuevos_compilan():
    app_module.app.jinja_env.get_template("admin_gastronomia_nueva.html")
    app_module.app.jinja_env.get_template("activar_cuenta.html")
    app_module.app.jinja_env.get_template("acceso.html")
    app_module.app.jinja_env.get_template("admin_comercios.html")
    admin = open("templates/admin_comercios.html", encoding="utf-8").read()
    assert "Acceso pendiente" not in admin  # La etiqueta llega desde el backend.
    assert "c.estado_acceso" in admin
    assert "Acceso por email" in admin
    assert "Acceso por WhatsApp" in admin
    assert "Restablecer por email" in admin
    assert "Restablecer por WhatsApp" in admin
