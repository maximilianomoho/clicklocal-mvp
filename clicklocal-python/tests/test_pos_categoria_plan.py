from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from gastronomia.routes import contexto_modo_gestor_gastronomia
from modulos import CATALOGO_MODULOS, modulo_disponible_para_comercio


COMERCIO_ID = "11111111-1111-4111-8111-111111111111"


class ConsultaComercioFalsa:
    def __init__(self, comercio):
        self.comercio = comercio

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(self.comercio)])


class DBComercioFalsa:
    def __init__(self, categoria):
        self.comercio = {
            "id": COMERCIO_ID,
            "user_id": "user-1",
            "categoria": categoria,
            "nombre_negocio": "Comercio de prueba",
        }

    def table(self, tabla):
        assert tabla == "comercios"
        return ConsultaComercioFalsa(self.comercio)


def test_plan_gastronomico_usa_estado_pos_compartido():
    with app_module.app.test_request_context("/"):
        app_module.session["comercio"] = {"id": COMERCIO_ID}
        with patch(
            "gastronomia.routes._pos_activo_gastronomia",
            return_value=False,
        ):
            assert contexto_modo_gestor_gastronomia()["plan_nombre_panel"] == (
                "Gastronomía Base"
            )

        with patch(
            "gastronomia.routes._pos_activo_gastronomia",
            return_value=True,
        ):
            assert contexto_modo_gestor_gastronomia()["plan_nombre_panel"] == (
                "Gastronomía POS"
            )


def test_pos_solo_es_visible_para_categoria_gastronomia_y_turnos_no_cambia():
    assert CATALOGO_MODULOS["pos"]["nombre"] == "Gastronomía POS"
    assert modulo_disponible_para_comercio(
        "pos", {"categoria": "Gastronomía"}
    )
    assert modulo_disponible_para_comercio(
        "pos", {"categoria": "Gastronomia"}
    )
    assert not modulo_disponible_para_comercio(
        "pos", {"categoria": "Indumentaria"}
    )
    assert modulo_disponible_para_comercio(
        "turnos", {"categoria": "Indumentaria"}
    )


def test_comercio_no_gastronomico_no_puede_solicitar_pos():
    db = DBComercioFalsa("Indumentaria")
    with (
        app_module.app.test_request_context(
            "/panel/modulos/pos/solicitar-instalacion",
            method="POST",
        ),
        patch.object(app_module, "_user_id_panel_efectivo", return_value="user-1"),
        patch.object(app_module, "supabase_admin", db),
        patch.object(app_module, "evaluar_vigencia_modulo") as evaluar,
    ):
        respuesta = app_module.solicitar_instalacion_modulo_panel("pos")

    assert respuesta.status_code == 302
    assert "modulo_solicitud_error=modulo_no_disponible" in respuesta.location
    evaluar.assert_not_called()


def _instalar_pos_admin(categoria):
    db = DBComercioFalsa(categoria)
    with (
        app_module.app.test_request_context(
            f"/admin/comercios/{COMERCIO_ID}/modulos/pos/instalar",
            method="POST",
            data={"duracion_meses": "1"},
        ),
        patch.object(app_module, "supabase_admin", db),
        patch.object(app_module, "instalar_modulo", return_value=True) as instalar,
    ):
        app_module.session["admin_logueado"] = True
        app_module.session["admin_user"] = "admin"
        respuesta = app_module.admin_instalar_modulo(COMERCIO_ID, "pos")
    return respuesta, instalar


def test_admin_no_puede_instalar_pos_a_comercio_no_gastronomico():
    respuesta, instalar = _instalar_pos_admin("Servicios")
    assert respuesta.status_code == 302
    assert "modulo_error=modulo_no_disponible" in respuesta.location
    instalar.assert_not_called()


def test_admin_puede_instalar_pos_a_comercio_gastronomico():
    respuesta, instalar = _instalar_pos_admin("Gastronomía")
    assert respuesta.status_code == 302
    assert "modulo_estado=instalado" in respuesta.location
    instalar.assert_called_once_with(COMERCIO_ID, "pos", 1)
