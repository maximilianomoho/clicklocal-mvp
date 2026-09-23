from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from app import app


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("slug", "frase", "url_publica"), [
    ("circulo", "¿Qué tan perfecto te sale un círculo? ⭕", "https://clicklocal.com.ar/jugar/circulo"),
    ("reflejos", "¿Cuánto hacés en Reflejos? ⚡", "https://clicklocal.com.ar/jugar/reflejos"),
])
def test_compartir_juego_registra_analytics_y_redirige_a_whatsapp(slug, frase, url_publica):
    with patch("juegos.routes._registrar_evento_clickjuegos") as analytics:
        respuesta = app.test_client().get(f"/jugar/compartir/{slug}")

    assert respuesta.status_code == 302
    destino = urlsplit(respuesta.headers["Location"])
    assert destino.scheme == "https" and destino.netloc == "wa.me"
    texto = parse_qs(destino.query)["text"][0]
    assert frase in texto
    assert url_publica in texto
    analytics.assert_called_once_with(
        "compartir_juego", juego=slug, destino="whatsapp",
    )


def test_juego_inexistente_no_registra_compartir():
    with patch("juegos.routes._registrar_evento_clickjuegos") as analytics:
        respuesta = app.test_client().get("/jugar/compartir/inexistente")
    assert respuesta.status_code == 302
    assert urlsplit(respuesta.headers["Location"]).path == "/jugar/"
    analytics.assert_not_called()


def test_botones_compartir_estan_en_ambos_juegos():
    for slug in ("circulo", "reflejos"):
        template = (ROOT / f"templates/juegos/{slug}.html").read_text(encoding="utf-8")
        assert "Compartir juego" in template
        assert f"juego_slug='{slug}'" in template
        assert "WhatsApp" in template


def test_mobile_reflejos_deja_informacion_antes_del_area_interactiva():
    template = (ROOT / "templates/juegos/reflejos.html").read_text(encoding="utf-8")
    assert template.index('class="reflejos-info"') < template.index('class="reflejos-juego-columna"')
    css = (ROOT / "static/juegos/juegos.css").read_text(encoding="utf-8")
    assert 'grid-template-areas: "info" "juego"' in css
    assert ".reflejos-info { grid-area: info; }" in css
    assert ".reflejos-juego-columna { grid-area: juego;" in css


def test_mobile_compacta_areas_sin_cambiar_touch_action():
    css = (ROOT / "static/juegos/juegos.css").read_text(encoding="utf-8")
    assert ".circulo-dibujo-columna { padding-inline: 6px; }" in css
    assert ".reflejos-juego-columna { grid-area: juego; padding-inline: 6px; }" in css
    assert ".circulo-tarjeta { width: min(100%, 330px); margin-inline: auto; }" in css
    assert ".circulo-lienzo-wrap { width: min(100%, 300px); aspect-ratio: 1; margin-inline: auto; }" in css
    assert ".cinco-area { width: min(100%, 420px)" not in css
    assert "width: min(100%, 340px); min-height: clamp(290px, 38svh, 340px);" in css
    assert ".circulo-lienzo-wrap" in css and "touch-action: none" in css
    assert "touch-action: manipulation" in css


def test_desktop_conserva_contrato_visual_compartido():
    css = (ROOT / "static/juegos/juegos.css").read_text(encoding="utf-8")
    assert "width: min(100%, 1120px);" in css
    assert "grid-template-columns: minmax(350px, .86fr) minmax(470px, 1.14fr);" in css
    assert "width: min(100%, 580px);" in css
    assert "height: min(62vh, 480px);" in css
    assert "min-height: 400px;" in css
    assert "height: min(58vh, 400px);" in css
    assert "min-height: 360px;" in css
