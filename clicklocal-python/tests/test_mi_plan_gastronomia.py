from types import SimpleNamespace
from unittest.mock import patch

import app as app_module


COMERCIO = {
    "id": "comercio-1",
    "user_id": "propietario-1",
    "nombre_negocio": "Restaurante Prueba",
    "categoria": "Gastronomía",
}


class ConsultaConfiguracionFalsa:
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=[{
            "comercio_id": "comercio-1",
            "activo": True,
            "acepta_delivery": True,
            "acepta_retiro": True,
            "pedido_minimo": 0,
            "costo_envio": 0,
            "tiempo_estimado_min": 30,
            "descuento_efectivo_pct": 0,
            "descuento_transferencia_pct": 0,
            "delivery_distancia_activo": False,
            "delivery_franjas": [],
            "delivery_origen_direccion": None,
            "delivery_origen_latitud": None,
            "delivery_origen_longitud": None,
        }])


class DBConfiguracionFalsa:
    def table(self, tabla):
        assert tabla == "gastronomia_configuracion"
        return ConsultaConfiguracionFalsa()


class ConsultaSolicitudFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.operacion = "select"
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self.operacion = "insert"
        self.payload = payload
        return self

    def execute(self):
        if self.tabla == "comercios":
            return SimpleNamespace(data=[dict(COMERCIO)])
        if self.tabla == "consultas_soporte" and self.operacion == "select":
            return SimpleNamespace(data=[])
        if self.operacion == "insert":
            self.db.solicitud = dict(self.payload)
            return SimpleNamespace(data=[{"id": "solicitud-1", **self.payload}])
        return SimpleNamespace(data=[])


class DBSolicitudFalsa:
    def __init__(self):
        self.solicitud = None

    def table(self, tabla):
        return ConsultaSolicitudFalsa(self, tabla)


def _abrir_plan(pos_activo):
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["user_id"] = "admin-user"
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = "comercio-1"
        sesion["comercio"] = dict(COMERCIO)

    with (
        patch("gastronomia.routes._comercio_panel_gastronomia", return_value=dict(COMERCIO)),
        patch("gastronomia.routes.supabase_admin", DBConfiguracionFalsa()),
        patch("gastronomia.routes._pos_activo_gastronomia", return_value=pos_activo),
    ):
        respuesta = cliente.get("/gastronomia/panel/plan")

    return cliente, respuesta


def test_mi_plan_sin_pos_muestra_base_y_solicitud_sin_turnos():
    _cliente, respuesta = _abrir_plan(False)
    html = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Gastronomía Base" in html
    assert "Gastronomía POS" in html
    assert "Solicitar activación" in html
    assert "/panel/modulos/pos/solicitar-instalacion" in html
    assert ">Ver<" in html
    assert "Gestión de turnos" not in html


def test_mi_plan_con_pos_muestra_activo_sin_solicitud():
    _cliente, respuesta = _abrir_plan(True)
    html = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Gastronomía POS está activo" in html
    assert "Solicitar activación" not in html
    assert "Gestión de turnos" not in html


def test_mi_plan_modo_gestor_conserva_identidad_y_comercio():
    cliente, respuesta = _abrir_plan(False)

    assert respuesta.status_code == 200
    with cliente.session_transaction() as sesion:
        assert sesion["user_id"] == "admin-user"
        assert sesion["gestor_comercio_id"] == "comercio-1"


def test_solicitud_pos_reutiliza_consultas_soporte_y_vuelve_a_mi_plan():
    db = DBSolicitudFalsa()
    with (
        app_module.app.test_request_context(
            "/panel/modulos/pos/solicitar-instalacion",
            method="POST",
            data={"retorno": "gastronomia"},
        ),
        patch.object(app_module, "_user_id_panel_efectivo", return_value="propietario-1"),
        patch.object(app_module, "supabase_admin", db),
        patch.object(app_module, "evaluar_vigencia_modulo", return_value={"existe": False}),
        patch.object(app_module, "enviar_notificacion_admin"),
    ):
        app_module.session["comercio"] = dict(COMERCIO)
        respuesta = app_module.solicitar_instalacion_modulo_panel("pos")

    assert db.solicitud["origen"] == "catalogo_modulos"
    assert db.solicitud["estado"] == "pendiente"
    assert db.solicitud["motivo"] == "Activación de Gastronomía POS"
    assert respuesta.location.endswith(
        "/gastronomia/panel/plan?modulo_solicitud=enviada"
    )


def test_mi_plan_usa_admin_modulos_y_aprobacion_existentes():
    plantilla = open("templates/admin_modulos.html", encoding="utf-8").read()
    assert "Solicitudes pendientes" in plantilla
    assert "admin_instalar_modulo_solicitado" in plantilla
    assert "/admin/solicitudes-modulos/<consulta_id>/instalar" in open(
        "app.py", encoding="utf-8"
    ).read()


def test_template_mi_plan_compila():
    app_module.app.jinja_env.get_template("gastronomia/plan.html")
