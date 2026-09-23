from decimal import Decimal, InvalidOperation
from uuid import UUID

from flask import abort, render_template, session

from modulos import requerir_modulo

from . import contenido_bp
from .services.fuentes import (
    obtener_publicacion_comercio,
    obtener_publicaciones_comercio,
)


FORMATOS_CONTENIDO = (
    {"slug": "post", "nombre": "Post", "medidas": "1080 × 1080"},
    {
        "slug": "historia",
        "nombre": "Historia",
        "medidas": "1080 × 1920",
    },
    {
        "slug": "estado",
        "nombre": "Estado de WhatsApp",
        "medidas": "1080 × 1920",
    },
)


def _precio_para_texto(valor):
    if valor is None or valor == "":
        return ""
    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        return str(valor).strip()
    if numero == numero.to_integral_value():
        return "$ " + f"{int(numero):,}".replace(",", ".")
    entero, decimales = f"{numero:.2f}".split(".")
    entero = f"{int(entero):,}".replace(",", ".")
    return f"$ {entero},{decimales}"


def _texto_inicial(publicacion, comercio):
    partes = [str(publicacion.get("nombre") or "").strip()]
    descripcion = str(publicacion.get("descripcion") or "").strip()
    precio = _precio_para_texto(publicacion.get("precio"))
    comercio_nombre = str(
        comercio.get("nombre_negocio")
        or comercio.get("nombre")
        or ""
    ).strip()
    partes.extend([descripcion, precio, comercio_nombre])
    return "\n\n".join(parte for parte in partes if parte)


@contenido_bp.route("/")
@requerir_modulo("contenido")
def panel_contenido():
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")
    publicaciones = obtener_publicaciones_comercio(comercio_id)

    return render_template(
        "contenido/panel.html",
        comercio=comercio,
        publicaciones=publicaciones,
    )


@contenido_bp.route("/publicacion/<publicacion_id>")
@requerir_modulo("contenido")
def crear_desde_publicacion(publicacion_id):
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    try:
        publicacion_id = str(UUID(str(publicacion_id)))
    except (TypeError, ValueError, AttributeError):
        abort(404)

    publicacion = obtener_publicacion_comercio(
        publicacion_id,
        comercio_id,
    )
    if not publicacion:
        abort(404)

    return render_template(
        "contenido/crear.html",
        comercio=comercio,
        publicacion=publicacion,
        formatos=FORMATOS_CONTENIDO,
        precio_mostrar=_precio_para_texto(publicacion.get("precio")),
        texto_inicial=_texto_inicial(publicacion, comercio),
    )
