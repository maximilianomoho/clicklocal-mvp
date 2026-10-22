"""Persistencia tolerante a fallos para distancias de delivery.

La caché es una optimización: los errores de lectura se expresan como miss y
los errores de escritura no invalidan una distancia calculada externamente.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone


LOGGER = logging.getLogger(__name__)
TABLA_CACHE_DELIVERY = "delivery_direcciones_cache"


@dataclass(frozen=True)
class IdentidadCacheDelivery:
    comercio_id: str
    direccion_normalizada: str
    origen_clave: str


def construir_identidad_cache(
    comercio_id,
    direccion_normalizada,
    origen_clave,
):
    comercio = str(comercio_id or "").strip()
    direccion = str(direccion_normalizada or "").strip()
    origen = str(origen_clave or "").strip()
    if not comercio or not direccion or not origen:
        raise ValueError("La identidad de caché de delivery no es válida.")
    return IdentidadCacheDelivery(comercio, direccion, origen)


def buscar_distancia_cache(db, identidad):
    try:
        filas = (
            db.table(TABLA_CACHE_DELIVERY)
            .select("distancia_m")
            .eq("comercio_id", identidad.comercio_id)
            .eq("direccion_normalizada", identidad.direccion_normalizada)
            .eq("origen_clave", identidad.origen_clave)
            .limit(1)
            .execute()
            .data
            or []
        )
        distancia = filas[0].get("distancia_m") if filas else None
        if isinstance(distancia, bool) or not isinstance(distancia, (int, float)):
            return None
        distancia = int(distancia)
        return distancia if distancia >= 0 else None
    except Exception:
        LOGGER.exception("No se pudo consultar la caché transversal de delivery")
        return None


def guardar_distancia_cache(db, identidad, distancia_m):
    if isinstance(distancia_m, bool) or not isinstance(distancia_m, (int, float)):
        return False
    distancia = int(distancia_m)
    if distancia < 0:
        return False
    try:
        (
            db.table(TABLA_CACHE_DELIVERY)
            .upsert(
                {
                    "comercio_id": identidad.comercio_id,
                    "direccion_normalizada": identidad.direccion_normalizada,
                    "origen_clave": identidad.origen_clave,
                    "distancia_m": distancia,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict=(
                    "comercio_id,direccion_normalizada,origen_clave"
                ),
            )
            .execute()
        )
        return True
    except Exception:
        LOGGER.exception("No se pudo guardar la caché transversal de delivery")
        return False
