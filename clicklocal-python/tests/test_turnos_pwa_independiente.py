import json
from pathlib import Path

from flask import Flask

import turnos.routes as routes


RAIZ = Path(__file__).resolve().parents[1]


def test_manifest_turnos_tiene_identidad_y_destino_propios():
    manifest = json.loads(
        (RAIZ / "static" / "turnos-manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["id"] == "/"
    assert manifest["name"] == "ClickLocal Turnos"
    assert manifest["short_name"] == "Turnos"
    assert manifest["start_url"] == "/turnos/agenda"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"


def test_agenda_separa_manifest_y_script_por_origen():
    agenda = (
        RAIZ / "turnos" / "templates" / "turnos" / "agenda.html"
    ).read_text(encoding="utf-8")

    assert 'href="/static/turnos-manifest.json"' in agenda
    assert 'href="/static/manifest.json"' in agenda
    assert 'src="/static/turnos-pwa.js"' in agenda
    assert 'src="/static/pwa.js" data-install-ui="false"' in agenda
    assert "ES_ORIGEN_APP_TURNOS" in agenda


def test_script_turnos_usa_estado_independiente_y_registra_sw_raiz():
    script = (RAIZ / "static" / "turnos-pwa.js").read_text(
        encoding="utf-8"
    )

    assert 'navigator.serviceWorker.register("/sw.js")' in script
    assert '"beforeinstallprompt"' in script
    assert '"appinstalled"' in script
    assert '"(display-mode: standalone)"' in script
    assert "clicklocal_turnos_app_instalada" in script
    assert "clickLocalTurnosSolicitarInstalacion" in script
    assert "clickLocalSolicitarInstalacion" not in script


def test_origen_turnos_se_detecta_por_host_sin_depender_del_esquema(
    monkeypatch,
):
    app = Flask(__name__)
    monkeypatch.setattr(
        routes,
        "TURNOS_APP_ORIGIN",
        "https://turnos.clicklocal.com",
    )

    with app.test_request_context(
        "/turnos/agenda",
        base_url="http://turnos.clicklocal.com",
    ):
        assert routes._es_origen_app_turnos() is True

    with app.test_request_context(
        "/turnos/agenda",
        base_url="https://clicklocal.com",
    ):
        assert routes._es_origen_app_turnos() is False
