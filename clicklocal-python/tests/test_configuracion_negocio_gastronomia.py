from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module


class ConsultaLogoFalsa:
    def __init__(self, db):
        self.db = db
        self.cambios = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def update(self, cambios):
        self.cambios = dict(cambios)
        self.db.actualizaciones.append(self.cambios)
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(self.db.comercio)])


class StorageLogoFalso:
    def __init__(self):
        self.subidas = []

    def from_(self, bucket):
        assert bucket == "publicaciones"
        return self

    def upload(self, ruta, contenido, *args, **kwargs):
        self.subidas.append((ruta, contenido, args, kwargs))

    def get_public_url(self, ruta):
        return {"publicURL": f"https://img.test/{ruta}"}


class DBLogoFalso:
    def __init__(self):
        self.comercio = {
            "id": "comercio-1",
            "user_id": "propietario-1",
            "activo": True,
            "nombre_negocio": "La Esquina",
            "logo_url": None,
        }
        self.actualizaciones = []
        self.storage = StorageLogoFalso()

    def table(self, tabla):
        assert tabla == "comercios"
        return ConsultaLogoFalsa(self)


def _cliente_gestor():
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = "comercio-1"
        sesion["user_id"] = "admin-no-suplanta"
        sesion["comercio"] = {"id": "comercio-1", "logo_url": None}
    return cliente


def test_logo_gastronomico_reutiliza_subida_y_vuelve_a_configuracion():
    db = DBLogoFalso()
    cliente = _cliente_gestor()
    with (
        patch.object(app_module, "supabase_admin", db),
        patch.object(
            app_module,
            "procesar_logo_clicklocal",
            return_value=BytesIO(b"webp"),
        ) as procesar,
    ):
        respuesta = cliente.post(
            "/panel/logo/subir",
            data={
                "retorno": "gastronomia",
                "logo": (BytesIO(b"imagen"), "logo.png"),
            },
            content_type="multipart/form-data",
        )

    assert respuesta.status_code == 302
    assert respuesta.location.endswith(
        "/gastronomia/panel/configuracion?logo_ok=subido#datos-comercio"
    )
    procesar.assert_called_once()
    assert db.storage.subidas
    assert db.actualizaciones[-1]["logo_url"].startswith("https://img.test/logos/")
    with cliente.session_transaction() as sesion:
        assert sesion["user_id"] == "admin-no-suplanta"
        assert sesion["gestor_comercio_id"] == "comercio-1"
        assert sesion["comercio"]["logo_url"].startswith("https://img.test/logos/")


def test_quitar_logo_gastronomico_reutiliza_ruta_y_conserva_gestor():
    db = DBLogoFalso()
    cliente = _cliente_gestor()
    with patch.object(app_module, "supabase_admin", db):
        respuesta = cliente.post(
            "/panel/logo/quitar",
            data={"retorno": "gastronomia"},
        )

    assert respuesta.status_code == 302
    assert respuesta.location.endswith(
        "/gastronomia/panel/configuracion?logo_ok=quitado#datos-comercio"
    )
    assert db.actualizaciones[-1] == {"logo_url": None}
    with cliente.session_transaction() as sesion:
        assert sesion["user_id"] == "admin-no-suplanta"
        assert sesion["gestor_comercio_id"] == "comercio-1"
        assert sesion["comercio"]["logo_url"] is None
