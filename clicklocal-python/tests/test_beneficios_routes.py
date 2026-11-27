from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask

from beneficios.errors import BeneficioNoEncontradoError, CodigoInvalidoError
from beneficios.routes import crear_blueprint_administracion_beneficios
from beneficios.types import ValidacionCodigo


PUBLICACION = "a1000000-0000-4000-8000-000000000002"
PRODUCTO_GASTRONOMICO = "a1000000-0000-4000-8000-000000000003"


def datos_validos():
    return {
        "titulo": "20% OFF", "tipo_descuento": "porcentaje",
        "valor_descuento": "20", "vigente_desde": "2026-11-20T00:00:00-03:00",
        "vigente_hasta": "2026-11-27T00:00:00-03:00",
        "dias_modo": "todos", "dias_semana": [0, 1, 2, 3, 4, 5, 6],
        "horario_modo": "todo", "hora_desde": None, "hora_hasta": None,
        "cupo_maximo": None, "maximo_unidades_codigo": 1,
        "mostrar_codigos_restantes": False, "tema_etiqueta": "clara",
        "metodos": ["whatsapp"],
    }


class ServicioFalso:
    def __init__(self, *, pertenece=True, codigo_valido=True):
        self.pertenece = pertenece
        self.codigo_valido = codigo_valido
        self.recibido = None
        self.validaciones = []
        self.consumos = []
        self.ya_consumido = False
        self.beneficios = []

    def crear_para_publicacion(self, beneficio):
        if not self.pertenece:
            raise BeneficioNoEncontradoError(beneficio.objetivo.id)
        self.recibido = beneficio
        self.beneficios.append(beneficio)
        return beneficio

    def crear_para_producto(self, beneficio):
        if not self.pertenece:
            raise BeneficioNoEncontradoError(beneficio.objetivo.id)
        self.recibido = beneficio
        self.beneficios.append(beneficio)
        return beneficio

    def listar_del_comercio(self, comercio_id):
        return tuple(self.beneficios)

    def editar_del_comercio(self, beneficio_id, comercio_id, beneficio):
        if not self.pertenece:
            raise BeneficioNoEncontradoError(beneficio_id)
        editado = replace(
            beneficio,
            serie_id=beneficio_id,
            numero_edicion=2,
        )
        self.beneficios = [editado]
        return editado

    def desactivar_del_comercio(self, beneficio_id, comercio_id):
        if not self.pertenece or not self.beneficios:
            raise BeneficioNoEncontradoError(beneficio_id)
        desactivado = replace(self.beneficios[0], activo=False)
        self.beneficios = [desactivado]
        return desactivado

    def _validacion(self, comercio_id, *, utilizado=False):
        instante = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
        return ValidacionCodigo(
            codigo_id="codigo-1", beneficio_id="beneficio-1",
            comercio_id=comercio_id, titulo="20% OFF",
            emitido_en=instante, vence_en=datetime(2026, 11, 30, tzinfo=timezone.utc),
            utilizado_en=instante if utilizado else None,
        )

    def validar_codigo_comercio(self, codigo, comercio_id):
        self.validaciones.append((codigo, comercio_id))
        if not self.codigo_valido or self.ya_consumido:
            raise CodigoInvalidoError()
        return self._validacion(comercio_id)

    def consumir_codigo_comercio(self, codigo, comercio_id):
        self.consumos.append((codigo, comercio_id))
        if not self.codigo_valido or self.ya_consumido:
            raise CodigoInvalidoError()
        self.ya_consumido = True
        return self._validacion(comercio_id, utilizado=True)


def aplicacion(servicio, comercio="a1000000-0000-4000-8000-000000000001"):
    app = Flask(__name__)
    app.secret_key = "prueba"
    app.register_blueprint(crear_blueprint_administracion_beneficios(
        lambda: servicio, lambda: comercio, lambda: "csrf-seguro"
    ))
    return app


def test_ruta_exige_sesion_y_csrf():
    servicio = ServicioFalso()
    cliente = aplicacion(servicio, comercio=None).test_client()
    assert cliente.post(f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos_validos()).status_code == 401
    cliente = aplicacion(servicio).test_client()
    assert cliente.post(f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos_validos()).status_code == 403


def test_ruta_guarda_solo_whatsapp_configurado():
    servicio = ServicioFalso()
    respuesta = aplicacion(servicio).test_client().post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos_validos(),
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 201
    assert respuesta.get_json()["beneficio"]["etiqueta"] == "20% OFF"
    assert [item.value for item in servicio.recibido.metodos] == ["whatsapp"]


def test_ruta_gastronomica_asocia_el_beneficio_al_producto_correcto():
    servicio = ServicioFalso()
    respuesta = aplicacion(servicio).test_client().post(
        f"/panel/gastronomia/productos/{PRODUCTO_GASTRONOMICO}/beneficios",
        json=datos_validos(),
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    datos = respuesta.get_json()["beneficio"]
    assert respuesta.status_code == 201
    assert servicio.recibido.objetivo.tipo.value == "gastronomia_producto"
    assert servicio.recibido.objetivo.id == PRODUCTO_GASTRONOMICO
    assert datos["objetivo_tipo"] == "gastronomia_producto"
    assert datos["gastronomia_producto_id"] == PRODUCTO_GASTRONOMICO
    assert datos["publicacion_id"] is None


def test_detalle_promocion_es_opcional_y_admite_hasta_200_caracteres():
    servicio = ServicioFalso()
    cliente = aplicacion(servicio).test_client()
    datos = datos_validos()
    datos["detalle_promocion"] = "  Promo exclusiva en salón  "
    respuesta = cliente.post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 201
    assert respuesta.get_json()["beneficio"]["detalle_promocion"] == "Promo exclusiva en salón"
    assert servicio.recibido.detalle_promocion == "Promo exclusiva en salón"

    sin_detalle = datos_validos()
    sin_detalle["detalle_promocion"] = ""
    respuesta = cliente.post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=sin_detalle,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 201
    assert respuesta.get_json()["beneficio"]["detalle_promocion"] is None

    demasiado_largo = datos_validos()
    demasiado_largo["detalle_promocion"] = "x" * 201
    respuesta = cliente.post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=demasiado_largo,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 400


def test_ruta_rechaza_qr_carrito_y_publicacion_ajena():
    datos = datos_validos(); datos["metodos"] = ["whatsapp", "qr"]
    respuesta = aplicacion(ServicioFalso()).test_client().post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 400
    respuesta = aplicacion(ServicioFalso(pertenece=False)).test_client().post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos_validos(),
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 404


def test_blueprint_expone_solo_creacion_y_canje_administrativo():
    reglas = {regla.rule for regla in aplicacion(ServicioFalso()).url_map.iter_rules()}
    assert reglas == {
        "/static/<path:filename>",
        "/panel/publicaciones/<publicacion_id>/beneficios",
        "/panel/gastronomia/productos/<producto_id>/beneficios",
        "/panel/beneficios",
        "/panel/beneficios/<beneficio_id>",
        "/panel/beneficios/<beneficio_id>/desactivar",
        "/panel/beneficios/codigos/validar",
        "/panel/beneficios/codigos/consumir",
    }


def test_lista_multiples_ofertas_propias_con_estado_y_edicion():
    servicio = ServicioFalso()
    primera = aplicacion(servicio).test_client().post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios",
        json=datos_validos(), headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert primera.status_code == 201
    servicio.beneficios.append(replace(servicio.beneficios[0], id="beneficio-2"))
    respuesta = aplicacion(servicio).test_client().get("/panel/beneficios")
    datos = respuesta.get_json()["beneficios"]
    assert respuesta.status_code == 200
    assert len(datos) == 2
    assert datos[0]["activo"] is True
    assert datos[0]["dias_semana"] == list(range(7))


def test_edita_como_nueva_revision_y_desactiva_explicita():
    servicio = ServicioFalso()
    cliente = aplicacion(servicio).test_client()
    creada = cliente.post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios",
        json=datos_validos(), headers={"X-CSRF-Token": "csrf-seguro"},
    ).get_json()["beneficio"]
    cambios = datos_validos()
    cambios.update({
        "publicacion_id": PUBLICACION,
        "valor_descuento": "25",
        "detalle_promocion": "No acumulable con otras promociones",
    })
    editada = cliente.put(
        f"/panel/beneficios/{creada['id']}", json=cambios,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert editada.status_code == 200
    assert editada.get_json()["beneficio"]["numero_edicion"] == 2
    assert editada.get_json()["beneficio"]["detalle_promocion"] == "No acumulable con otras promociones"
    desactivada = cliente.post(
        f"/panel/beneficios/{editada.get_json()['beneficio']['id']}/desactivar",
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert desactivada.status_code == 200
    assert desactivada.get_json()["beneficio"]["activo"] is False


def test_edicion_y_desactivacion_exigen_csrf_y_rechazan_otro_comercio():
    datos = datos_validos(); datos["publicacion_id"] = PUBLICACION
    assert aplicacion(ServicioFalso(), comercio=None).test_client().get(
        "/panel/beneficios"
    ).status_code == 401
    cliente = aplicacion(ServicioFalso()).test_client()
    assert cliente.put("/panel/beneficios/ajeno", json=datos).status_code == 403
    ajeno = aplicacion(ServicioFalso(pertenece=False)).test_client()
    assert ajeno.put(
        "/panel/beneficios/ajeno", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    ).status_code == 404
    assert ajeno.post(
        "/panel/beneficios/ajeno/desactivar",
        headers={"X-CSRF-Token": "csrf-seguro"},
    ).status_code == 404


def test_consultar_codigo_no_lo_consume_ni_registra_venta():
    servicio = ServicioFalso()
    respuesta = aplicacion(servicio).test_client().post(
        "/panel/beneficios/codigos/validar", json={"codigo": "CL-ABC-123"},
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 200
    assert respuesta.get_json()["cupon"]["estado"] == "disponible"
    assert servicio.validaciones == [("CL-ABC-123", "a1000000-0000-4000-8000-000000000001")]
    assert servicio.consumos == []
    assert "venta" not in respuesta.get_json()["mensaje"].lower()


def test_consumo_requiere_confirmacion_explicita_y_rechaza_repeticion():
    servicio = ServicioFalso()
    cliente = aplicacion(servicio).test_client()
    respuesta = cliente.post(
        "/panel/beneficios/codigos/consumir", json={"codigo": "CL-ABC-123"},
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 200
    assert respuesta.get_json()["cupon"]["estado"] == "utilizado"
    assert "no registra una venta" in respuesta.get_json()["mensaje"].lower()
    repetida = cliente.post(
        "/panel/beneficios/codigos/consumir", json={"codigo": "CL-ABC-123"},
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert repetida.status_code == 409


def test_canje_exige_sesion_y_csrf():
    assert aplicacion(ServicioFalso(), comercio=None).test_client().post(
        "/panel/beneficios/codigos/validar", json={"codigo": "CL-ABC-123"},
    ).status_code == 401
    cliente = aplicacion(ServicioFalso()).test_client()
    assert cliente.post(
        "/panel/beneficios/codigos/validar", json={"codigo": "CL-ABC-123"},
    ).status_code == 403
    assert cliente.post(
        "/panel/beneficios/codigos/consumir", json={"codigo": "CL-ABC-123"},
    ).status_code == 403


def test_codigo_invalido_vencido_usado_o_ajeno_no_se_consume():
    servicio = ServicioFalso(codigo_valido=False)
    respuesta = aplicacion(servicio).test_client().post(
        "/panel/beneficios/codigos/validar", json={"codigo": "AJENO"},
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 404
    mensaje = respuesta.get_json()["mensaje"].lower()
    assert all(texto in mensaje for texto in ("no existe", "venció", "utilizado", "no pertenece"))
    assert servicio.consumos == []


def test_panel_separa_validacion_de_confirmacion_de_consumo():
    panel = Path("templates/panel.html").read_text(encoding="utf-8")
    javascript = Path("static/beneficios/beneficios-20.js").read_text(encoding="utf-8")
    assert "Marcar como utilizado" in panel
    assert "Consultar este código no lo utiliza ni registra una venta" in panel
    assert '"/panel/beneficios/codigos/validar"' in javascript
    assert '"/panel/beneficios/codigos/consumir"' in javascript


def test_panel_lista_edita_desactiva_y_bloquea_doble_envio():
    panel = Path("templates/panel.html").read_text(encoding="utf-8")
    javascript = Path("static/beneficios/beneficios-20.js").read_text(encoding="utf-8")
    assert "Mis ofertas de Beneficios" in panel
    assert 'fetch("/panel/beneficios"' in javascript
    assert 'method: beneficioActual ? "PUT" : "POST"' in javascript
    assert '"/desactivar"' in javascript
    assert "if (enviando) return;" in javascript
    assert "botonGuardar.disabled = true" in javascript


def test_todos_los_dias_y_todo_el_dia_ignoran_valores_ocultos():
    servicio = ServicioFalso()
    datos = datos_validos()
    datos.update({"dias_semana": [3], "hora_desde": "18:00", "hora_hasta": "21:00"})
    respuesta = aplicacion(servicio).test_client().post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 201
    assert servicio.recibido.dias_semana == frozenset(range(7))
    assert servicio.recibido.hora_desde is None
    assert servicio.recibido.hora_hasta is None


def test_dias_especificos_y_franja_que_cruza_medianoche():
    servicio = ServicioFalso()
    datos = datos_validos()
    datos.update({
        "dias_modo": "especificos", "dias_semana": [3, 5],
        "horario_modo": "franja", "hora_desde": "22:00", "hora_hasta": "02:00",
    })
    respuesta = aplicacion(servicio).test_client().post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    )
    assert respuesta.status_code == 201
    assert servicio.recibido.dias_semana == frozenset({3, 5})
    assert servicio.recibido.hora_desde.isoformat() == "22:00:00"
    assert servicio.recibido.hora_hasta.isoformat() == "02:00:00"


def test_rechaza_dias_especificos_vacios_y_franja_incompleta():
    cliente = aplicacion(ServicioFalso()).test_client()
    datos = datos_validos()
    datos.update({"dias_modo": "especificos", "dias_semana": []})
    assert cliente.post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    ).status_code == 400
    datos = datos_validos()
    datos.update({"horario_modo": "franja", "hora_desde": "22:00", "hora_hasta": None})
    assert cliente.post(
        f"/panel/publicaciones/{PUBLICACION}/beneficios", json=datos,
        headers={"X-CSRF-Token": "csrf-seguro"},
    ).status_code == 400


def test_detalle_publico_integra_ofertas_generales_y_especificas():
    app = Path("app.py").read_text(encoding="utf-8")
    detalle = Path("templates/detalle.html").read_text(encoding="utf-8")

    assert "TipoObjetivo.PUBLICACION" in app
    assert "TipoObjetivo.COMERCIO" in app
    assert "ofertas_por_id.setdefault" in app
    assert "ofertas_beneficios=ofertas_beneficios" in app
    assert "{% for oferta in ofertas_beneficios %}" in detalle
    assert "data-beneficios-selector" in detalle
    assert "oferta.beneficio.detalle_promocion" in detalle


def test_detalle_sin_ofertas_no_agrega_bloque_visual():
    detalle = Path("templates/detalle.html").read_text(encoding="utf-8")

    assert "{% if ofertas_beneficios %}" in detalle
    assert "beneficios-20-publico" in detalle
