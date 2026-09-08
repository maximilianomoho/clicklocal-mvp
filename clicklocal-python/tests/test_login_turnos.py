from types import SimpleNamespace
from unittest.mock import patch

from app import app


class ConsultaComercioFalsa:
    def __init__(self, comercio):
        self.comercio = comercio

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.comercio)


class SupabaseFalso:
    def __init__(self, comercio):
        self.comercio = comercio

    def table(self, _):
        return ConsultaComercioFalsa(self.comercio)


def autenticar(categoria, next_destino=""):
    auth = SimpleNamespace(
        auth=SimpleNamespace(
            sign_in_with_password=lambda _: SimpleNamespace(
                user=SimpleNamespace(id="usuario-1")
            )
        )
    )
    comercio = {
        "id": "comercio-1",
        "activo": True,
        "categoria": categoria,
    }
    with (
        patch("app.supabase_auth", auth),
        patch("app.supabase_admin", SupabaseFalso(comercio)),
    ):
        return app.test_client().post(
            "/login",
            data={
                "email": "test@example.com",
                "password": "clave",
                "next": next_destino,
            },
        )


def test_login_con_destino_turnos_vuelve_a_la_agenda():
    respuesta = autenticar("Comercio", "/turnos/agenda")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/turnos/agenda")


def test_login_gastronomico_con_destino_turnos_vuelve_a_la_agenda():
    respuesta = autenticar("Gastronomía", "/turnos/agenda")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/turnos/agenda")


def test_login_descarta_next_externo_y_conserva_panel_general():
    respuesta = autenticar("Comercio", "https://sitio-malicioso.test/")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith(("/panel", "/panel.html"))


def test_login_sin_next_conserva_destino_gastronomia_de_head():
    respuesta = autenticar("Gastronomía")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/gastronomia/configuracion-inicial")


def test_login_get_preserva_unicamente_next_turnos():
    cliente = app.test_client()
    respuesta_valida = cliente.get("/login?next=/turnos/agenda")
    respuesta_invalida = cliente.get(
        "/login?next=https://sitio-malicioso.test/"
    )

    assert b'name="next" value="/turnos/agenda"' in respuesta_valida.data
    assert b"sitio-malicioso.test" not in respuesta_invalida.data
