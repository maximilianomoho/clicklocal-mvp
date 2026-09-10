from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from flask import Flask, session

import modulos
import turnos.routes as routes
from turnos import turnos_bp
from whatsapp import construir_url_whatsapp


class SupabaseVacio:
    def __init__(self, datos=None):
        self.datos = [] if datos is None else datos

    def table(self, *args, **kwargs):
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.datos)


class SupabasePorTabla:
    def __init__(self, datos_por_tabla):
        self.datos_por_tabla = datos_por_tabla
        self.tabla = ""

    def table(self, nombre):
        self.tabla = nombre
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.datos_por_tabla.get(self.tabla, []))


def aplicacion_prueba():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(turnos_bp)

    @app.route("/login")
    def login():
        return "login"

    @app.route("/comercio/<comercio_id>", endpoint="perfil_comercio")
    def perfil_comercio(comercio_id):
        return comercio_id

    return app


def vigencia(**cambios):
    datos = {
        "existe": True,
        "habilitado_manual": True,
        "estado_vigencia": "activo",
        "fecha_activacion": date(2026, 9, 1),
        "fecha_vencimiento": date(2026, 10, 1),
        "aviso_dias_antes": 5,
        "gracia_dias": 5,
        "inicio_aviso": date(2026, 9, 26),
        "dias_restantes": 21,
        "fecha_fin_gracia": date(2026, 10, 6),
        "dias_gracia_restantes": None,
        "acceso_operativo": True,
        "acceso_publico": True,
        "motivo_bloqueo": None,
    }
    datos.update(cambios)
    return datos


def ejecutar_agenda(
    monkeypatch,
    estado,
    whatsapp="",
    ruta="/turnos/agenda",
):
    contexto = {}

    def render_falso(nombre, **datos):
        contexto.update(datos)
        contexto["template"] = nombre
        return contexto

    monkeypatch.setattr(routes, "supabase_admin", SupabaseVacio())
    monkeypatch.setattr(
        routes,
        "evaluar_vigencia_modulo",
        lambda comercio_id, slug: estado,
    )
    monkeypatch.setattr(routes, "render_template", render_falso)
    monkeypatch.setattr(routes, "CLICKLOCAL_WHATSAPP", whatsapp)

    app = aplicacion_prueba()
    with app.test_request_context(ruta):
        session["comercio"] = {
            "id": "comercio-1",
            "nombre_negocio": "La casa del sombrero",
        }
        return routes.agenda_turnos()


def test_get_agenda_permite_vencido_en_modo_limitado(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(
            estado_vigencia="vencido",
            acceso_operativo=False,
            acceso_publico=False,
            motivo_bloqueo="vigencia_vencida",
        ),
    )
    assert contexto["template"] == "turnos/agenda.html"
    assert contexto["modo_limitado"] is True
    assert contexto["vigencia_modulo"]["estado_vigencia"] == "vencido"


def test_get_agenda_permite_suspendido_en_modo_limitado(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(
            habilitado_manual=False,
            acceso_operativo=False,
            acceso_publico=False,
            motivo_bloqueo="suspension_manual",
        ),
    )
    assert contexto["modo_limitado"] is True
    assert contexto["vigencia_modulo"]["habilitado_manual"] is False


def test_get_agenda_cierra_si_no_existe_relacion(monkeypatch):
    app = aplicacion_prueba()
    monkeypatch.setattr(
        routes,
        "evaluar_vigencia_modulo",
        lambda comercio_id, slug: {"existe": False},
    )
    with app.test_request_context("/turnos/agenda"):
        session["comercio"] = {"id": "comercio-1"}
        respuesta, estado = routes.agenda_turnos()
    assert estado == 403
    assert "no activo" in respuesta


def test_get_agenda_sin_sesion_conserva_destino_login():
    app = aplicacion_prueba()
    cliente = app.test_client()
    respuesta = cliente.get("/turnos/agenda")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login?next=/turnos/agenda")


def test_qr_privado_usa_exactamente_url_publica(monkeypatch):
    capturado = {}

    class ImagenQrFalsa:
        def save(self, archivo, format):
            assert format == "PNG"
            archivo.write(b"png-de-prueba")

    def crear_qr_falso(url):
        capturado["url"] = url
        return ImagenQrFalsa()

    app = aplicacion_prueba()
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    monkeypatch.setattr(routes.qrcode, "make", crear_qr_falso)

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {
                "id": "11111111-1111-1111-1111-111111111111"
            }
        respuesta = cliente.get("/turnos/agenda/qr.png")

    assert respuesta.status_code == 200
    assert respuesta.mimetype == "image/png"
    assert capturado["url"] == (
        "http://localhost/turnos/comercio/"
        "11111111-1111-1111-1111-111111111111"
    )


def test_qr_sin_sesion_no_se_genera():
    app = aplicacion_prueba()
    respuesta = app.test_client().get("/turnos/agenda/qr.png")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login")


def test_manifest_conserva_inicio_general_y_agrega_shortcut_turnos():
    raiz = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (raiz / "static" / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "ClickLocal"
    assert manifest["short_name"] == "ClickLocal"
    assert manifest["start_url"] == "/index.html"
    assert manifest["scope"] == "/"
    assert any(
        acceso.get("name") == "ClickLocal Turnos"
        and acceso.get("short_name") == "Turnos"
        and acceso.get("url") == "/turnos/agenda"
        for acceso in manifest["shortcuts"]
    )


def test_agenda_carga_pwa_y_reutiliza_url_publica_para_compartir():
    raiz = Path(__file__).resolve().parents[1]
    agenda = (
        raiz / "turnos" / "templates" / "turnos" / "agenda.html"
    ).read_text(encoding="utf-8")

    assert 'rel="manifest" href="/static/manifest.json"' in agenda
    assert 'src="/static/pwa.js" data-install-ui="false"' in agenda
    assert "const TURNERA_PUBLICA_URL = {{ turnera_publica_url" in agenda
    assert "const enlace = TURNERA_PUBLICA_URL;" in agenda


def test_mutacion_privada_bloqueada_vencido(monkeypatch):
    app = aplicacion_prueba()
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: False)
    with app.test_request_context(
        "/turnos/agenda/profesionales/nuevo",
        method="POST",
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta, estado = routes.crear_profesional()
    assert estado == 403
    assert "no activo" in respuesta


def test_acceso_publico_bloqueado_vencido(monkeypatch):
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabaseVacio([{
            "id": "11111111-1111-1111-1111-111111111111",
            "nombre_negocio": "Comercio",
            "activo": True,
        }]),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: False)
    respuesta, estado = routes.turnera_publica(
        "11111111-1111-1111-1111-111111111111"
    )
    assert respuesta == ""
    assert estado == 404


def test_turnera_publica_abre_sin_login(monkeypatch):
    comercio_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabasePorTabla({
            "comercios": [{
                "id": comercio_id,
                "nombre_negocio": "Comercio",
                "activo": True,
            }],
        }),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: True)

    app = aplicacion_prueba()
    respuesta = app.test_client().get(f"/turnos/comercio/{comercio_id}")

    assert respuesta.status_code == 200
    assert b"Nuevo turno" in respuesta.data
    assert b"Iniciar sesi" not in respuesta.data


def test_reserva_publica_sigue_sin_requerir_login(monkeypatch):
    comercio_id = "11111111-1111-1111-1111-111111111111"
    servicio_id = "22222222-2222-2222-2222-222222222222"
    profesional_id = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabasePorTabla({
            "comercios": [{"id": comercio_id, "whatsapp": ""}],
        }),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: True)
    monkeypatch.setattr(
        routes,
        "_crear_reserva_validada",
        lambda *args: ({"ok": True}, 201),
    )

    app = aplicacion_prueba()
    respuesta = app.test_client().post(
        f"/turnos/comercio/{comercio_id}/reservas",
        data={
            "servicio_id": servicio_id,
            "profesional_id": profesional_id,
        },
    )

    assert respuesta.status_code == 201
    assert respuesta.get_json() == {"ok": True}


def test_reserva_publica_cualquiera_no_exige_profesional_id(monkeypatch):
    comercio_id = "11111111-1111-1111-1111-111111111111"
    servicio_id = "22222222-2222-2222-2222-222222222222"
    capturado = {}
    monkeypatch.setattr(
        routes,
        "supabase_admin",
        SupabasePorTabla({
            "comercios": [{"id": comercio_id, "whatsapp": ""}],
        }),
    )
    monkeypatch.setattr(routes, "modulo_activo", lambda *args: True)

    def crear_cualquiera(comercio, datos, whatsapp):
        capturado["comercio"] = comercio
        capturado["modo"] = datos.get("profesional_modo")
        return {"ok": True}, 201

    monkeypatch.setattr(
        routes,
        "_crear_reserva_cualquier_profesional",
        crear_cualquiera,
    )
    app = aplicacion_prueba()
    respuesta = app.test_client().post(
        f"/turnos/comercio/{comercio_id}/reservas",
        data={
            "servicio_id": servicio_id,
            "profesional_id": "",
            "profesional_modo": "cualquiera",
        },
    )

    assert respuesta.status_code == 201
    assert capturado == {
        "comercio": comercio_id,
        "modo": "cualquiera",
    }


def test_cta_configurado_incluye_comercio_y_modulo(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(estado_vigencia="por_vencer"),
        whatsapp="3434150049",
    )
    url = contexto["whatsapp_clicklocal_url"]
    assert url.startswith("https://wa.me/5493434150049?")
    mensaje = parse_qs(urlparse(url).query)["text"][0]
    assert mensaje == (
        "Hola ClickLocal, soy La casa del sombrero. "
        "Quiero renovar el módulo Turnos."
    )


def test_sin_whatsapp_no_genera_url(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(estado_vigencia="en_gracia"),
        whatsapp="",
    )
    assert contexto["whatsapp_clicklocal_url"] is None
    assert construir_url_whatsapp("", "mensaje") == ""


def test_agenda_abre_subseccion_de_configuracion(monkeypatch):
    contexto = ejecutar_agenda(
        monkeypatch,
        vigencia(),
        ruta="/turnos/agenda?configuracion=horarios",
    )

    assert contexto["abrir_configuracion"] is True
    assert contexto["seccion_configuracion"] == "horarios"

    contexto_inicio = ejecutar_agenda(
        monkeypatch,
        vigencia(),
        ruta="/turnos/agenda?configuracion=1",
    )
    assert contexto_inicio["abrir_configuracion"] is True
    assert contexto_inicio["seccion_configuracion"] == ""


def test_configuracion_turnos_tiene_tres_flujos_visuales():
    raiz = Path(__file__).resolve().parents[1]
    agenda = (
        raiz / "turnos" / "templates" / "turnos" / "agenda.html"
    ).read_text(encoding="utf-8")

    assert 'data-abrir-config="servicios"' in agenda
    assert 'data-abrir-config="profesionales"' in agenda
    assert 'data-abrir-config="horarios"' in agenda
    assert "Orden recomendado para empezar" in agenda
    assert "Agregá y administrá los servicios que ofrecés." in agenda
    assert "Agregá las personas que atienden" in agenda
    assert "Definí los días y horarios de atención" in agenda
    assert 'name="dia_semana" value="{{ numero_dia }}"' in agenda
    assert 'id="dia-{{ profesional.id }}-{{ numero_dia }}"' in agenda
    assert "restaurarEnfoqueConfiguracion" in agenda


class SupabaseCandidatosTurnos:
    def __init__(self):
        self.tabla = ""
        self.ordenes = []

    def table(self, nombre):
        self.tabla = nombre
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, columna):
        self.ordenes.append(columna)
        return self

    def execute(self):
        if self.tabla == "turnos_profesional_servicios":
            return SimpleNamespace(data=[
                {"profesional_id": "profesional-1"},
                {"profesional_id": "profesional-2"},
            ])
        if self.tabla == "turnos_profesionales":
            return SimpleNamespace(data=[
                {
                    "id": "profesional-1",
                    "nombre": "Carolina",
                    "activo": True,
                    "orden": 1,
                },
                {
                    "id": "profesional-2",
                    "nombre": "Andrea",
                    "activo": True,
                    "orden": 2,
                },
            ])
        return SimpleNamespace(data=[])


def datos_reserva_cualquiera():
    return {
        "cliente_nombre": "Cliente",
        "cliente_whatsapp": "3430000000",
        "servicio_id": "servicio-1",
        "profesional_modo": "cualquiera",
        "fecha": "2026-09-18",
        "hora_inicio": "10:00",
        "observacion": "",
    }


def test_cualquiera_elige_deterministicamente_el_primero(monkeypatch):
    supabase = SupabaseCandidatosTurnos()
    llamados = []
    monkeypatch.setattr(routes, "supabase_admin", supabase)

    def crear(comercio_id, datos, whatsapp):
        llamados.append(datos["profesional_id"])
        return {"ok": True, "reserva": {"id": "reserva-1"}}

    monkeypatch.setattr(routes, "_crear_reserva_validada", crear)
    resultado, estado = routes._crear_reserva_cualquier_profesional(
        "comercio-1",
        datos_reserva_cualquiera(),
    )

    assert estado == 200
    assert llamados == ["profesional-1"]
    assert supabase.ordenes == ["orden", "id"]
    assert resultado["profesional_asignado"] == {
        "id": "profesional-1",
        "nombre": "Carolina",
    }


def test_cualquiera_prueba_segundo_si_primero_ya_no_disponible(
    monkeypatch,
):
    llamados = []
    monkeypatch.setattr(routes, "supabase_admin", SupabaseCandidatosTurnos())

    def crear(comercio_id, datos, whatsapp):
        llamados.append(datos["profesional_id"])
        if datos["profesional_id"] == "profesional-1":
            return {"ok": False, "error": "horario_ocupado"}, 400
        return {"ok": True, "reserva": {"id": "reserva-2"}}

    monkeypatch.setattr(routes, "_crear_reserva_validada", crear)
    resultado, estado = routes._crear_reserva_cualquier_profesional(
        "comercio-1",
        datos_reserva_cualquiera(),
    )

    assert estado == 200
    assert llamados == ["profesional-1", "profesional-2"]
    assert resultado["profesional_asignado"]["id"] == "profesional-2"


def test_cualquiera_rechaza_si_ninguno_sigue_disponible(monkeypatch):
    monkeypatch.setattr(routes, "supabase_admin", SupabaseCandidatosTurnos())
    monkeypatch.setattr(
        routes,
        "_crear_reserva_validada",
        lambda *args: ({"ok": False, "error": "fuera_horario"}, 400),
    )

    resultado, estado = routes._crear_reserva_cualquier_profesional(
        "comercio-1",
        datos_reserva_cualquiera(),
    )

    assert estado == 409
    assert resultado == {
        "ok": False,
        "error": "horario_ya_no_disponible",
    }


def test_turnera_publica_deduplica_hora_en_modo_cualquiera():
    raiz = Path(__file__).resolve().parents[1]
    publico = (
        raiz / "turnos" / "templates" / "turnos" / "publico.html"
    ).read_text(encoding="utf-8")

    assert '"Cualquiera"' in publico
    assert "professionalsByTime[time] ||= [];" in publico
    assert "professionalsByTime[time].push(professional.id);" in publico
    assert "Object.keys(professionalsByTime).sort()" in publico
    assert 'profesional_modo: reviewData.professionalMode === "any"' in publico


def test_configuracion_turnos_renderiza_sin_datos(monkeypatch):
    monkeypatch.setattr(routes, "supabase_admin", SupabaseVacio())
    monkeypatch.setattr(
        routes,
        "evaluar_vigencia_modulo",
        lambda *args: vigencia(),
    )
    app = aplicacion_prueba()

    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion["comercio"] = {
                "id": "comercio-1",
                "nombre_negocio": "Peluquería de prueba",
            }
        respuesta = cliente.get("/turnos/agenda?configuracion=1")

    assert respuesta.status_code == 200
    assert b"Orden recomendado para empezar" in respuesta.data
    assert b"config-accesos" in respuesta.data


class SupabaseEdicionTurnos:
    def __init__(self, existente):
        self.existente = existente
        self.tabla = None
        self.accion = None
        self.cambios = []

    def table(self, nombre):
        self.tabla = nombre
        self.accion = None
        return self

    def select(self, *args, **kwargs):
        self.accion = "select"
        return self

    def update(self, datos):
        self.accion = "update"
        self.cambios.append((self.tabla, datos))
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.accion == "select":
            return SimpleNamespace(data=[{"id": self.existente}])
        return SimpleNamespace(data=[])


def test_editar_servicio_reutiliza_columnas_existentes(monkeypatch):
    supabase = SupabaseEdicionTurnos("servicio-1")
    monkeypatch.setattr(routes, "supabase_admin", supabase)
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    app = aplicacion_prueba()

    with app.test_request_context(
        "/turnos/agenda/servicios/servicio-1/editar",
        method="POST",
        data={
            "nombre": "Corte",
            "duracion_min": "30",
            "intervalo_inicio_min": "15",
            "capacidad_max": "1",
            "precio": "12500,50",
        },
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta = routes.editar_servicio("servicio-1")

    assert respuesta.status_code == 302
    assert "configuracion=servicios" in respuesta.location
    assert supabase.cambios == [(
        "turnos_servicios",
        {
            "nombre": "Corte",
            "duracion_min": 30,
            "capacidad_max": 1,
            "intervalo_inicio_min": 15,
            "precio": 12500.5,
        },
    )]


def test_editar_profesional_limita_actualizacion_al_comercio(monkeypatch):
    supabase = SupabaseEdicionTurnos("profesional-1")
    monkeypatch.setattr(routes, "supabase_admin", supabase)
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    app = aplicacion_prueba()

    with app.test_request_context(
        "/turnos/agenda/profesionales/profesional-1/editar",
        method="POST",
        data={"nombre": "Paola", "rol": "Peluquera"},
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta = routes.editar_profesional("profesional-1")

    assert respuesta.status_code == 302
    assert "configuracion=profesionales" in respuesta.location
    assert supabase.cambios == [(
        "turnos_profesionales",
        {"nombre": "Paola", "rol": "Peluquera"},
    )]


class SupabaseEliminarHorario:
    def __init__(self):
        self.accion = None
        self.eliminado = False

    def table(self, nombre):
        assert nombre == "turnos_horarios"
        return self

    def select(self, *args, **kwargs):
        self.accion = "select"
        return self

    def delete(self):
        self.accion = "delete"
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.accion == "select":
            return SimpleNamespace(data=[{
                "id": "horario-1",
                "profesional_id": "profesional-2",
                "dia_semana": 4,
            }])
        self.eliminado = True
        return SimpleNamespace(data=[])


def test_eliminar_viernes_conserva_profesional_y_dia(monkeypatch):
    supabase = SupabaseEliminarHorario()
    monkeypatch.setattr(routes, "supabase_admin", supabase)
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    app = aplicacion_prueba()

    with app.test_request_context(
        "/turnos/agenda/horarios/horario-1/eliminar",
        method="POST",
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta = routes.eliminar_horario("horario-1")

    assert supabase.eliminado is True
    assert respuesta.status_code == 302
    assert "configuracion=horarios" in respuesta.location
    assert "profesional=profesional-2" in respuesta.location
    assert respuesta.location.endswith("#dia-profesional-2-4")


class SupabaseHorarioSuperpuesto:
    def __init__(self):
        self.tabla = ""
        self.intento_insert = False

    def table(self, nombre):
        self.tabla = nombre
        return self

    def select(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        self.intento_insert = True
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.tabla == "turnos_profesionales":
            return SimpleNamespace(data=[{"id": "profesional-2"}])
        if self.tabla == "turnos_horarios":
            return SimpleNamespace(data=[{
                "id": "horario-existente",
                "hora_desde": "08:00:00",
                "hora_hasta": "12:00:00",
            }])
        return SimpleNamespace(data=[])


def test_crear_horario_rechaza_superposicion_y_conserva_contexto(
    monkeypatch,
):
    supabase = SupabaseHorarioSuperpuesto()
    monkeypatch.setattr(routes, "supabase_admin", supabase)
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    app = aplicacion_prueba()

    with app.test_request_context(
        "/turnos/agenda/profesionales/profesional-2/horarios/nuevo",
        method="POST",
        data={
            "dia_semana": "4",
            "hora_desde": "10:00",
            "hora_hasta": "14:00",
        },
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta = routes.crear_horario("profesional-2")

    assert supabase.intento_insert is False
    assert respuesta.status_code == 302
    assert "configuracion=horarios" in respuesta.location
    assert "error=horario_superpuesto" in respuesta.location
    assert "profesional=profesional-2" in respuesta.location
    assert respuesta.location.endswith("#dia-profesional-2-4")


def test_crear_horario_permite_franja_contigua(monkeypatch):
    supabase = SupabaseHorarioSuperpuesto()
    monkeypatch.setattr(routes, "supabase_admin", supabase)
    monkeypatch.setattr(modulos, "modulo_activo", lambda *args: True)
    app = aplicacion_prueba()

    with app.test_request_context(
        "/turnos/agenda/profesionales/profesional-2/horarios/nuevo",
        method="POST",
        data={
            "dia_semana": "4",
            "hora_desde": "12:00",
            "hora_hasta": "16:00",
        },
    ):
        session["comercio"] = {"id": "comercio-1"}
        respuesta = routes.crear_horario("profesional-2")

    assert supabase.intento_insert is True
    assert respuesta.status_code == 302
    assert "error=" not in respuesta.location
    assert respuesta.location.endswith("#dia-profesional-2-4")
