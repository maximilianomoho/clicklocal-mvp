from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import abort, current_app, g, jsonify, redirect, render_template, request, session, url_for

from config.supabase_config import supabase_admin
from modulos import obtener_modulo, pos_activo_para_comercio
from whatsapp import limpiar_numero_whatsapp

from . import gastronomia_bp
from .services.delivery import (
    DeliveryError,
    clave_cache_delivery,
    clave_origen_coordenadas,
    completar_direccion_destino,
    consultar_distancia_osm,
    firmar_cotizacion,
    geocodificar_direccion_nominatim,
    normalizar_direccion,
    seleccionar_franja,
    validar_configuracion_delivery,
    validar_coordenadas,
    validar_cotizacion,
)
from .services.pedidos import (
    ESTADOS_PAGO,
    ESTADOS_PEDIDO,
    ORIGENES_PEDIDO,
    TIPOS_ENTREGA,
    PedidoError,
    buscar_pedido_idempotente,
    construir_idempotency_fingerprint,
    crear_pedido,
    pedido_es_venta,
    preparar_actualizacion_estados,
    preparar_transicion_pedido,
)


LIMITE_PRODUCTOS_ACTIVOS_GASTRONOMIA = 30


def _limite_productos_gastronomia_alcanzado(cantidad_activos):
    return cantidad_activos >= LIMITE_PRODUCTOS_ACTIVOS_GASTRONOMIA


def _modo_gestor_gastronomia_activo():
    return bool(
        session.get("admin_logueado")
        and session.get("gestor_comercio_id")
    )


def _pos_activo_gastronomia(comercio_id):
    """Consulta una sola vez por request el POS del comercio resuelto."""
    cache = g.setdefault("pos_activo_gastronomia", {})
    clave = str(comercio_id or "")
    if clave not in cache:
        cache[clave] = bool(pos_activo_para_comercio(comercio_id))
    return cache[clave]


def _respuesta_pos_requerido(json_api=False):
    if json_api:
        return jsonify({
            "ok": False,
            "error": "Esta función requiere tener POS activo.",
        }), 403
    return redirect(url_for(
        "gastronomia.ventas_gastronomia",
        pos_requerido="1",
    ))


@gastronomia_bp.context_processor
def contexto_modo_gestor_gastronomia():
    """Evita exigir al admin los términos pendientes del propietario."""
    comercio_id = (session.get("comercio") or {}).get("id")
    pos_activo = (
        _pos_activo_gastronomia(comercio_id)
        if comercio_id else False
    )
    return {
        "modo_gestor": _modo_gestor_gastronomia_activo(),
        "pos_activo": pos_activo,
        "plan_nombre_panel": (
            "Gastronomía POS"
            if pos_activo
            else "Gastronomía Base"
        ),
    }


def _fecha_desde_iso(valor):
    if not valor:
        return None

    try:
        return date.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


def _esta_vigente(desde=None, hasta=None):
    hoy = date.today()

    fecha_desde = _fecha_desde_iso(desde)
    fecha_hasta = _fecha_desde_iso(hasta)

    if fecha_desde and hoy < fecha_desde:
        return False

    if fecha_hasta and hoy > fecha_hasta:
        return False

    return True


def _parsear_importe_config(valor):
    texto = str(valor or "").strip()

    if not texto:
        return 0.0

    texto = (
        texto
        .replace("$", "")
        .replace(" ", "")
    )

    if "." in texto and "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")
    elif "." in texto:
        parte_entera, parte_decimal = texto.split(".", 1)

        if len(parte_decimal) == 3 and parte_entera:
            texto = parte_entera + parte_decimal

    importe = float(texto)

    if importe < 0:
        raise ValueError

    return importe


def _formatear_precio(valor):
    if valor is None:
        return ""

    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)

    if numero.is_integer():
        return "$ " + f"{int(numero):,}".replace(",", ".")

    entero, decimal = f"{numero:.2f}".split(".")
    entero = f"{int(entero):,}".replace(",", ".")

    return f"$ {entero},{decimal}"


def _precio_producto_input(valor):
    if valor is None:
        return ""

    numero = float(valor)

    if numero.is_integer():
        return str(int(numero))

    return str(numero)


def _parsear_precio_producto(valor):
    if not str(valor or "").strip():
        raise ValueError

    importe = _parsear_importe_config(valor)
    return int(importe) if importe.is_integer() else importe


def _cargar_catalogo_gastronomico(comercio_id):
    """Carga el catalogo activo con sus grupos y opciones activas."""

    productos_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,descripcion,categoria,precio,"
            "precio_promocional,imagen_url,disponible,"
            "activo,destacado,destacado_hasta,"
            "promocion_desde,promocion_hasta,orden"
        )
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .order("orden")
        .execute()
    )

    productos = productos_res.data or []
    producto_ids = [
        producto.get("id")
        for producto in productos
        if producto.get("id")
    ]
    grupos = []
    opciones = []

    if producto_ids:
        grupos_res = (
            supabase_admin
            .table("gastronomia_grupos_opciones")
            .select(
                "id,producto_id,nombre,minimo,maximo,"
                "orden,activo"
            )
            .in_("producto_id", producto_ids)
            .eq("activo", True)
            .order("orden")
            .execute()
        )
        grupos = grupos_res.data or []
        grupo_ids = [
            grupo.get("id")
            for grupo in grupos
            if grupo.get("id")
        ]

        if grupo_ids:
            opciones_res = (
                supabase_admin
                .table("gastronomia_opciones")
                .select(
                    "id,grupo_id,nombre,precio_extra,"
                    "disponible,activo,orden"
                )
                .in_("grupo_id", grupo_ids)
                .eq("activo", True)
                .order("orden")
                .execute()
            )
            opciones = opciones_res.data or []

    opciones_por_grupo = {}

    for opcion in opciones:
        grupo_id = opcion.get("grupo_id")
        opcion["precio_extra_mostrar"] = _formatear_precio(
            opcion.get("precio_extra")
        )
        opciones_por_grupo.setdefault(grupo_id, []).append(opcion)

    grupos_por_producto = {}

    for grupo in grupos:
        grupo["opciones"] = opciones_por_grupo.get(
            grupo.get("id"),
            [],
        )
        grupos_por_producto.setdefault(
            grupo.get("producto_id"),
            [],
        ).append(grupo)

    for producto in productos:
        precio = producto.get("precio")
        precio_promocional = producto.get("precio_promocional")
        producto["precio_mostrar"] = _formatear_precio(precio)
        producto["precio_promocional_mostrar"] = (
            _formatear_precio(precio_promocional)
            if precio_promocional is not None
            else ""
        )
        producto["precio_venta"] = (
            precio_promocional
            if precio_promocional is not None
            else precio
        )
        producto["grupos_opciones"] = grupos_por_producto.get(
            producto.get("id"),
            [],
        )

    return productos


def _categorias_catalogo(productos):
    categorias = []
    claves_vistas = set()

    for producto in productos:
        categoria = str(producto.get("categoria") or "").strip()
        clave = categoria.lower()

        if not categoria or clave in claves_vistas:
            continue

        claves_vistas.add(clave)
        categorias.append(categoria)

    return categorias


def _limpiar_categoria_producto(valor):
    return str(valor or "").strip() or None


@gastronomia_bp.route("")
@gastronomia_bp.route("/")
def inicio():
    busqueda = str(
        request.args.get("q") or ""
    ).strip()

    config_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,"
            "acepta_retiro,pedido_minimo,costo_envio,"
            "tiempo_estimado_min"
        )
        .eq("activo", True)
        .execute()
    )

    configuraciones = config_res.data or []

    config_por_comercio = {
        str(config.get("comercio_id")): config
        for config in configuraciones
        if config.get("comercio_id")
    }

    comercio_ids = list(config_por_comercio.keys())
    comercios_gastronomicos = []

    if comercio_ids:
        comercios_res = (
            supabase_admin
            .table("comercios")
            .select(
                "id,nombre_negocio,categoria,descripcion,"
                "logo_url,direccion,whatsapp"
            )
            .in_("id", comercio_ids)
            .execute()
        )

        comercios = comercios_res.data or []

        productos_res = (
            supabase_admin
            .table("gastronomia_productos")
            .select(
                "id,comercio_id,nombre,descripcion,imagen_url,"
                "precio,precio_promocional,"
                "activo,disponible,destacado,"
                "destacado_hasta,promocion_desde,"
                "promocion_hasta,orden"
            )
            .in_("comercio_id", comercio_ids)
            .eq("activo", True)
            .order("orden")
            .execute()
        )

        productos = productos_res.data or []

        # ====================================================
        # CLICKLOCAL GASTRONOMIA - TEXTO BUSCABLE POR COMERCIO
        # Permite encontrar un comercio por cualquiera de sus
        # productos activos y disponibles.
        # ====================================================

        productos_busqueda_por_comercio = {}

        for producto in productos:
            if not producto.get("disponible"):
                continue

            comercio_id_producto = str(
                producto.get("comercio_id") or ""
            )

            nombre_producto = str(
                producto.get("nombre") or ""
            ).strip()

            if (
                comercio_id_producto
                and nombre_producto
            ):
                productos_busqueda_por_comercio.setdefault(
                    comercio_id_producto,
                    []
                ).append(nombre_producto)

        imagen_por_comercio = {}

        for producto in productos:
            comercio_id = str(producto.get("comercio_id"))

            if (
                comercio_id not in imagen_por_comercio
                and producto.get("imagen_url")
            ):
                imagen_por_comercio[comercio_id] = (
                    producto.get("imagen_url")
                )

        for comercio in comercios:
            comercio_id = str(comercio.get("id"))
            config = config_por_comercio.get(comercio_id, {})

            comercio["acepta_delivery"] = bool(
                config.get("acepta_delivery")
            )
            comercio["acepta_retiro"] = bool(
                config.get("acepta_retiro")
            )
            comercio["tiempo_estimado_min"] = (
                config.get("tiempo_estimado_min")
            )
            comercio["costo_envio"] = config.get("costo_envio")
            comercio["imagen_portada"] = (
                imagen_por_comercio.get(comercio_id)
                or comercio.get("logo_url")
            )

            comercio["busqueda_texto"] = " ".join([
                str(comercio.get("nombre_negocio") or ""),
                str(comercio.get("categoria") or ""),
                str(comercio.get("descripcion") or ""),
                " ".join(
                    productos_busqueda_por_comercio.get(
                        comercio_id,
                        []
                    )
                ),
            ]).strip().lower()

            comercios_gastronomicos.append(comercio)

    comercios_gastronomicos.sort(
        key=lambda comercio: (
            comercio.get("nombre_negocio") or ""
        ).lower()
    )

    nombre_comercio_por_id = {
        str(comercio.get("id")): comercio.get("nombre_negocio")
        for comercio in comercios_gastronomicos
    }

    destacados_gastronomicos = []
    promos_gastronomicas = []

    destacados_por_comercio = {}

    for producto in productos if comercio_ids else []:
        comercio_id = str(producto.get("comercio_id"))

        if (
            not producto.get("imagen_url")
            or not producto.get("disponible")
        ):
            continue

        # ------------------------------------------------------
        # PRODUCTOS DESTACADOS
        # Máximo 3 vigentes por comercio.
        # ------------------------------------------------------

        if (
            producto.get("destacado")
            and _esta_vigente(
                hasta=producto.get("destacado_hasta")
            )
        ):
            cantidad_comercio = (
                destacados_por_comercio.get(comercio_id, 0)
            )

            if cantidad_comercio < 3:
                destacados_gastronomicos.append({
                    "id": producto.get("id"),
                    "comercio_id": comercio_id,
                    "comercio_nombre": (
                        nombre_comercio_por_id.get(
                            comercio_id,
                            ""
                        )
                    ),
                    "nombre": producto.get("nombre"),
                    "imagen_url": producto.get("imagen_url"),
                    "precio_mostrar": _formatear_precio(
                        producto.get("precio")
                    ),
                    "destacado_hasta": (
                        producto.get("destacado_hasta")
                    ),
                })

                destacados_por_comercio[comercio_id] = (
                    cantidad_comercio + 1
                )

        # ------------------------------------------------------
        # PROMOCIONES VIGENTES
        # ------------------------------------------------------

        precio_promocional = producto.get(
            "precio_promocional"
        )

        if (
            precio_promocional is not None
            and _esta_vigente(
                desde=producto.get("promocion_desde"),
                hasta=producto.get("promocion_hasta"),
            )
        ):
            promos_gastronomicas.append({
                "id": producto.get("id"),
                "comercio_id": comercio_id,
                "comercio_nombre": (
                    nombre_comercio_por_id.get(
                        comercio_id,
                        ""
                    )
                ),
                "nombre": producto.get("nombre"),
                "imagen_url": producto.get("imagen_url"),
                "precio_mostrar": _formatear_precio(
                    precio_promocional
                ),
                "precio_anterior_mostrar": (
                    _formatear_precio(
                        producto.get("precio")
                    )
                ),
                "promocion_desde": (
                    producto.get("promocion_desde")
                ),
                "promocion_hasta": (
                    producto.get("promocion_hasta")
                ),
            })

    destacados_gastronomicos = (
        destacados_gastronomicos[:12]
    )

    promos_gastronomicas = promos_gastronomicas[:12]

    return render_template(
        "gastronomia/inicio.html",
        comercios_gastronomicos=comercios_gastronomicos,
        destacados_gastronomicos=destacados_gastronomicos,
        promos_gastronomicas=promos_gastronomicas,
        busqueda=busqueda,
    )


@gastronomia_bp.route("/comercio/<comercio_id>")
def comercio_gastronomico(comercio_id):
    comercio_res = (
        supabase_admin
        .table("comercios")
        .select(
            "id,nombre_negocio,whatsapp,direccion,ciudad,"
            "categoria,descripcion,logo_url"
        )
        .eq("id", comercio_id)
        .limit(1)
        .execute()
    )

    comercios = comercio_res.data or []

    if not comercios:
        abort(404)

    comercio = comercios[0]

    config_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,"
            "acepta_retiro,pedido_minimo,costo_envio,"
            "tiempo_estimado_min,descuento_efectivo_pct,"
            "descuento_transferencia_pct,delivery_distancia_activo,"
            "delivery_franjas,delivery_origen_direccion"
        )
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .limit(1)
        .execute()
    )

    configuraciones = config_res.data or []

    if not configuraciones:
        return redirect(
            url_for(
                "gastronomia.inicio"
            )
        )

    configuracion = configuraciones[0]

    productos = _cargar_catalogo_gastronomico(comercio_id)

    configuracion["pedido_minimo_mostrar"] = (
        _formatear_precio(
            configuracion.get("pedido_minimo")
        )
        if configuracion.get("pedido_minimo") is not None
        else ""
    )

    configuracion["costo_envio_mostrar"] = (
        _formatear_precio(
            configuracion.get("costo_envio")
        )
    )

    return render_template(
        "gastronomia/menu.html",
        comercio=comercio,
        configuracion=configuracion,
        productos=productos,
    )

# ==============================================================
# CLICKLOCAL GASTRONOMIA PANEL LECTURA V1
# ==============================================================

def _comercio_panel_gastronomia():
    """
    Resuelve el comercio que puede operar el panel gastronómico.

    Casos:
    - comerciante normal: session["user_id"] + session["comercio"]
    - modo gestor: admin_logueado + gestor_comercio_id

    No modifica la sesión ni suplanta usuarios.
    """

    comercio_sesion = session.get("comercio") or {}
    comercio_id = comercio_sesion.get("id")

    if not comercio_id:
        return None

    gestor_id = session.get("gestor_comercio_id")
    modo_gestor = bool(
        session.get("admin_logueado")
        and gestor_id
    )

    if modo_gestor:
        if str(gestor_id) != str(comercio_id):
            return None

        consulta = (
            supabase_admin
            .table("comercios")
            .select(
                "id,user_id,nombre_negocio,whatsapp,"
                "direccion,direccion_mostrar,ciudad,categoria,descripcion,"
                "logo_url,activo,"
                "terminos_version,terminos_aceptados_at"
            )
            .eq("id", comercio_id)
            .limit(1)
            .execute()
        )

    else:
        user_id = session.get("user_id")

        if not user_id:
            return None

        consulta = (
            supabase_admin
            .table("comercios")
            .select(
                "id,user_id,nombre_negocio,whatsapp,"
                "direccion,direccion_mostrar,ciudad,categoria,descripcion,"
                "logo_url,activo,"
                "terminos_version,terminos_aceptados_at"
            )
            .eq("id", comercio_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

    comercios = consulta.data or []

    if not comercios:
        return None

    comercio = comercios[0]

    if comercio.get("activo") is False:
        return None

    return comercio


ESTADOS_PEDIDO_ACTIVOS = (
    "pendiente",
    "marchando",
    "preparado",
)

ESTADOS_PEDIDO_FINALIZADOS = (
    "cerrado",
    "cancelado",
)

ESTADOS_PEDIDO_VALIDOS = ESTADOS_PEDIDO
ORIGENES_PEDIDO_VALIDOS = ORIGENES_PEDIDO
TIPOS_ENTREGA_VALIDOS = TIPOS_ENTREGA
ESTADOS_PAGO_VALIDOS = ESTADOS_PAGO
ESTADOS_KANBAN = (
    "pendiente",
    "marchando",
    "preparado",
    "cerrado",
)

COLUMNAS_PEDIDO_PANEL = (
    "id,numero_pedido,caja_id,created_at,updated_at,estado,estado_pago,"
    "pagado_at,cerrado_at,entregado_at,cancelado_at,motivo_cancelacion,"
    "origen,tipo_entrega,nombre_cliente,"
    "apellido_cliente,telefono_cliente,direccion_entrega,"
    "referencia_direccion,forma_pago,paga_con,subtotal,costo_envio,"
    "descuento,total,observaciones,detalle,enviado_whatsapp_at"
)


def pedido_visible_tablero(pedido):
    return not (
        str(pedido.get("estado_pago") or "").strip().lower() == "pagado"
        and pedido.get("entregado_at") is not None
    )

FORMAS_PAGO_VENTAS = (
    "efectivo",
    "transferencia",
    "qr",
    "debito",
    "credito",
)

ETIQUETAS_FORMA_PAGO_VENTAS = {
    "efectivo": "Efectivo",
    "transferencia": "Transferencia",
    "qr": "QR",
    "debito": "Débito",
    "credito": "Crédito",
}

ZONA_HORARIA_GASTRONOMIA = ZoneInfo("America/Argentina/Cordoba")
COLUMNAS_CAJA = (
    "id,comercio_id,numero,abierto_at,cerrado_at,total_vendido,"
    "cantidad_ventas,ticket_promedio,efectivo,transferencia,qr,debito,credito"
)


def _etiqueta_operacion_venta(pedido):
    origen = str(pedido.get("origen") or "").strip().lower()
    estado = str(pedido.get("estado") or "").strip().lower()
    if origen == "pos":
        return "Preparación" if estado in ESTADOS_PEDIDO_ACTIVOS else "Venta POS"
    return {
        "clicklocal": "Pedido online",
        "whatsapp": "Pedido por WhatsApp",
        "telefono": "Pedido telefónico",
        "qr_mesa": "Pedido de mesa",
    }.get(origen, "Venta")


def _resumir_ventas(operaciones):
    ventas = [pedido for pedido in operaciones if pedido_es_venta(pedido)]
    total_vendido = 0.0
    totales_forma_pago = {forma: 0.0 for forma in FORMAS_PAGO_VENTAS}

    for venta in ventas:
        try:
            total = float(venta.get("total") or 0)
        except (TypeError, ValueError):
            total = 0.0
        total_vendido += total
        forma_pago = str(venta.get("forma_pago") or "").strip().lower()
        if forma_pago in totales_forma_pago:
            totales_forma_pago[forma_pago] += total

    cantidad_ventas = len(ventas)
    return {
        "ventas": ventas,
        "total_vendido": round(total_vendido, 2),
        "cantidad_ventas": cantidad_ventas,
        "ticket_promedio": round(total_vendido / cantidad_ventas, 2)
        if cantidad_ventas else 0.0,
        "totales_forma_pago": {
            forma: round(total, 2)
            for forma, total in totales_forma_pago.items()
        },
    }


def _consultar_caja_abierta(comercio_id):
    respuesta = (
        supabase_admin
        .table("gastronomia_cajas")
        .select(COLUMNAS_CAJA)
        .eq("comercio_id", comercio_id)
        .is_("cerrado_at", "null")
        .limit(1)
        .execute()
    )
    return (respuesta.data or [None])[0]


def _consultar_ultima_caja(comercio_id):
    respuesta = (
        supabase_admin
        .table("gastronomia_cajas")
        .select(COLUMNAS_CAJA)
        .eq("comercio_id", comercio_id)
        .order("numero", desc=True)
        .limit(1)
        .execute()
    )
    return (respuesta.data or [None])[0]


def _consultar_ventas_caja(comercio_id, caja_id):
    respuesta = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select(COLUMNAS_PEDIDO_PANEL)
        .eq("comercio_id", comercio_id)
        .eq("caja_id", caja_id)
        .eq("estado_pago", "pagado")
        .order("created_at", desc=True)
        .execute()
    )
    return _preparar_pedidos_panel(respuesta.data or [])


def _preparar_caja(caja):
    caja = dict(caja)
    caja["abierto_at_mostrar"] = _fecha_hora_pedido_mostrar(
        caja.get("abierto_at")
    )
    caja["cerrado_at_mostrar"] = (
        _fecha_hora_pedido_mostrar(caja.get("cerrado_at"))
        if caja.get("cerrado_at") else ""
    )
    return caja


@gastronomia_bp.route("/panel/pos", methods=["GET"])
def pos_gastronomia():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido()

    productos = _cargar_catalogo_gastronomico(
        comercio.get("id")
    )
    categorias = _categorias_catalogo(productos)

    return render_template(
        "gastronomia/pos.html",
        comercio=comercio,
        productos=productos,
        categorias=categorias,
    )


@gastronomia_bp.route("/panel/pos/confirmar", methods=["POST"])
def confirmar_venta_pos_gastronomia():
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido(json_api=True)

    payload = request.get_json(silent=True) or {}
    detalle = payload.get("detalle")
    if not isinstance(detalle, list) or not detalle:
        return jsonify({"ok": False, "error": "El carrito está vacío."}), 400

    forma_pago = str(payload.get("forma_pago") or "").strip().lower()
    if forma_pago not in {"efectivo", "transferencia", "qr", "debito", "credito"}:
        return jsonify({"ok": False, "error": "Elegí una forma de pago válida."}), 400

    tipo_venta = str(payload.get("tipo_venta") or "").strip().lower()
    estados_por_tipo_venta = {
        "rapida": ("cerrado", "pagado"),
        "preparacion": ("pendiente", "pagado"),
    }
    if tipo_venta not in estados_por_tipo_venta:
        return jsonify({"ok": False, "error": "Elegí un tipo de venta válido."}), 400
    estado_inicial, estado_pago_inicial = estados_por_tipo_venta[tipo_venta]

    try:
        resultado = crear_pedido(
            comercio_id=comercio.get("id"),
            nombre="",
            apellido="",
            telefono="",
            telefono_normalizado="",
            modalidad="mostrador",
            direccion="",
            forma_pago=forma_pago,
            paga_con=None,
            observaciones="",
            items=detalle,
            origen="pos",
            aplicar_condiciones_comerciales=False,
            estado_inicial=estado_inicial,
            estado_pago_inicial=estado_pago_inicial,
        )
    except PedidoError as error:
        return jsonify({"ok": False, "error": error.mensaje}), error.status_code

    return jsonify({
        "ok": True,
        "pedido_id": resultado.get("id"),
        "numero_pedido": resultado.get("numero_pedido"),
        "total": resultado.get("total"),
    })


def _fecha_hora_pedido_mostrar(valor):
    try:
        fecha = datetime.fromisoformat(
            str(valor or "").replace("Z", "+00:00")
        )

        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)

        return (
            fecha
            .astimezone(ZoneInfo("America/Argentina/Cordoba"))
            .strftime("%d/%m/%Y %H:%M")
        )
    except (TypeError, ValueError):
        return "Fecha no disponible"


def _preparar_pedidos_panel(pedidos):
    for pedido in pedidos:
        pedido["created_at_mostrar"] = _fecha_hora_pedido_mostrar(
            pedido.get("created_at")
        )
        pedido["cancelado_at_mostrar"] = (
            _fecha_hora_pedido_mostrar(pedido.get("cancelado_at"))
            if pedido.get("cancelado_at")
            else ""
        )
        detalle = pedido.get("detalle")
        pedido["detalle_items"] = (
            [item for item in detalle if isinstance(item, dict)]
            if isinstance(detalle, list)
            else []
        )
    return pedidos


@gastronomia_bp.route("/panel/pedidos", methods=["GET"])
def pedidos_gastronomia():
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return redirect(url_for("login"))
    pos_activo = _pos_activo_gastronomia(comercio.get("id"))

    zona_local = ZoneInfo("America/Argentina/Cordoba")
    hoy_local = datetime.now(zona_local).date()
    desde = datetime.combine(
        hoy_local,
        datetime.min.time(),
        tzinfo=zona_local,
    ).astimezone(timezone.utc)
    hasta = desde + timedelta(days=1)

    consulta = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select(COLUMNAS_PEDIDO_PANEL)
        .eq("comercio_id", comercio.get("id"))
        .in_("estado", list(ESTADOS_KANBAN))
        .gte("created_at", desde.isoformat())
        .lt("created_at", hasta.isoformat())
    )
    if not pos_activo:
        consulta = consulta.eq("origen", "clicklocal")
    respuesta = consulta.order("created_at").execute()
    pedidos = _preparar_pedidos_panel([
        pedido
        for pedido in (respuesta.data or [])
        if pedido_visible_tablero(pedido)
    ])
    columnas = {
        estado: [p for p in pedidos if p.get("estado") == estado]
        for estado in ESTADOS_KANBAN
    }
    return render_template(
        "gastronomia/pedidos.html",
        comercio=comercio,
        columnas=columnas,
        estados_kanban=ESTADOS_KANBAN,
        fecha_tablero=hoy_local.strftime("%d/%m/%Y"),
        pos_activo=pos_activo,
    )


def _consultar_ventas_clicklocal(comercio_id):
    respuesta = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select(COLUMNAS_PEDIDO_PANEL)
        .eq("comercio_id", comercio_id)
        .eq("origen", "clicklocal")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return _preparar_pedidos_panel(respuesta.data or [])


@gastronomia_bp.route("/panel/ventas", methods=["GET"])
def ventas_gastronomia():
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")
    pos_activo = _pos_activo_gastronomia(comercio_id)
    caja_abierta = None
    ultima_caja = None
    if pos_activo:
        caja_abierta = _consultar_caja_abierta(comercio_id)
        if caja_abierta:
            caja_abierta = _preparar_caja(caja_abierta)
            operaciones = _consultar_ventas_caja(
                comercio_id,
                caja_abierta.get("id"),
            )
        else:
            ultima_caja = _consultar_ultima_caja(comercio_id)
            if ultima_caja:
                ultima_caja = _preparar_caja(ultima_caja)
            operaciones = []
    else:
        operaciones = _consultar_ventas_clicklocal(comercio_id)

    resumen = _resumir_ventas(operaciones)
    for venta in operaciones:
        venta["hora_mostrar"] = _fecha_hora_pedido_mostrar(
            venta.get("created_at")
        ).split(" ")[-1]
        venta["operacion_mostrar"] = _etiqueta_operacion_venta(venta)
        venta["forma_pago_mostrar"] = ETIQUETAS_FORMA_PAGO_VENTAS.get(
            str(venta.get("forma_pago") or "").strip().lower(),
            "Sin informar",
        )

    return render_template(
        "gastronomia/ventas.html",
        comercio=comercio,
        formas_pago=FORMAS_PAGO_VENTAS,
        etiquetas_forma_pago=ETIQUETAS_FORMA_PAGO_VENTAS,
        operaciones=operaciones,
        caja_abierta=caja_abierta,
        ultima_caja=ultima_caja,
        proximo_numero=(int(ultima_caja.get("numero") or 0) + 1 if ultima_caja else 1),
        pos_activo=pos_activo,
        **resumen,
    )


@gastronomia_bp.route("/panel/ventas/cerrar-caja", methods=["POST"])
def cerrar_caja_ventas_gastronomia():
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido(json_api=True)

    try:
        resultado = (
            supabase_admin
            .rpc(
                "cerrar_gastronomia_caja",
                {"p_comercio_id": comercio.get("id")},
            )
            .execute()
        )
    except Exception as error:
        mensaje = str(error)
        if "No hay una caja abierta" in mensaje or "ya fue cerrada" in mensaje:
            return jsonify({"ok": False, "error": "No hay una caja abierta."}), 409
        return jsonify({"ok": False, "error": "No se pudo cerrar la caja."}), 500

    if not resultado.data:
        return jsonify({
            "ok": False,
            "error": "No se pudo cerrar la caja.",
        }), 500

    return jsonify({
        "ok": True,
        "caja_id": resultado.data,
        "mensaje": "Caja cerrada correctamente.",
    })


@gastronomia_bp.route(
    "/panel/ventas/<pedido_id>/anular",
    methods=["POST"],
)
def anular_venta_gastronomia(pedido_id):
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido(json_api=True)

    datos = request.get_json(silent=True) or {}
    if datos.get("confirmado") is not True:
        return jsonify({
            "ok": False,
            "error": "La anulación requiere confirmación.",
        }), 400

    motivo = str(datos.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"ok": False, "error": "Ingresá el motivo de anulación."}), 400
    if len(motivo) > 200:
        return jsonify({
            "ok": False,
            "error": "El motivo puede tener hasta 200 caracteres.",
        }), 400

    comercio_id = comercio.get("id")
    existente = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select("id,estado,estado_pago")
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )
    if not (existente.data or []):
        return jsonify({"ok": False, "error": "Venta no encontrada."}), 404

    venta = existente.data[0]
    if venta.get("estado") == "cancelado":
        return jsonify({"ok": False, "error": "La venta ya está anulada."}), 400
    if venta.get("estado_pago") != "pagado":
        return jsonify({"ok": False, "error": "La operación no es una venta pagada."}), 400

    cancelado_at = datetime.now(timezone.utc).isoformat()
    actualizado = (
        supabase_admin
        .table("gastronomia_pedidos")
        .update({
            "estado": "cancelado",
            "cancelado_at": cancelado_at,
            "motivo_cancelacion": motivo,
        })
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .eq("estado", venta.get("estado"))
        .eq("estado_pago", "pagado")
        .execute()
    )
    if not (actualizado.data or []):
        return jsonify({
            "ok": False,
            "error": "La venta cambió; actualizá la pantalla.",
        }), 409

    return jsonify({
        "ok": True,
        "pedido_id": pedido_id,
        "estado": "cancelado",
        "cancelado_at": cancelado_at,
        "motivo_cancelacion": motivo,
    })


@gastronomia_bp.route(
    "/panel/pedidos/<pedido_id>/estado",
    methods=["POST"],
)
def actualizar_estado_pedido_gastronomia(pedido_id):
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido(json_api=True)

    datos = request.get_json(silent=True) or {}
    comercio_id = comercio.get("id")
    existente = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select("id,estado,estado_pago,entregado_at")
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )
    if not (existente.data or []):
        return jsonify({"ok": False, "error": "Pedido no encontrado."}), 404

    pedido = existente.data[0]
    if (
        datos.get("accion") == "cancelar"
        and datos.get("confirmado") is not True
    ):
        return jsonify({
            "ok": False,
            "error": "La cancelación requiere confirmación.",
        }), 400
    try:
        cambios = preparar_transicion_pedido(
            estado_actual=pedido.get("estado"),
            accion=datos.get("accion"),
        )
    except PedidoError as error:
        return jsonify({"ok": False, "error": error.mensaje}), 400
    if pedido.get("estado") == "cerrado" and cambios.get("estado") == "preparado":
        cambios["entregado_at"] = None

    actualizado = (
        supabase_admin
        .table("gastronomia_pedidos")
        .update(cambios)
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .eq("estado", pedido.get("estado"))
        .execute()
    )
    filas = actualizado.data or []
    if not filas:
        return jsonify({"ok": False, "error": "No se pudo actualizar el pedido."}), 409

    return jsonify({
        "ok": True,
        "pedido_id": pedido_id,
        "estado": cambios["estado"],
        "cerrado_at": cambios["cerrado_at"],
        "entregado_at": cambios.get("entregado_at", pedido.get("entregado_at")),
    })


@gastronomia_bp.route(
    "/panel/pedidos/<pedido_id>/entrega",
    methods=["POST"],
)
def marcar_entregado_pedido_gastronomia(pedido_id):
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido(json_api=True)

    comercio_id = comercio.get("id")
    existente = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select("id,estado,estado_pago,entregado_at")
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )
    if not (existente.data or []):
        return jsonify({"ok": False, "error": "Pedido no encontrado."}), 404

    pedido = existente.data[0]
    if pedido.get("estado") != "cerrado":
        return jsonify({
            "ok": False,
            "error": "Solo se puede entregar un pedido cerrado.",
        }), 400

    entregado_at = datetime.now(timezone.utc).isoformat()
    actualizado = (
        supabase_admin
        .table("gastronomia_pedidos")
        .update({"entregado_at": entregado_at})
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .eq("estado", "cerrado")
        .execute()
    )
    if not (actualizado.data or []):
        return jsonify({"ok": False, "error": "No se pudo entregar el pedido."}), 409

    pedido_actualizado = dict(pedido, entregado_at=entregado_at)
    return jsonify({
        "ok": True,
        "pedido_id": pedido_id,
        "entregado_at": entregado_at,
        "ocultar_tablero": not pedido_visible_tablero(pedido_actualizado),
    })


@gastronomia_bp.route(
    "/panel/pedidos/<pedido_id>/pago",
    methods=["POST"],
)
def actualizar_pago_pedido_gastronomia(pedido_id):
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido(json_api=True)

    datos = request.get_json(silent=True) or {}
    comercio_id = comercio.get("id")
    existente = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select("id,estado,estado_pago,entregado_at")
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )
    if not (existente.data or []):
        return jsonify({"ok": False, "error": "Pedido no encontrado."}), 404

    pedido = existente.data[0]
    if pedido.get("estado") == "cancelado":
        return jsonify({
            "ok": False,
            "error": "No se puede cambiar el pago de un pedido cancelado.",
        }), 400

    try:
        cambios = preparar_actualizacion_estados(
            estado_pago=datos.get("estado_pago")
        )
    except PedidoError as error:
        return jsonify({"ok": False, "error": error.mensaje}), 400

    actualizado = (
        supabase_admin
        .table("gastronomia_pedidos")
        .update(cambios)
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .eq("estado_pago", pedido.get("estado_pago") or "pendiente")
        .execute()
    )
    filas = actualizado.data or []
    if not filas:
        return jsonify({"ok": False, "error": "El pedido cambió; actualizá la bandeja."}), 409

    return jsonify({
        "ok": True,
        "pedido_id": pedido_id,
        "estado_pago": cambios["estado_pago"],
        "pagado_at": cambios["pagado_at"],
        "ocultar_tablero": not pedido_visible_tablero({
            **pedido,
            "estado_pago": cambios["estado_pago"],
        }),
    })


@gastronomia_bp.route("/panel/pedidos/historial", methods=["GET"])
def historial_pedidos_gastronomia():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")
    pos_activo = _pos_activo_gastronomia(comercio_id)

    vista = str(
        request.args.get("vista") or "activos"
    ).strip().lower()
    if vista not in ("activos", "finalizados", "todos"):
        vista = "activos"

    estado = str(
        request.args.get("estado") or ""
    ).strip().lower()
    if estado not in ESTADOS_PEDIDO_VALIDOS:
        estado = ""

    if pos_activo:
        origen = str(
            request.args.get("origen") or ""
        ).strip().lower()
        if origen not in ORIGENES_PEDIDO_VALIDOS:
            origen = ""
    else:
        origen = "clicklocal"

    tipo_entrega = str(
        request.args.get("tipo_entrega") or ""
    ).strip().lower()
    if tipo_entrega not in TIPOS_ENTREGA_VALIDOS:
        tipo_entrega = ""

    estado_pago = str(
        request.args.get("estado_pago") or ""
    ).strip().lower()
    if estado_pago not in ESTADOS_PAGO_VALIDOS:
        estado_pago = ""

    try:
        pagina = int(request.args.get("pagina") or 1)
    except (TypeError, ValueError):
        pagina = 1

    if pagina < 1:
        pagina = 1

    por_pagina = 25
    desde = (pagina - 1) * por_pagina
    hasta = desde + por_pagina - 1

    consulta = (
        supabase_admin
        .table("gastronomia_pedidos")
        .select(
            COLUMNAS_PEDIDO_PANEL,
            count="exact",
        )
        .eq("comercio_id", comercio_id)
    )

    if estado:
        consulta = consulta.eq("estado", estado)
    elif vista == "activos":
        consulta = consulta.in_(
            "estado",
            list(ESTADOS_PEDIDO_ACTIVOS),
        )
    elif vista == "finalizados":
        consulta = consulta.in_(
            "estado",
            list(ESTADOS_PEDIDO_FINALIZADOS),
        )

    if origen:
        consulta = consulta.eq("origen", origen)

    if tipo_entrega:
        consulta = consulta.eq(
            "tipo_entrega",
            tipo_entrega,
        )

    if estado_pago:
        consulta = consulta.eq("estado_pago", estado_pago)

    pedidos_res = (
        consulta
        .order("created_at", desc=True)
        .order("numero_pedido", desc=True)
        .range(desde, hasta)
        .execute()
    )

    pedidos = _preparar_pedidos_panel(pedidos_res.data or [])

    total_pedidos = (
        pedidos_res.count
        if isinstance(pedidos_res.count, int)
        else len(pedidos)
    )
    total_paginas = max(
        1,
        (total_pedidos + por_pagina - 1) // por_pagina,
    )

    return render_template(
        "gastronomia/pedidos_historial.html",
        comercio=comercio,
        pedidos=pedidos,
        filtros={
            "vista": vista,
            "estado": estado,
            "origen": origen,
            "tipo_entrega": tipo_entrega,
            "estado_pago": estado_pago,
        },
        estados=ESTADOS_PEDIDO_VALIDOS,
        origenes=ORIGENES_PEDIDO_VALIDOS,
        tipos_entrega=TIPOS_ENTREGA_VALIDOS,
        estados_pago=ESTADOS_PAGO_VALIDOS,
        pagina=pagina,
        total_paginas=total_paginas,
        total_pedidos=total_pedidos,
        pos_activo=pos_activo,
    )


@gastronomia_bp.route("/panel/pedidos/historial/cajas", methods=["GET"])
def historial_cajas_gastronomia():
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return redirect(url_for("login"))
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido()

    respuesta = (
        supabase_admin
        .table("gastronomia_cajas")
        .select(COLUMNAS_CAJA)
        .eq("comercio_id", comercio.get("id"))
        .order("numero", desc=True)
        .execute()
    )
    cajas = [
        _preparar_caja(caja)
        for caja in (respuesta.data or [])
        if caja.get("cerrado_at")
    ]
    return render_template(
        "gastronomia/cierres_historial.html",
        comercio=comercio,
        cajas=cajas,
        caja_seleccionada=None,
        operaciones=[],
    )


@gastronomia_bp.route(
    "/panel/pedidos/historial/cajas/<caja_id>",
    methods=["GET"],
)
def detalle_caja_gastronomia(caja_id):
    comercio = _comercio_panel_gastronomia()
    if not comercio:
        return redirect(url_for("login"))
    if not _pos_activo_gastronomia(comercio.get("id")):
        return _respuesta_pos_requerido()

    comercio_id = comercio.get("id")
    respuesta = (
        supabase_admin
        .table("gastronomia_cajas")
        .select(COLUMNAS_CAJA)
        .eq("id", caja_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )
    if not (respuesta.data or []):
        abort(404)

    caja = respuesta.data[0]
    if not caja.get("cerrado_at"):
        abort(404)
    caja = _preparar_caja(caja)

    operaciones = _consultar_ventas_caja(comercio_id, caja_id)
    for venta in operaciones:
        venta["hora_mostrar"] = _fecha_hora_pedido_mostrar(
            venta.get("created_at")
        ).split(" ")[-1]
        venta["operacion_mostrar"] = _etiqueta_operacion_venta(venta)
        venta["forma_pago_mostrar"] = ETIQUETAS_FORMA_PAGO_VENTAS.get(
            str(venta.get("forma_pago") or "").strip().lower(),
            "Sin informar",
        )

    return render_template(
        "gastronomia/cierres_historial.html",
        comercio=comercio,
        cajas=[],
        caja_seleccionada=caja,
        operaciones=operaciones,
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/toggle-activo",
    methods=["POST"]
)
def toggle_producto_activo(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select("id,activo")
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    productos = producto_res.data or []

    if not productos:
        abort(404)

    producto = productos[0]
    nuevo_estado = not bool(producto.get("activo"))

    # Al activar, respetar la capacidad comercial de Gastronomía.
    if nuevo_estado:
        activos_res = (
            supabase_admin
            .table("gastronomia_productos")
            .select("id")
            .eq("comercio_id", comercio_id)
            .eq("activo", True)
            .execute()
        )

        cantidad_activos = len(activos_res.data or [])

        if _limite_productos_gastronomia_alcanzado(cantidad_activos):
            return redirect(
                url_for(
                    "gastronomia.panel_gastronomia",
                    limite_productos="1"
                )
                + "#mis-productos"
            )

    (
        supabase_admin
        .table("gastronomia_productos")
        .update({"activo": nuevo_estado})
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    return redirect(
        url_for("gastronomia.panel_gastronomia")
        + "#mis-productos"
    )



@gastronomia_bp.route(
    "/panel/producto/<producto_id>/destacar",
    methods=["POST"]
)
def destacar_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,activo,destacado,destacado_hasta"
        )
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    productos = producto_res.data or []

    if not productos:
        abort(404)

    producto = productos[0]

    if not producto.get("activo"):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                destacado_error="inactivo"
            )
            + "#mis-productos"
        )

    # Si ya está destacado, esta acción renueva 30 días.
    if producto.get("destacado"):
        nueva_fecha = date.today() + timedelta(days=30)

        (
            supabase_admin
            .table("gastronomia_productos")
            .update({
                "destacado": True,
                "destacado_hasta": nueva_fecha.isoformat(),
            })
            .eq("id", producto_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )

        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                destacado_renovado="1"
            )
            + "#mis-productos"
        )

    destacados_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select("id,destacado_hasta")
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .eq("destacado", True)
        .execute()
    )

    destacados_vigentes = [
        item
        for item in (destacados_res.data or [])
        if _esta_vigente(
            hasta=item.get("destacado_hasta")
        )
    ]

    if len(destacados_vigentes) >= 3:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                destacado_error="maximo"
            )
            + "#mis-productos"
        )

    nueva_fecha = date.today() + timedelta(days=30)

    (
        supabase_admin
        .table("gastronomia_productos")
        .update({
            "destacado": True,
            "destacado_hasta": nueva_fecha.isoformat(),
        })
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            destacado_ok="1"
        )
        + "#mis-productos"
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/quitar-destacado",
    methods=["POST"]
)
def quitar_destacado_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    (
        supabase_admin
        .table("gastronomia_productos")
        .update({
            "destacado": False,
            "destacado_hasta": None,
        })
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            destacado_quitado="1"
        )
        + "#mis-productos"
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/editar",
    methods=["POST"]
)
def editar_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    nombre = str(
        request.form.get("nombre") or ""
    ).strip()

    descripcion = str(
        request.form.get("descripcion") or ""
    ).strip()

    categoria = _limpiar_categoria_producto(
        request.form.get("categoria")
    )

    precio_raw = str(
        request.form.get("precio") or ""
    ).strip()

    imagen_url = str(
        request.form.get("imagen_url") or ""
    ).strip()

    if not nombre:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                producto_error="nombre"
            )
            + "#producto"
        )

    try:
        precio = _parsear_precio_producto(precio_raw)
    except (TypeError, ValueError):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                producto_error="precio"
            )
            + "#producto"
        )

    existente_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select("id,imagen_url")
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    existentes = existente_res.data or []

    if not existentes:
        abort(404)

    imagen_actual = str(
        existentes[0].get("imagen_url") or ""
    ).strip()

    imagen_final = imagen_url or imagen_actual

    if not imagen_final:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                producto_error="foto"
            )
            + "#producto"
        )

    (
        supabase_admin
        .table("gastronomia_productos")
        .update({
            "nombre": nombre,
            "descripcion": descripcion,
            "categoria": categoria,
            "precio": precio,
            "imagen_url": imagen_final,
        })
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            producto_editado="1"
        )
        + "#mis-productos"
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/promocion",
    methods=["POST"]
)
def guardar_promocion_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select("id,precio,activo")
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    productos = producto_res.data or []

    if not productos:
        abort(404)

    producto = productos[0]

    if not producto.get("activo"):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                promo_error="inactivo"
            )
            + "#mis-productos"
        )

    precio_raw = str(
        request.form.get("precio_promocional") or ""
    ).strip()

    try:
        precio_promocional = _parsear_precio_producto(precio_raw)
    except (TypeError, ValueError):
        precio_promocional = 0

    try:
        precio_normal = float(
            producto.get("precio") or 0
        )
    except (TypeError, ValueError):
        precio_normal = 0

    if (
        precio_promocional <= 0
        or precio_promocional >= precio_normal
    ):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                promo_error="precio"
            )
            + "#mis-productos"
        )

    desde_raw = str(
        request.form.get("promocion_desde") or ""
    ).strip()

    hasta_raw = str(
        request.form.get("promocion_hasta") or ""
    ).strip()

    try:
        promocion_desde = (
            date.fromisoformat(desde_raw)
            if desde_raw
            else date.today()
        )

        promocion_hasta = (
            date.fromisoformat(hasta_raw)
            if hasta_raw
            else promocion_desde + timedelta(days=30)
        )
    except ValueError:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                promo_error="fecha"
            )
            + "#mis-productos"
        )

    if promocion_hasta < promocion_desde:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                promo_error="fecha"
            )
            + "#mis-productos"
        )

    (
        supabase_admin
        .table("gastronomia_productos")
        .update({
            "precio_promocional": precio_promocional,
            "promocion_desde": promocion_desde.isoformat(),
            "promocion_hasta": promocion_hasta.isoformat(),
        })
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            promo_ok="1"
        )
        + "#mis-productos"
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/quitar-promocion",
    methods=["POST"]
)
def quitar_promocion_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    (
        supabase_admin
        .table("gastronomia_productos")
        .update({
            "precio_promocional": None,
            "promocion_desde": None,
            "promocion_hasta": None,
        })
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            promo_quitada="1"
        )
        + "#mis-productos"
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/eliminar",
    methods=["POST"]
)
def eliminar_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    existente_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select("id,imagen_url")
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    existentes = existente_res.data or []

    if not existentes:
        abort(404)

    imagen_url = str(
        existentes[0].get("imagen_url") or ""
    ).strip()

    (
        supabase_admin
        .table("gastronomia_productos")
        .delete()
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .execute()
    )

    # Limpiar la imagen del bucket si pertenece al bucket publicaciones.
    try:
        marcador = (
            "/storage/v1/object/public/publicaciones/"
        )

        if marcador in imagen_url:
            ruta_storage = (
                imagen_url
                .split(marcador, 1)[1]
                .split("?", 1)[0]
                .strip("/")
            )

            if ruta_storage and ".." not in ruta_storage:
                supabase_admin.storage.from_(
                    "publicaciones"
                ).remove([ruta_storage])

    except Exception as error:
        print(
            "AVISO LIMPIANDO FOTO PRODUCTO:",
            type(error),
            error,
            flush=True
        )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            producto_eliminado="1"
        )
        + "#mis-productos"
    )



@gastronomia_bp.route(
    "/configuracion-inicial",
    methods=["GET", "POST"],
)
def configuracion_inicial():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    categoria = str(
        comercio.get("categoria") or ""
    ).strip().lower()

    if categoria not in {
        "gastronomía",
        "gastronomia",
    }:
        return redirect(url_for("panel"))

    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    configuracion_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,"
            "acepta_retiro,pedido_minimo,costo_envio,"
            "tiempo_estimado_min"
        )
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    configuraciones = configuracion_res.data or []

    # Si ya está configurado, no vuelve a mostrar el alta.
    if configuraciones:
        return redirect(
            url_for("gastronomia.panel_gastronomia")
        )

    error = ""

    if request.method == "POST":
        acepta_delivery = (
            request.form.get("acepta_delivery") == "on"
        )

        acepta_retiro = (
            request.form.get("acepta_retiro") == "on"
        )

        pedido_minimo_raw = str(
            request.form.get("pedido_minimo") or ""
        ).strip()

        costo_envio_raw = str(
            request.form.get("costo_envio") or ""
        ).strip()

        tiempo_estimado_raw = str(
            request.form.get("tiempo_estimado_min") or ""
        ).strip()

        if not acepta_delivery and not acepta_retiro:
            error = (
                "Elegí al menos una modalidad: "
                "Delivery o Retiro en el local."
            )

        pedido_minimo = 0.0
        costo_envio = 0.0
        tiempo_estimado_min = None

        if not error:
            try:
                pedido_minimo = _parsear_importe_config(
                    pedido_minimo_raw
                )
            except (TypeError, ValueError):
                error = "El pedido mínimo no es válido."

        if not error and acepta_delivery:
            try:
                costo_envio = _parsear_importe_config(
                    costo_envio_raw
                )
            except (TypeError, ValueError):
                error = "El costo de envío no es válido."

        if not error:
            try:
                tiempo_estimado_min = int(
                    tiempo_estimado_raw
                )

                if tiempo_estimado_min <= 0:
                    raise ValueError

            except (TypeError, ValueError):
                error = (
                    "Ingresá un tiempo estimado válido "
                    "en minutos."
                )

        if not error:
            datos_configuracion = {
                "comercio_id": comercio_id,
                "activo": True,
                "acepta_delivery": acepta_delivery,
                "acepta_retiro": acepta_retiro,
                "pedido_minimo": pedido_minimo,
                "costo_envio": (
                    costo_envio
                    if acepta_delivery
                    else 0.0
                ),
                "tiempo_estimado_min": tiempo_estimado_min,
            }

            try:
                (
                    supabase_admin
                    .table("gastronomia_configuracion")
                    .insert(datos_configuracion)
                    .execute()
                )

                return redirect(
                    url_for(
                        "gastronomia.panel_gastronomia"
                    )
                )

            except Exception as exc:
                print(
                    "ERROR CONFIGURACION INICIAL GASTRONOMIA:",
                    type(exc),
                    exc,
                    flush=True,
                )

                error = (
                    "No se pudo guardar la configuración. "
                    "Intentá nuevamente."
                )

    return render_template(
        "gastronomia/configuracion_inicial.html",
        comercio=comercio,
        error=error,
    )



@gastronomia_bp.route(
    "/panel/datos-comercio",
    methods=["POST"],
)
def guardar_datos_comercio():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    nombre_negocio = str(
        request.form.get("nombre_negocio") or ""
    ).strip()
    descripcion = str(request.form.get("descripcion") or "").strip()
    direccion = str(request.form.get("direccion") or "").strip()
    ciudad = str(request.form.get("ciudad") or "").strip()
    whatsapp = str(request.form.get("whatsapp") or "").strip()
    whatsapp_limpio = limpiar_numero_whatsapp(whatsapp)

    if (
        not nombre_negocio
        or not direccion
        or not ciudad
        or not whatsapp_limpio
    ):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                datos_comercio_error="campos",
            )
            + "#datos-comercio"
        )

    datos = {
        "nombre_negocio": nombre_negocio,
        "descripcion": descripcion,
        "direccion": direccion,
        "direccion_mostrar": direccion,
        "ciudad": ciudad,
        "whatsapp": whatsapp_limpio,
    }

    try:
        (
            supabase_admin
            .table("comercios")
            .update(datos)
            .eq("id", comercio_id)
            .execute()
        )
    except Exception as error:
        print(
            "ERROR ACTUALIZANDO DATOS DEL COMERCIO GASTRONOMICO:",
            type(error),
            error,
            flush=True,
        )
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                datos_comercio_error="guardar",
            )
            + "#datos-comercio"
        )

    comercio_sesion = session.get("comercio")
    if isinstance(comercio_sesion, dict):
        comercio_sesion.update(datos)
        session["comercio"] = comercio_sesion
        session.modified = True

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            datos_comercio_ok="1",
        )
        + "#datos-comercio"
    )


@gastronomia_bp.route(
    "/panel/delivery/ubicacion",
    methods=["POST"],
)
def guardar_ubicacion_delivery():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")
    direccion = str(
        request.form.get("delivery_origen_direccion") or ""
    ).strip()

    try:
        latitud, longitud = geocodificar_direccion_nominatim(
            direccion,
            comercio.get("ciudad"),
        )
    except DeliveryError:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                ubicacion_delivery_error="1",
            )
            + "#delivery-distancia"
        )

    try:
        (
            supabase_admin
            .table("gastronomia_configuracion")
            .update({
                "delivery_origen_direccion": direccion,
                "delivery_origen_latitud": latitud,
                "delivery_origen_longitud": longitud,
            })
            .eq("comercio_id", comercio_id)
            .execute()
        )
    except Exception as error:
        print(
            "ERROR GUARDANDO UBICACION DE DELIVERY:",
            type(error),
            error,
            flush=True,
        )
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="guardar",
            )
            + "#delivery-distancia"
        )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            ubicacion_delivery_ok="1",
        )
        + "#delivery-distancia"
    )


@gastronomia_bp.route(
    "/panel/configuracion",
    methods=["POST"],
)
def guardar_configuracion_negocio():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    acepta_delivery = (
        request.form.get("acepta_delivery") == "on"
    )

    acepta_retiro = (
        request.form.get("acepta_retiro") == "on"
    )

    if not acepta_delivery and not acepta_retiro:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="modalidad",
            )
            + "#configuracion-negocio"
        )

    pedido_minimo_raw = str(
        request.form.get("pedido_minimo") or ""
    ).strip()

    costo_envio_raw = str(
        request.form.get("costo_envio") or ""
    ).strip()

    tiempo_raw = str(
        request.form.get("tiempo_estimado_min") or ""
    ).strip()

    descuento_efectivo_raw = str(
        request.form.get("descuento_efectivo_pct") or "0"
    ).strip()

    descuento_transferencia_raw = str(
        request.form.get("descuento_transferencia_pct") or "0"
    ).strip()

    delivery_distancia_activo = (
        request.form.get("delivery_distancia_activo") == "on"
    )
    if delivery_distancia_activo and not acepta_delivery:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="delivery_distancia",
            )
            + "#configuracion-negocio"
        )
    configuracion_actual_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "delivery_origen_direccion,delivery_origen_latitud,"
            "delivery_origen_longitud"
        )
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )
    configuraciones_actuales = configuracion_actual_res.data or []
    configuracion_actual = (
        configuraciones_actuales[0]
        if configuraciones_actuales
        else {}
    )
    delivery_origen_direccion = str(
        configuracion_actual.get("delivery_origen_direccion") or ""
    ).strip()
    if delivery_distancia_activo:
        try:
            validar_coordenadas(
                configuracion_actual.get("delivery_origen_latitud"),
                configuracion_actual.get("delivery_origen_longitud"),
            )
        except DeliveryError:
            return redirect(
                url_for(
                    "gastronomia.panel_gastronomia",
                    configuracion_error="delivery_ubicacion",
                )
                + "#delivery-distancia"
            )
    try:
        delivery_franjas = validar_configuracion_delivery(
            delivery_distancia_activo,
            delivery_origen_direccion,
            request.form.getlist("delivery_hasta_km"),
            request.form.getlist("delivery_precio"),
        )
    except ValueError:
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="delivery_distancia",
            )
            + "#configuracion-negocio"
        )

    try:
        pedido_minimo = _parsear_importe_config(
            pedido_minimo_raw
        )
    except (TypeError, ValueError):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="pedido_minimo",
            )
            + "#configuracion-negocio"
        )

    costo_envio = 0.0

    if acepta_delivery:
        try:
            costo_envio = _parsear_importe_config(
                costo_envio_raw
            )
        except (TypeError, ValueError):
            return redirect(
                url_for(
                    "gastronomia.panel_gastronomia",
                    configuracion_error="costo_envio",
                )
                + "#configuracion-negocio"
            )

    try:
        tiempo_estimado_min = int(tiempo_raw)

        if tiempo_estimado_min <= 0:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="tiempo",
            )
            + "#configuracion-negocio"
        )

    try:
        descuento_efectivo_pct = float(
            descuento_efectivo_raw.replace(",", ".")
        )
        descuento_transferencia_pct = float(
            descuento_transferencia_raw.replace(",", ".")
        )

        if not 0 <= descuento_efectivo_pct <= 100:
            raise ValueError

        if not 0 <= descuento_transferencia_pct <= 100:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="descuentos",
            )
            + "#configuracion-negocio"
        )

    datos = {
        "acepta_delivery": acepta_delivery,
        "acepta_retiro": acepta_retiro,
        "pedido_minimo": pedido_minimo,
        "costo_envio": (
            costo_envio
            if acepta_delivery
            else 0.0
        ),
        "tiempo_estimado_min": tiempo_estimado_min,
        "descuento_efectivo_pct": descuento_efectivo_pct,
        "descuento_transferencia_pct": descuento_transferencia_pct,
        "delivery_distancia_activo": delivery_distancia_activo,
        "delivery_franjas": delivery_franjas,
    }

    try:
        (
            supabase_admin
            .table("gastronomia_configuracion")
            .update(datos)
            .eq("comercio_id", comercio_id)
            .execute()
        )

    except Exception as error:
        print(
            "ERROR ACTUALIZANDO CONFIGURACION GASTRONOMIA:",
            type(error),
            error,
            flush=True,
        )

        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                configuracion_error="guardar",
            )
            + "#configuracion-negocio"
        )

    return redirect(
        url_for(
            "gastronomia.panel_gastronomia",
            configuracion_ok="1",
        )
        + "#configuracion-negocio"
    )


@gastronomia_bp.route("/panel/productos", methods=["GET"])
def productos_gastronomia():
    return panel_gastronomia("productos")


@gastronomia_bp.route("/panel/configuracion", methods=["GET"])
def configuracion_gastronomia():
    return panel_gastronomia("configuracion")


@gastronomia_bp.route("/panel/plan", methods=["GET"])
def plan_gastronomia():
    return panel_gastronomia("plan")


@gastronomia_bp.route("/panel", methods=["GET", "POST"])
def panel_gastronomia(panel_seccion="productos"):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    if request.method == "POST" or any(
        request.args.get(clave)
        for clave in (
            "producto_ok", "producto_editado", "producto_eliminado",
            "limite_productos", "destacado_error",
        )
    ):
        panel_seccion = "productos"
    elif any(
        request.args.get(clave)
        for clave in (
            "datos_comercio_ok", "datos_comercio_error",
            "configuracion_ok", "configuracion_error",
            "ubicacion_delivery_ok", "ubicacion_delivery_error",
        )
    ):
        panel_seccion = "configuracion"

    configuracion_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,"
            "acepta_retiro,pedido_minimo,costo_envio,"
            "tiempo_estimado_min,descuento_efectivo_pct,"
            "descuento_transferencia_pct,delivery_distancia_activo,"
            "delivery_franjas,delivery_origen_direccion,"
            "delivery_origen_latitud,delivery_origen_longitud"
        )
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    configuraciones = configuracion_res.data or []

    if not configuraciones:
        return redirect(
            url_for(
                "gastronomia.configuracion_inicial"
            )
        )

    configuracion = configuraciones[0]

    for campo in (
        "pedido_minimo",
        "costo_envio",
        "descuento_efectivo_pct",
        "descuento_transferencia_pct",
    ):
        configuracion[campo + "_input"] = _precio_producto_input(
            configuracion.get(campo)
        )

    configuracion["pedido_minimo_mostrar"] = (
        _formatear_precio(
            configuracion.get("pedido_minimo")
        )
        if configuracion.get("pedido_minimo") is not None
        else ""
    )

    configuracion["costo_envio_mostrar"] = (
        _formatear_precio(
            configuracion.get("costo_envio")
        )
    )

    configuracion["delivery_franjas_input"] = (
        configuracion.get("delivery_franjas")
        if isinstance(configuracion.get("delivery_franjas"), list)
        else []
    )

    if panel_seccion == "plan":
        return render_template(
            "gastronomia/plan.html",
            comercio=comercio,
            panel_seccion="plan",
            pos_activo=_pos_activo_gastronomia(comercio_id),
            modulo_pos=obtener_modulo("pos"),
        )

    if panel_seccion == "configuracion":
        return render_template(
            "gastronomia/configuracion.html",
            comercio=comercio,
            configuracion=configuracion,
            panel_seccion="configuracion",
        )

    productos_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,descripcion,categoria,precio,"
            "precio_promocional,imagen_url,disponible,"
            "activo,destacado,destacado_hasta,"
            "promocion_desde,promocion_hasta,orden"
        )
        .eq("comercio_id", comercio_id)
        .order("orden", desc=True)
        .execute()
    )

    productos = productos_res.data or []
    categorias = _categorias_catalogo(productos)

    cantidad_productos_activos = sum(
        1
        for producto in productos
        if bool(producto.get("activo"))
    )

    for producto in productos:
        producto["precio_mostrar"] = _formatear_precio(
            producto.get("precio")
        )
        producto["precio_input"] = _precio_producto_input(
            producto.get("precio")
        )

        precio_promocional = producto.get(
            "precio_promocional"
        )

        producto["precio_promocional_mostrar"] = (
            _formatear_precio(precio_promocional)
            if precio_promocional is not None
            else ""
        )
        producto["precio_promocional_input"] = _precio_producto_input(
            precio_promocional
        )

    error_producto = ""

    if request.args.get("limite_productos") == "1":
        error_producto = (
            "Gastronomía permite hasta 30 productos activos. "
            "Pausá otro producto para activar uno nuevo."
        )

    if request.method == "POST":
        nombre = str(
            request.form.get("nombre") or ""
        ).strip()

        descripcion = str(
            request.form.get("descripcion") or ""
        ).strip()

        categoria = _limpiar_categoria_producto(
            request.form.get("categoria")
        )

        precio_raw = str(
            request.form.get("precio") or ""
        ).strip()

        imagen_url = str(
            request.form.get("imagen_url") or ""
        ).strip()

        if not nombre:
            error_producto = "Ingresá el nombre del producto."

        elif not precio_raw:
            error_producto = "Ingresá el precio del producto."

        elif not imagen_url:
            error_producto = "Seleccioná una foto del producto."

        else:
            try:
                precio = _parsear_precio_producto(precio_raw)
            except (TypeError, ValueError):
                error_producto = (
                    "El precio ingresado no es válido."
                )

        if (
            not error_producto
            and _limite_productos_gastronomia_alcanzado(
                cantidad_productos_activos
            )
        ):
            error_producto = (
                "Gastronomía permite hasta 30 productos activos. "
                "Pausá otro producto para activar uno nuevo."
            )

        if not error_producto:
            try:
                orden_actual = [
                    int(producto.get("orden") or 0)
                    for producto in productos
                ]

                siguiente_orden = (
                    max(orden_actual) + 1
                    if orden_actual
                    else 1
                )

                datos_producto = {
                    "comercio_id": comercio_id,
                    "nombre": nombre,
                    "descripcion": descripcion,
                    "categoria": categoria,
                    "precio": precio,
                    "precio_promocional": None,
                    "imagen_url": imagen_url,
                    "disponible": True,
                    "activo": True,
                    "destacado": False,
                    "orden": siguiente_orden,
                }

                (
                    supabase_admin
                    .table("gastronomia_productos")
                    .insert(datos_producto)
                    .execute()
                )

                return redirect(
                    url_for(
                        "gastronomia.panel_gastronomia",
                        producto_ok="1"
                    )
                    + "#mis-productos"
                )

            except Exception as error:
                print(
                    "ERROR CREANDO PRODUCTO GASTRONOMICO:",
                    type(error),
                    error,
                    flush=True
                )

                error_producto = (
                    "No se pudo guardar el producto. "
                    "No se modificó el menú."
                )

    periodo_metricas = str(
        request.args.get("periodo_metricas") or "30"
    ).strip().lower()

    if periodo_metricas not in {
        "hoy",
        "30",
        "60",
        "90",
    }:
        periodo_metricas = "30"

    metricas_gastronomia = {
        "pedidos": 0,
        "ventas": 0.0,
        "ticket_promedio": 0.0,
    }

    metricas_por_producto = {}

    pos_activo = _pos_activo_gastronomia(comercio_id)

    try:
            ahora_utc = datetime.now(timezone.utc)

            zona_local = ZoneInfo(
                "America/Argentina/Cordoba"
            )

            hoy_local = datetime.now(
                zona_local
            ).date()

            if periodo_metricas == "hoy":
                desde_local = datetime.combine(
                    hoy_local,
                    datetime.min.time(),
                    tzinfo=zona_local,
                )

                desde_consulta = (
                    desde_local
                    .astimezone(timezone.utc)
                    .isoformat()
                )

            else:
                dias = int(periodo_metricas)

                desde_consulta = (
                    ahora_utc
                    - timedelta(days=dias)
                ).isoformat()

            consulta_metricas = (
                supabase_admin
                .table("gastronomia_pedidos")
                .select(
                    "id,created_at,total,detalle,"
                    "estado,estado_pago,origen,"
                    "visitante_id,sesion_id,"
                    "telefono_normalizado,"
                    "nombre_cliente,apellido_cliente,"
                    "direccion_entrega"
                )
                .eq("comercio_id", comercio_id)
                .eq("estado_pago", "pagado")
                .neq("estado", "cancelado")
                .gte(
                    "created_at",
                    desde_consulta
                )
            )

            if not pos_activo:
                consulta_metricas = consulta_metricas.eq(
                    "origen",
                    "clicklocal",
                )

            pedidos_metricas_res = consulta_metricas.execute()

            pedidos_metricas = (
                pedidos_metricas_res.data or []
            )

            # Si el período es HOY, aseguramos día local argentino.
            if periodo_metricas == "hoy":
                pedidos_filtrados = []

                for pedido in pedidos_metricas:
                    created_at = str(
                        pedido.get("created_at") or ""
                    )

                    try:
                        fecha_pedido = (
                            datetime.fromisoformat(
                                created_at.replace(
                                    "Z",
                                    "+00:00"
                                )
                            )
                        )

                        if fecha_pedido.tzinfo is None:
                            fecha_pedido = (
                                fecha_pedido.replace(
                                    tzinfo=timezone.utc
                                )
                            )

                        if (
                            fecha_pedido
                            .astimezone(zona_local)
                            .date()
                            == hoy_local
                        ):
                            pedidos_filtrados.append(
                                pedido
                            )

                    except (
                        TypeError,
                        ValueError,
                    ):
                        pass

                pedidos_metricas = pedidos_filtrados

            pedidos_metricas = [
                pedido
                for pedido in pedidos_metricas
                if pedido_es_venta(pedido)
            ]

            total_ventas = 0.0

            for pedido in pedidos_metricas:
                try:
                    total_ventas += float(
                        pedido.get("total") or 0
                    )
                except (TypeError, ValueError):
                    pass

                productos_vistos_en_pedido = set()

                detalle = pedido.get("detalle") or []

                if not isinstance(detalle, list):
                    continue

                for item in detalle:
                    if not isinstance(item, dict):
                        continue

                    producto_id = str(
                        item.get("id") or ""
                    ).strip()

                    if not producto_id:
                        continue

                    metrica = (
                        metricas_por_producto
                        .setdefault(
                            producto_id,
                            {
                                "unidades": 0,
                                "pedidos": 0,
                                "ventas": 0.0,
                            },
                        )
                    )

                    try:
                        cantidad = int(
                            item.get("cantidad") or 0
                        )
                    except (TypeError, ValueError):
                        cantidad = 0

                    try:
                        subtotal_item = float(
                            item.get("subtotal") or 0
                        )
                    except (TypeError, ValueError):
                        subtotal_item = 0.0

                    metrica["unidades"] += cantidad
                    metrica["ventas"] += subtotal_item

                    if producto_id not in productos_vistos_en_pedido:
                        metrica["pedidos"] += 1
                        productos_vistos_en_pedido.add(
                            producto_id
                        )

            cantidad_pedidos = len(
                pedidos_metricas
            )

            metricas_gastronomia = {
                "pedidos": cantidad_pedidos,
                "ventas": round(
                    total_ventas,
                    2
                ),
                "ticket_promedio": round(
                    (
                        total_ventas / cantidad_pedidos
                        if cantidad_pedidos
                        else 0
                    ),
                    2
                ),
            }

            for metrica in metricas_por_producto.values():
                metrica["ventas"] = round(
                    metrica["ventas"],
                    2
                )

    except Exception as error:
        print(
            "ERROR METRICAS GASTRONOMIA:",
            type(error),
            error,
            flush=True,
        )

    return render_template(
        "gastronomia/productos.html",
        comercio=comercio,
        configuracion=configuracion,
        productos=productos,
        categorias=categorias,
        error_producto=error_producto,
        es_cine_teatro=False,
        cantidad_productos_activos=cantidad_productos_activos,
        metricas_gastronomia=metricas_gastronomia,
        metricas_por_producto=metricas_por_producto,
        periodo_metricas=periodo_metricas,
        panel_seccion="productos",
    )



# ==============================================================
# CLICKLOCAL GASTRONOMIA - EXTRAS / OPCIONES V1
# ==============================================================

def _producto_gastronomia_del_comercio(
    producto_id,
    comercio_id
):
    res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,descripcion,precio,imagen_url"
        )
        .eq("id", producto_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    productos = res.data or []

    return productos[0] if productos else None


def _grupo_extra_del_producto(
    grupo_id,
    producto_id
):
    res = (
        supabase_admin
        .table("gastronomia_grupos_opciones")
        .select(
            "id,producto_id,nombre,minimo,maximo,"
            "orden,activo"
        )
        .eq("id", grupo_id)
        .eq("producto_id", producto_id)
        .limit(1)
        .execute()
    )

    grupos = res.data or []

    return grupos[0] if grupos else None


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/extras",
    methods=["GET"]
)
def extras_producto(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto = _producto_gastronomia_del_comercio(
        producto_id,
        comercio_id
    )

    if not producto:
        abort(404)

    grupos_res = (
        supabase_admin
        .table("gastronomia_grupos_opciones")
        .select(
            "id,producto_id,nombre,minimo,maximo,"
            "orden,activo"
        )
        .eq("producto_id", producto_id)
        .eq("activo", True)
        .order("orden")
        .execute()
    )

    grupos = grupos_res.data or []

    grupo_ids = [
        grupo.get("id")
        for grupo in grupos
        if grupo.get("id")
    ]

    opciones = []

    if grupo_ids:
        opciones_res = (
            supabase_admin
            .table("gastronomia_opciones")
            .select(
                "id,grupo_id,nombre,precio_extra,"
                "disponible,activo,orden"
            )
            .in_("grupo_id", grupo_ids)
            .eq("activo", True)
            .order("orden")
            .execute()
        )

        opciones = opciones_res.data or []

    opciones_por_grupo = {}

    for opcion in opciones:
        opcion["precio_extra_mostrar"] = (
            _formatear_precio(
                opcion.get("precio_extra")
            )
        )

        opciones_por_grupo.setdefault(
            opcion.get("grupo_id"),
            []
        ).append(opcion)

    for grupo in grupos:
        grupo["opciones"] = (
            opciones_por_grupo.get(
                grupo.get("id"),
                []
            )
        )

    producto["precio_mostrar"] = (
        _formatear_precio(
            producto.get("precio")
        )
    )

    return render_template(
        "gastronomia/extras.html",
        comercio=comercio,
        producto=producto,
        grupos=grupos,
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/extras/grupo",
    methods=["POST"]
)
def crear_grupo_extra(producto_id):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto = _producto_gastronomia_del_comercio(
        producto_id,
        comercio_id
    )

    if not producto:
        abort(404)

    nombre = str(
        request.form.get("nombre") or ""
    ).strip()

    minimo_raw = str(
        request.form.get("minimo") or "0"
    ).strip()

    maximo_raw = str(
        request.form.get("maximo") or "0"
    ).strip()

    if not nombre:
        return redirect(
            url_for(
                "gastronomia.extras_producto",
                producto_id=producto_id,
                error="grupo_nombre"
            )
        )

    try:
        minimo = int(minimo_raw or 0)
        maximo = int(maximo_raw or 0)

        if minimo < 0 or maximo < 0:
            raise ValueError

        if maximo and minimo > maximo:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "gastronomia.extras_producto",
                producto_id=producto_id,
                error="limites"
            )
        )

    existentes = (
        supabase_admin
        .table("gastronomia_grupos_opciones")
        .select("orden")
        .eq("producto_id", producto_id)
        .execute()
    ).data or []

    ordenes = [
        int(item.get("orden") or 0)
        for item in existentes
    ]

    siguiente_orden = (
        max(ordenes) + 1
        if ordenes
        else 1
    )

    (
        supabase_admin
        .table("gastronomia_grupos_opciones")
        .insert({
            "producto_id": producto_id,
            "nombre": nombre,
            "minimo": minimo,
            "maximo": maximo,
            "orden": siguiente_orden,
            "activo": True,
        })
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.extras_producto",
            producto_id=producto_id,
            grupo_ok="1"
        )
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/extras/"
    "grupo/<grupo_id>/opcion",
    methods=["POST"]
)
def crear_opcion_extra(
    producto_id,
    grupo_id
):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto = _producto_gastronomia_del_comercio(
        producto_id,
        comercio_id
    )

    if not producto:
        abort(404)

    grupo = _grupo_extra_del_producto(
        grupo_id,
        producto_id
    )

    if not grupo:
        abort(404)

    nombre = str(
        request.form.get("nombre") or ""
    ).strip()

    precio_raw = str(
        request.form.get("precio_extra") or "0"
    ).strip()

    if not nombre:
        return redirect(
            url_for(
                "gastronomia.extras_producto",
                producto_id=producto_id,
                error="opcion_nombre"
            )
        )

    try:
        precio_extra = float(
            precio_raw
            .replace("$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
            or 0
        )

        if precio_extra < 0:
            raise ValueError

    except (TypeError, ValueError):
        return redirect(
            url_for(
                "gastronomia.extras_producto",
                producto_id=producto_id,
                error="precio_extra"
            )
        )

    existentes = (
        supabase_admin
        .table("gastronomia_opciones")
        .select("orden")
        .eq("grupo_id", grupo_id)
        .execute()
    ).data or []

    ordenes = [
        int(item.get("orden") or 0)
        for item in existentes
    ]

    siguiente_orden = (
        max(ordenes) + 1
        if ordenes
        else 1
    )

    (
        supabase_admin
        .table("gastronomia_opciones")
        .insert({
            "grupo_id": grupo_id,
            "nombre": nombre,
            "precio_extra": precio_extra,
            "disponible": True,
            "activo": True,
            "orden": siguiente_orden,
        })
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.extras_producto",
            producto_id=producto_id,
            opcion_ok="1"
        )
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/extras/"
    "grupo/<grupo_id>/eliminar",
    methods=["POST"]
)
def eliminar_grupo_extra(
    producto_id,
    grupo_id
):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto = _producto_gastronomia_del_comercio(
        producto_id,
        comercio_id
    )

    if not producto:
        abort(404)

    grupo = _grupo_extra_del_producto(
        grupo_id,
        producto_id
    )

    if not grupo:
        abort(404)

    opciones = (
        supabase_admin
        .table("gastronomia_opciones")
        .select("id")
        .eq("grupo_id", grupo_id)
        .execute()
    ).data or []

    opcion_ids = [
        opcion.get("id")
        for opcion in opciones
        if opcion.get("id")
    ]

    if opcion_ids:
        (
            supabase_admin
            .table("gastronomia_opciones")
            .delete()
            .in_("id", opcion_ids)
            .execute()
        )

    (
        supabase_admin
        .table("gastronomia_grupos_opciones")
        .delete()
        .eq("id", grupo_id)
        .eq("producto_id", producto_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.extras_producto",
            producto_id=producto_id,
            grupo_eliminado="1"
        )
    )


@gastronomia_bp.route(
    "/panel/producto/<producto_id>/extras/"
    "grupo/<grupo_id>/opcion/<opcion_id>/eliminar",
    methods=["POST"]
)
def eliminar_opcion_extra(
    producto_id,
    grupo_id,
    opcion_id
):
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    producto = _producto_gastronomia_del_comercio(
        producto_id,
        comercio_id
    )

    if not producto:
        abort(404)

    grupo = _grupo_extra_del_producto(
        grupo_id,
        producto_id
    )

    if not grupo:
        abort(404)

    (
        supabase_admin
        .table("gastronomia_opciones")
        .delete()
        .eq("id", opcion_id)
        .eq("grupo_id", grupo_id)
        .execute()
    )

    return redirect(
        url_for(
            "gastronomia.extras_producto",
            producto_id=producto_id,
            opcion_eliminada="1"
        )
    )


# ==============================================================
# CLICKLOCAL GASTRONOMIA - REGISTRO DE PEDIDOS V1
# ==============================================================

def _normalizar_telefono_pedido(valor):
    """
    Normaliza celulares argentinos.

    Ejemplos admitidos:
    3436123456
    03436123456
    343 15 6123456
    0343 15 6123456
    +54 9 343 6123456
    5493436123456

    Devuelve 10 dígitos nacionales.
    """

    digitos = "".join(
        caracter
        for caracter in str(valor or "")
        if caracter.isdigit()
    )

    if not digitos:
        return ""

    if digitos.startswith("0054"):
        digitos = digitos[2:]

    if digitos.startswith("54"):
        digitos = digitos[2:]

        if digitos.startswith("9"):
            digitos = digitos[1:]

    if digitos.startswith("0"):
        digitos = digitos[1:]

    # característica + 15 + número
    if len(digitos) == 12:
        for largo_caracteristica in (2, 3, 4):
            posicion = largo_caracteristica

            if digitos[posicion:posicion + 2] != "15":
                continue

            candidato = (
                digitos[:posicion]
                + digitos[posicion + 2:]
            )

            if len(candidato) == 10:
                digitos = candidato
                break

    if len(digitos) != 10:
        return ""

    # 15xxxxxxxx sin característica es ambiguo.
    if digitos.startswith("15"):
        return ""

    if digitos.startswith("0"):
        return ""

    return digitos


@gastronomia_bp.route(
    "/comercio/<comercio_id>/delivery/cotizar",
    methods=["POST"],
)
def cotizar_delivery(comercio_id):
    payload = request.get_json(silent=True) or {}
    direccion = str(
        payload.get("direccion_entrega")
        or payload.get("direccion")
        or ""
    ).strip()
    telefono_normalizado = _normalizar_telefono_pedido(
        payload.get("telefono_cliente")
    )
    if not direccion or len(direccion) > 160:
        return jsonify({"ok": False, "error": "Ingresá una dirección válida."}), 400
    if not telefono_normalizado:
        return jsonify({
            "ok": False,
            "error": "Ingresá un WhatsApp válido antes de calcular el envío.",
        }), 400

    config_res = (
        supabase_admin.table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,delivery_distancia_activo,"
            "delivery_franjas,delivery_origen_direccion,"
            "delivery_origen_latitud,delivery_origen_longitud"
        )
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .limit(1)
        .execute()
    )
    configuraciones = config_res.data or []
    if not configuraciones:
        return jsonify({"ok": False, "error": "El comercio no está recibiendo pedidos."}), 404
    configuracion = configuraciones[0]
    if not configuracion.get("acepta_delivery"):
        return jsonify({"ok": False, "error": "El comercio no tiene Delivery habilitado."}), 400
    if not configuracion.get("delivery_distancia_activo"):
        return jsonify({"ok": False, "error": "El envío por distancia no está habilitado."}), 400

    comercio_res = (
        supabase_admin.table("comercios")
        .select("id,ciudad")
        .eq("id", comercio_id)
        .limit(1)
        .execute()
    )
    comercios = comercio_res.data or []
    if not comercios:
        return jsonify({"ok": False, "error": "Comercio no encontrado."}), 404

    origen = str(configuracion.get("delivery_origen_direccion") or "").strip()
    try:
        origen_latitud, origen_longitud = validar_coordenadas(
            configuracion.get("delivery_origen_latitud"),
            configuracion.get("delivery_origen_longitud"),
        )
    except DeliveryError:
        return jsonify({
            "ok": False,
            "error": (
                "Este comercio todavía debe configurar la ubicación "
                "de salida del delivery."
            ),
        }), 409
    direccion_normalizada = normalizar_direccion(direccion)
    origen_normalizado = clave_origen_coordenadas(
        origen_latitud,
        origen_longitud,
    )
    origen_cache_validado = clave_cache_delivery(origen_normalizado)
    try:
        distancia_m = None
        try:
            cache_res = (
                supabase_admin.table("gastronomia_cliente_direcciones")
                .select("distancia_m,origen_normalizado")
                .eq("comercio_id", comercio_id)
                .eq("telefono_normalizado", telefono_normalizado)
                .eq("direccion_normalizada", direccion_normalizada)
                .limit(1)
                .execute()
            )
            cache = cache_res.data or []
            if (
                cache
                and cache[0].get("origen_normalizado")
                == origen_cache_validado
            ):
                distancia_cache = cache[0].get("distancia_m")
                if (
                    not isinstance(distancia_cache, bool)
                    and isinstance(distancia_cache, (int, float))
                    and distancia_cache >= 0
                ):
                    distancia_m = int(distancia_cache)
        except Exception:
            current_app.logger.exception(
                "No se pudo consultar la caché privada de delivery"
            )

        if distancia_m is None:
            distancia_m = consultar_distancia_osm(
                origen_latitud,
                origen_longitud,
                direccion,
                comercios[0].get("ciudad"),
            )
            try:
                (
                    supabase_admin.table("gastronomia_cliente_direcciones")
                    .upsert({
                        "comercio_id": comercio_id,
                        "telefono_normalizado": telefono_normalizado,
                        "direccion": direccion,
                        "direccion_normalizada": direccion_normalizada,
                        "distancia_m": distancia_m,
                        "origen_normalizado": origen_cache_validado,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }, on_conflict=(
                        "comercio_id,telefono_normalizado,direccion_normalizada"
                    ))
                    .execute()
                )
            except Exception:
                current_app.logger.exception(
                    "No se pudo actualizar la caché privada de delivery"
                )

        franja = seleccionar_franja(
            configuracion.get("delivery_franjas"),
            distancia_m,
        )
    except (DeliveryError, ValueError) as error:
        mensaje = getattr(error, "mensaje", "La configuración de delivery no es válida.")
        status = getattr(error, "status_code", 500)
        return jsonify({"ok": False, "error": mensaje}), status

    if franja is None:
        return jsonify({"ok": False, "fuera_zona": True, "error": "Fuera de la zona de delivery."}), 422

    costo_envio = float(franja["precio"])
    token = firmar_cotizacion(
        comercio_id,
        telefono_normalizado,
        direccion,
        distancia_m,
        costo_envio,
        origen_normalizado,
    )
    distancia_km = distancia_m / 1000
    distancia_mostrar = f"{distancia_km:.1f}".replace(".", ",") + " km"
    return jsonify({
        "ok": True,
        "distancia_m": distancia_m,
        "distancia_mostrar": distancia_mostrar,
        "costo_envio": costo_envio,
        "mensaje": "Envío calculado correctamente.",
        "cotizacion_token": token,
    })


@gastronomia_bp.route(
    "/comercio/<comercio_id>/pedido",
    methods=["POST"]
)
def registrar_pedido(comercio_id):

    payload = request.get_json(silent=True) or {}

    idempotency_key_raw = str(
        payload.get("idempotency_key") or ""
    ).strip()
    try:
        idempotency_key = str(UUID(idempotency_key_raw))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "ok": False,
            "error": "La identificación de la solicitud es inválida.",
        }), 400

    nombre = str(
        payload.get("nombre") or ""
    ).strip()

    apellido = str(
        payload.get("apellido") or ""
    ).strip()

    telefono = str(
        payload.get("whatsapp") or ""
    ).strip()

    telefono_normalizado = (
        _normalizar_telefono_pedido(telefono)
    )

    modalidad = str(
        payload.get("modalidad") or ""
    ).strip().lower()

    direccion = str(
        payload.get("direccion") or ""
    ).strip()

    forma_pago = str(
        payload.get("forma_pago") or ""
    ).strip().lower()

    observaciones = str(
        payload.get("observaciones") or ""
    ).strip()[:220]

    detalle_cliente = payload.get("detalle") or []

    idempotency_fingerprint = construir_idempotency_fingerprint(
        comercio_id=comercio_id,
        nombre=nombre,
        apellido=apellido,
        telefono_normalizado=telefono_normalizado,
        modalidad=modalidad,
        direccion=direccion,
        forma_pago=forma_pago,
        paga_con=payload.get("paga_con"),
        observaciones=observaciones,
        items=detalle_cliente,
        cotizacion_delivery=payload.get("cotizacion_delivery"),
    )

    try:
        resultado_existente = buscar_pedido_idempotente(
            comercio_id,
            idempotency_key,
            idempotency_fingerprint,
        )
    except PedidoError as error:
        return jsonify({
            "ok": False,
            "error": error.mensaje,
        }), error.status_code

    if resultado_existente:
        return jsonify({
            "ok": True,
            "pedido_id": resultado_existente.get("id"),
            "numero_pedido": resultado_existente.get("numero_pedido"),
            "created_at": resultado_existente.get("created_at"),
            "texto_pedido": resultado_existente.get("texto_pedido"),
            "whatsapp_comercio": (
                resultado_existente.get("whatsapp_comercio") or ""
            ),
            "idempotent_replay": True,
        })

    # ----------------------------------------------------------
    # Validaciones base
    # ----------------------------------------------------------

    if not nombre:
        return jsonify({
            "ok": False,
            "error": "Ingresá tu nombre."
        }), 400

    if not apellido:
        return jsonify({
            "ok": False,
            "error": "Ingresá tu apellido."
        }), 400

    if not telefono:
        return jsonify({
            "ok": False,
            "error": "Ingresá tu WhatsApp."
        }), 400

    if not telefono_normalizado:
        return jsonify({
            "ok": False,
            "error": (
                "Ingresá un WhatsApp válido con característica. "
                "Ejemplo: 343 6123456."
            )
        }), 400

    if modalidad not in ("delivery", "retiro"):
        return jsonify({
            "ok": False,
            "error": "Elegí Delivery o Retiro."
        }), 400

    if modalidad == "delivery" and not direccion:
        return jsonify({
            "ok": False,
            "error": "Ingresá la dirección de entrega."
        }), 400

    if forma_pago not in ("efectivo", "transferencia"):
        return jsonify({
            "ok": False,
            "error": "Elegí una forma de pago."
        }), 400

    if not isinstance(detalle_cliente, list) or not detalle_cliente:
        return jsonify({
            "ok": False,
            "error": "El pedido está vacío."
        }), 400

    try:
        cotizacion_delivery = None
        if modalidad == "delivery":
            config_delivery_res = (
                supabase_admin.table("gastronomia_configuracion")
                .select(
                    "delivery_distancia_activo,delivery_origen_direccion,"
                    "delivery_origen_latitud,delivery_origen_longitud"
                )
                .eq("comercio_id", comercio_id)
                .eq("activo", True)
                .limit(1)
                .execute()
            )
            config_delivery = config_delivery_res.data or []
            if config_delivery and config_delivery[0].get("delivery_distancia_activo"):
                origen_token = clave_origen_coordenadas(
                    config_delivery[0].get("delivery_origen_latitud"),
                    config_delivery[0].get("delivery_origen_longitud"),
                )
                cotizacion_delivery = validar_cotizacion(
                    payload.get("cotizacion_delivery"),
                    comercio_id,
                    telefono_normalizado,
                    direccion,
                    origen_token,
                )

        resultado = crear_pedido(
            comercio_id=comercio_id,
            nombre=nombre,
            apellido=apellido,
            telefono=telefono,
            telefono_normalizado=telefono_normalizado,
            modalidad=modalidad,
            direccion=direccion,
            forma_pago=forma_pago,
            paga_con=payload.get("paga_con"),
            observaciones=observaciones,
            items=detalle_cliente,
            visitante_id=getattr(
                g,
                "analytics_visitante_id",
                None,
            ),
            sesion_id=getattr(
                g,
                "analytics_sesion_id",
                None,
            ),
            cotizacion_delivery=cotizacion_delivery,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=idempotency_fingerprint,
        )
    except (PedidoError, DeliveryError) as error:
        return jsonify({
            "ok": False,
            "error": error.mensaje,
        }), error.status_code

    return jsonify({
        "ok": True,
        "pedido_id": resultado.get("id"),
        "numero_pedido": resultado.get("numero_pedido"),
        "created_at": resultado.get("created_at"),
        "texto_pedido": resultado.get("texto_pedido"),
        "whatsapp_comercio": resultado.get("whatsapp_comercio") or "",
        "idempotent_replay": bool(resultado.get("idempotent_replay")),
    })
