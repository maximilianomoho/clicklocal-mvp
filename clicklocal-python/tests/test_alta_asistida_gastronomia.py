from types import SimpleNamespace
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


def test_templates_nuevos_compilan():
    app_module.app.jinja_env.get_template("admin_gastronomia_nueva.html")
    app_module.app.jinja_env.get_template("activar_cuenta.html")
    app_module.app.jinja_env.get_template("admin_comercios.html")
    admin = open("templates/admin_comercios.html", encoding="utf-8").read()
    assert "Acceso pendiente" not in admin  # La etiqueta llega desde el backend.
    assert "c.estado_acceso" in admin
    assert "Restablecer acceso" in admin
    assert "Dar acceso" in admin
