from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from flask import Flask

from beneficios.errors import BeneficioNoVigenteError
from beneficios.routes import crear_blueprint_publico_beneficios
from beneficios.identity import CLAVE_CONSUMIDOR, CLAVE_IDEMPOTENCIAS
from beneficios.types import (
    Beneficio,
    CodigoBeneficio,
    ObjetivoBeneficio,
    TipoDescuento,
    TipoObjetivo,
)


BENEFICIO_ID = "10000000-0000-4000-8000-000000000001"
COMERCIO_ID = "20000000-0000-4000-8000-000000000001"
AHORA = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)


def beneficio():
    return Beneficio(
        id=BENEFICIO_ID,
        comercio_id=COMERCIO_ID,
        titulo="20% OFF",
        objetivo=ObjetivoBeneficio(TipoObjetivo.COMERCIO, COMERCIO_ID),
        tipo_descuento=TipoDescuento.PORCENTAJE,
        valor_descuento=Decimal("20"),
        vigente_desde=AHORA - timedelta(days=1),
        vigente_hasta=AHORA + timedelta(days=1),
    )


class ServicioFalso:
    def __init__(self, error=None):
        self.error = error
        self.solicitudes = []
        self.consumos = []
        self.ventas = []

    def obtener_detalle(self, beneficio_id):
        assert beneficio_id == BENEFICIO_ID
        return beneficio()

    def generar_codigo(self, solicitud):
        if self.error:
            raise self.error
        self.solicitudes.append(solicitud)
        return CodigoBeneficio(
            beneficio_id=BENEFICIO_ID,
            codigo="ABCD-EFGH-IJKL-MNOP",
            emitido_en=AHORA,
            vence_en=AHORA + timedelta(hours=8),
        )


def aplicacion(
    servicio,
    whatsapp="https://wa.me/5493430000000?text=codigo",
    llamadas_whatsapp=None,
    comercio_habilitado=True,
):
    app = Flask(__name__)
    app.secret_key = "secreto-ficticio-de-prueba"

    def construir_whatsapp(comercio_id, mensaje):
        if llamadas_whatsapp is not None:
            llamadas_whatsapp.append((comercio_id, mensaje))
        return whatsapp

    app.register_blueprint(crear_blueprint_publico_beneficios(
        lambda: servicio,
        lambda: "csrf-publico",
        construir_whatsapp,
        lambda comercio_id: comercio_habilitado,
    ))
    return app


def test_publico_exige_csrf():
    respuesta = aplicacion(ServicioFalso()).test_client().post(
        f"/beneficios/{BENEFICIO_ID}/codigo"
    )
    assert respuesta.status_code == 403


def test_publico_emite_con_identidad_e_idempotencia_estables():
    servicio = ServicioFalso()
    llamadas_whatsapp = []
    cliente = aplicacion(
        servicio,
        llamadas_whatsapp=llamadas_whatsapp,
    ).test_client()
    cabeceras = {"X-CSRF-Token": "csrf-publico"}

    primera = cliente.post(f"/beneficios/{BENEFICIO_ID}/codigo", headers=cabeceras)
    segunda = cliente.post(f"/beneficios/{BENEFICIO_ID}/codigo", headers=cabeceras)

    assert primera.status_code == segunda.status_code == 200
    assert primera.get_json()["codigo"] == "ABCD-EFGH-IJKL-MNOP"
    assert primera.get_json()["whatsapp_url"].startswith("https://wa.me/")
    assert servicio.solicitudes[0].consumidor_id == servicio.solicitudes[1].consumidor_id
    assert servicio.solicitudes[0].idempotency_key == servicio.solicitudes[1].idempotency_key
    assert llamadas_whatsapp[0][0] == COMERCIO_ID
    assert "ABCD-EFGH-IJKL-MNOP" in llamadas_whatsapp[0][1]


def test_comercio_desactivado_responde_404_sin_emitir_ni_crear_identidad():
    servicio = ServicioFalso()
    cliente = aplicacion(servicio, comercio_habilitado=False).test_client()

    respuesta = cliente.post(
        f"/beneficios/{BENEFICIO_ID}/codigo",
        headers={"X-CSRF-Token": "csrf-publico"},
    )

    assert respuesta.status_code == 404
    assert servicio.solicitudes == []
    with cliente.session_transaction() as sesion:
        assert CLAVE_CONSUMIDOR not in sesion
        assert CLAVE_IDEMPOTENCIAS not in sesion


def test_comercio_gastronomico_no_publicado_responde_404_sin_emitir():
    servicio = ServicioFalso()
    respuesta = aplicacion(
        servicio,
        comercio_habilitado=False,
    ).test_client().post(
        f"/beneficios/{BENEFICIO_ID}/codigo",
        headers={"X-CSRF-Token": "csrf-publico"},
    )

    assert respuesta.status_code == 404
    assert servicio.solicitudes == []


def test_publico_sin_whatsapp_permite_presentacion_presencial():
    servicio = ServicioFalso()
    respuesta = aplicacion(servicio, whatsapp="").test_client().post(
        f"/beneficios/{BENEFICIO_ID}/codigo",
        headers={"X-CSRF-Token": "csrf-publico"},
    )
    datos = respuesta.get_json()
    assert datos["whatsapp_url"] is None
    assert datos["permite_presentar"] is True
    assert len(servicio.solicitudes) == 1


def test_publico_no_convierte_emision_en_consumo():
    rutas = Path("beneficios/routes.py").read_text(encoding="utf-8")
    bloque = rutas.split("def crear_blueprint_publico_beneficios", 1)[1]
    assert "generar_codigo" in bloque
    assert "consumir_codigo" not in bloque
    assert "activacion_whatsapp" not in bloque


def test_obtener_o_compartir_codigo_no_registra_consumo_ni_venta():
    servicio = ServicioFalso()
    llamadas_whatsapp = []
    respuesta = aplicacion(
        servicio,
        llamadas_whatsapp=llamadas_whatsapp,
    ).test_client().post(
        f"/beneficios/{BENEFICIO_ID}/codigo",
        headers={"X-CSRF-Token": "csrf-publico"},
    )

    assert respuesta.status_code == 200
    assert llamadas_whatsapp
    assert servicio.consumos == []
    assert servicio.ventas == []


def test_publico_oculta_error_interno_y_controla_no_vigente():
    cliente = aplicacion(ServicioFalso(BeneficioNoVigenteError())).test_client()
    respuesta = cliente.post(
        f"/beneficios/{BENEFICIO_ID}/codigo",
        headers={"X-CSRF-Token": "csrf-publico"},
    )
    assert respuesta.status_code == 409
    assert respuesta.get_json()["mensaje"] == "El beneficio no está vigente."

    cliente = aplicacion(ServicioFalso(RuntimeError("dato sensible"))).test_client()
    respuesta = cliente.post(
        f"/beneficios/{BENEFICIO_ID}/codigo",
        headers={"X-CSRF-Token": "csrf-publico"},
    )
    assert respuesta.status_code == 503
    assert "dato sensible" not in respuesta.get_data(as_text=True)


def test_presentacion_incluye_codigo_whatsapp_y_alternativa_presencial():
    html = Path("templates/detalle.html").read_text(encoding="utf-8")
    javascript = Path("static/beneficios/publico.js").read_text(encoding="utf-8")

    assert "Obtener código" in html
    assert "Enviar por WhatsApp" in html
    assert "Mostrá este código en el comercio" in html
    assert "X-CSRF-Token" in javascript
    assert "datos.whatsapp_url" in javascript


def test_app_conecta_beneficios_con_configuracion_explicita_del_entorno():
    fuente = Path("app.py").read_text(encoding="utf-8")

    assert "ConfiguracionBeneficios.desde_entorno" in fuente
    assert "_configuracion_beneficios.parametros_conexion()" in fuente
    assert "secreto_codigos=_configuracion_beneficios.secreto_codigos" in fuente
    assert "clicklocal_beneficios_ui_test" not in fuente
    assert "crear_blueprint_publico_beneficios" in fuente
    assert "_cargar_comercio_publico(comercio_id) is not None" in fuente
