"""Servicios de identidad, puntuación y rankings de ClickJuegos."""

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
import secrets
from threading import Lock
import unicodedata
from zoneinfo import ZoneInfo

from config.supabase_config import supabase_admin

COOKIE_JUGADOR = "clickjuegos_jugador"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60
MAX_PUNTOS = 400
MIN_PUNTOS = 24
ALGORITHM_VERSION = 1
NONCE_TTL_SECONDS = 120
REFLEJOS_DELAY_MIN_MS = 1500
REFLEJOS_DELAY_MAX_MS = 4500
REFLEJOS_CONSISTENCY_TOLERANCE_MS = 350
REFLEJOS_MAX_OVERHEAD_MS = 5000
PARTIDAS_POR_MINUTO = 8
RANKING_LIMITE = 20
ZONA_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")

_rate_local = defaultdict(deque)
_rate_lock = Lock()


class JuegoError(ValueError):
    def __init__(self, mensaje, status=400):
        super().__init__(mensaje)
        self.status = status


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_valido(token):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{40,100}", str(token or "")))


def resolver_o_crear_jugador(token_cookie=None, db=None):
    """Devuelve (jugador, token_nuevo); en base sólo persiste SHA-256."""
    db = db or supabase_admin
    token = str(token_cookie or "")
    if _token_valido(token):
        res = (db.table("jugadores")
               .select("id,alias,activo,created_at")
               .eq("browser_token_hash", _hash_token(token))
               .limit(1).execute())
        if res.data:
            return res.data[0], None

    token_nuevo = secrets.token_urlsafe(48)
    res = db.table("jugadores").insert({
        "browser_token_hash": _hash_token(token_nuevo),
        "activo": True,
    }).execute()
    if not res.data:
        raise JuegoError("No se pudo crear la identidad del jugador.", 503)
    return res.data[0], token_nuevo


def aplicar_cookie_jugador(respuesta, token_nuevo, secure=False):
    if token_nuevo:
        respuesta.set_cookie(
            COOKIE_JUGADOR, token_nuevo, max_age=COOKIE_MAX_AGE,
            httponly=True, secure=bool(secure), samesite="Lax", path="/jugar",
        )
    return respuesta


def obtener_juego(slug, db=None):
    db = db or supabase_admin
    res = (db.table("juegos")
           .select("id,slug,nombre,activo,ranking_direction,score_unit,score_min,score_max")
           .eq("slug", slug).eq("activo", True).limit(1).execute())
    if not res.data:
        raise JuegoError("El juego no está disponible.", 503)
    return res.data[0]


def obtener_juego_circulo(db=None):
    return obtener_juego("circulo", db=db)


def obtener_juego_reflejos(db=None):
    return obtener_juego("reflejos", db=db)


def contar_partidas_validas(juego_id, db=None):
    """Cuenta partidas válidas sin descargar sus filas."""
    db = db or supabase_admin
    resultado = (
        db.table("partidas")
        .select("id", count="exact", head=True)
        .eq("juego_id", juego_id)
        .eq("valida", True)
        .execute()
    )
    return resultado.count if isinstance(resultado.count, int) else 0


def formatear_partidas_jugadas(cantidad):
    cantidad = max(0, int(cantidad or 0))
    numero = f"{cantidad:,}".replace(",", ".")
    return f"{numero} partida jugada" if cantidad == 1 else f"{numero} partidas jugadas"


def obtener_resumen_juegos(slugs, db=None):
    """Devuelve catálogo y contador público para varios juegos activos."""
    db = db or supabase_admin
    slugs = list(dict.fromkeys(str(slug) for slug in slugs))
    if not slugs:
        return {}
    juegos = (
        db.table("juegos")
        .select("id,slug,nombre,activo,ranking_direction,score_unit,score_min,score_max")
        .in_("slug", slugs)
        .eq("activo", True)
        .execute()
    ).data or []
    resumen = {}
    for juego in juegos:
        total = contar_partidas_validas(juego["id"], db=db)
        resumen[juego["slug"]] = {
            **juego,
            "partidas_jugadas": total,
            "partidas_texto": formatear_partidas_jugadas(total),
        }
    return resumen


def iniciar_intento(jugador_id, juego_id, ahora=None, db=None, ready_at=None):
    ahora = ahora or datetime.now(timezone.utc)
    db = db or supabase_admin
    nonce = secrets.token_urlsafe(32)
    resultado = (
        db.table("jugadores")
        .update({
            "intento_token_hash": _hash_token(nonce),
            "intento_juego_id": juego_id,
            "intento_expires_at": (
                ahora + timedelta(seconds=NONCE_TTL_SECONDS)
            ).isoformat(),
            "intento_ready_at": ready_at.isoformat() if ready_at else None,
        })
        .eq("id", jugador_id)
        .execute()
    )
    if not resultado.data:
        raise JuegoError("No se pudo iniciar la partida.", 503)
    return nonce


def iniciar_intento_reflejos(jugador_id, juego_id, ahora=None, db=None):
    ahora = ahora or datetime.now(timezone.utc)
    delay_ms = REFLEJOS_DELAY_MIN_MS + secrets.randbelow(
        REFLEJOS_DELAY_MAX_MS - REFLEJOS_DELAY_MIN_MS + 1
    )
    ready_at = ahora + timedelta(milliseconds=delay_ms)
    nonce = iniciar_intento(
        jugador_id, juego_id, ahora=ahora, db=db, ready_at=ready_at,
    )
    return {"nonce": nonce, "delay_ms": delay_ms, "ready_at": ready_at}


def consumir_intento(jugador_id, juego_id, nonce, ahora=None, db=None,
                     devolver_detalles=False):
    ahora = ahora or datetime.now(timezone.utc)
    db = db or supabase_admin
    if not _token_valido(nonce):
        raise JuegoError("El intento no es válido. Iniciá una partida nueva.", 409)

    token_hash = _hash_token(nonce)
    intento = (
        db.table("jugadores")
        .select("id,intento_ready_at,intento_expires_at")
        .eq("id", jugador_id)
        .eq("intento_token_hash", token_hash)
        .eq("intento_juego_id", juego_id)
        .gt("intento_expires_at", ahora.isoformat())
        .limit(1)
        .execute()
    )
    if not intento.data:
        raise JuegoError("El intento no es válido. Iniciá una partida nueva.", 409)

    # PostgreSQL vuelve a comprobar los filtros del UPDATE después de
    # adquirir el lock de la fila. Dos requests concurrentes no pueden
    # limpiar correctamente el mismo hash: sólo uno recibe la fila.
    resultado = (
        db.table("jugadores")
        .update({
            "intento_token_hash": None,
            "intento_juego_id": None,
            "intento_expires_at": None,
            "intento_ready_at": None,
        })
        .eq("id", jugador_id)
        .eq("intento_token_hash", token_hash)
        .eq("intento_juego_id", juego_id)
        .gt("intento_expires_at", ahora.isoformat())
        .execute()
    )
    if not resultado.data:
        raise JuegoError("El intento no es válido. Iniciá una partida nueva.", 409)
    return intento.data[0] if devolver_detalles else True


def _validar_puntos(payload):
    if not isinstance(payload, dict):
        raise JuegoError("El contenido de la partida no es válido.")
    puntos_crudos = payload.get("points")
    if not isinstance(puntos_crudos, list):
        raise JuegoError("Falta el trazo de la partida.")
    if not MIN_PUNTOS <= len(puntos_crudos) <= MAX_PUNTOS:
        raise JuegoError(f"El trazo debe contener entre {MIN_PUNTOS} y {MAX_PUNTOS} puntos.")
    puntos = []
    for punto in puntos_crudos:
        if not isinstance(punto, list) or len(punto) != 2:
            raise JuegoError("El trazo contiene un punto inválido.")
        x, y = punto
        if isinstance(x, bool) or isinstance(y, bool):
            raise JuegoError("El trazo contiene coordenadas inválidas.")
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            raise JuegoError("El trazo contiene coordenadas inválidas.")
        if not math.isfinite(x) or not math.isfinite(y):
            raise JuegoError("El trazo contiene coordenadas no finitas.")
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise JuegoError("El trazo contiene coordenadas fuera de rango.")
        puntos.append((x, y))
    return puntos


def calcular_score_circulo(payload):
    """Score V1: radios, cierre, cobertura angular y proporción del trazo."""
    puntos = _validar_puntos(payload)
    xs, ys = [p[0] for p in puntos], [p[1] for p in puntos]
    ancho, alto = max(xs) - min(xs), max(ys) - min(ys)
    if min(ancho, alto) < 0.08:
        raise JuegoError("El trazo es demasiado pequeño o degenerado.")
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    radios = [math.hypot(x - cx, y - cy) for x, y in puntos]
    radio_medio = sum(radios) / len(radios)
    if radio_medio < 0.04:
        raise JuegoError("El trazo es demasiado pequeño.")
    varianza = sum((r - radio_medio) ** 2 for r in radios) / len(radios)
    dispersion = math.sqrt(varianza) / radio_medio
    cierre = math.hypot(puntos[0][0] - puntos[-1][0], puntos[0][1] - puntos[-1][1]) / radio_medio
    angulos = sorted((math.atan2(y - cy, x - cx) + 2 * math.pi) % (2 * math.pi) for x, y in puntos)
    saltos = [angulos[i + 1] - angulos[i] for i in range(len(angulos) - 1)]
    saltos.append(angulos[0] + 2 * math.pi - angulos[-1])
    cobertura = max(0.0, min(1.0, 1.0 - max(saltos) / math.pi))
    proporcion = min(ancho, alto) / max(ancho, alto)
    factor_cierre = max(0.0, min(1.0, 1.0 - cierre / 0.75))
    score = round(max(0.0, min(100.0, 100 * math.exp(-4.2 * dispersion) * factor_cierre * cobertura * proporcion ** 0.35)), 2)
    return {"score": score, "metadata": {
        "algorithm_version": ALGORITHM_VERSION,
        "point_count": len(puntos),
        "radial_dispersion": round(dispersion, 6),
        "closure_ratio": round(cierre, 6),
        "angular_coverage": round(cobertura, 6),
        "aspect_ratio": round(proporcion, 6),
    }}


def verificar_rate_limit(jugador_id, ahora=None, db=None):
    ahora, db = ahora or datetime.now(timezone.utc), db or supabase_admin
    marca, clave = ahora.timestamp(), str(jugador_id)
    with _rate_lock:
        marcas = _rate_local[clave]
        while marcas and marcas[0] <= marca - 60:
            marcas.popleft()
        if len(marcas) >= PARTIDAS_POR_MINUTO:
            raise JuegoError("Jugaste demasiadas partidas seguidas. Esperá un momento.", 429)
    desde = (ahora - timedelta(minutes=1)).isoformat()
    res = (db.table("partidas").select("id").eq("jugador_id", jugador_id)
           .gte("created_at", desde).limit(PARTIDAS_POR_MINUTO).execute())
    if len(res.data or []) >= PARTIDAS_POR_MINUTO:
        raise JuegoError("Jugaste demasiadas partidas seguidas. Esperá un momento.", 429)


def marcar_rate_limit(jugador_id, ahora=None):
    with _rate_lock:
        _rate_local[str(jugador_id)].append((ahora or datetime.now(timezone.utc)).timestamp())


def mejor_marca(jugador_id, juego_id, db=None, ranking_direction="higher"):
    db = db or supabase_admin
    res = (db.table("partidas").select("id,score,created_at")
           .eq("jugador_id", jugador_id).eq("juego_id", juego_id).eq("valida", True)
           .order("score", desc=ranking_direction != "lower")
           .order("created_at").limit(1).execute())
    return res.data[0] if res.data else None


def registrar_partida(jugador, juego, payload, db=None, ahora=None):
    db, ahora = db or supabase_admin, ahora or datetime.now(timezone.utc)
    verificar_rate_limit(jugador["id"], ahora=ahora, db=db)
    calculo = calcular_score_circulo(payload)
    anterior = mejor_marca(
        jugador["id"], juego["id"], db=db,
        ranking_direction=juego.get("ranking_direction", "higher"),
    )
    res = db.table("partidas").insert({
        "jugador_id": jugador["id"], "juego_id": juego["id"],
        "score": calculo["score"], "metadata": calculo["metadata"], "valida": True,
    }).execute()
    if not res.data:
        raise JuegoError("No se pudo guardar la partida.", 503)
    marcar_rate_limit(jugador["id"], ahora=ahora)
    anterior_score = float(anterior["score"]) if anterior else None
    return {"partida": res.data[0], "score": calculo["score"],
            "personal_best": max(calculo["score"], anterior_score or 0),
            "is_new_record": anterior_score is None or calculo["score"] > anterior_score,
            "metadata": calculo["metadata"]}


def validar_reaccion_reflejos(reaction_ms, intento, juego, ahora=None):
    ahora = ahora or datetime.now(timezone.utc)
    if isinstance(reaction_ms, bool):
        raise JuegoError("El tiempo de reacción no es válido.")
    try:
        reaction_ms = float(reaction_ms)
    except (TypeError, ValueError):
        raise JuegoError("El tiempo de reacción no es válido.")
    if not math.isfinite(reaction_ms):
        raise JuegoError("El tiempo de reacción no es válido.")

    ready_raw = intento.get("intento_ready_at")
    if not ready_raw:
        raise JuegoError("El intento no es válido. Iniciá una partida nueva.", 409)
    ready_at = datetime.fromisoformat(str(ready_raw).replace("Z", "+00:00"))
    if ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    elapsed_ms = (ahora - ready_at).total_seconds() * 1000
    if elapsed_ms < 0:
        raise JuegoError("Te adelantaste. Esperá a que cambie la pantalla.", 409)

    minimo = float(juego.get("score_min") if juego.get("score_min") is not None else 80)
    maximo = float(juego.get("score_max") if juego.get("score_max") is not None else 3000)
    if reaction_ms < minimo or reaction_ms > maximo:
        raise JuegoError(
            f"El tiempo debe estar entre {int(minimo)} y {int(maximo)} ms."
        )
    if reaction_ms > elapsed_ms + REFLEJOS_CONSISTENCY_TOLERANCE_MS:
        raise JuegoError("El tiempo informado no coincide con el intento.", 409)
    if elapsed_ms - reaction_ms > REFLEJOS_MAX_OVERHEAD_MS:
        raise JuegoError("El tiempo informado no coincide con el intento.", 409)
    return int(math.floor(reaction_ms + 0.5))


def registrar_partida_reflejos(jugador, juego, reaction_ms, intento, db=None,
                               ahora=None):
    db, ahora = db or supabase_admin, ahora or datetime.now(timezone.utc)
    verificar_rate_limit(jugador["id"], ahora=ahora, db=db)
    score = validar_reaccion_reflejos(reaction_ms, intento, juego, ahora=ahora)
    anterior = mejor_marca(
        jugador["id"], juego["id"], db=db, ranking_direction="lower",
    )
    metadata = {
        "measurement": "performance.now",
        "server_validation": "ready_at_v1",
    }
    res = db.table("partidas").insert({
        "jugador_id": jugador["id"], "juego_id": juego["id"],
        "score": score, "metadata": metadata, "valida": True,
    }).execute()
    if not res.data:
        raise JuegoError("No se pudo guardar la partida.", 503)
    marcar_rate_limit(jugador["id"], ahora=ahora)
    anterior_score = int(float(anterior["score"])) if anterior else None
    personal_best = score if anterior_score is None else min(score, anterior_score)
    return {
        "partida": res.data[0], "score": score,
        "personal_best": personal_best,
        "is_new_record": anterior_score is None or score < anterior_score,
        "metadata": metadata,
    }


def normalizar_alias(alias):
    alias = " ".join(str(alias or "").strip().split())
    if not 3 <= len(alias) <= 24:
        raise JuegoError("El nombre debe tener entre 3 y 24 caracteres.")
    if not re.fullmatch(r"[\w\- ]+", alias, flags=re.UNICODE):
        raise JuegoError("Usá solamente letras, números, espacios, guion o guión bajo.")
    normalizado = unicodedata.normalize("NFKD", alias.casefold())
    normalizado = "".join(c for c in normalizado if not unicodedata.combining(c))
    if normalizado in {"admin", "administrador", "clicklocal", "clickjuegos", "soporte"} or any(p in normalizado.split() for p in {"puto", "puta", "mierda", "forro"}):
        raise JuegoError("Ese nombre no está disponible.")
    return alias, normalizado


def guardar_alias(jugador_id, alias, db=None):
    db = db or supabase_admin
    alias, normalizado = normalizar_alias(alias)
    existente = (db.table("jugadores").select("id").eq("alias_normalizado", normalizado)
                 .neq("id", jugador_id).limit(1).execute())
    if existente.data:
        raise JuegoError("Ese nombre ya está en uso.", 409)
    try:
        res = (db.table("jugadores").update({"alias": alias, "alias_normalizado": normalizado})
               .eq("id", jugador_id).execute())
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            raise JuegoError("Ese nombre ya está en uso.", 409) from exc
        raise
    if not res.data:
        raise JuegoError("No se pudo guardar el nombre.", 503)
    return res.data[0]


def limites_periodo(periodo, ahora=None):
    local = (ahora or datetime.now(timezone.utc)).astimezone(ZONA_ARGENTINA)
    if periodo == "semana":
        inicio = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        fin = inicio + timedelta(days=7)
    elif periodo == "mes":
        inicio = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        fin = inicio.replace(year=inicio.year + 1, month=1) if inicio.month == 12 else inicio.replace(month=inicio.month + 1)
    elif periodo == "historico":
        return None, None
    else:
        raise JuegoError("Período de ranking inválido.")
    return inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc)


def obtener_ranking(juego_id, periodo, jugador_id=None, db=None, ahora=None,
                    limite=RANKING_LIMITE, ranking_direction="higher"):
    db = db or supabase_admin
    inicio, fin = limites_periodo(periodo, ahora=ahora)
    consulta = db.table("partidas").select("id,jugador_id,score,created_at").eq("juego_id", juego_id).eq("valida", True)
    if inicio:
        consulta = consulta.gte("created_at", inicio.isoformat()).lt("created_at", fin.isoformat())
    partidas = consulta.order("created_at").execute().data or []
    ids = sorted({str(p["jugador_id"]) for p in partidas})
    if not ids:
        return {"entries": [], "player_position": None}
    jugadores = (db.table("jugadores").select("id,alias,activo").in_("id", ids).eq("activo", True).execute()).data or []
    visibles = {str(j["id"]): j for j in jugadores if j.get("alias")}
    mejores = {}
    for partida in partidas:
        jid = str(partida["jugador_id"])
        if jid not in visibles:
            continue
        candidato = (float(partida["score"]), str(partida["created_at"]), str(partida["id"]))
        actual = mejores.get(jid)
        if actual is None:
            mejores[jid] = candidato
            continue
        valor_candidato = candidato[0] if ranking_direction == "lower" else -candidato[0]
        valor_actual = actual[0] if ranking_direction == "lower" else -actual[0]
        if (valor_candidato, candidato[1], candidato[2]) < (valor_actual, actual[1], actual[2]):
            mejores[jid] = candidato
    signo = 1 if ranking_direction == "lower" else -1
    ordenados = sorted(mejores.items(), key=lambda item: (signo * item[1][0], item[1][1], item[1][2]))
    entries, posicion_jugador = [], None
    for posicion, (jid, datos) in enumerate(ordenados, start=1):
        if posicion <= limite:
            entries.append({"position": posicion, "alias": visibles[jid]["alias"], "score": datos[0]})
        if jugador_id and jid == str(jugador_id):
            posicion_jugador = posicion
    return {"entries": entries, "player_position": posicion_jugador}
