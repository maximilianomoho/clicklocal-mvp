from urllib.parse import urlsplit

from config.supabase_config import supabase_admin


CAMPOS_PUBLICACION_CONTENIDO = (
    "id,comercio_id,nombre,descripcion,precio,imagenes,"
    "imagen_principal,imagen_url,activa,eliminada,created_at"
)


def _url_imagen_permitida(valor):
    url = str(valor or "").strip()
    if not url:
        return ""

    if url.startswith("/static/uploads/"):
        return url

    try:
        partes = urlsplit(url)
    except ValueError:
        return ""

    if partes.scheme in {"http", "https"} and partes.netloc:
        return url

    return ""


def obtener_imagenes_publicacion(publicacion):
    """Deduplica solamente las imágenes almacenadas en la publicación."""
    publicacion = publicacion or {}
    candidatas = [
        publicacion.get("imagen_principal"),
        publicacion.get("imagen_url"),
    ]

    imagenes = publicacion.get("imagenes") or []
    if isinstance(imagenes, list):
        candidatas.extend(imagenes)

    resultado = []
    vistas = set()
    for candidata in candidatas:
        url = _url_imagen_permitida(candidata)
        if url and url not in vistas:
            resultado.append(url)
            vistas.add(url)

    return resultado


def preparar_publicacion_contenido(publicacion):
    """Agrega datos visuales derivados sin modificar la fila original."""
    preparada = dict(publicacion or {})
    preparada["imagenes_disponibles"] = obtener_imagenes_publicacion(preparada)
    preparada["imagen_mostrar"] = (
        preparada["imagenes_disponibles"][0]
        if preparada["imagenes_disponibles"]
        else ""
    )
    return preparada


def obtener_publicaciones_comercio(comercio_id, cliente=None):
    """Recupera únicamente publicaciones pertenecientes al comercio."""
    if not comercio_id:
        return []

    db = cliente or supabase_admin
    respuesta = (
        db.table("publicaciones")
        .select(CAMPOS_PUBLICACION_CONTENIDO)
        .eq("comercio_id", comercio_id)
        .eq("eliminada", False)
        .order("created_at", desc=True)
        .execute()
    )
    return [
        preparar_publicacion_contenido(publicacion)
        for publicacion in (respuesta.data or [])
    ]


def obtener_publicacion_comercio(publicacion_id, comercio_id, cliente=None):
    """Busca una publicación validando simultáneamente su comercio dueño."""
    if not publicacion_id or not comercio_id:
        return None

    db = cliente or supabase_admin
    respuesta = (
        db.table("publicaciones")
        .select(CAMPOS_PUBLICACION_CONTENIDO)
        .eq("id", publicacion_id)
        .eq("comercio_id", comercio_id)
        .eq("eliminada", False)
        .limit(1)
        .execute()
    )
    publicaciones = respuesta.data or []
    return (
        preparar_publicacion_contenido(publicaciones[0])
        if publicaciones
        else None
    )
