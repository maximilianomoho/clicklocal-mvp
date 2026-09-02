from copy import deepcopy
from functools import wraps

from flask import redirect, session, url_for

from config.supabase_config import supabase_admin


CATALOGO_MODULOS = {
    "turnos": {
        "slug": "turnos",
        "nombre": "Gestión de turnos",
        "descripcion_corta": (
            "Administrá servicios, profesionales, horarios y reservas "
            "desde una agenda simple."
        ),
        "descripcion_detalle": (
            "Organizá la agenda de tu negocio en un solo lugar y ofrecé "
            "a tus clientes una turnera online disponible en todo momento."
        ),
        "beneficios": [
            "Agenda diaria con profesionales y servicios.",
            "Horarios y duraciones configurables.",
            "Turnera pública para recibir reservas.",
            "Historial conservado aunque el módulo se desactive.",
        ],
        "precio": "Consultá el precio y las condiciones de activación.",
        "endpoint_operativo": "turnos.agenda_turnos",
        "disponible": True,
        "imagenes": [],
    },
}


def obtener_modulo(slug):
    """Devuelve una copia del módulo si el slug existe."""
    slug_normalizado = str(slug or "").strip().lower()
    modulo = CATALOGO_MODULOS.get(slug_normalizado)
    return deepcopy(modulo) if modulo else None


def slug_modulo_valido(slug):
    return obtener_modulo(slug) is not None


def obtener_estados_modulos(comercio_id):
    """Devuelve {slug: activo}. Ante un error, falla cerrado."""
    if not comercio_id:
        return {}

    try:
        respuesta = (
            supabase_admin
            .table("comercio_modulos")
            .select("modulo,activo")
            .eq("comercio_id", comercio_id)
            .execute()
        )
    except Exception as error:
        print(
            "ERROR CONSULTANDO MODULOS DEL COMERCIO:",
            type(error),
            error,
            flush=True,
        )
        return {}

    return {
        str(fila.get("modulo") or "").strip().lower(): (
            fila.get("activo") is True
        )
        for fila in (respuesta.data or [])
        if fila.get("modulo")
    }


def modulo_activo(comercio_id, slug):
    if not comercio_id or not slug_modulo_valido(slug):
        return False

    return obtener_estados_modulos(comercio_id).get(
        str(slug).strip().lower(),
        False,
    )


def obtener_modulos_activos(comercio_id):
    return [
        modulo
        for modulo in combinar_catalogo_con_estado(comercio_id)
        if modulo["activo"]
    ]


def combinar_catalogo_con_estado(comercio_id):
    estados = obtener_estados_modulos(comercio_id)
    modulos = []

    for slug, datos in CATALOGO_MODULOS.items():
        modulo = deepcopy(datos)
        modulo["activo"] = estados.get(slug, False)
        modulos.append(modulo)

    return modulos


def requerir_modulo(slug):
    """Protege una vista privada usando el comercio de la sesión."""
    def decorador(funcion):
        @wraps(funcion)
        def wrapper(*args, **kwargs):
            comercio = session.get("comercio") or {}
            comercio_id = comercio.get("id")

            if not comercio_id:
                return redirect(url_for("login"))

            if not modulo_activo(comercio_id, slug):
                return "Módulo no activo para este comercio.", 403

            return funcion(*args, **kwargs)

        return wrapper

    return decorador
