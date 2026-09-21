from flask import jsonify, make_response, redirect, render_template, request, url_for
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from . import juegos_bp
from .services import (
    COOKIE_JUGADOR, JuegoError, aplicar_cookie_jugador, consumir_intento,
    contar_partidas_validas, formatear_partidas_jugadas, guardar_alias,
    iniciar_intento, iniciar_intento_reflejos, mejor_marca,
    obtener_juego_circulo, obtener_juego_reflejos, obtener_ranking,
    obtener_resumen_juegos, registrar_partida, registrar_partida_reflejos,
    resolver_o_crear_jugador,
)

MAX_PAYLOAD_BYTES = 48 * 1024
ANALYTICS_UTM_CAMPOS = ("utm_source", "utm_medium", "utm_campaign")


def _identidad_actual():
    return resolver_o_crear_jugador(request.cookies.get(COOKIE_JUGADOR))


def _con_cookie(respuesta, token_nuevo):
    return aplicar_cookie_jugador(respuesta, token_nuevo, secure=request.is_secure)


def _json_error(error):
    status = error.status if isinstance(error, JuegoError) else 500
    mensaje = str(error) if isinstance(error, JuegoError) else "Ocurrió un error inesperado."
    return jsonify({"ok": False, "error": mensaje}), status


def _mismo_origen(valor):
    try:
        origen = urlsplit(str(valor or ""))
    except ValueError:
        return False
    esquema_proxy = str(
        request.headers.get("X-Forwarded-Proto") or request.scheme
    ).split(",", 1)[0].strip().lower()
    if esquema_proxy not in {"http", "https"}:
        esquema_proxy = request.scheme
    return (
        origen.scheme.lower() == esquema_proxy
        and origen.netloc.lower() == request.host.lower()
    )


def _validar_origen_post():
    """Valida navegador same-origin sin romper clientes sin esos headers."""
    origin = request.headers.get("Origin")
    if origin:
        return _mismo_origen(origin)
    referer = request.headers.get("Referer")
    if referer:
        return _mismo_origen(referer)
    return True


def _proteccion_post_json():
    if not request.is_json:
        return jsonify({"ok": False, "error": "Se esperaba contenido JSON."}), 415
    if not _validar_origen_post():
        return jsonify({"ok": False, "error": "Origen de solicitud no permitido."}), 403
    return None


def _texto_analytics(valor, limite=160):
    texto = " ".join(str(valor or "").strip().split())
    return texto[:limite] or None


def _referrer_saneado():
    valor = _texto_analytics(request.referrer, 500)
    if not valor:
        return None
    try:
        parsed = urlsplit(valor)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    # Para adquisición alcanza el origen. No se guardan paths ni queries,
    # que podrían contener información innecesaria.
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _visitante_nuevo():
    try:
        UUID(str(request.cookies.get("clicklocal_visitante_id")))
        return False
    except (ValueError, TypeError, AttributeError):
        return True


def _metadata_analytics(evento, pagina=None, jugador_id=None, **extras):
    metadata = {
        "evento": evento,
        "pagina": pagina or request.path,
    }
    if jugador_id:
        metadata["jugador_id"] = str(jugador_id)
    referrer = _referrer_saneado()
    if referrer:
        metadata["referrer"] = referrer
    for campo in ANALYTICS_UTM_CAMPOS:
        valor = _texto_analytics(request.args.get(campo), 100)
        if valor:
            metadata[campo] = valor
    metadata.update({k: v for k, v in extras.items() if v is not None})
    return metadata


def _registrar_evento_clickjuegos(evento, **metadata):
    # Import tardío: app.py registra el blueprint antes de definir el
    # registrador. Al ejecutarse una request, la aplicación ya está cargada.
    from app import analytics_registrar_evento

    return analytics_registrar_evento(
        "clickjuegos_evento",
        origen="clickjuegos",
        metadata=_metadata_analytics(evento, **metadata),
    )


@juegos_bp.route("")
@juegos_bp.route("/")
def inicio():
    jugador, token_nuevo = _identidad_actual()
    juegos = obtener_resumen_juegos(("circulo", "reflejos"))
    _registrar_evento_clickjuegos(
        "visita_clickjuegos",
        pagina="/jugar",
        jugador_id=jugador["id"],
        visitante_nuevo=_visitante_nuevo(),
        jugador_nuevo=bool(token_nuevo),
    )
    return _con_cookie(make_response(render_template(
        "juegos/index.html", jugador=jugador, juegos=juegos,
    )), token_nuevo)


@juegos_bp.get("/circulo")
def circulo():
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_circulo()
        mejor = mejor_marca(jugador["id"], juego["id"])
        partidas_texto = formatear_partidas_jugadas(
            contar_partidas_validas(juego["id"]),
        )
        _registrar_evento_clickjuegos(
            "juego_abierto",
            pagina="/jugar/circulo",
            jugador_id=jugador["id"],
            juego_slug="circulo",
            visitante_nuevo=_visitante_nuevo(),
            jugador_nuevo=bool(token_nuevo),
        )
        respuesta = make_response(render_template(
            "juegos/circulo.html", jugador=jugador,
            mejor_score=float(mejor["score"]) if mejor else None,
            partidas_texto=partidas_texto,
        ))
        return _con_cookie(respuesta, token_nuevo)
    except JuegoError as error:
        return str(error), error.status


@juegos_bp.get("/reflejos")
def reflejos():
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_reflejos()
        mejor = mejor_marca(
            jugador["id"], juego["id"], ranking_direction="lower",
        )
        partidas_texto = formatear_partidas_jugadas(
            contar_partidas_validas(juego["id"]),
        )
        _registrar_evento_clickjuegos(
            "juego_abierto", pagina="/jugar/reflejos",
            jugador_id=jugador["id"], juego="reflejos",
            visitante_nuevo=_visitante_nuevo(),
            jugador_nuevo=bool(token_nuevo),
        )
        respuesta = make_response(render_template(
            "juegos/reflejos.html", jugador=jugador,
            mejor_score=int(float(mejor["score"])) if mejor else None,
            partidas_texto=partidas_texto,
        ))
        return _con_cookie(respuesta, token_nuevo)
    except JuegoError as error:
        return str(error), error.status


@juegos_bp.post("/api/circulo/intentos")
def api_iniciar_intento_circulo():
    proteccion = _proteccion_post_json()
    if proteccion:
        return proteccion
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_circulo()
        nonce = iniciar_intento(jugador["id"], juego["id"])
        _registrar_evento_clickjuegos(
            "partida_iniciada",
            jugador_id=jugador["id"],
            juego_slug="circulo",
        )
        return _con_cookie(jsonify({"ok": True, "nonce": nonce, "expires_in": 120}), token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.post("/api/reflejos/intentos")
def api_iniciar_intento_reflejos():
    proteccion = _proteccion_post_json()
    if proteccion:
        return proteccion
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_reflejos()
        intento = iniciar_intento_reflejos(jugador["id"], juego["id"])
        _registrar_evento_clickjuegos(
            "partida_iniciada", jugador_id=jugador["id"], juego="reflejos",
        )
        respuesta = jsonify({
            "ok": True, "nonce": intento["nonce"],
            "delay_ms": intento["delay_ms"], "expires_in": 120,
        })
        return _con_cookie(respuesta, token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.post("/api/circulo/partidas")
def api_registrar_partida_circulo():
    if request.content_length and request.content_length > MAX_PAYLOAD_BYTES:
        return jsonify({"ok": False, "error": "El trazo enviado es demasiado grande."}), 413
    proteccion = _proteccion_post_json()
    if proteccion:
        return proteccion
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_circulo()
        payload = request.get_json(silent=False)
        nonce = payload.get("nonce") if isinstance(payload, dict) else None
        consumir_intento(jugador["id"], juego["id"], nonce)
        resultado = registrar_partida(jugador, juego, payload)
        ranking = obtener_ranking(juego["id"], "semana", jugador_id=jugador["id"])
        _registrar_evento_clickjuegos(
            "partida_completada",
            jugador_id=jugador["id"],
            juego_slug="circulo",
            score=resultado["score"],
        )
        if resultado["is_new_record"]:
            _registrar_evento_clickjuegos(
                "nuevo_record",
                jugador_id=jugador["id"],
                juego_slug="circulo",
                score=resultado["score"],
            )
        respuesta = jsonify({
            "ok": True, "score": resultado["score"],
            "personal_best": resultado["personal_best"],
            "is_new_record": resultado["is_new_record"],
            "weekly_position": ranking["player_position"],
            "has_alias": bool(jugador.get("alias")),
        })
        return _con_cookie(respuesta, token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.post("/api/reflejos/partidas")
def api_registrar_partida_reflejos():
    proteccion = _proteccion_post_json()
    if proteccion:
        return proteccion
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_reflejos()
        payload = request.get_json(silent=False)
        nonce = payload.get("nonce") if isinstance(payload, dict) else None
        reaction_ms = payload.get("reaction_ms") if isinstance(payload, dict) else None
        ahora = datetime.now(timezone.utc)
        intento = consumir_intento(
            jugador["id"], juego["id"], nonce, ahora=ahora,
            devolver_detalles=True,
        )
        resultado = registrar_partida_reflejos(
            jugador, juego, reaction_ms, intento, ahora=ahora,
        )
        ranking = obtener_ranking(
            juego["id"], "semana", jugador_id=jugador["id"],
            ranking_direction="lower",
        )
        _registrar_evento_clickjuegos(
            "partida_completada", jugador_id=jugador["id"],
            juego="reflejos", score=resultado["score"],
        )
        if resultado["is_new_record"]:
            _registrar_evento_clickjuegos(
                "nuevo_record", jugador_id=jugador["id"],
                juego="reflejos", score=resultado["score"],
            )
        respuesta = jsonify({
            "ok": True, "score": resultado["score"],
            "personal_best": resultado["personal_best"],
            "is_new_record": resultado["is_new_record"],
            "weekly_position": ranking["player_position"],
            "has_alias": bool(jugador.get("alias")),
        })
        return _con_cookie(respuesta, token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.post("/api/reflejos/intentos/cancelar")
def api_cancelar_intento_reflejos():
    proteccion = _proteccion_post_json()
    if proteccion:
        return proteccion
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_reflejos()
        payload = request.get_json(silent=False)
        nonce = payload.get("nonce") if isinstance(payload, dict) else None
        consumir_intento(jugador["id"], juego["id"], nonce)
        return _con_cookie(jsonify({"ok": True, "cancelled": True}), token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.post("/api/jugador/alias")
def api_guardar_alias():
    proteccion = _proteccion_post_json()
    if proteccion:
        return proteccion
    try:
        jugador, token_nuevo = _identidad_actual()
        actualizado = guardar_alias(jugador["id"], (request.get_json(silent=False) or {}).get("alias"))
        _registrar_evento_clickjuegos(
            "alias_creado",
            jugador_id=jugador["id"],
        )
        return _con_cookie(jsonify({"ok": True, "alias": actualizado["alias"]}), token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.get("/api/circulo/ranking")
def api_ranking_circulo():
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_circulo()
        periodo = str(request.args.get("periodo") or "semana")
        ranking = obtener_ranking(juego["id"], periodo, jugador_id=jugador["id"])
        _registrar_evento_clickjuegos(
            "ranking_visto",
            jugador_id=jugador["id"],
            juego_slug="circulo",
            periodo=periodo,
        )
        return _con_cookie(jsonify({"ok": True, "period": periodo, **ranking}), token_nuevo)
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.get("/api/reflejos/ranking")
def api_ranking_reflejos():
    try:
        jugador, token_nuevo = _identidad_actual()
        juego = obtener_juego_reflejos()
        periodo = str(request.args.get("periodo") or "semana")
        ranking = obtener_ranking(
            juego["id"], periodo, jugador_id=jugador["id"],
            ranking_direction="lower",
        )
        _registrar_evento_clickjuegos(
            "ranking_visto", jugador_id=jugador["id"],
            juego="reflejos", periodo=periodo,
        )
        return _con_cookie(
            jsonify({"ok": True, "period": periodo, **ranking}), token_nuevo,
        )
    except JuegoError as error:
        return _json_error(error)


@juegos_bp.get("/salir/galeria")
def salida_galeria():
    _registrar_evento_clickjuegos(
        "click_galeria",
        pagina=_texto_analytics(request.args.get("desde"), 80) or request.path,
        destino="galeria",
    )
    return redirect(url_for("inicio"))


@juegos_bp.get("/salir/gastronomia")
def salida_gastronomia():
    _registrar_evento_clickjuegos(
        "click_gastronomia",
        pagina=_texto_analytics(request.args.get("desde"), 80) or request.path,
        destino="gastronomia",
    )
    return redirect(url_for("gastronomia.inicio"))
