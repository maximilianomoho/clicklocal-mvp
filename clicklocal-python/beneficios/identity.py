"""Identidades de Beneficios sin reemplazar autenticación ni autorización."""

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4


CLAVE_CONSUMIDOR = "beneficios_consumidor_id"
CLAVE_IDEMPOTENCIAS = "beneficios_idempotencias"


@dataclass(frozen=True, slots=True)
class IdentidadConsumidor:
    consumidor_id: str


@dataclass(frozen=True, slots=True)
class ContextoComercianteAutenticado:
    """
    Contexto producido por la autenticación existente de ClickLocal.

    Crear esta estructura no autoriza por sí solo una publicación.
    """

    usuario_id: str
    comercio_id: str


@dataclass(frozen=True, slots=True)
class IdentidadComerciante:
    usuario_id: str
    comercio_id: str


def resolver_identidad_consumidor(
    sesion: MutableMapping[str, Any],
) -> IdentidadConsumidor:
    """
    Crea o reutiliza un UUID guardado en la sesión firmada.

    La función no recibe identidad desde formularios, JSON, cabeceras
    ni parámetros de URL.
    """

    consumidor_id = _uuid_canonico(
        sesion.get(CLAVE_CONSUMIDOR)
    )
    if consumidor_id is None:
        consumidor_id = str(uuid4())
        sesion[CLAVE_CONSUMIDOR] = consumidor_id

    return IdentidadConsumidor(
        consumidor_id=consumidor_id,
    )


def resolver_idempotencia_beneficio(
    sesion: MutableMapping[str, Any],
    beneficio_id: str,
) -> str:
    """Crea una UUID estable por beneficio sin confiar en el navegador."""

    beneficio = _uuid_canonico(beneficio_id)
    if beneficio is None:
        raise ValueError("beneficio_id no es válido")

    guardadas = sesion.get(CLAVE_IDEMPOTENCIAS)
    if not isinstance(guardadas, dict):
        guardadas = {}

    existente = _uuid_canonico(guardadas.get(beneficio))
    if existente is None:
        existente = str(uuid4())
        guardadas = {
            clave: valor
            for clave, valor in guardadas.items()
            if _uuid_canonico(clave) is not None
            and _uuid_canonico(valor) is not None
        }
        guardadas[beneficio] = existente
        sesion[CLAVE_IDEMPOTENCIAS] = guardadas

    return existente


def resolver_identidad_comerciante(
    obtener_contexto_autenticado: Callable[
        [], ContextoComercianteAutenticado | None
    ],
) -> IdentidadComerciante:
    """
    Consume autenticación ya resuelta por ClickLocal.

    No consulta ni confía directamente en valores de la sesión.
    La pertenencia de cada publicación debe comprobarse aparte.
    """

    contexto = obtener_contexto_autenticado()
    if contexto is None:
        raise PermissionError(
            "Se requiere una sesión autenticada de comercio"
        )

    usuario_id = _uuid_canonico(contexto.usuario_id)
    comercio_id = _uuid_canonico(contexto.comercio_id)
    if usuario_id is None or comercio_id is None:
        raise PermissionError(
            "El contexto autenticado no es válido"
        )

    return IdentidadComerciante(
        usuario_id=usuario_id,
        comercio_id=comercio_id,
    )


def _uuid_canonico(valor: object) -> str | None:
    try:
        return str(UUID(str(valor)))
    except (AttributeError, TypeError, ValueError):
        return None
