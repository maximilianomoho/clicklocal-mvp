from pathlib import Path

from gastronomia.routes import (
    _categorias_catalogo,
    _limpiar_categoria_producto,
)


def test_selector_sin_categorias_admite_producto_sin_categoria():
    assert _categorias_catalogo([
        {"categoria": None},
        {"categoria": "   "},
    ]) == []
    assert _limpiar_categoria_producto("") is None


def test_selector_usa_categorias_reales_unicas_del_comercio():
    categorias = _categorias_catalogo([
        {"categoria": "Hamburguesas"},
        {"categoria": "hamburguesas"},
        {"categoria": "Bebidas"},
        {"categoria": None},
    ])

    assert categorias == ["Hamburguesas", "Bebidas"]


def test_nueva_categoria_se_limpia_y_puede_quedar_vacia():
    assert _limpiar_categoria_producto("  Postres  ") == "Postres"
    assert _limpiar_categoria_producto("   ") is None


def test_formulario_conserva_un_solo_campo_categoria_para_backend():
    raiz = Path(__file__).resolve().parents[1]
    plantilla = (
        raiz / "templates/gastronomia/panel.html"
    ).read_text(encoding="utf-8")

    assert '<option value="">Sin categoría</option>' in plantilla
    assert '<option value="__nueva__">+ Nueva categoría</option>' in plantilla
    assert 'type="hidden" name="categoria"' in plantilla
    assert 'id="gastronomia-categoria-nueva"' in plantilla
    assert "seleccionarCategoriaActual(" in plantilla
    assert "sincronizarCategoria();" in plantilla
