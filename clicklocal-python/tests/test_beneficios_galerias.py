from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]


def leer(ruta):
    return (RAIZ / ruta).read_text(encoding="utf-8")


def test_galerias_reutilizan_distintivo_modal_y_endpoint_existentes():
    general = leer("templates/index.html")
    gastronomia = leer("templates/gastronomia/inicio.html")
    modal = leer("templates/beneficios/_publico_modal.html")
    for html in (general, gastronomia):
        assert "beneficios_20_etiqueta" in html
        assert "data-beneficios-galeria-indice" in html
        assert "_publico_modal.html" in html
    assert "/beneficios/{{ oferta.beneficio.id }}/codigo" in modal
    assert "beneficios/publico.js" in modal


def test_gastronomia_no_mezcla_beneficio_con_precio_o_carrito():
    rutas = leer("gastronomia/routes.py")
    plantilla = leer("templates/gastronomia/inicio.html")
    assert 'producto["beneficio_20_etiqueta"]' not in rutas
    assert "precio_promocional" in rutas
    assert "promo.precio_anterior_mostrar" in plantilla
    assert "data-carrito" not in leer("templates/beneficios/_publico_modal.html")


def test_formulario_usa_fecha_visible_y_normaliza_controles():
    panel = leer("templates/panel.html")
    javascript = leer("static/beneficios/beneficios-20.js")
    estilos = leer("static/beneficios/beneficios-20.css")
    assert panel.count('placeholder="DD-MM-AAAA"') == 2
    assert "sincronizarDias" in javascript
    assert "sincronizarHorario" in javascript
    assert 'hora_desde: franja ? horaDesde : null' in javascript
    assert ".beneficios-20-dias[hidden]" in estilos


def test_arnes_expone_ambas_galerias_sin_importar_app():
    arnes = leer("tests/beneficios_preview_app.py")
    assert '"/preview/galeria"' in arnes
    assert '"/preview/gastronomia"' in arnes
    assert "import app" not in arnes
