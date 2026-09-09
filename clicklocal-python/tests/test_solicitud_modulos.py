from types import SimpleNamespace

import app as app_module
import modulos


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
        self.operacion = "insert"
        self.payload = payload
        return self

    def execute(self):
        self.db.operaciones.append(self)
        if self.tabla == "comercios":
            return SimpleNamespace(data=[self.db.comercio])
        if self.tabla == "consultas_soporte" and self.operacion == "select":
            return SimpleNamespace(data=self.db.pendientes)
        if self.operacion == "insert":
            return SimpleNamespace(data=[{"id": "solicitud-1", **self.payload}])
        return SimpleNamespace(data=[])


class SupabaseFalso:
    def __init__(self, pendientes=None):
        self.pendientes = pendientes or []
        self.comercio = {
            "id": "comercio-1",
            "user_id": "user-1",
            "nombre_negocio": "Comercio de prueba",
            "email": "comercio@example.test",
            "whatsapp": "3434000000",
        }
        self.operaciones = []

    def table(self, tabla):
        return ConsultaFalsa(self, tabla)


def ejecutar_solicitud(monkeypatch, db, asignado=False):
    monkeypatch.setattr(app_module, "supabase_admin", db)
    monkeypatch.setattr(app_module, "_user_id_panel_efectivo", lambda: "user-1")
    monkeypatch.setattr(
        app_module,
        "evaluar_vigencia_modulo",
        lambda *args: {"existe": asignado},
    )
    with app_module.app.test_request_context(
        "/panel/modulos/turnos/solicitar-instalacion",
        method="POST",
    ):
        app_module.session["comercio"] = db.comercio
        return app_module.solicitar_instalacion_modulo_panel("turnos")


def test_solicitud_nueva_crea_consulta_soporte(monkeypatch):
    db = SupabaseFalso()
    respuesta = ejecutar_solicitud(monkeypatch, db)
    inserciones = [op for op in db.operaciones if op.operacion == "insert"]
    assert respuesta.status_code == 302
    assert len(inserciones) == 1
    assert inserciones[0].payload["origen"] == "catalogo_modulos"
    assert inserciones[0].payload["estado"] == "pendiente"


def test_solicitud_pendiente_no_se_duplica(monkeypatch):
    db = SupabaseFalso(pendientes=[{"id": "existente"}])
    ejecutar_solicitud(monkeypatch, db)
    assert not any(op.operacion == "insert" for op in db.operaciones)


def test_modulo_asignado_no_permite_solicitud(monkeypatch):
    db = SupabaseFalso()
    respuesta = ejecutar_solicitud(monkeypatch, db, asignado=True)
    assert "modulo_solicitud_error=ya_instalado" in respuesta.location
    assert not any(op.tabla == "consultas_soporte" for op in db.operaciones)


def test_solicitudes_modulos_se_excluyen_de_soporte_general():
    contenido_admin = open("templates/admin.html", encoding="utf-8").read()
    contenido_modulos = open(
        "templates/admin_modulos.html",
        encoding="utf-8",
    ).read()
    assert "admin_instalar_modulo_solicitado" not in contenido_admin
    assert "admin_instalar_modulo_solicitado" in contenido_modulos


def test_admin_usa_accion_activar_y_resolver():
    contenido = open("templates/admin.html", encoding="utf-8").read()
    contenido_modulos = open(
        "templates/admin_modulos.html",
        encoding="utf-8",
    ).read()
    assert "Activar y resolver" in contenido_modulos
    assert "admin_instalar_modulo_solicitado" in contenido_modulos
    assert "<th>Módulos</th>" not in contenido
    assert "admin_toggle_modulo" not in contenido
    assert "admin_instalar_modulo" not in contenido


def test_templates_panel_y_admin_compilan():
    app_module.app.jinja_env.get_template("panel.html")
    app_module.app.jinja_env.get_template("admin.html")
    app_module.app.jinja_env.get_template("admin_modulos.html")


class ConsultaResolucionFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.operacion = "select"

    def select(self, *args, **kwargs):
        return self

    def update(self, payload):
        self.operacion = "update"
        self.db.actualizacion = payload
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.tabla == "consultas_soporte" and self.operacion == "select":
            return SimpleNamespace(data=[self.db.solicitud])
        if self.tabla == "comercios":
            return SimpleNamespace(data=[{"id": self.db.comercio_id}])
        if self.tabla == "consultas_soporte" and self.operacion == "update":
            self.db.resuelta = True
            return SimpleNamespace(data=[{"id": self.db.solicitud["id"]}])
        return SimpleNamespace(data=[])


class SupabaseResolucionFalso:
    def __init__(self):
        self.comercio_id = "11111111-1111-1111-1111-111111111111"
        self.solicitud = {
            "id": "22222222-2222-2222-2222-222222222222",
            "estado": "pendiente",
            "origen": "catalogo_modulos",
            "motivo": "Activación de Gestión de turnos",
            "comercio_id": self.comercio_id,
        }
        self.resuelta = False
        self.actualizacion = None

    def table(self, tabla):
        return ConsultaResolucionFalsa(self, tabla)


def ejecutar_activacion_solicitada(monkeypatch, vigencias):
    db = SupabaseResolucionFalso()
    llamadas = {"instalar": 0, "reactivar": 0, "renovar": 0}
    secuencia = iter(vigencias)
    monkeypatch.setattr(app_module, "supabase_admin", db)
    monkeypatch.setattr(
        app_module,
        "evaluar_vigencia_modulo",
        lambda *args: next(secuencia),
    )
    monkeypatch.setattr(
        app_module,
        "instalar_modulo",
        lambda *args: llamadas.__setitem__("instalar", llamadas["instalar"] + 1),
    )
    monkeypatch.setattr(
        app_module,
        "cambiar_activo_modulo",
        lambda *args: llamadas.__setitem__("reactivar", llamadas["reactivar"] + 1),
    )
    monkeypatch.setattr(
        app_module,
        "activar_renovar_modulo",
        lambda *args: llamadas.__setitem__("renovar", llamadas["renovar"] + 1),
    )
    with app_module.app.test_request_context(
        "/admin/solicitudes-modulos/22222222-2222-2222-2222-222222222222/instalar",
        method="POST",
        data={"duracion_meses": "1"},
    ):
        app_module.session["admin_logueado"] = True
        app_module.session["admin_user"] = "admin"
        respuesta = app_module.admin_instalar_modulo_solicitado(
            db.solicitud["id"]
        )
    return db, llamadas, respuesta


VIGENCIA_ACTIVA = {
    "existe": True,
    "habilitado_manual": True,
    "acceso_operativo": True,
    "estado_vigencia": "activo",
}


def test_activar_y_resolver_instala_modulo_no_instalado(monkeypatch):
    db, llamadas, _ = ejecutar_activacion_solicitada(
        monkeypatch,
        [{"existe": False}, VIGENCIA_ACTIVA],
    )
    assert llamadas == {"instalar": 1, "reactivar": 0, "renovar": 0}
    assert db.resuelta is True


def test_activar_y_resolver_reactiva_modulo_inactivo(monkeypatch):
    db, llamadas, _ = ejecutar_activacion_solicitada(
        monkeypatch,
        [{
            "existe": True,
            "habilitado_manual": False,
            "estado_vigencia": "activo",
        }, VIGENCIA_ACTIVA],
    )
    assert llamadas == {"instalar": 0, "reactivar": 1, "renovar": 0}
    assert db.resuelta is True


def test_activar_y_resolver_renueva_y_reactiva_modulo_vencido(monkeypatch):
    db, llamadas, _ = ejecutar_activacion_solicitada(
        monkeypatch,
        [{
            "existe": True,
            "habilitado_manual": False,
            "estado_vigencia": "vencido",
        }, VIGENCIA_ACTIVA],
    )
    assert llamadas == {"instalar": 0, "reactivar": 1, "renovar": 1}
    assert db.resuelta is True


def test_activar_y_resolver_es_idempotente_si_ya_esta_activo(monkeypatch):
    db, llamadas, _ = ejecutar_activacion_solicitada(
        monkeypatch,
        [VIGENCIA_ACTIVA, VIGENCIA_ACTIVA],
    )
    assert llamadas == {"instalar": 0, "reactivar": 0, "renovar": 0}
    assert db.resuelta is True


def test_activar_y_resolver_no_resuelve_si_activacion_falla(monkeypatch):
    db, _, respuesta = ejecutar_activacion_solicitada(
        monkeypatch,
        [{"existe": False}, {
            "existe": True,
            "habilitado_manual": False,
            "acceso_operativo": False,
        }],
    )
    assert db.resuelta is False
    assert "activacion_incompleta" in respuesta.location


class ConsultaModulosAdminFalsa:
    def __init__(self, tabla):
        self.tabla = tabla

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        if self.tabla == "comercios":
            return SimpleNamespace(data=[{
                "id": "comercio-sin-modulos",
                "nombre_negocio": "Sin Turnos",
                "categoria": "Servicios",
                "whatsapp": "-",
            }])
        return SimpleNamespace(data=[])


class SupabaseModulosAdminFalso:
    def table(self, tabla):
        return ConsultaModulosAdminFalsa(tabla)


def test_admin_modulos_incluye_comercio_sin_modulos(monkeypatch):
    contexto = {}
    monkeypatch.setattr(
        app_module,
        "supabase_admin",
        SupabaseModulosAdminFalso(),
    )

    def render_falso(template, **kwargs):
        contexto.update(kwargs)
        return template

    monkeypatch.setattr(app_module, "render_template", render_falso)
    with app_module.app.test_request_context("/admin/modulos"):
        app_module.session["admin_logueado"] = True
        respuesta = app_module.admin_modulos()

    assert respuesta == "admin_modulos.html"
    filas = contexto["modulos_comercios"]
    assert any(
        fila["comercio_id"] == "comercio-sin-modulos"
        and fila["slug"] == "turnos"
        and fila["etiqueta_estado"] == "No instalado"
        for fila in filas
    )
