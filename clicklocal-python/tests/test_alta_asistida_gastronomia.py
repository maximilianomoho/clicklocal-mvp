import json
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

import app as app_module


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
        action_link="https://proyecto.supabase.co/auth/v1/verify?token=secreto",
        verification_type="recovery",
        redirect_to="http://localhost/activar-cuenta",
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
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
    with patch("app.supabase_admin", db):
        respuesta = cliente.post(
            "/admin/gastronomia/11111111-1111-1111-1111-111111111111/dar-acceso-whatsapp",
        )

    assert respuesta.status_code == 302
    assert db.llamadas == [{
        "type": "recovery",
        "email": "duena@example.com",
        "options": {
            "redirect_to": "http://localhost/activar-cuenta",
        },
    }]
    destino = urlparse(respuesta.location)
    assert destino.scheme == "https"
    assert destino.netloc == "wa.me"
    assert destino.path == "/5493434000000"
    mensaje = parse_qs(destino.query)["text"][0]
    assert "https://proyecto.supabase.co/auth/v1/verify?token=secreto" in mensaje
    assert "contraseña" in mensaje
    assert "contraseña provisoria" not in mensaje
    assert "password" not in mensaje.lower()
    with cliente.session_transaction() as sesion:
        assert "token=secreto" not in repr(dict(sesion))


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


def test_templates_nuevos_compilan():
    app_module.app.jinja_env.get_template("admin_gastronomia_nueva.html")
    app_module.app.jinja_env.get_template("activar_cuenta.html")
    app_module.app.jinja_env.get_template("admin_comercios.html")
    admin = open("templates/admin_comercios.html", encoding="utf-8").read()
    assert "Acceso pendiente" not in admin  # La etiqueta llega desde el backend.
    assert "c.estado_acceso" in admin
    assert "Acceso por email" in admin
    assert "Acceso por WhatsApp" in admin
    assert "Restablecer por email" in admin
    assert "Restablecer por WhatsApp" in admin
