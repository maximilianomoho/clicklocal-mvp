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
    monkeypatch.setattr(app_module, "modulo_asignado", lambda *args: asignado)
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
    modulo = {"id": "1", "origen": "catalogo_modulos"}
    soporte = {"id": "2", "origen": "contacto"}
    solicitudes, consultas = app_module._separar_solicitudes_modulos([modulo, soporte])
    assert solicitudes == [modulo]
    assert consultas == [soporte]


def test_catalogo_distingue_modulo_asignado(monkeypatch):
    monkeypatch.setattr(
        modulos,
        "obtener_estados_modulos",
        lambda comercio_id: {"turnos": False},
    )
    catalogo = modulos.combinar_catalogo_con_estado("comercio-1")
    assert catalogo[0]["asignado"] is True
    assert catalogo[0]["activo"] is False


def test_admin_reutiliza_accion_existente_para_resolver():
    contenido = open("templates/admin.html", encoding="utf-8").read()
    assert "Solicitudes de Módulos / Verticales" in contenido
    assert "admin_resolver_consulta_soporte" in contenido
    assert "admin_instalar_modulo" not in contenido


def test_templates_panel_y_admin_compilan():
    app_module.app.jinja_env.get_template("panel.html")
    app_module.app.jinja_env.get_template("admin.html")
