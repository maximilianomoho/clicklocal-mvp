"""Rutas administrativas y públicas de Beneficios 2.0."""

from collections.abc import Callable
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from secrets import compare_digest
from uuid import uuid4

from flask import Blueprint, jsonify, request, session

from .errors import (
    BeneficioNoEncontradoError,
    BeneficiosError,
    CodigoInvalidoError,
    ValidacionBeneficioError,
)
from .identity import resolver_idempotencia_beneficio, resolver_identidad_consumidor
from .services import ServicioBeneficios
from .types import (
    Beneficio, MetodoUtilizacion, ObjetivoBeneficio, TemaEtiqueta,
    SolicitudCodigo, TipoDescuento, TipoObjetivo,
)


def crear_blueprint_administracion_beneficios(
    obtener_servicio: Callable[[], ServicioBeneficios],
    obtener_comercio_id: Callable[[], str | None],
    obtener_csrf_esperado: Callable[[], str],
) -> Blueprint:
    """Expone creación y canje administrativo; no registra rutas públicas."""
    blueprint = Blueprint("beneficios_admin", __name__)

    @blueprint.post("/panel/publicaciones/<publicacion_id>/beneficios")
    def crear_para_publicacion(publicacion_id: str):
        comercio_id = obtener_comercio_id()
        if not comercio_id:
            return jsonify({"ok": False, "mensaje": "Sesión requerida."}), 401
        esperado = str(obtener_csrf_esperado() or "")
        recibido = str(request.headers.get("X-CSRF-Token") or "")
        if not esperado or not recibido or not compare_digest(esperado, recibido):
            return jsonify({"ok": False, "mensaje": "Solicitud no válida."}), 403
        try:
            beneficio = _beneficio_desde_json(
                request.get_json(silent=True) or {},
                objetivo=ObjetivoBeneficio(
                    TipoObjetivo.PUBLICACION,
                    publicacion_id,
                ),
                comercio_id=str(comercio_id),
            )
            creado = obtener_servicio().crear_para_publicacion(beneficio)
            return jsonify({"ok": True, "beneficio": _beneficio_json(creado)}), 201
        except ValidacionBeneficioError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 400
        except BeneficioNoEncontradoError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 404
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    @blueprint.post(
        "/panel/gastronomia/productos/<producto_id>/beneficios"
    )
    def crear_para_producto_gastronomico(producto_id: str):
        comercio_id, error = _autorizar_canje(
            obtener_comercio_id,
            obtener_csrf_esperado,
        )
        if error is not None:
            return error
        try:
            beneficio = _beneficio_desde_json(
                request.get_json(silent=True) or {},
                objetivo=ObjetivoBeneficio(
                    TipoObjetivo.GASTRONOMIA_PRODUCTO,
                    producto_id,
                ),
                comercio_id=comercio_id,
            )
            creado = obtener_servicio().crear_para_producto(beneficio)
            return jsonify({
                "ok": True,
                "beneficio": _beneficio_json(creado),
            }), 201
        except ValidacionBeneficioError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 400
        except BeneficioNoEncontradoError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 404
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    @blueprint.get("/panel/beneficios")
    def listar_beneficios():
        comercio_id = obtener_comercio_id()
        if not comercio_id:
            return jsonify({"ok": False, "mensaje": "Sesión requerida."}), 401
        try:
            beneficios = obtener_servicio().listar_del_comercio(
                str(comercio_id)
            )
            return jsonify({
                "ok": True,
                "beneficios": [_beneficio_json(item) for item in beneficios],
            })
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    @blueprint.put("/panel/beneficios/<beneficio_id>")
    def editar_beneficio(beneficio_id: str):
        comercio_id, error = _autorizar_canje(
            obtener_comercio_id,
            obtener_csrf_esperado,
        )
        if error is not None:
            return error
        datos = request.get_json(silent=True) or {}
        try:
            beneficio = _beneficio_desde_json(
                datos,
                objetivo=_objetivo_desde_json(datos),
                comercio_id=comercio_id,
            )
            editado = obtener_servicio().editar_del_comercio(
                beneficio_id,
                comercio_id,
                beneficio,
            )
            return jsonify({
                "ok": True,
                "beneficio": _beneficio_json(editado),
                "mensaje": "Oferta actualizada sin alterar su historial.",
            })
        except ValidacionBeneficioError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 400
        except BeneficioNoEncontradoError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 404
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    @blueprint.post("/panel/beneficios/<beneficio_id>/desactivar")
    def desactivar_beneficio(beneficio_id: str):
        comercio_id, error = _autorizar_canje(
            obtener_comercio_id,
            obtener_csrf_esperado,
        )
        if error is not None:
            return error
        try:
            desactivado = obtener_servicio().desactivar_del_comercio(
                beneficio_id,
                comercio_id,
            )
            return jsonify({
                "ok": True,
                "beneficio": _beneficio_json(desactivado),
                "mensaje": "Oferta desactivada.",
            })
        except BeneficioNoEncontradoError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 404
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    @blueprint.post("/panel/beneficios/codigos/validar")
    def validar_codigo():
        comercio_id, error = _autorizar_canje(
            obtener_comercio_id,
            obtener_csrf_esperado,
        )
        if error is not None:
            return error
        try:
            datos = request.get_json(silent=True) or {}
            validacion = obtener_servicio().validar_codigo_comercio(
                datos.get("codigo"),
                comercio_id,
            )
            return jsonify({
                "ok": True,
                "cupon": _validacion_codigo_json(validacion),
                "mensaje": "Cupón válido. Todavía no fue utilizado.",
            })
        except CodigoInvalidoError:
            return _respuesta_codigo_invalido(404)
        except ValidacionBeneficioError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 400
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    @blueprint.post("/panel/beneficios/codigos/consumir")
    def consumir_codigo():
        comercio_id, error = _autorizar_canje(
            obtener_comercio_id,
            obtener_csrf_esperado,
        )
        if error is not None:
            return error
        try:
            datos = request.get_json(silent=True) or {}
            validacion = obtener_servicio().consumir_codigo_comercio(
                datos.get("codigo"),
                comercio_id,
            )
            return jsonify({
                "ok": True,
                "cupon": _validacion_codigo_json(validacion),
                "mensaje": (
                    "Cupón marcado como utilizado. "
                    "Esta acción no registra una venta."
                ),
            })
        except CodigoInvalidoError:
            return _respuesta_codigo_invalido(409)
        except ValidacionBeneficioError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 400
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 503

    return blueprint


def _autorizar_canje(obtener_comercio_id, obtener_csrf_esperado):
    comercio_id = obtener_comercio_id()
    if not comercio_id:
        return None, (jsonify({"ok": False, "mensaje": "Sesión requerida."}), 401)
    esperado = str(obtener_csrf_esperado() or "")
    recibido = str(request.headers.get("X-CSRF-Token") or "")
    if not esperado or not recibido or not compare_digest(esperado, recibido):
        return None, (jsonify({"ok": False, "mensaje": "Solicitud no válida."}), 403)
    return str(comercio_id), None


def _respuesta_codigo_invalido(estado: int):
    return jsonify({
        "ok": False,
        "mensaje": (
            "El código no existe, venció, ya fue utilizado "
            "o no pertenece a este comercio."
        ),
    }), estado


def _validacion_codigo_json(validacion) -> dict[str, object]:
    return {
        "beneficio_id": validacion.beneficio_id,
        "titulo": validacion.titulo,
        "emitido_en": validacion.emitido_en.isoformat(),
        "vence_en": validacion.vence_en.isoformat(),
        "utilizado_en": (
            validacion.utilizado_en.isoformat()
            if validacion.utilizado_en is not None
            else None
        ),
        "estado": "utilizado" if validacion.utilizado_en else "disponible",
    }


def crear_blueprint_publico_beneficios(
    obtener_servicio: Callable[[], ServicioBeneficios],
    obtener_csrf_esperado: Callable[[], str],
    construir_whatsapp: Callable[[str, str], str],
    comercio_publico_habilitado: Callable[[str], bool],
) -> Blueprint:
    """Emite códigos; mostrar o compartir nunca los consume."""

    blueprint = Blueprint("beneficios_publico", __name__)

    @blueprint.post("/beneficios/<beneficio_id>/codigo")
    def obtener_codigo(beneficio_id: str):
        esperado = str(obtener_csrf_esperado() or "")
        recibido = str(request.headers.get("X-CSRF-Token") or "")
        if not esperado or not recibido or not compare_digest(esperado, recibido):
            return jsonify({"ok": False, "mensaje": "Solicitud no válida."}), 403

        try:
            servicio = obtener_servicio()
            beneficio = servicio.obtener_detalle(beneficio_id)
            if not comercio_publico_habilitado(beneficio.comercio_id):
                raise BeneficioNoEncontradoError()
            identidad = resolver_identidad_consumidor(session)
            idempotencia = resolver_idempotencia_beneficio(
                session,
                beneficio.id,
            )
            emitido = servicio.generar_codigo(SolicitudCodigo(
                beneficio_id=beneficio.id,
                consumidor_id=identidad.consumidor_id,
                idempotency_key=idempotencia,
            ))
            mensaje = (
                f"Hola, quiero usar el beneficio {beneficio.titulo}. "
                f"Mi código ClickLocal es {emitido.codigo}."
            )
            whatsapp_url = construir_whatsapp(
                beneficio.comercio_id,
                mensaje,
            )
            return jsonify({
                "ok": True,
                "codigo": emitido.codigo,
                "vence_en": (
                    emitido.vence_en.isoformat()
                    if emitido.vence_en is not None
                    else None
                ),
                "titulo": beneficio.titulo,
                "whatsapp_url": whatsapp_url or None,
                "permite_presentar": True,
            })
        except BeneficioNoEncontradoError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 404
        except ValidacionBeneficioError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 400
        except BeneficiosError as error:
            return jsonify({"ok": False, "mensaje": error.mensaje_publico}), 409
        except Exception:
            return jsonify({
                "ok": False,
                "mensaje": "Beneficios no está disponible temporalmente.",
            }), 503

    return blueprint


def _objetivo_desde_json(datos) -> ObjetivoBeneficio:
    tipo_crudo = str(datos.get("objetivo_tipo") or "").strip()
    objetivo_id = str(datos.get("objetivo_id") or "").strip()
    if not tipo_crudo:
        tipo_crudo = TipoObjetivo.PUBLICACION.value
        objetivo_id = str(datos.get("publicacion_id") or "").strip()
    try:
        return ObjetivoBeneficio(TipoObjetivo(tipo_crudo), objetivo_id)
    except ValueError as error:
        raise ValidacionBeneficioError("objetivo inválido") from error


def _beneficio_desde_json(
    datos,
    *,
    objetivo: ObjetivoBeneficio,
    comercio_id: str,
) -> Beneficio:
    try:
        detalle_crudo = datos.get("detalle_promocion")
        if detalle_crudo is not None and not isinstance(detalle_crudo, str):
            raise ValidacionBeneficioError("el detalle de la promoción no es válido")
        detalle_promocion = str(detalle_crudo or "").strip() or None
        if detalle_promocion is not None and len(detalle_promocion) > 200:
            raise ValidacionBeneficioError(
                "el detalle de la promoción admite hasta 200 caracteres"
            )
        tipo = TipoDescuento(str(datos.get("tipo_descuento") or ""))
        valor_crudo = datos.get("valor_descuento")
        valor = None if valor_crudo in (None, "") else Decimal(str(valor_crudo))
        cupo_crudo = datos.get("cupo_maximo")
        cupo = None if cupo_crudo in (None, "") else int(cupo_crudo)
        metodos = tuple(datos.get("metodos") or ())
        if metodos != (MetodoUtilizacion.WHATSAPP.value,):
            raise ValidacionBeneficioError("sólo WhatsApp puede configurarse en esta etapa")
        dias_modo = str(datos.get("dias_modo") or "")
        if dias_modo == "todos":
            dias = frozenset(range(7))
        elif dias_modo == "especificos":
            dias_crudos = datos.get("dias_semana")
            if not isinstance(dias_crudos, list):
                raise ValidacionBeneficioError("los días específicos no son válidos")
            dias = frozenset(int(dia) for dia in dias_crudos)
            if not dias:
                raise ValidacionBeneficioError("seleccione al menos un día")
        else:
            raise ValidacionBeneficioError("modo de días inválido")
        horario_modo = str(datos.get("horario_modo") or "")
        if horario_modo == "todo":
            hora_desde = hora_hasta = None
        elif horario_modo == "franja":
            hora_desde = _hora(datos.get("hora_desde"))
            hora_hasta = _hora(datos.get("hora_hasta"))
            if hora_desde is None or hora_hasta is None:
                raise ValidacionBeneficioError("informe las horas Desde y Hasta")
        else:
            raise ValidacionBeneficioError("modo de horario inválido")
        return Beneficio(
            id=str(uuid4()), comercio_id=comercio_id,
            titulo=str(datos.get("titulo") or "").strip(),
            objetivo=objetivo,
            tipo_descuento=tipo, valor_descuento=valor,
            vigente_desde=_fecha(datos.get("vigente_desde")),
            vigente_hasta=_fecha(datos.get("vigente_hasta")),
            detalle_promocion=detalle_promocion,
            dias_semana=dias, hora_desde=hora_desde, hora_hasta=hora_hasta,
            cupo_maximo=cupo,
            maximo_unidades_codigo=int(datos.get("maximo_unidades_codigo", 1)),
            mostrar_codigos_restantes=bool(datos.get("mostrar_codigos_restantes")),
            tema_etiqueta=TemaEtiqueta(str(datos.get("tema_etiqueta") or "")),
            metodos=frozenset({MetodoUtilizacion.WHATSAPP}),
        )
    except ValidacionBeneficioError:
        raise
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidacionBeneficioError("configuración inválida") from error


def _fecha(valor) -> datetime:
    fecha = datetime.fromisoformat(str(valor or ""))
    if fecha.tzinfo is None or fecha.utcoffset() is None:
        raise ValidacionBeneficioError("las fechas deben incluir zona horaria")
    return fecha


def _hora(valor) -> time | None:
    return None if valor in (None, "") else time.fromisoformat(str(valor))


def _beneficio_json(beneficio: Beneficio) -> dict[str, object]:
    return {
        "id": beneficio.id,
        "objetivo_tipo": beneficio.objetivo.tipo.value,
        "objetivo_id": beneficio.objetivo.id,
        "publicacion_id": (
            beneficio.objetivo.id
            if beneficio.objetivo.tipo is TipoObjetivo.PUBLICACION
            else None
        ),
        "gastronomia_producto_id": (
            beneficio.objetivo.id
            if beneficio.objetivo.tipo is TipoObjetivo.GASTRONOMIA_PRODUCTO
            else None
        ),
        "serie_id": beneficio.serie_id or beneficio.id,
        "numero_edicion": beneficio.numero_edicion,
        "titulo": beneficio.titulo,
        "detalle_promocion": beneficio.detalle_promocion,
        "tipo_descuento": beneficio.tipo_descuento.value,
        "valor_descuento": str(beneficio.valor_descuento) if beneficio.valor_descuento is not None else None,
        "vigente_desde": beneficio.vigente_desde.isoformat(),
        "vigente_hasta": beneficio.vigente_hasta.isoformat(),
        "dias_semana": sorted(beneficio.dias_semana),
        "hora_desde": beneficio.hora_desde.isoformat() if beneficio.hora_desde else None,
        "hora_hasta": beneficio.hora_hasta.isoformat() if beneficio.hora_hasta else None,
        "cupo_maximo": beneficio.cupo_maximo,
        "maximo_unidades_codigo": beneficio.maximo_unidades_codigo,
        "mostrar_codigos_restantes": beneficio.mostrar_codigos_restantes,
        "tema_etiqueta": beneficio.tema_etiqueta.value,
        "metodos": [MetodoUtilizacion.WHATSAPP.value],
        "activo": beneficio.activo,
        "etiqueta": texto_etiqueta(beneficio),
    }


def texto_etiqueta(beneficio: Beneficio) -> str:
    fijas = {
        TipoDescuento.DOS_POR_UNO: "2x1", TipoDescuento.TRES_POR_DOS: "3x2",
        TipoDescuento.SEGUNDA_UNIDAD_50: "2ª AL 50%",
    }
    if beneficio.tipo_descuento in fijas:
        return fijas[beneficio.tipo_descuento]
    numero = format(beneficio.valor_descuento, "f")
    if "." in numero:
        numero = numero.rstrip("0").rstrip(".")
    if beneficio.tipo_descuento is TipoDescuento.PORCENTAJE:
        return f"{numero}% OFF"
    if beneficio.tipo_descuento is TipoDescuento.MONTO_FIJO:
        return f"$ {numero} OFF"
    return f"$ {numero}"
