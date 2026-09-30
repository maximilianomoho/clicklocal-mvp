from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import app as app_module


COMERCIO_ID = "11111111-1111-1111-1111-111111111111"
URL_MENU = f"https://clicklocal.com.ar/gastronomia/comercio/{COMERCIO_ID}"


class ImagenQrFalsa:
    def save(self, archivo, format):
        assert format == "PNG"
        assert isinstance(archivo, BytesIO)
        archivo.write(b"png-menu-prueba")


def test_qr_menu_requiere_sesion_comercial():
    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value=None,
    ):
        respuesta = app_module.app.test_client().get(
            "/gastronomia/panel/qr-menu.png"
        )

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/login.html?next=/gastronomia/panel")


def test_qr_menu_usa_comercio_resuelto_y_codifica_url_publica_exacta():
    capturado = {}

    def crear_qr(url):
        capturado["url"] = url
        return ImagenQrFalsa()

    comercio = {"id": COMERCIO_ID}
    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value=comercio,
    ) as resolver, patch("gastronomia.routes.qrcode.make", crear_qr):
        respuesta = app_module.app.test_client().get(
            "/gastronomia/panel/qr-menu.png"
        )

    resolver.assert_called_once_with()
    assert capturado["url"] == URL_MENU
    assert respuesta.status_code == 200
    assert respuesta.mimetype == "image/png"
    assert respuesta.data == b"png-menu-prueba"


def test_qr_menu_descarga_png_con_nombre_claro():
    with patch(
        "gastronomia.routes._comercio_panel_gastronomia",
        return_value={"id": COMERCIO_ID},
    ), patch(
        "gastronomia.routes.qrcode.make",
        return_value=ImagenQrFalsa(),
    ):
        respuesta = app_module.app.test_client().get(
            "/gastronomia/panel/qr-menu.png?descargar=1"
        )

    assert respuesta.status_code == 200
    assert respuesta.mimetype == "image/png"
    assert respuesta.headers["Content-Disposition"] == (
        "attachment; filename=clicklocal-menu-qr.png"
    )


def test_endpoint_no_acepta_comercio_id_arbitrario():
    reglas = {
        regla.rule
        for regla in app_module.app.url_map.iter_rules()
        if regla.endpoint == "gastronomia.qr_menu_gastronomia"
    }
    assert reglas == {"/gastronomia/panel/qr-menu.png"}
    assert app_module.app.test_client().get(
        f"/gastronomia/panel/qr-menu.png/{COMERCIO_ID}"
    ).status_code == 404


def test_panel_ofrece_ver_descargar_y_compartir_qr_sin_romper_promocion():
    plantilla = Path("templates/gastronomia/_panel_nav.html").read_text(
        encoding="utf-8"
    )

    assert "Promocionar mi menú" in plantilla
    assert "Copiar enlace" in plantilla
    assert "Copiar texto para redes" in plantilla
    assert "Copiar enlace para Instagram" in plantilla
    assert "QR del menú" in plantilla
    assert "gastronomia.qr_menu_gastronomia" in plantilla
    assert "Descargar QR" in plantilla
    assert "Compartir QR" in plantilla
    assert "navigator.canShare({ files: [archivo] })" in plantilla
    assert "navigator.share({" in plantilla
    assert "files: [archivo]" in plantilla
    assert "descargarArchivoQr(archivo)" in plantilla
    assert "clicklocal-menu-qr.png" in plantilla


def test_qr_gastronomia_reutiliza_patron_sin_modificar_turnos():
    rutas_gastronomia = Path("gastronomia/routes.py").read_text(encoding="utf-8")
    rutas_turnos = Path("turnos/routes.py").read_text(encoding="utf-8")
    plantilla_turnos = Path(
        "turnos/templates/turnos/agenda.html"
    ).read_text(encoding="utf-8")

    assert "qrcode.make(url_menu)" in rutas_gastronomia
    assert "BytesIO()" in rutas_gastronomia
    assert '@turnos_bp.route("/agenda/qr.png")' in rutas_turnos
    assert "qrcode.make(turnera_publica_url)" in rutas_turnos
    assert "clicklocal-turnos-qr.png" in rutas_turnos
    assert "Código QR de tus turnos" in plantilla_turnos
