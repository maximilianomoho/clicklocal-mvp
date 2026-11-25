"""Contratos públicos estables para consumidores internos de ClickLocal."""

from datetime import datetime

from .services import ServicioBeneficios
from .types import (
    Beneficio,
    CodigoBeneficio,
    ConsultaBeneficios,
    ObjetivoBeneficio,
    SolicitudCodigo,
    TipoObjetivo,
)


def consultar_beneficios_vigentes(
    objetivos: tuple[ObjetivoBeneficio, ...],
    instante: datetime,
    *,
    servicio: ServicioBeneficios,
) -> tuple[Beneficio, ...]:
    return servicio.consultar_vigentes(ConsultaBeneficios(objetivos, instante))


def consultar_para_publicaciones(
    publicacion_ids: tuple[str, ...],
    instante: datetime,
    *,
    servicio: ServicioBeneficios,
) -> tuple[Beneficio, ...]:
    objetivos = tuple(
        ObjetivoBeneficio(TipoObjetivo.PUBLICACION, item_id)
        for item_id in publicacion_ids
    )
    return consultar_beneficios_vigentes(objetivos, instante, servicio=servicio)


def consultar_para_comercios(
    comercio_ids: tuple[str, ...],
    instante: datetime,
    *,
    servicio: ServicioBeneficios,
) -> tuple[Beneficio, ...]:
    objetivos = tuple(
        ObjetivoBeneficio(TipoObjetivo.COMERCIO, comercio_id)
        for comercio_id in comercio_ids
    )
    return consultar_beneficios_vigentes(objetivos, instante, servicio=servicio)


def consultar_para_gastronomia_productos(
    producto_ids: tuple[str, ...],
    instante: datetime,
    *,
    servicio: ServicioBeneficios,
) -> tuple[Beneficio, ...]:
    objetivos = tuple(
        ObjetivoBeneficio(TipoObjetivo.GASTRONOMIA_PRODUCTO, item_id)
        for item_id in producto_ids
    )
    return consultar_beneficios_vigentes(objetivos, instante, servicio=servicio)


def obtener_detalle_beneficio(
    beneficio_id: str,
    *,
    servicio: ServicioBeneficios,
) -> Beneficio:
    return servicio.obtener_detalle(beneficio_id)


def generar_codigo_beneficio(
    solicitud: SolicitudCodigo,
    *,
    servicio: ServicioBeneficios,
) -> CodigoBeneficio:
    return servicio.generar_codigo(solicitud)
