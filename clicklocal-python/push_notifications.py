"""Servicio central de Web Push para dispositivos de Administración."""

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse


TABLA_SUSCRIPCIONES_ADMIN = "admin_push_suscripciones"


def configuracion_vapid():
    return {
        "public_key": os.getenv("WEBPUSH_VAPID_PUBLIC_KEY", "").strip(),
        "private_key": os.getenv("WEBPUSH_VAPID_PRIVATE_KEY", "").strip(),
        "subject": os.getenv("WEBPUSH_VAPID_SUBJECT", "").strip(),
    }


def webpush_disponible():
    config = configuracion_vapid()
    return all(config.values())


def validar_suscripcion(datos):
    if not isinstance(datos, dict):
        return None

    endpoint = datos.get("endpoint")
    keys = datos.get("keys")
    p256dh = keys.get("p256dh") if isinstance(keys, dict) else None
    auth = keys.get("auth") if isinstance(keys, dict) else None

    if not all(isinstance(valor, str) for valor in (endpoint, p256dh, auth)):
        return None

    endpoint = endpoint.strip()
    p256dh = p256dh.strip()
    auth = auth.strip()
    endpoint_parseado = urlparse(endpoint)
    if (
        endpoint_parseado.scheme != "https"
        or not endpoint_parseado.netloc
        or len(endpoint) > 2048
        or not 20 <= len(p256dh) <= 512
        or not 8 <= len(auth) <= 256
    ):
        return None

    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def guardar_suscripcion_admin(supabase, admin_user, datos):
    suscripcion = validar_suscripcion(datos)
    if not suscripcion or not admin_user:
        raise ValueError("Suscripción Web Push inválida")

    ahora = datetime.now(timezone.utc).isoformat()
    fila = {
        **suscripcion,
        "admin_user": str(admin_user),
        "activo": True,
        "updated_at": ahora,
    }
    return (
        supabase.table(TABLA_SUSCRIPCIONES_ADMIN)
        .upsert(fila, on_conflict="endpoint")
        .execute()
    )


def desactivar_suscripcion_admin(supabase, admin_user, endpoint):
    if not admin_user or not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("Endpoint Web Push inválido")

    return (
        supabase.table(TABLA_SUSCRIPCIONES_ADMIN)
        .update({
            "activo": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("admin_user", str(admin_user))
        .eq("endpoint", endpoint.strip())
        .execute()
    )


def _codigo_http_error(error):
    respuesta = getattr(error, "response", None)
    codigo = getattr(respuesta, "status_code", None)
    if codigo is None:
        codigo = getattr(error, "status_code", None)
    return codigo


def enviar_notificacion_admin(
    supabase,
    titulo,
    cuerpo,
    webpush_func=None,
):
    """Envía a cada dispositivo activo; nunca propaga fallos individuales."""
    if not webpush_disponible():
        return {"intentados": 0, "enviados": 0, "errores": 0, "disponible": False}

    if webpush_func is None:
        try:
            from pywebpush import webpush as webpush_func
        except ImportError as error:
            print("WEB PUSH NO DISPONIBLE: falta pywebpush", type(error).__name__, flush=True)
            return {"intentados": 0, "enviados": 0, "errores": 1, "disponible": False}

    try:
        respuesta = (
            supabase.table(TABLA_SUSCRIPCIONES_ADMIN)
            .select("id,endpoint,p256dh,auth")
            .eq("activo", True)
            .execute()
        )
        suscripciones = respuesta.data or []
    except Exception as error:
        print("ERROR LEYENDO SUSCRIPCIONES WEB PUSH:", type(error).__name__, error, flush=True)
        return {"intentados": 0, "enviados": 0, "errores": 1, "disponible": True}

    config = configuracion_vapid()
    resultado = {"intentados": 0, "enviados": 0, "errores": 0, "disponible": True}
    payload = json.dumps({"title": str(titulo), "body": str(cuerpo), "url": "/admin"})

    for fila in suscripciones:
        resultado["intentados"] += 1
        try:
            webpush_func(
                subscription_info={
                    "endpoint": fila.get("endpoint"),
                    "keys": {"p256dh": fila.get("p256dh"), "auth": fila.get("auth")},
                },
                data=payload,
                vapid_private_key=config["private_key"],
                vapid_claims={"sub": config["subject"]},
            )
            resultado["enviados"] += 1
        except Exception as error:
            resultado["errores"] += 1
            codigo = _codigo_http_error(error)
            print(
                "ERROR ENVIANDO WEB PUSH ADMIN:",
                type(error).__name__,
                f"status={codigo}" if codigo else "sin_status",
                flush=True,
            )
            if codigo in (404, 410):
                try:
                    (
                        supabase.table(TABLA_SUSCRIPCIONES_ADMIN)
                        .update({
                            "activo": False,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        })
                        .eq("id", fila.get("id"))
                        .execute()
                    )
                except Exception as error_actualizando:
                    print(
                        "ERROR DESACTIVANDO SUSCRIPCIÓN WEB PUSH:",
                        type(error_actualizando).__name__,
                        flush=True,
                    )

    return resultado
