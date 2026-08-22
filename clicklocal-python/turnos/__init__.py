from flask import Blueprint

turnos_bp = Blueprint(
    "turnos",
    __name__,
    url_prefix="/turnos",
    template_folder="templates"
)

from . import routes
