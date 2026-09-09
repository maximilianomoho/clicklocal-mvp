from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
AGENDA = RAIZ / "turnos" / "templates" / "turnos" / "agenda.html"
RUTAS = RAIZ / "turnos" / "routes.py"


def test_modal_ofrece_descargar_y_compartir_qr_sin_duplicar_whatsapp():
    contenido = AGENDA.read_text(encoding="utf-8")

    assert "Descargar QR" in contenido
    assert "Compartir QR" in contenido
    assert "Enviar QR por WhatsApp" not in contenido


def test_compartir_qr_reutiliza_endpoint_entregado_por_backend():
    contenido = AGENDA.read_text(encoding="utf-8")

    assert "const TURNERA_QR_URL = {{ turnera_qr_url | tojson }};" in contenido
    assert "fetch(TURNERA_QR_URL" in contenido
    assert 'href="{{ turnera_qr_url }}?descargar=1"' in contenido
    assert '"/turnos/agenda/qr.png"' not in contenido


def test_compartir_qr_crea_y_comparte_archivo_png():
    contenido = AGENDA.read_text(encoding="utf-8")

    assert "new File(" in contenido
    assert '"clicklocal-turnos-qr.png"' in contenido
    assert '{ type: "image/png" }' in contenido
    assert "navigator.canShare({ files: [archivo] })" in contenido
    assert "navigator.share({" in contenido
    assert "files: [archivo]" in contenido


def test_fallback_descarga_la_imagen_y_exhibe_mensaje_claro():
    contenido = AGENDA.read_text(encoding="utf-8")

    assert "descargarArchivoQr(archivo)" in contenido
    assert (
        "QR descargado. Podés compartirlo desde Fotos o Archivos."
        in contenido
    )


def test_compartir_turnos_sigue_compartiendo_el_link_publico():
    contenido = AGENDA.read_text(encoding="utf-8")

    assert 'id="btnCompartirTurnera"' in contenido
    assert "const enlace = TURNERA_PUBLICA_URL;" in contenido
    assert "navigator.share({\n          url: enlace\n        });" in contenido


def test_sigue_existiendo_un_solo_endpoint_qr():
    contenido = RUTAS.read_text(encoding="utf-8")

    assert contenido.count('@turnos_bp.route("/agenda/qr.png")') == 1
