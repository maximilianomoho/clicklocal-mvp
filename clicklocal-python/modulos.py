from copy import deepcopy
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

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


ZONA_HORARIA_MODULOS = ZoneInfo("America/Argentina/Cordoba")


def _resultado_vigencia_bloqueado(motivo, existe=False):
    return {
        "existe": existe,
        "habilitado_manual": False,
        "estado_vigencia": "vencido",
        "fecha_activacion": None,
        "fecha_vencimiento": None,
        "aviso_dias_antes": None,
        "gracia_dias": None,
        "inicio_aviso": None,
        "dias_restantes": 0,
        "fecha_fin_gracia": None,
        "dias_gracia_restantes": 0,
        "acceso_operativo": False,
        "acceso_publico": False,
        "motivo_bloqueo": motivo,
    }


def _fecha_modulo(valor):
    if valor is None or valor == "":
        return None

    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=ZONA_HORARIA_MODULOS)
        else:
            valor = valor.astimezone(ZONA_HORARIA_MODULOS)
        return valor.date()

    if isinstance(valor, date):
        return valor

    return date.fromisoformat(str(valor)[:10])


def _fecha_actual_modulo(fecha_actual):
    if fecha_actual is None:
        return datetime.now(ZONA_HORARIA_MODULOS).date()

    return _fecha_modulo(fecha_actual)


def obtener_modulo(slug):
    """Devuelve una copia del módulo si el slug existe."""
    slug_normalizado = str(slug or "").strip().lower()
    modulo = CATALOGO_MODULOS.get(slug_normalizado)
    return deepcopy(modulo) if modulo else None


def slug_modulo_valido(slug):
    return obtener_modulo(slug) is not None


def evaluar_vigencia_modulo(comercio_id, modulo, fecha_actual=None):
    """Evalúa la vigencia comercial sin persistir estados derivados."""
    slug = str(modulo or "").strip().lower()

    if not comercio_id or not slug_modulo_valido(slug):
        return _resultado_vigencia_bloqueado("modulo_no_asignado")

    try:
        respuesta = (
            supabase_admin
            .table("comercio_modulos")
            .select(
                "activo,fecha_activacion,fecha_vencimiento,"
                "aviso_dias_antes,gracia_dias"
            )
            .eq("comercio_id", comercio_id)
            .eq("modulo", slug)
            .limit(1)
            .execute()
        )
    except Exception as error:
        print(
            "ERROR EVALUANDO VIGENCIA DEL MODULO:",
            type(error),
            error,
            flush=True,
        )
        return _resultado_vigencia_bloqueado("error_consulta")

    filas = respuesta.data or []
    if not filas:
        return _resultado_vigencia_bloqueado("modulo_no_asignado")

    fila = filas[0]

    try:
        hoy = _fecha_actual_modulo(fecha_actual)
        fecha_activacion = _fecha_modulo(fila.get("fecha_activacion"))
        fecha_vencimiento = _fecha_modulo(fila.get("fecha_vencimiento"))
        aviso_dias_antes = int(fila.get("aviso_dias_antes"))
        gracia_dias = int(fila.get("gracia_dias"))

        if aviso_dias_antes < 0 or gracia_dias < 0:
            raise ValueError("La configuración de vigencia es negativa")

        if (
            fecha_activacion
            and fecha_vencimiento
            and fecha_vencimiento < fecha_activacion
        ):
            raise ValueError("El rango de vigencia es inválido")
    except (TypeError, ValueError, OverflowError):
        return _resultado_vigencia_bloqueado(
            "datos_invalidos",
            existe=True,
        )

    habilitado_manual = fila.get("activo") is True
    inicio_aviso = None
    fecha_fin_gracia = None
    dias_restantes = None
    dias_gracia_restantes = None
    estado_vigencia = "activo"
    acceso_por_fecha = True
    motivo_bloqueo = None

    if fecha_activacion and hoy < fecha_activacion:
        acceso_por_fecha = False
        motivo_bloqueo = "vigencia_no_iniciada"

    if fecha_vencimiento:
        inicio_aviso = fecha_vencimiento - timedelta(
            days=aviso_dias_antes
        )
        fecha_fin_gracia = fecha_vencimiento + timedelta(
            days=gracia_dias
        )

        if hoy <= fecha_vencimiento:
            dias_restantes = (fecha_vencimiento - hoy).days
            estado_vigencia = (
                "por_vencer"
                if hoy >= inicio_aviso
                else "activo"
            )
        elif hoy <= fecha_fin_gracia and gracia_dias > 0:
            estado_vigencia = "en_gracia"
            dias_restantes = 0
            dias_gracia_restantes = (fecha_fin_gracia - hoy).days
        else:
            estado_vigencia = "vencido"
            dias_restantes = 0
            dias_gracia_restantes = 0
            acceso_por_fecha = False
            motivo_bloqueo = "vigencia_vencida"

    acceso = habilitado_manual and acceso_por_fecha
    if not habilitado_manual:
        motivo_bloqueo = "suspension_manual"

    return {
        "existe": True,
        "habilitado_manual": habilitado_manual,
        "estado_vigencia": estado_vigencia,
        "fecha_activacion": fecha_activacion,
        "fecha_vencimiento": fecha_vencimiento,
        "aviso_dias_antes": aviso_dias_antes,
        "gracia_dias": gracia_dias,
        "inicio_aviso": inicio_aviso,
        "dias_restantes": dias_restantes,
        "fecha_fin_gracia": fecha_fin_gracia,
        "dias_gracia_restantes": dias_gracia_restantes,
        "acceso_operativo": acceso,
        "acceso_publico": acceso,
        "motivo_bloqueo": motivo_bloqueo,
    }


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
    return evaluar_vigencia_modulo(
        comercio_id,
        slug,
    )["acceso_operativo"]


def modulo_asignado(comercio_id, slug):
    """Indica si existe la relación, aunque el módulo esté inactivo."""
    if not comercio_id or not slug_modulo_valido(slug):
        return False

    return str(slug).strip().lower() in obtener_estados_modulos(comercio_id)


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
        modulo["asignado"] = slug in estados
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
