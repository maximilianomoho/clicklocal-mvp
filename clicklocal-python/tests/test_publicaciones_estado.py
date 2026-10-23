from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module


COMERCIO_ID = "11111111-1111-1111-1111-111111111111"
OTRO_COMERCIO_ID = "22222222-2222-2222-2222-222222222222"
PUBLICACION_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USUARIO_ID = "persona-prueba"
CSRF = "csrf-publicacion-prueba"


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, *_args):
        return self

    def update(self, payload):
        self.payload = dict(payload)
        return self

    def execute(self):
        filas = self.db.datos.get(self.tabla, [])
        coincidentes = [
            fila
            for fila in filas
            if all(fila.get(campo) == valor for campo, valor in self.filtros)
        ]

        if self.payload is not None:
            for fila in coincidentes:
                fila.update(self.payload)
            if coincidentes:
                self.db.actualizaciones.append(
                    (self.tabla, dict(self.payload), list(self.filtros))
                )

        return SimpleNamespace(data=[dict(fila) for fila in coincidentes])


class SupabaseFalso:
    def __init__(
        self,
        *,
        activa=True,
        comercio_publicacion=COMERCIO_ID,
        estado_revision="aprobada",
        ocultada=False,
        activas_adicionales=0,
        plan="gratis",
    ):
        self.actualizaciones = []
        publicaciones = [{
            "id": PUBLICACION_ID,
            "comercio_id": comercio_publicacion,
            "activa": activa,
            "eliminada": False,
            "estado_revision": estado_revision,
            "ocultada_por_moderacion": ocultada,
            "activa_solicitada": activa,
            "pausada_por_limite_plan": False,
        }]
        publicaciones.extend({
            "id": f"publicacion-{indice}",
            "comercio_id": COMERCIO_ID,
            "activa": True,
            "eliminada": False,
        } for indice in range(activas_adicionales))

        self.datos = {
            "comercios": [{
                "id": COMERCIO_ID,
                "user_id": USUARIO_ID,
                "activo": True,
                "plan": plan,
            }],
            "publicaciones": publicaciones,
            "eventos_analytics": [{
                "publicacion_id": PUBLICACION_ID,
                "tipo_evento": "click_whatsapp",
            }],
        }

    def table(self, tabla):
        return ConsultaFalsa(self, tabla)


def cliente_autenticado(*, csrf=CSRF):
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["user_id"] = USUARIO_ID
        sesion["comercio"] = {
            "id": COMERCIO_ID,
            "user_id": USUARIO_ID,
            "activo": True,
        }
        if csrf:
            sesion["csrf_estado_publicacion"] = csrf
    return cliente


def cambiar_estado(db, accion, *, csrf=CSRF):
    eventos_antes = [dict(fila) for fila in db.datos["eventos_analytics"]]
    with (
        patch("app.supabase_admin", db),
        patch("app.invalidar_cache_publicaciones_portada") as invalidar_cache,
    ):
        respuesta = cliente_autenticado(csrf=csrf).post(
            f"/publicacion/{PUBLICACION_ID}/estado",
            data={"accion": accion, "csrf_token": csrf or ""},
        )
    assert db.datos["eventos_analytics"] == eventos_antes
    return respuesta, invalidar_cache


def test_propietario_puede_pausar_y_conserva_metricas():
    db = SupabaseFalso(activa=True)

    respuesta, invalidar_cache = cambiar_estado(db, "pausar")

    assert respuesta.status_code == 302
    assert "publicacion_estado=pausada" in respuesta.location
    assert db.datos["publicaciones"][0]["activa"] is False
    assert db.datos["publicaciones"][0]["activa_solicitada"] is False
    invalidar_cache.assert_called_once_with()


def test_propietario_puede_reactivar_publicacion_aprobada():
    db = SupabaseFalso(activa=False)

    respuesta, invalidar_cache = cambiar_estado(db, "activar")

    assert respuesta.status_code == 302
    assert "publicacion_estado=activa" in respuesta.location
    assert db.datos["publicaciones"][0]["activa"] is True
    assert db.datos["publicaciones"][0]["activa_solicitada"] is True
    invalidar_cache.assert_called_once_with()


def test_no_permite_modificar_publicacion_de_otro_comercio():
    db = SupabaseFalso(
        activa=True,
        comercio_publicacion=OTRO_COMERCIO_ID,
    )

    respuesta, invalidar_cache = cambiar_estado(db, "pausar")

    assert respuesta.status_code == 302
    assert "publicacion_estado_error=no_encontrada" in respuesta.location
    assert db.datos["publicaciones"][0]["activa"] is True
    assert db.actualizaciones == []
    invalidar_cache.assert_not_called()


def test_rechaza_csrf_ausente_o_incorrecto():
    db = SupabaseFalso(activa=True)
    cliente = cliente_autenticado()

    with patch("app.supabase_admin", db):
        sin_token = cliente.post(
            f"/publicacion/{PUBLICACION_ID}/estado",
            data={"accion": "pausar"},
        )
        token_incorrecto = cliente.post(
            f"/publicacion/{PUBLICACION_ID}/estado",
            data={"accion": "pausar", "csrf_token": "otro-token"},
        )

    assert sin_token.status_code == 400
    assert token_incorrecto.status_code == 400
    assert db.actualizaciones == []


def test_requiere_comercio_autenticado():
    db = SupabaseFalso(activa=True)

    with patch("app.supabase_admin", db):
        respuesta = app_module.app.test_client().post(
            f"/publicacion/{PUBLICACION_ID}/estado",
            data={"accion": "pausar", "csrf_token": CSRF},
        )

    assert respuesta.status_code == 302
    assert respuesta.location.endswith(("/login", "/login.html"))
    assert db.actualizaciones == []


def test_reactivar_respeta_ocultamiento_por_moderacion():
    db = SupabaseFalso(
        activa=False,
        estado_revision="oculta",
        ocultada=True,
    )

    respuesta, invalidar_cache = cambiar_estado(db, "activar")

    assert respuesta.status_code == 302
    assert "publicacion_estado_error=moderacion" in respuesta.location
    assert db.datos["publicaciones"][0]["activa"] is False
    assert db.actualizaciones == []
    invalidar_cache.assert_not_called()


def test_reactivar_respeta_limite_del_plan():
    db = SupabaseFalso(
        activa=False,
        activas_adicionales=30,
        plan="gratis",
    )

    respuesta, invalidar_cache = cambiar_estado(db, "activar")

    assert respuesta.status_code == 302
    assert "publicacion_estado_error=limite_plan" in respuesta.location
    assert db.datos["publicaciones"][0]["activa"] is False
    assert db.actualizaciones == []
    invalidar_cache.assert_not_called()


def test_panel_presenta_accion_post_y_aclara_metricas_whatsapp():
    plantilla = (
        Path(app_module.app.root_path) / "templates" / "panel.html"
    ).read_text(encoding="utf-8")

    assert "url_for('cambiar_estado_publicacion'" in plantilla
    assert 'name="csrf_token"' in plantilla
    assert 'name="accion"' in plantilla
    assert 'value="{{ \'pausar\' if pub.activa else \'activar\' }}"' in plantilla
    assert "Tasa de clics a WhatsApp" in plantilla
    assert "Los clics no garantizan mensajes enviados" in plantilla
    assert "de conversión" not in plantilla
