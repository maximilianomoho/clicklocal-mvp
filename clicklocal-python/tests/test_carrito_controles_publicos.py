from pathlib import Path


JS = Path("static/carrito/carrito.js").read_text()
CSS = Path("static/carrito/carrito.css").read_text()
PERFIL = Path("templates/perfil.html").read_text()
DETALLE = Path("templates/detalle.html").read_text()


def test_controles_parten_del_carrito_persistido_por_comercio():
    assert '"clicklocal_carrito_" + config.comercioId' in JS
    assert "let carrito = leerCarrito();" in JS
    assert "registrarBotonesProducto();\n    renderizar();" in JS
    assert "const item = carrito[id];" in JS


def test_estado_vacio_y_estado_con_cantidad_se_renderizan_desde_el_carrito():
    assert 'agregar.textContent = "Agregar al carrito";' in JS
    assert 'cantidad.textContent = String(item.cantidad);' in JS
    assert 'restar.textContent = "−";' in JS
    assert 'sumar.textContent = "+";' in JS
    assert 'renderizarControlesProducto();' in JS


def test_sumar_restar_y_llegar_a_cero_reutilizan_la_logica_existente():
    assert 'cambiarCantidad(id, accion.dataset.carritoCantidadAccion === "sumar" ? 1 : -1);' in JS
    assert "carrito[id].cantidad = Math.min(99, carrito[id].cantidad + cambio);" in JS
    assert "if (carrito[id].cantidad <= 0) {\n        delete carrito[id];" in JS
    assert "guardarCarrito();\n      renderizar();" in JS


def test_todos_los_controles_del_mismo_producto_se_actualizan_juntos():
    assert 'document.querySelectorAll(".cl-carrito-producto-control")' in JS
    assert "control.replaceChildren();" in JS
    assert "if (carrito[id]) {\n            return;" in JS


def test_modal_resumen_y_totales_siguen_actualizandose_en_el_render_general():
    inicio = JS.index("function renderizar()")
    fin = JS.index("function formatearPrecio", inicio)
    render = JS[inicio:fin]
    assert "renderizarControlesProducto();" in render
    assert "interfaz.botonFlotante.textContent" in render
    assert "interfaz.resumen.textContent" in render
    assert "actualizarResumenCheckout();" in render


def test_perfil_y_detalle_conservan_condiciones_y_comparten_el_mismo_js():
    assert "{% if publicacion.comprable %}" in PERFIL
    assert "{% if publicacion_comprable %}" in DETALLE
    assert "data-carrito-producto" in PERFIL
    assert "data-carrito-producto" in DETALLE
    carga = "filename='carrito/carrito.js'"
    assert carga in PERFIL
    assert carga in DETALLE


def test_controles_son_botones_accesibles_y_tienen_area_tactil():
    assert 'restar.type = "button";' in JS
    assert 'sumar.type = "button";' in JS
    assert 'agregar.type = "button";' in JS
    assert 'restar.setAttribute("aria-label"' in JS
    assert 'sumar.setAttribute("aria-label"' in JS
    assert ".cl-carrito-producto-cantidad" in CSS
    assert "min-width: 44px" in CSS
    assert "min-height: 44px" in CSS
