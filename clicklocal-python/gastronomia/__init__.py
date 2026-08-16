from flask import Blueprint

gastronomia_bp = Blueprint(
    "gastronomia",
    __name__,
    url_prefix="/gastronomia",
)

from . import routes
