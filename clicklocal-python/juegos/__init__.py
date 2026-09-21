from flask import Blueprint


juegos_bp = Blueprint(
    "juegos",
    __name__,
    url_prefix="/jugar",
)


from . import routes
