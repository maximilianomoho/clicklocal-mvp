from flask import Blueprint


pedidos_comercio_bp = Blueprint("pedidos_comercio", __name__)

from . import routes  # noqa: E402,F401
