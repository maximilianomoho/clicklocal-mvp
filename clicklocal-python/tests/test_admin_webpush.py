import json
from types import SimpleNamespace

import app as app_module
import push_notifications as push


SUSCRIPCION_VALIDA = {
    "endpoint": "https://push.example.test/device-1",
    "keys": {
        "p256dh": "p" * 32,
        "auth": "a" * 16,
    },
}


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.operacion = "select"
        self.payload = None
        self.filtros = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self.operacion, self.payload = "insert", payload
        return self

    def upsert(self, payload, **kwargs):
        self.operacion, self.payload = "upsert", payload
        return self

    def update(self, payload):
        self.operacion, self.payload = "update", payload
        return self

    def execute(self):
        self.db.operaciones.append(self)
        if self.db.error_push_table and self.tabla == push.TABLA_SUSCRIPCIONES_ADMIN:
            raise RuntimeError("push caído")
        if self.operacion == "insert":
            return SimpleNamespace(data=[{"id": "consulta-1", **self.payload}])
        if self.tabla == "comercios":
            return SimpleNamespace(data=[self.db.comercio])
        if self.tabla == "consultas_soporte":
            return SimpleNamespace(data=self.db.pendientes)
        if self.tabla == push.TABLA_SUSCRIPCIONES_ADMIN and self.operacion == "select":
            return SimpleNamespace(data=self.db.suscripciones)
        return SimpleNamespace(data=[self.payload] if self.payload else [])


class SupabaseFalso:
    def __init__(self, pendientes=None, suscripciones=None, error_push_table=False):
        self.pendientes = pendientes or []
        self.suscripciones = suscripciones or []
        self.error_push_table = error_push_table
        self.comercio = {
            "id": "comercio-1",
            "user_id": "user-1",
            "nombre_negocio": "La casa del sombrero",
        }
        self.operaciones = []

    def table(self, tabla):
        return ConsultaFalsa(self, tabla)


def configurar_vapid(monkeypatch):
    monkeypatch.setenv("WEBPUSH_VAPID_PUBLIC_KEY", "publica")
    monkeypatch.setenv("WEBPUSH_VAPID_PRIVATE_KEY", "privada")
    monkeypatch.setenv("WEBPUSH_VAPID_SUBJECT", "mailto:admin@example.test")


def test_sin_claves_vapid_no_rompe(monkeypatch):
    for clave in (
        "WEBPUSH_VAPID_PUBLIC_KEY",
        "WEBPUSH_VAPID_PRIVATE_KEY",
        "WEBPUSH_VAPID_SUBJECT",
    ):
        monkeypatch.delenv(clave, raising=False)
    llamado = []
    resultado = push.enviar_notificacion_admin(
        SupabaseFalso(), "Título", "Cuerpo", webpush_func=lambda **kwargs: llamado.append(kwargs)
    )
    assert resultado["disponible"] is False
    assert llamado == []


def test_suscripcion_valida_se_acepta(monkeypatch):
    configurar_vapid(monkeypatch)
    db = SupabaseFalso()
    push.guardar_suscripcion_admin(db, "admin", SUSCRIPCION_VALIDA)
    operacion = db.operaciones[-1]
    assert operacion.operacion == "upsert"
    assert operacion.payload["admin_user"] == "admin"
    assert operacion.payload["activo"] is True


def test_suscripcion_invalida_se_rechaza():
    assert push.validar_suscripcion({"endpoint": "javascript:alert(1)"}) is None


def test_no_admin_no_puede_suscribirse(monkeypatch):
    configurar_vapid(monkeypatch)
    cliente = app_module.app.test_client()
    respuesta = cliente.post("/admin/push/suscribir", json=SUSCRIPCION_VALIDA)
    assert respuesta.status_code == 302
    assert "/admin/login" in respuesta.headers["Location"]


def _ejecutar_solicitud(monkeypatch, db, push_mock):
    monkeypatch.setattr(app_module, "supabase_admin", db)
    monkeypatch.setattr(app_module, "_user_id_panel_efectivo", lambda: "user-1")
    monkeypatch.setattr(app_module, "modulo_asignado", lambda *args: False)
    monkeypatch.setattr(app_module, "enviar_notificacion_admin", push_mock)
    with app_module.app.test_request_context(
        "/panel/modulos/turnos/solicitar-instalacion", method="POST"
    ):
        app_module.session["comercio"] = db.comercio
        return app_module.solicitar_instalacion_modulo_panel("turnos")


def test_solicitud_nueva_intenta_un_solo_push(monkeypatch):
    db = SupabaseFalso()
    pushes = []
    _ejecutar_solicitud(monkeypatch, db, lambda *args: pushes.append(args))
    inserts = [op for op in db.operaciones if op.tabla == "consultas_soporte" and op.operacion == "insert"]
    assert len(inserts) == 1
    assert len(pushes) == 1
    assert pushes[0][1:] == (
        "Nueva solicitud de módulo",
        "La casa del sombrero pidió Gestión de turnos",
    )


def test_solicitud_duplicada_no_inserta_ni_envia_push(monkeypatch):
    db = SupabaseFalso(pendientes=[{"id": "existente"}])
    pushes = []
    _ejecutar_solicitud(monkeypatch, db, lambda *args: pushes.append(args))
    assert not any(op.operacion == "insert" for op in db.operaciones)
    assert pushes == []


def test_fallo_push_no_revierte_solicitud(monkeypatch):
    db = SupabaseFalso()

    def push_fallido(*args):
        raise RuntimeError("falló push")

    respuesta = _ejecutar_solicitud(monkeypatch, db, push_fallido)
    assert respuesta.status_code == 302
    assert any(op.operacion == "insert" for op in db.operaciones)


def test_endpoint_404_y_410_desactivan_suscripcion(monkeypatch):
    configurar_vapid(monkeypatch)
    db = SupabaseFalso(suscripciones=[{
        "id": "push-1",
        "endpoint": SUSCRIPCION_VALIDA["endpoint"],
        "p256dh": SUSCRIPCION_VALIDA["keys"]["p256dh"],
        "auth": SUSCRIPCION_VALIDA["keys"]["auth"],
    }])

    for codigo in (404, 410):
        class ErrorSuscripcion(Exception):
            response = SimpleNamespace(status_code=codigo)

        resultado = push.enviar_notificacion_admin(
            db,
            "Título",
            "Cuerpo",
            webpush_func=lambda **kwargs: (_ for _ in ()).throw(ErrorSuscripcion()),
        )
        actualizaciones = [op for op in db.operaciones if op.operacion == "update"]
        assert resultado["errores"] == 1
        assert actualizaciones[-1].payload["activo"] is False


def test_service_worker_conserva_eventos_y_agrega_push():
    contenido = open("static/sw.js", encoding="utf-8").read()
    for evento in ("install", "activate", "fetch", "push", "notificationclick"):
        assert f'addEventListener("{evento}"' in contenido


def test_payload_push_es_json(monkeypatch):
    configurar_vapid(monkeypatch)
    db = SupabaseFalso(suscripciones=[{
        "id": "push-1",
        "endpoint": SUSCRIPCION_VALIDA["endpoint"],
        "p256dh": SUSCRIPCION_VALIDA["keys"]["p256dh"],
        "auth": SUSCRIPCION_VALIDA["keys"]["auth"],
    }])
    llamadas = []
    push.enviar_notificacion_admin(db, "Título", "Cuerpo", webpush_func=lambda **kw: llamadas.append(kw))
    assert json.loads(llamadas[0]["data"])["url"] == "/admin"
