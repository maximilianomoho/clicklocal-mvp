"""Superficie pública del módulo Beneficios ClickLocal 2.0."""

from .contracts import (
    generar_codigo_beneficio,
    obtener_detalle_beneficio,
    consultar_beneficios_vigentes,
    consultar_para_gastronomia_productos,
    consultar_para_publicaciones,
)
__all__ = (
    "consultar_beneficios_vigentes",
    "consultar_para_publicaciones",
    "consultar_para_gastronomia_productos",
    "obtener_detalle_beneficio",
    "generar_codigo_beneficio",
    "crear_blueprint_beneficios",
)


def crear_blueprint_beneficios(*args, **kwargs):
    """Carga Flask sólo cuando la integración web solicita el blueprint."""

    from .routes import crear_blueprint_beneficios as crear

    return crear(*args, **kwargs)
