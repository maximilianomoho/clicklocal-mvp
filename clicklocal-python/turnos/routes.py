from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import redirect, render_template, request, session, url_for

from config.supabase_config import supabase_admin

from . import turnos_bp


@turnos_bp.route("/agenda")
def agenda_turnos():
    comercio = session.get("comercio") or {}

    comercio_id = comercio.get("id")

    nombre_comercio = (
        comercio.get("nombre_negocio")
        or comercio.get("nombre")
        or "Mi negocio"
    )

    servicios = []
    servicios_configuracion = []

    if comercio_id:
        try:
            servicios_res = (
                supabase_admin
                .table("turnos_servicios")
                .select(
                    "id,nombre,duracion_min,capacidad_max,"
                    "intervalo_inicio_min,precio,activo,orden"
                )
                .eq("comercio_id", comercio_id)
                .order("orden")
                .execute()
            )

            servicios_configuracion = servicios_res.data or []

            servicios = [
                servicio
                for servicio in servicios_configuracion
                if servicio.get("activo") is True
            ]

        except Exception as error:
            print(
                "ERROR CARGANDO SERVICIOS TURNOS:",
                type(error),
                error,
                flush=True
            )

    duraciones_servicio = {
        str(servicio.get("nombre")): int(
            servicio.get("duracion_min") or 30
        )
        for servicio in servicios
        if servicio.get("nombre")
    }

    profesionales = []
    profesionales_configuracion = []
    servicios_por_profesional = {}
    horarios_por_profesional = {}

    if comercio_id:
        try:
            profesionales_res = (
                supabase_admin
                .table("turnos_profesionales")
                .select(
                    "id,nombre,rol,activo,orden,color"
                )
                .eq("comercio_id", comercio_id)
                .order("orden")
                .execute()
            )

            profesionales_configuracion = (
                profesionales_res.data or []
            )

            profesionales = [
                profesional
                for profesional in profesionales_configuracion
                if profesional.get("activo") is True
            ]

        except Exception as error:
            print(
                "ERROR CARGANDO PROFESIONALES TURNOS:",
                type(error),
                error,
                flush=True
            )

    if comercio_id and profesionales_configuracion:
        try:
            profesional_ids = [
                profesional.get("id")
                for profesional in profesionales_configuracion
                if profesional.get("id")
            ]

            if profesional_ids:
                relaciones_res = (
                    supabase_admin
                    .table("turnos_profesional_servicios")
                    .select("profesional_id,servicio_id")
                    .in_("profesional_id", profesional_ids)
                    .execute()
                )

                for relacion in (relaciones_res.data or []):
                    profesional_id = relacion.get("profesional_id")
                    servicio_id = relacion.get("servicio_id")

                    if profesional_id and servicio_id:
                        servicios_por_profesional.setdefault(
                            profesional_id,
                            []
                        ).append(servicio_id)

        except Exception as error:
            print(
                "ERROR CARGANDO RELACIONES PROFESIONAL-SERVICIO:",
                type(error),
                error,
                flush=True
            )

    if comercio_id:
        try:
            horarios_res = (
                supabase_admin
                .table("turnos_horarios")
                .select(
                    "id,profesional_id,dia_semana,"
                    "hora_desde,hora_hasta,activo"
                )
                .eq("comercio_id", comercio_id)
                .eq("activo", True)
                .order("dia_semana")
                .order("hora_desde")
                .execute()
            )

            for horario in (horarios_res.data or []):
                profesional_id = horario.get("profesional_id")

                if profesional_id:
                    horarios_por_profesional.setdefault(
                        profesional_id,
                        []
                    ).append(horario)

        except Exception as error:
            print(
                "ERROR CARGANDO HORARIOS TURNOS:",
                type(error),
                error,
                flush=True
            )

    reservas = []

    if comercio_id:
        try:
            reservas_res = (
                supabase_admin
                .table("turnos_reservas")
                .select(
                    "id,servicio_id,profesional_id,fecha,"
                    "hora_inicio,hora_fin,cliente_nombre,"
                    "cliente_whatsapp,observacion,estado"
                )
                .eq("comercio_id", comercio_id)
                .order("fecha")
                .order("hora_inicio")
                .execute()
            )

            servicios_por_id = {
                servicio.get("id"): servicio
                for servicio in servicios_configuracion
                if servicio.get("id")
            }

            profesionales_por_id = {
                profesional.get("id"): profesional
                for profesional in profesionales_configuracion
                if profesional.get("id")
            }

            for reserva in (reservas_res.data or []):
                servicio = servicios_por_id.get(
                    reserva.get("servicio_id")
                ) or {}

                profesional = profesionales_por_id.get(
                    reserva.get("profesional_id")
                ) or {}

                hora_inicio = str(
                    reserva.get("hora_inicio") or ""
                )[:5]

                hora_fin = str(
                    reserva.get("hora_fin") or ""
                )[:5]

                reservas.append({
                    "id": reserva.get("id"),
                    "fecha": str(reserva.get("fecha") or ""),
                    "horaInicio": hora_inicio,
                    "horaFin": hora_fin,
                    "cliente": reserva.get("cliente_nombre") or "",
                    "whatsapp": reserva.get("cliente_whatsapp") or "",
                    "servicio": servicio.get("nombre") or "",
                    "duracion": int(
                        servicio.get("duracion_min") or 30
                    ),
                    "profesional": profesional.get("nombre") or "",
                    "observacion": reserva.get("observacion") or "",
                    "estado": reserva.get("estado") or "pendiente",
                })

        except Exception as error:
            print(
                "ERROR CARGANDO RESERVAS TURNOS:",
                type(error),
                error,
                flush=True
            )

    return render_template(
        "turnos/agenda.html",
        nombre_comercio=nombre_comercio,
        servicios=servicios,
        servicios_configuracion=servicios_configuracion,
        duraciones_servicio=duraciones_servicio,
        profesionales=profesionales,
        profesionales_configuracion=profesionales_configuracion,
        servicios_por_profesional=servicios_por_profesional,
        horarios_por_profesional=horarios_por_profesional,
        reservas=reservas,
        abrir_configuracion=(
            request.args.get("configuracion") == "1"
        ),
        configuracion_error=request.args.get("error") or ""
    )


@turnos_bp.route("/comercio/<comercio_id>")
def turnera_publica(comercio_id):
    try:
        comercio_id = str(UUID(str(comercio_id)))
    except (TypeError, ValueError, AttributeError):
        return "", 404

    comercio_res = (
        supabase_admin
        .table("comercios")
        .select("id,nombre_negocio,activo")
        .eq("id", comercio_id)
        .eq("activo", True)
        .limit(1)
        .execute()
    )

    comercios = comercio_res.data or []

    if not comercios:
        return "", 404

    modulo_res = (
        supabase_admin
        .table("comercio_modulos")
        .select("comercio_id")
        .eq("comercio_id", comercio_id)
        .eq("modulo", "turnos")
        .eq("activo", True)
        .limit(1)
        .execute()
    )

    if not (modulo_res.data or []):
        return "", 404

    servicios_res = (
        supabase_admin
        .table("turnos_servicios")
        .select("id,nombre,duracion_min")
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .order("orden")
        .execute()
    )

    profesionales_res = (
        supabase_admin
        .table("turnos_profesionales")
        .select("id,nombre,rol")
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .order("orden")
        .execute()
    )

    servicios = servicios_res.data or []
    profesionales = profesionales_res.data or []
    servicio_ids = [
        servicio.get("id")
        for servicio in servicios
        if servicio.get("id")
    ]
    profesional_ids = [
        profesional.get("id")
        for profesional in profesionales
        if profesional.get("id")
    ]

    servicios_por_profesional = {
        profesional_id: []
        for profesional_id in profesional_ids
    }
    dias_por_profesional = {
        profesional_id: []
        for profesional_id in profesional_ids
    }

    if profesional_ids and servicio_ids:
        relaciones_res = (
            supabase_admin
            .table("turnos_profesional_servicios")
            .select("profesional_id,servicio_id")
            .in_("profesional_id", profesional_ids)
            .in_("servicio_id", servicio_ids)
            .execute()
        )

        for relacion in (relaciones_res.data or []):
            profesional_id = relacion.get("profesional_id")
            servicio_id = relacion.get("servicio_id")

            if profesional_id in servicios_por_profesional:
                servicios_por_profesional[profesional_id].append(
                    servicio_id
                )

    if profesional_ids:
        horarios_res = (
            supabase_admin
            .table("turnos_horarios")
            .select("profesional_id,dia_semana")
            .eq("comercio_id", comercio_id)
            .eq("activo", True)
            .in_("profesional_id", profesional_ids)
            .execute()
        )

        dias_temporales = {
            profesional_id: set()
            for profesional_id in profesional_ids
        }

        for horario in (horarios_res.data or []):
            profesional_id = horario.get("profesional_id")
            dia_semana = horario.get("dia_semana")

            if (
                profesional_id in dias_temporales
                and isinstance(dia_semana, int)
                and 0 <= dia_semana <= 6
            ):
                dias_temporales[profesional_id].add(dia_semana)

        dias_por_profesional = {
            profesional_id: sorted(dias)
            for profesional_id, dias in dias_temporales.items()
        }

    comercio = comercios[0]

    return render_template(
        "turnos/publico.html",
        comercio={
            "id": comercio.get("id"),
            "nombre_negocio": comercio.get("nombre_negocio") or "Comercio",
        },
        servicios=servicios,
        profesionales=profesionales,
        servicios_por_profesional=servicios_por_profesional,
        dias_por_profesional=dias_por_profesional,
    )


@turnos_bp.route("/comercio/<comercio_id>/disponibilidad")
def disponibilidad_turnos_publica(comercio_id):
    try:
        comercio_id = str(UUID(str(comercio_id)))
    except (TypeError, ValueError, AttributeError):
        return {"ok": False, "error": "no_encontrado"}, 404

    servicio_id = str(
        request.args.get("servicio_id") or ""
    ).strip()
    profesional_id = str(
        request.args.get("profesional_id") or ""
    ).strip()
    fecha = str(request.args.get("fecha") or "").strip()

    if not all([servicio_id, profesional_id, fecha]):
        return {
            "ok": False,
            "error": "campos_obligatorios",
        }, 400

    try:
        servicio_id = str(UUID(servicio_id))
        profesional_id = str(UUID(profesional_id))
    except (TypeError, ValueError, AttributeError):
        return {"ok": False, "error": "identificador"}, 400

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id")
            .eq("id", comercio_id)
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        if not (comercio_res.data or []):
            return {"ok": False, "error": "no_encontrado"}, 404

        modulo_res = (
            supabase_admin
            .table("comercio_modulos")
            .select("comercio_id")
            .eq("comercio_id", comercio_id)
            .eq("modulo", "turnos")
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        if not (modulo_res.data or []):
            return {"ok": False, "error": "no_encontrado"}, 404

        resultado, estado = _calcular_horarios_disponibles(
            comercio_id,
            servicio_id,
            profesional_id,
            fecha,
        )

        if estado != 200:
            return resultado, estado

        return {
            "ok": True,
            "horarios": resultado.get("horarios") or [],
        }, 200

    except Exception as error:
        print(
            "ERROR CALCULANDO DISPONIBILIDAD PUBLICA TURNOS:",
            type(error),
            error,
            flush=True,
        )

        return {"ok": False, "error": "disponibilidad"}, 500


def _calcular_horarios_disponibles(
    comercio_id,
    servicio_id,
    profesional_id,
    fecha,
):
    """Calcula opciones de inicio usando las reglas actuales de reservas."""
    try:
        fecha_base = datetime.strptime(fecha, "%Y-%m-%d")
    except (TypeError, ValueError):
        return {"ok": False, "error": "fecha"}, 400

    servicio_res = (
        supabase_admin
        .table("turnos_servicios")
        .select(
            "id,duracion_min,ocupacion_profesional_min,"
            "intervalo_inicio_min,capacidad_max,activo"
        )
        .eq("id", servicio_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    servicios = servicio_res.data or []

    if not servicios:
        return {"ok": False, "error": "servicio"}, 404

    servicio = servicios[0]

    if servicio.get("activo") is not True:
        return {"ok": False, "error": "servicio_inactivo"}, 400

    profesional_res = (
        supabase_admin
        .table("turnos_profesionales")
        .select("id,activo")
        .eq("id", profesional_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    profesionales = profesional_res.data or []

    if not profesionales:
        return {"ok": False, "error": "profesional"}, 404

    if profesionales[0].get("activo") is not True:
        return {"ok": False, "error": "profesional_inactivo"}, 400

    relacion_res = (
        supabase_admin
        .table("turnos_profesional_servicios")
        .select("profesional_id,servicio_id")
        .eq("profesional_id", profesional_id)
        .eq("servicio_id", servicio_id)
        .limit(1)
        .execute()
    )

    if not (relacion_res.data or []):
        return {
            "ok": False,
            "error": "profesional_no_presta_servicio",
        }, 400

    try:
        duracion_min = int(servicio.get("duracion_min") or 30)
        intervalo_inicio_min = int(
            servicio.get("intervalo_inicio_min") or 1
        )
        capacidad_max = int(servicio.get("capacidad_max") or 1)
        ocupacion_profesional_min = int(
            servicio.get("ocupacion_profesional_min")
            or duracion_min
        )
    except (TypeError, ValueError):
        return {"ok": False, "error": "configuracion_servicio"}, 500

    if duracion_min <= 0:
        return {"ok": False, "error": "configuracion_servicio"}, 500

    if intervalo_inicio_min <= 0:
        intervalo_inicio_min = 1

    if capacidad_max <= 0:
        capacidad_max = 1

    if ocupacion_profesional_min <= 0:
        ocupacion_profesional_min = duracion_min

    horarios_res = (
        supabase_admin
        .table("turnos_horarios")
        .select("id,dia_semana,hora_desde,hora_hasta,activo")
        .eq("comercio_id", comercio_id)
        .eq("profesional_id", profesional_id)
        .eq("dia_semana", fecha_base.weekday())
        .eq("activo", True)
        .execute()
    )

    horarios_dia = horarios_res.data or []

    reservas_res = (
        supabase_admin
        .table("turnos_reservas")
        .select("id,servicio_id,hora_inicio,hora_fin,estado")
        .eq("comercio_id", comercio_id)
        .eq("profesional_id", profesional_id)
        .eq("fecha", fecha)
        .in_("estado", ["pendiente", "confirmado"])
        .execute()
    )

    reservas_existentes = reservas_res.data or []

    servicio_ids_existentes = list({
        reserva.get("servicio_id")
        for reserva in reservas_existentes
        if reserva.get("servicio_id")
    })

    ocupacion_por_servicio = {}

    if servicio_ids_existentes:
        servicios_existentes_res = (
            supabase_admin
            .table("turnos_servicios")
            .select("id,duracion_min,ocupacion_profesional_min")
            .eq("comercio_id", comercio_id)
            .in_("id", servicio_ids_existentes)
            .execute()
        )

        for servicio_existente in (
            servicios_existentes_res.data or []
        ):
            servicio_existente_id = servicio_existente.get("id")

            try:
                duracion_existente = int(
                    servicio_existente.get("duracion_min") or 30
                )
                ocupacion_existente = int(
                    servicio_existente.get(
                        "ocupacion_profesional_min"
                    )
                    or duracion_existente
                )
            except (TypeError, ValueError):
                continue

            if ocupacion_existente <= 0:
                ocupacion_existente = duracion_existente

            ocupacion_por_servicio[
                servicio_existente_id
            ] = ocupacion_existente

    ahora_argentina = datetime.now(
        ZoneInfo("America/Argentina/Cordoba")
    ).replace(tzinfo=None)

    opciones_por_hora = {}

    for horario in horarios_dia:
        hora_desde_raw = str(
            horario.get("hora_desde") or ""
        )[:5]
        hora_hasta_raw = str(
            horario.get("hora_hasta") or ""
        )[:5]

        try:
            hora_desde = datetime.strptime(
                f"{fecha} {hora_desde_raw}",
                "%Y-%m-%d %H:%M",
            )
            hora_hasta = datetime.strptime(
                f"{fecha} {hora_hasta_raw}",
                "%Y-%m-%d %H:%M",
            )
        except ValueError:
            continue

        inicio = hora_desde

        while inicio + timedelta(minutes=duracion_min) <= hora_hasta:
            fin = inicio + timedelta(minutes=duracion_min)
            fin_ocupacion = inicio + timedelta(
                minutes=ocupacion_profesional_min
            )
            hora_inicio = inicio.strftime("%H:%M")

            inicio_disponible = inicio >= ahora_argentina

            reservas_misma_sesion = [
                reserva
                for reserva in reservas_existentes
                if (
                    reserva.get("servicio_id") == servicio_id
                    and str(
                        reserva.get("hora_inicio") or ""
                    )[:5] == hora_inicio
                )
            ]

            if len(reservas_misma_sesion) >= capacidad_max:
                inicio_disponible = False

            if inicio_disponible:
                for reserva_existente in reservas_existentes:
                    existente_inicio_raw = str(
                        reserva_existente.get("hora_inicio") or ""
                    )[:5]

                    es_misma_sesion = (
                        reserva_existente.get("servicio_id")
                        == servicio_id
                        and existente_inicio_raw == hora_inicio
                    )

                    if es_misma_sesion:
                        continue

                    try:
                        existente_inicio = datetime.strptime(
                            f"{fecha} {existente_inicio_raw}",
                            "%Y-%m-%d %H:%M",
                        )
                    except ValueError:
                        continue

                    servicio_existente_id = (
                        reserva_existente.get("servicio_id")
                    )
                    ocupacion_existente_min = (
                        ocupacion_por_servicio.get(
                            servicio_existente_id
                        )
                    )

                    if ocupacion_existente_min is None:
                        existente_fin_raw = str(
                            reserva_existente.get("hora_fin") or ""
                        )[:5]

                        try:
                            existente_fin_ocupacion = datetime.strptime(
                                f"{fecha} {existente_fin_raw}",
                                "%Y-%m-%d %H:%M",
                            )
                        except ValueError:
                            continue
                    else:
                        existente_fin_ocupacion = (
                            existente_inicio
                            + timedelta(
                                minutes=ocupacion_existente_min
                            )
                        )

                    hay_solapamiento = (
                        inicio < existente_fin_ocupacion
                        and fin_ocupacion > existente_inicio
                    )

                    if hay_solapamiento:
                        inicio_disponible = False
                        break

            if inicio_disponible:
                opciones_por_hora[hora_inicio] = {
                    "hora_inicio": hora_inicio,
                    "hora_fin": fin.strftime("%H:%M"),
                    "lugares_disponibles": (
                        capacidad_max - len(reservas_misma_sesion)
                    ),
                }

            inicio += timedelta(minutes=intervalo_inicio_min)

    horarios_disponibles = [
        opciones_por_hora[hora]
        for hora in sorted(opciones_por_hora)
    ]

    return {
        "ok": True,
        "servicio_id": servicio_id,
        "profesional_id": profesional_id,
        "fecha": fecha,
        "duracion_min": duracion_min,
        "intervalo_inicio_min": intervalo_inicio_min,
        "capacidad_max": capacidad_max,
        "horarios": horarios_disponibles,
    }, 200


@turnos_bp.route("/disponibilidad")
def disponibilidad_turnos():
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return {"ok": False, "error": "comercio"}, 401

    servicio_id = str(
        request.args.get("servicio_id") or ""
    ).strip()
    profesional_id = str(
        request.args.get("profesional_id") or ""
    ).strip()
    fecha = str(request.args.get("fecha") or "").strip()

    if not all([servicio_id, profesional_id, fecha]):
        return {
            "ok": False,
            "error": "campos_obligatorios",
        }, 400

    try:
        return _calcular_horarios_disponibles(
            comercio_id,
            servicio_id,
            profesional_id,
            fecha,
        )
    except Exception as error:
        print(
            "ERROR CALCULANDO DISPONIBILIDAD TURNOS:",
            type(error),
            error,
            flush=True,
        )

        return {"ok": False, "error": "disponibilidad"}, 500



def _crear_reserva_validada(
    comercio_id,
    datos,
    whatsapp_comercio="",
):
    cliente_nombre = str(
        datos.get("cliente_nombre") or ""
    ).strip()

    cliente_whatsapp = str(
        datos.get("cliente_whatsapp") or ""
    ).strip()

    servicio_id = str(
        datos.get("servicio_id") or ""
    ).strip()

    profesional_id = str(
        datos.get("profesional_id") or ""
    ).strip()

    fecha = str(datos.get("fecha") or "").strip()

    hora_inicio_raw = str(
        datos.get("hora_inicio") or ""
    ).strip()

    observacion = str(
        datos.get("observacion") or ""
    ).strip()

    if not all([
        cliente_nombre,
        cliente_whatsapp,
        servicio_id,
        profesional_id,
        fecha,
        hora_inicio_raw,
    ]):
        return {
            "ok": False,
            "error": "campos_obligatorios"
        }, 400

    try:
        servicio_res = (
            supabase_admin
            .table("turnos_servicios")
            .select(
                "id,duracion_min,ocupacion_profesional_min,"
                "intervalo_inicio_min,capacidad_max,activo"
            )
            .eq("id", servicio_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        servicio_filas = servicio_res.data or []

        if not servicio_filas:
            return {
                "ok": False,
                "error": "servicio"
            }, 404

        servicio = servicio_filas[0]

        if servicio.get("activo") is not True:
            return {
                "ok": False,
                "error": "servicio_inactivo"
            }, 400

        profesional_res = (
            supabase_admin
            .table("turnos_profesionales")
            .select("id,activo")
            .eq("id", profesional_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        profesional_filas = profesional_res.data or []

        if not profesional_filas:
            return {
                "ok": False,
                "error": "profesional"
            }, 404

        profesional = profesional_filas[0]

        if profesional.get("activo") is not True:
            return {
                "ok": False,
                "error": "profesional_inactivo"
            }, 400

        relacion_res = (
            supabase_admin
            .table("turnos_profesional_servicios")
            .select("profesional_id,servicio_id")
            .eq("profesional_id", profesional_id)
            .eq("servicio_id", servicio_id)
            .limit(1)
            .execute()
        )

        if not (relacion_res.data or []):
            return {
                "ok": False,
                "error": "profesional_no_presta_servicio"
            }, 400

        duracion_min = int(
            servicio.get("duracion_min") or 30
        )

        inicio = datetime.strptime(
            f"{fecha} {hora_inicio_raw}",
            "%Y-%m-%d %H:%M"
        )

        # ====================================================
        # NO PERMITIR CREAR TURNOS EN EL PASADO
        #
        # La agenda del comercio conserva los turnos pasados
        # como historial, pero no se pueden crear nuevos turnos
        # para una fecha/hora que ya pasó.
        # ====================================================

        ahora_argentina = datetime.now(
            ZoneInfo("America/Argentina/Cordoba")
        ).replace(tzinfo=None)

        if inicio < ahora_argentina:
            return {
                "ok": False,
                "error": "horario_pasado",
                "mensaje": (
                    "Ese horario ya pasó. "
                    "Elegí un horario futuro."
                )
            }, 400

        fin = inicio + timedelta(minutes=duracion_min)

        hora_fin = fin.strftime("%H:%M")

        ocupacion_profesional_min = int(
            servicio.get("ocupacion_profesional_min")
            or duracion_min
        )

        if ocupacion_profesional_min <= 0:
            ocupacion_profesional_min = duracion_min

        capacidad_max = int(
            servicio.get("capacidad_max") or 1
        )

        if capacidad_max <= 0:
            capacidad_max = 1

        fin_ocupacion = inicio + timedelta(
            minutes=ocupacion_profesional_min
        )

        # ====================================================
        # VALIDAR HORARIO REAL DEL PROFESIONAL
        #
        # El turno completo debe entrar dentro de alguno de
        # los tramos activos configurados para ese profesional
        # en ese día.
        #
        # Ejemplo:
        # horario 09:00-12:00
        # servicio de 30 min
        # 11:30 -> válido
        # 11:45 -> inválido
        # ====================================================

        dia_semana = inicio.weekday()

        horarios_res = (
            supabase_admin
            .table("turnos_horarios")
            .select(
                "id,dia_semana,hora_desde,hora_hasta,activo"
            )
            .eq("comercio_id", comercio_id)
            .eq("profesional_id", profesional_id)
            .eq("dia_semana", dia_semana)
            .eq("activo", True)
            .execute()
        )

        horarios_dia = horarios_res.data or []

        turno_dentro_horario = False

        for horario in horarios_dia:
            hora_desde_raw = str(
                horario.get("hora_desde") or ""
            )[:5]

            hora_hasta_raw = str(
                horario.get("hora_hasta") or ""
            )[:5]

            try:
                hora_desde = datetime.strptime(
                    f"{fecha} {hora_desde_raw}",
                    "%Y-%m-%d %H:%M"
                )

                hora_hasta = datetime.strptime(
                    f"{fecha} {hora_hasta_raw}",
                    "%Y-%m-%d %H:%M"
                )

            except ValueError:
                continue

            if (
                inicio >= hora_desde
                and fin <= hora_hasta
            ):
                turno_dentro_horario = True
                break

        if not turno_dentro_horario:
            return {
                "ok": False,
                "error": "fuera_horario"
            }, 400

        # ====================================================
        # VALIDAR INTERVALO DE INICIO DEL SERVICIO
        #
        # El intervalo se calcula desde el comienzo del tramo
        # horario donde entra el turno.
        #
        # Ejemplo:
        # tramo 09:15-12:00
        # intervalo 30 min
        # válidos: 09:15, 09:45, 10:15, 10:45, 11:15
        # ====================================================

        intervalo_inicio_min = int(
            servicio.get("intervalo_inicio_min") or 1
        )

        if intervalo_inicio_min <= 0:
            intervalo_inicio_min = 1

        inicio_valido_por_intervalo = False

        for horario in horarios_dia:
            hora_desde_raw = str(
                horario.get("hora_desde") or ""
            )[:5]

            hora_hasta_raw = str(
                horario.get("hora_hasta") or ""
            )[:5]

            try:
                hora_desde = datetime.strptime(
                    f"{fecha} {hora_desde_raw}",
                    "%Y-%m-%d %H:%M"
                )

                hora_hasta = datetime.strptime(
                    f"{fecha} {hora_hasta_raw}",
                    "%Y-%m-%d %H:%M"
                )

            except ValueError:
                continue

            if not (
                inicio >= hora_desde
                and fin <= hora_hasta
            ):
                continue

            minutos_desde_inicio = int(
                (inicio - hora_desde).total_seconds() // 60
            )

            if (
                minutos_desde_inicio
                % intervalo_inicio_min
                == 0
            ):
                inicio_valido_por_intervalo = True
                break

        if not inicio_valido_por_intervalo:
            return {
                "ok": False,
                "error": "intervalo_inicio"
            }, 400

        # ====================================================
        # VALIDAR SOLAPAMIENTO DE OCUPACION PROFESIONAL
        #
        # La reserva conserva su duracion completa para el
        # cliente, pero los conflictos del profesional se
        # calculan usando ocupacion_profesional_min.
        #
        # Ejemplo:
        # Color:
        # duracion cliente = 90 min
        # ocupacion profesional = 15 min
        #
        # Una reserva 09:00-10:30 bloquea al profesional
        # solamente de 09:00 a 09:15.
        # ====================================================

        reservas_existentes_res = (
            supabase_admin
            .table("turnos_reservas")
            .select(
                "id,servicio_id,hora_inicio,hora_fin,estado"
            )
            .eq("comercio_id", comercio_id)
            .eq("profesional_id", profesional_id)
            .eq("fecha", fecha)
            .in_("estado", ["pendiente", "confirmado"])
            .execute()
        )

        reservas_existentes = (
            reservas_existentes_res.data or []
        )

        servicio_ids_existentes = list({
            reserva.get("servicio_id")
            for reserva in reservas_existentes
            if reserva.get("servicio_id")
        })

        ocupacion_por_servicio = {}

        if servicio_ids_existentes:
            servicios_existentes_res = (
                supabase_admin
                .table("turnos_servicios")
                .select(
                    "id,duracion_min,ocupacion_profesional_min"
                )
                .eq("comercio_id", comercio_id)
                .in_("id", servicio_ids_existentes)
                .execute()
            )

            for servicio_existente in (
                servicios_existentes_res.data or []
            ):
                servicio_existente_id = (
                    servicio_existente.get("id")
                )

                duracion_existente = int(
                    servicio_existente.get("duracion_min")
                    or 30
                )

                ocupacion_existente = int(
                    servicio_existente.get(
                        "ocupacion_profesional_min"
                    )
                    or duracion_existente
                )

                if ocupacion_existente <= 0:
                    ocupacion_existente = duracion_existente

                ocupacion_por_servicio[
                    servicio_existente_id
                ] = ocupacion_existente

        reservas_misma_sesion = []

        for reserva_existente in reservas_existentes:
            existente_inicio_raw = str(
                reserva_existente.get("hora_inicio") or ""
            )[:5]

            es_misma_sesion = (
                reserva_existente.get("servicio_id")
                == servicio_id
                and existente_inicio_raw
                == inicio.strftime("%H:%M")
            )

            if es_misma_sesion:
                reservas_misma_sesion.append(
                    reserva_existente
                )

        if len(reservas_misma_sesion) >= capacidad_max:
            return {
                "ok": False,
                "error": "capacidad_completa"
            }, 400

        for reserva_existente in reservas_existentes:
            existente_inicio_raw = str(
                reserva_existente.get("hora_inicio") or ""
            )[:5]

            es_misma_sesion = (
                reserva_existente.get("servicio_id")
                == servicio_id
                and existente_inicio_raw
                == inicio.strftime("%H:%M")
            )

            if es_misma_sesion:
                continue

            try:
                existente_inicio = datetime.strptime(
                    f"{fecha} {existente_inicio_raw}",
                    "%Y-%m-%d %H:%M"
                )

            except ValueError:
                continue

            servicio_existente_id = (
                reserva_existente.get("servicio_id")
            )

            ocupacion_existente_min = (
                ocupacion_por_servicio.get(
                    servicio_existente_id
                )
            )

            if ocupacion_existente_min is None:
                existente_fin_raw = str(
                    reserva_existente.get("hora_fin") or ""
                )[:5]

                try:
                    existente_fin_ocupacion = (
                        datetime.strptime(
                            f"{fecha} {existente_fin_raw}",
                            "%Y-%m-%d %H:%M"
                        )
                    )

                except ValueError:
                    continue

            else:
                existente_fin_ocupacion = (
                    existente_inicio
                    + timedelta(
                        minutes=ocupacion_existente_min
                    )
                )

            hay_solapamiento = (
                inicio < existente_fin_ocupacion
                and fin_ocupacion > existente_inicio
            )

            if hay_solapamiento:
                return {
                    "ok": False,
                    "error": "horario_ocupado"
                }, 400

        reserva_res = (
            supabase_admin
            .table("turnos_reservas")
            .insert({
                "comercio_id": comercio_id,
                "servicio_id": servicio_id,
                "profesional_id": profesional_id,
                "fecha": fecha,
                "hora_inicio": hora_inicio_raw,
                "hora_fin": hora_fin,
                "cliente_nombre": cliente_nombre,
                "cliente_whatsapp": cliente_whatsapp,
                "observacion": observacion or None,
                "estado": "pendiente",
            })
            .execute()
        )

        reserva_guardada = (
            (reserva_res.data or [None])[0]
        ) or {}

        reserva = {
            "id": reserva_guardada.get("id"),
            "servicio_id": servicio_id,
            "profesional_id": profesional_id,
            "fecha": fecha,
            "hora_inicio": inicio.strftime("%H:%M"),
            "hora_fin": hora_fin,
            "estado": reserva_guardada.get("estado") or "pendiente",
        }

        return {
            "ok": True,
            "reserva": reserva,
            "whatsapp_comercio": whatsapp_comercio or ""
        }

    except ValueError:
        return {
            "ok": False,
            "error": "fecha_hora"
        }, 400

    except Exception as error:
        print(
            "ERROR CREANDO RESERVA TURNOS:",
            type(error),
            error,
            flush=True
        )

        return {
            "ok": False,
            "error": "guardar"
        }, 500


@turnos_bp.route(
    "/agenda/reservas/nueva",
    methods=["POST"],
)
def crear_reserva():
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return {"ok": False, "error": "comercio"}, 401

    return _crear_reserva_validada(
        comercio_id,
        request.form,
        comercio.get("whatsapp") or "",
    )


@turnos_bp.route(
    "/comercio/<comercio_id>/reservas",
    methods=["POST"],
)
def crear_reserva_publica(comercio_id):
    try:
        comercio_id = str(UUID(str(comercio_id)))
    except (TypeError, ValueError, AttributeError):
        return {"ok": False, "error": "no_encontrado"}, 404

    servicio_id = str(
        request.form.get("servicio_id") or ""
    ).strip()
    profesional_id = str(
        request.form.get("profesional_id") or ""
    ).strip()

    try:
        UUID(servicio_id)
        UUID(profesional_id)
    except (TypeError, ValueError, AttributeError):
        return {"ok": False, "error": "identificador"}, 400

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,whatsapp")
            .eq("id", comercio_id)
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return {"ok": False, "error": "no_encontrado"}, 404

        modulo_res = (
            supabase_admin
            .table("comercio_modulos")
            .select("comercio_id")
            .eq("comercio_id", comercio_id)
            .eq("modulo", "turnos")
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        if not (modulo_res.data or []):
            return {"ok": False, "error": "no_encontrado"}, 404

        return _crear_reserva_validada(
            comercio_id,
            request.form,
            comercios[0].get("whatsapp") or "",
        )

    except Exception as error:
        print(
            "ERROR CREANDO RESERVA PUBLICA TURNOS:",
            type(error),
            error,
            flush=True,
        )

        return {"ok": False, "error": "guardar"}, 500


@turnos_bp.route(
    "/agenda/profesionales/nuevo",
    methods=["POST"],
)
def crear_profesional():
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    nombre = str(
        request.form.get("nombre") or ""
    ).strip()

    rol = str(
        request.form.get("rol") or ""
    ).strip()

    if not nombre:
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="profesional_nombre"
            )
        )

    try:
        paleta_profesionales = [
            "#1A73E8",
            "#16A34A",
            "#9333EA",
            "#EA580C",
            "#DB2777",
            "#0891B2",
            "#D97706",
            "#4F46E5",
            "#DC2626",
            "#059669",
            "#C026D3",
            "#65A30D",
            "#0284C7",
            "#0F766E",
            "#E11D48",
            "#57534E",
            "#A16207",
            "#6D28D9",
            "#C2410C",
            "#0D9488",
            "#BE185D",
            "#1D4ED8",
            "#4D7C0F",
            "#BE123C",
        ]

        colores_res = (
            supabase_admin
            .table("turnos_profesionales")
            .select("color")
            .eq("comercio_id", comercio_id)
            .execute()
        )

        colores_usados = {
            str(fila.get("color") or "").upper()
            for fila in (colores_res.data or [])
            if fila.get("color")
        }

        color = next(
            (
                candidato
                for candidato in paleta_profesionales
                if candidato.upper() not in colores_usados
            ),
            paleta_profesionales[
                len(colores_usados) % len(paleta_profesionales)
            ],
        )

        (
            supabase_admin
            .table("turnos_profesionales")
            .insert({
                "comercio_id": comercio_id,
                "nombre": nombre,
                "rol": rol or None,
                "color": color,
                "activo": True,
            })
            .execute()
        )

    except Exception as error:
        print(
            "ERROR CREANDO PROFESIONAL TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="profesional_guardar"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route(
    "/agenda/profesionales/<profesional_id>/toggle",
    methods=["POST"],
)
def toggle_profesional(profesional_id):
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    try:
        profesional_res = (
            supabase_admin
            .table("turnos_profesionales")
            .select("id,activo")
            .eq("id", profesional_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        profesionales = profesional_res.data or []

        if not profesionales:
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="profesional"
                )
            )

        activo_actual = bool(
            profesionales[0].get("activo")
        )

        (
            supabase_admin
            .table("turnos_profesionales")
            .update({
                "activo": not activo_actual
            })
            .eq("id", profesional_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )

    except Exception as error:
        print(
            "ERROR CAMBIANDO ESTADO PROFESIONAL TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="profesional_guardar"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route(
    "/agenda/profesionales/<profesional_id>/servicios/<servicio_id>/toggle",
    methods=["POST"],
)
def toggle_profesional_servicio(profesional_id, servicio_id):
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    try:
        profesional_res = (
            supabase_admin
            .table("turnos_profesionales")
            .select("id")
            .eq("id", profesional_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        if not (profesional_res.data or []):
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="profesional"
                )
            )

        servicio_res = (
            supabase_admin
            .table("turnos_servicios")
            .select("id")
            .eq("id", servicio_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        if not (servicio_res.data or []):
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="servicio"
                )
            )

        relacion_res = (
            supabase_admin
            .table("turnos_profesional_servicios")
            .select("profesional_id,servicio_id")
            .eq("profesional_id", profesional_id)
            .eq("servicio_id", servicio_id)
            .limit(1)
            .execute()
        )

        if relacion_res.data or []:
            (
                supabase_admin
                .table("turnos_profesional_servicios")
                .delete()
                .eq("profesional_id", profesional_id)
                .eq("servicio_id", servicio_id)
                .execute()
            )
        else:
            (
                supabase_admin
                .table("turnos_profesional_servicios")
                .insert({
                    "profesional_id": profesional_id,
                    "servicio_id": servicio_id,
                })
                .execute()
            )

    except Exception as error:
        print(
            "ERROR CAMBIANDO PROFESIONAL-SERVICIO TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="profesional_servicio"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route(
    "/agenda/servicios/nuevo",
    methods=["POST"],
)
def crear_servicio():
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    nombre = str(
        request.form.get("nombre") or ""
    ).strip()

    duracion_raw = str(
        request.form.get("duracion_min") or ""
    ).strip()

    intervalo_raw = str(
        request.form.get("intervalo_inicio_min") or ""
    ).strip()

    capacidad_raw = str(
        request.form.get("capacidad_max") or ""
    ).strip()

    precio_raw = str(
        request.form.get("precio") or ""
    ).strip()

    if not nombre:
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="nombre"
            )
        )

    try:
        duracion_min = int(duracion_raw)

        if duracion_min <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="duracion"
            )
        )

    try:
        intervalo_inicio_min = int(intervalo_raw)

        if intervalo_inicio_min <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="intervalo"
            )
        )

    try:
        capacidad_max = int(capacidad_raw)

        if capacidad_max <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="capacidad"
            )
        )

    precio = None

    if precio_raw:
        try:
            precio = float(
                precio_raw
                .replace("$", "")
                .replace(" ", "")
                .replace(".", "")
                .replace(",", ".")
            )

            if precio < 0:
                raise ValueError

        except (TypeError, ValueError):
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="precio"
                )
            )

    try:
        (
            supabase_admin
            .table("turnos_servicios")
            .insert({
                "comercio_id": comercio_id,
                "nombre": nombre,
                "duracion_min": duracion_min,
                "ocupacion_profesional_min": duracion_min,
                "capacidad_max": capacidad_max,
                "intervalo_inicio_min": intervalo_inicio_min,
                "precio": precio,
                "activo": True,
            })
            .execute()
        )

    except Exception as error:
        print(
            "ERROR CREANDO SERVICIO TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="guardar"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route(
    "/agenda/servicios/<servicio_id>/toggle",
    methods=["POST"],
)
def toggle_servicio(servicio_id):
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    try:
        servicio_res = (
            supabase_admin
            .table("turnos_servicios")
            .select("id,activo")
            .eq("id", servicio_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        servicios = servicio_res.data or []

        if not servicios:
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="servicio"
                )
            )

        activo_actual = bool(
            servicios[0].get("activo")
        )

        (
            supabase_admin
            .table("turnos_servicios")
            .update({
                "activo": not activo_actual
            })
            .eq("id", servicio_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )

    except Exception as error:
        print(
            "ERROR CAMBIANDO ESTADO SERVICIO TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="guardar"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route(
    "/agenda/profesionales/<profesional_id>/horarios/nuevo",
    methods=["POST"],
)
def crear_horario(profesional_id):
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    dia_raw = str(
        request.form.get("dia_semana") or ""
    ).strip()

    hora_desde = str(
        request.form.get("hora_desde") or ""
    ).strip()

    hora_hasta = str(
        request.form.get("hora_hasta") or ""
    ).strip()

    try:
        dia_semana = int(dia_raw)

        if dia_semana < 0 or dia_semana > 6:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="horario_dia"
            )
        )

    if not hora_desde or not hora_hasta or hora_hasta <= hora_desde:
        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="horario_rango"
            )
        )

    try:
        profesional_res = (
            supabase_admin
            .table("turnos_profesionales")
            .select("id")
            .eq("id", profesional_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        if not (profesional_res.data or []):
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="profesional"
                )
            )

        (
            supabase_admin
            .table("turnos_horarios")
            .insert({
                "comercio_id": comercio_id,
                "profesional_id": profesional_id,
                "dia_semana": dia_semana,
                "hora_desde": hora_desde,
                "hora_hasta": hora_hasta,
                "activo": True,
            })
            .execute()
        )

    except Exception as error:
        print(
            "ERROR CREANDO HORARIO TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="horario_guardar"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route(
    "/agenda/horarios/<horario_id>/eliminar",
    methods=["POST"],
)
def eliminar_horario(horario_id):
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    try:
        horario_res = (
            supabase_admin
            .table("turnos_horarios")
            .select("id")
            .eq("id", horario_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        if not (horario_res.data or []):
            return redirect(
                url_for(
                    "turnos.agenda_turnos",
                    configuracion="1",
                    error="horario"
                )
            )

        (
            supabase_admin
            .table("turnos_horarios")
            .delete()
            .eq("id", horario_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )

    except Exception as error:
        print(
            "ERROR ELIMINANDO HORARIO TURNOS:",
            type(error),
            error,
            flush=True
        )

        return redirect(
            url_for(
                "turnos.agenda_turnos",
                configuracion="1",
                error="horario_guardar"
            )
        )

    return redirect(
        url_for(
            "turnos.agenda_turnos",
            configuracion="1"
        )
    )


@turnos_bp.route("/prueba")
def prueba_turnos():
    return "CLICKLOCAL TURNOS OK"
