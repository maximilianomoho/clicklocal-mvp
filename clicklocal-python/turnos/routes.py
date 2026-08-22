from flask import render_template, session

from . import turnos_bp


@turnos_bp.route("/agenda")
def agenda_turnos():
    comercio = session.get("comercio") or {}

    nombre_comercio = (
        comercio.get("nombre_negocio")
        or comercio.get("nombre")
        or "Mi negocio"
    )

    return render_template(
        "turnos/agenda.html",
        nombre_comercio=nombre_comercio
    )


@turnos_bp.route("/prueba")
def prueba_turnos():
    return "CLICKLOCAL TURNOS OK"
