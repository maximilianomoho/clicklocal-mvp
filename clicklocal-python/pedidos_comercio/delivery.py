"""Puente del dominio transversal a primitivas de delivery ya probadas.

Las funciones no dependen de productos ni pedidos gastronómicos. Se mantienen
en su ubicación actual para evitar una refactorización riesgosa en esta etapa.
"""

from gastronomia.services.delivery import (  # noqa: F401
    DeliveryError,
    clave_cache_delivery,
    clave_origen_coordenadas,
    completar_direccion_destino,
    consultar_distancia_osm,
    firmar_cotizacion,
    geocodificar_direccion_nominatim,
    normalizar_direccion,
    seleccionar_franja,
    validar_configuracion_delivery,
    validar_coordenadas,
    validar_cotizacion,
)
