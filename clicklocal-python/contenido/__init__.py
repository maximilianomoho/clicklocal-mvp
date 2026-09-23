from flask import Blueprint


contenido_bp = Blueprint(
    "contenido",
    __name__,
    url_prefix="/contenido",
    template_folder="templates",
)


from . import routes
