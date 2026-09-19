import re
from pathlib import Path


MENU_PATH = Path(__file__).parents[1] / "templates" / "gastronomia" / "menu.html"
MENU = MENU_PATH.read_text(encoding="utf-8")


def bloque_funcion(nombre, siguiente=None):
    inicio = MENU.index(f"function {nombre}(")
    fin = MENU.index(f"function {siguiente}(", inicio) if siguiente else len(MENU)
    return MENU[inicio:fin]


def test_existe_estado_checkout_compartido_con_todos_los_datos():
    bloque = MENU[MENU.index("const estadoCheckout = {"):MENU.index(
        "function limpiarCotizacionEstadoCheckout"
    )]
    for campo in (
        "nombre", "apellido", "whatsapp", "modalidad", "direccion",
        "formaPago", "pagaCon", "notaGeneral", "cotizacion", "costo",
        "distancia", "telefono", "token",
    ):
        assert re.search(rf"\b{campo}\s*:", bloque)


def test_captura_y_restaura_todos_los_campos_del_checkout():
    captura = bloque_funcion("capturarEstadoCheckout", "capturarEstadoCheckoutActivo")
    restauracion = bloque_funcion("restaurarEstadoCheckout", "autoAjustarAclaracionProducto")
    selectores = (
        ".cart-customer-first-name",
        ".cart-customer-last-name",
        ".cart-customer-phone",
        ".cart-delivery-address-input",
        ".cart-cash-input",
        ".cart-general-note-input",
        'input[name^="modalidad-pedido"]',
        'input[name^="forma-pago-"]',
    )
    for selector in selectores:
        assert selector in captura
        assert selector in restauracion


def test_render_mobile_captura_antes_y_restaura_despues_del_inner_html():
    bloque = bloque_funcion("renderizarCarritoMobile", "cambiarCantidadCarrito")
    posicion_render = bloque.rindex("contenedor.innerHTML = html")
    assert bloque.index("capturarEstadoCheckoutActivo(contenedor)") < posicion_render
    assert bloque.index("restaurarEstadoCheckout(contenedor)") > posicion_render


def test_render_desktop_captura_antes_y_restaura_despues_del_inner_html():
    bloque = bloque_funcion("actualizarCarrito", "agregarProductoDesdeModal")
    posicion_render = bloque.rindex("carritoVacio.innerHTML = html")
    assert bloque.index("capturarEstadoCheckoutActivo()") < posicion_render
    assert bloque.index("restaurarEstadoCheckout(cart)") > posicion_render


def test_carrito_vacio_no_limpia_estado_checkout():
    mobile = bloque_funcion("renderizarCarritoMobile", "cambiarCantidadCarrito")
    desktop = bloque_funcion("actualizarCarrito", "agregarProductoDesdeModal")
    for bloque in (mobile, desktop):
        assert "limpiarCotizacionEstadoCheckout" not in bloque
        assert "estadoCheckout =" not in bloque


def test_cotizacion_se_captura_y_restaura_solo_si_sigue_vigente():
    captura = bloque_funcion("capturarEstadoCheckout", "capturarEstadoCheckoutActivo")
    restauracion = bloque_funcion("restaurarEstadoCheckout", "autoAjustarAclaracionProducto")
    for dataset in (
        "deliveryCosto", "deliveryDistancia", "deliveryDireccion",
        "deliveryTelefono", "deliveryToken",
    ):
        assert f"dataset.{dataset}" in captura
        assert f"dataset.{dataset}" in restauracion
    assert 'estadoCheckout.modalidad === "Delivery"' in restauracion
    assert "direccion.value.trim() === cotizacion.direccion" in restauracion
    assert "estadoCheckout.whatsapp.trim() === cotizacion.telefono" in restauracion
    assert "dispatchEvent" not in restauracion
    assert "cotizarDelivery(" not in restauracion


def test_modificar_carrito_no_invalida_delivery_por_si_mismo():
    for nombre, siguiente in (
        ("cambiarCantidadCarrito", "vaciarCarrito"),
        ("vaciarCarrito", "actualizarContadoresMenu"),
        ("actualizarCarrito", "agregarProductoDesdeModal"),
        ("agregarProductoDesdeModal", "abrirModal"),
    ):
        bloque = bloque_funcion(nombre, siguiente)
        assert not re.search(r"^\s*invalidarCotizacionDelivery\(", bloque, re.MULTILINE)
        assert "limpiarCotizacionEstadoCheckout" not in bloque


def test_telefono_direccion_y_modalidad_conservan_invalidacion_existente():
    assert 'oninput="invalidarCotizacionPorTelefono(this)"' in MENU
    assert "oninput=\"invalidarCotizacionDelivery(this.closest(" in MENU
    modalidad = bloque_funcion("actualizarDireccionEntrega", "inicializarSelectoresCompactos")
    telefono = bloque_funcion("invalidarCotizacionPorTelefono", "cotizarDelivery")
    assert "invalidarCotizacionDelivery(bloque)" in modalidad
    assert "invalidarCotizacionDelivery(" in telefono


def test_idempotencia_permanece_separada_del_rerender():
    assert "function obtenerIdempotencyKey(payload)" in MENU
    assert "sessionStorage.setItem" in MENU
    assert "pedidoPayload.idempotency_key" in MENU
    restauracion = bloque_funcion("restaurarEstadoCheckout", "autoAjustarAclaracionProducto")
    assert "obtenerIdempotencyKey" not in restauracion
    assert "crypto.randomUUID" not in restauracion
