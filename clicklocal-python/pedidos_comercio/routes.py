import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from flask import abort, current_app, jsonify, redirect, render_template, request, session, url_for

from config.supabase_config import supabase_admin
from modulos import modulo_activo
from whatsapp import construir_url_whatsapp, limpiar_numero_whatsapp

from . import pedidos_comercio_bp
from .delivery import (
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
from delivery_cache import (
    buscar_distancia_cache,
    construir_identidad_cache,
    guardar_distancia_cache,
)

ESTADOS = ("nuevo", "aceptado", "preparando", "listo", "entregado", "cancelado")
TRANSICIONES = {
    "nuevo": {"aceptado", "cancelado"},
    "aceptado": {"preparando", "cancelado"},
    "preparando": {"listo", "cancelado"},
    "listo": {"entregado", "cancelado"},
    "entregado": set(),
    "cancelado": set(),
}
CONFIG_DEFAULT = {
    "acepta_retiro": True,
    "acepta_delivery": False,
    "acepta_efectivo": True,
    "acepta_transferencia": True,
    "pedido_minimo": None,
    "delivery_modo": "fijo",
    "delivery_costo_fijo": 0,
    "delivery_franjas": [],
}
ZONA_HORARIA_PEDIDOS = ZoneInfo("America/Argentina/Cordoba")
HISTORIAL_POR_PAGINA = 50
HISTORIAL_MAX_CANDIDATOS = 500
CLIENTES_POR_PAGINA = 50
CLIENTES_MAX_PEDIDOS = 2000
METRICAS_MAX_PEDIDOS = 2000
METRICAS_ITEMS_POR_CONSULTA = 200


def _uuid(valor):
    try:
        return str(UUID(str(valor)))
    except (TypeError, ValueError, AttributeError):
        return None


def _dinero(valor, permite_none=False):
    if permite_none and valor in (None, ""):
        return None
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return numero if numero.is_finite() and numero >= 0 else None


def _precio(valor):
    numero = _dinero(valor)
    return numero


def _filtrar_franjas_formulario(limites, precios):
    if len(limites) != len(precios):
        raise ValueError("Completá el límite y el precio de cada franja.")
    limites_validos = []
    precios_validos = []
    for limite_raw, precio_raw in zip(limites, precios):
        limite = str(limite_raw or "").strip()
        precio = str(precio_raw or "").strip()
        if not limite and not precio:
            continue
        if not limite or not precio:
            raise ValueError("Completá el límite y el precio de cada franja.")
        limites_validos.append(limite)
        precios_validos.append(precio)
    return limites_validos, precios_validos


def _formatear(valor):
    numero = Decimal(str(valor)).quantize(Decimal("0.01"))
    entero, decimales = f"{numero:.2f}".split(".")
    texto = f"{int(entero):,}".replace(",", ".")
    return "$ " + texto + ("," + decimales if decimales != "00" else "")


def _agregar_cambio_presentacion(pedido):
    pedido["cambio"] = None
    if str(pedido.get("forma_pago") or "").strip().lower() != "efectivo":
        return pedido
    paga_con = _dinero(pedido.get("paga_con"), permite_none=True)
    total = _dinero(pedido.get("total"))
    if paga_con is not None and total is not None and paga_con >= total:
        pedido["cambio"] = float(paga_con - total)
    return pedido


def _ahora_local():
    return datetime.now(ZONA_HORARIA_PEDIDOS)


def _limites_periodo(periodo, ahora=None):
    ahora = (ahora or _ahora_local()).astimezone(ZONA_HORARIA_PEDIDOS)
    hoy = ahora.date()
    dias = {"hoy": 1, "ayer": 1, "7d": 7, "30d": 30}
    periodo = periodo if periodo in dias else "hoy"
    fin_fecha = hoy + timedelta(days=1)
    inicio_fecha = hoy
    if periodo == "ayer":
        inicio_fecha = hoy - timedelta(days=1)
        fin_fecha = hoy
    elif periodo in {"7d", "30d"}:
        inicio_fecha = fin_fecha - timedelta(days=dias[periodo])
    inicio = datetime.combine(inicio_fecha, time.min, ZONA_HORARIA_PEDIDOS)
    fin = datetime.combine(fin_fecha, time.min, ZONA_HORARIA_PEDIDOS)
    return inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc), periodo


def _fecha_local_presentacion(valor):
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.astimezone(ZONA_HORARIA_PEDIDOS).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def _fecha_pedido(valor):
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _clave_cliente(pedido):
    normalizado = str(pedido.get("whatsapp_normalizado") or "").strip()
    if not normalizado:
        normalizado = limpiar_numero_whatsapp(pedido.get("whatsapp_cliente"))
    return normalizado or "pedido:" + str(pedido.get("id") or "sin-id")


def _identificador_cliente(comercio_id, clave):
    contenido = f"{comercio_id}:{clave}".encode("utf-8")
    return hashlib.sha256(contenido).hexdigest()


def _agrupar_clientes(pedidos, comercio_id):
    ordenados = sorted(
        pedidos, key=lambda pedido: _fecha_pedido(pedido.get("created_at")), reverse=True
    )
    clientes = {}
    for pedido in ordenados:
        clave = _clave_cliente(pedido)
        cliente = clientes.setdefault(clave, {
            "identificador": _identificador_cliente(comercio_id, clave),
            "clave": clave,
            "nombre": "Cliente sin nombre",
            "whatsapp": str(pedido.get("whatsapp_cliente") or clave),
            "pedidos_compra": 0,
            "total_comprado": Decimal("0.00"),
            "primera_compra_dt": None,
            "ultima_compra_dt": None,
            "ultimo_pedido_dt": _fecha_pedido(pedido.get("created_at")),
            "pedidos": [],
        })
        cliente["pedidos"].append(pedido)
        nombre = " ".join(str(pedido.get("nombre_cliente") or "").split())
        if cliente["nombre"] == "Cliente sin nombre" and nombre:
            cliente["nombre"] = nombre
        if pedido.get("estado") == "cancelado":
            continue
        fecha = _fecha_pedido(pedido.get("created_at"))
        total = _dinero(pedido.get("total")) or Decimal("0.00")
        cliente["pedidos_compra"] += 1
        cliente["total_comprado"] += total
        if cliente["ultima_compra_dt"] is None:
            cliente["ultima_compra_dt"] = fecha
        cliente["primera_compra_dt"] = fecha
    resultado = []
    for cliente in clientes.values():
        cantidad = cliente["pedidos_compra"]
        cliente["ticket_promedio"] = (
            float(cliente["total_comprado"] / cantidad) if cantidad else 0
        )
        cliente["total_comprado"] = float(cliente["total_comprado"])
        cliente["primera_compra"] = _fecha_local_presentacion(
            cliente["primera_compra_dt"].isoformat()
        ) if cliente["primera_compra_dt"] else "Sin compras realizadas"
        cliente["ultima_compra"] = _fecha_local_presentacion(
            cliente["ultima_compra_dt"].isoformat()
        ) if cliente["ultima_compra_dt"] else "Sin compras realizadas"
        for pedido in cliente["pedidos"]:
            pedido["fecha_local"] = _fecha_local_presentacion(pedido.get("created_at"))
        resultado.append(cliente)
    return sorted(
        resultado,
        key=lambda cliente: cliente["ultima_compra_dt"] or cliente["ultimo_pedido_dt"],
        reverse=True,
    )


def _consulta_pedidos_clientes(comercio_id, db=None):
    db = db or supabase_admin
    campos = (
        "id,numero,nombre_cliente,whatsapp_cliente,whatsapp_normalizado,"
        "created_at,total,estado,tipo_entrega"
    )
    return (
        db.table("pedidos").select(campos)
        .eq("comercio_id", comercio_id)
        .order("created_at", desc=True)
        .limit(CLIENTES_MAX_PEDIDOS)
        .execute().data
        or []
    )


def _consulta_datos_metricas(comercio_id, inicio, fin, db=None):
    db = db or supabase_admin
    campos = (
        "id,nombre_cliente,whatsapp_cliente,whatsapp_normalizado,"
        "created_at,total,estado"
    )
    pedidos = (
        db.table("pedidos").select(campos)
        .eq("comercio_id", comercio_id)
        .gte("created_at", inicio.isoformat())
        .lt("created_at", fin.isoformat())
        .order("created_at", desc=True)
        .limit(METRICAS_MAX_PEDIDOS)
        .execute().data
        or []
    )
    ids_validos = [
        pedido.get("id") for pedido in pedidos
        if pedido.get("id") and pedido.get("estado") != "cancelado"
    ]
    items = []
    campos_items = (
        "pedido_id,publicacion_id,nombre_snapshot,"
        "precio_unitario_snapshot,cantidad,subtotal"
    )
    for posicion in range(0, len(ids_validos), METRICAS_ITEMS_POR_CONSULTA):
        lote = ids_validos[posicion:posicion + METRICAS_ITEMS_POR_CONSULTA]
        items.extend(
            db.table("pedido_items").select(campos_items)
            .in_("pedido_id", lote).execute().data
            or []
        )
    return pedidos, items


def _calcular_metricas(pedidos, items, comercio_id):
    pedidos_validos = [
        pedido for pedido in pedidos if pedido.get("estado") != "cancelado"
    ]
    ventas = sum(
        (_dinero(pedido.get("total")) or Decimal("0.00") for pedido in pedidos_validos),
        Decimal("0.00"),
    )
    cantidad_pedidos = len(pedidos_validos)
    clientes = [
        cliente for cliente in _agrupar_clientes(pedidos, comercio_id)
        if cliente["pedidos_compra"] > 0
    ]
    orden_pedidos = {
        str(pedido.get("id")): posicion for posicion, pedido in enumerate(pedidos)
        if pedido.get("estado") != "cancelado"
    }
    productos = {}
    for item in items:
        pedido_id = str(item.get("pedido_id") or "")
        if pedido_id not in orden_pedidos:
            continue
        clave = str(item.get("publicacion_id") or "") or (
            "snapshot:" + str(item.get("nombre_snapshot") or "Producto")
        )
        producto = productos.setdefault(clave, {
            "nombre": str(item.get("nombre_snapshot") or "Producto"),
            "unidades": 0,
            "facturacion": Decimal("0.00"),
            "posicion_nombre": orden_pedidos[pedido_id],
        })
        if orden_pedidos[pedido_id] < producto["posicion_nombre"]:
            producto["nombre"] = str(item.get("nombre_snapshot") or "Producto")
            producto["posicion_nombre"] = orden_pedidos[pedido_id]
        try:
            cantidad = int(item.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0
        producto["unidades"] += max(0, cantidad)
        producto["facturacion"] += _dinero(item.get("subtotal")) or Decimal("0.00")
    productos_presentacion = [
        {
            "nombre": producto["nombre"],
            "unidades": producto["unidades"],
            "facturacion": float(producto["facturacion"]),
        }
        for producto in productos.values()
    ]
    return {
        "ventas": float(ventas),
        "pedidos": cantidad_pedidos,
        "ticket_promedio": float(ventas / cantidad_pedidos) if cantidad_pedidos else 0,
        "clientes": len(clientes),
        "productos_unidades": sorted(
            productos_presentacion,
            key=lambda producto: (-producto["unidades"], producto["nombre"].casefold()),
        )[:10],
        "productos_facturacion": sorted(
            productos_presentacion,
            key=lambda producto: (-producto["facturacion"], producto["nombre"].casefold()),
        )[:10],
        "top_clientes": sorted(
            clientes,
            key=lambda cliente: (-cliente["total_comprado"], cliente["nombre"].casefold()),
        )[:10],
    }
def _adjuntar_resumen_items(pedidos, db=None, incluir_detalle=False):
    db = db or supabase_admin
    ids = [pedido.get("id") for pedido in pedidos if pedido.get("id")]
    items = (
        db.table("pedido_items").select("*").in_("pedido_id", ids).execute().data
        or []
    ) if ids else []
    por_pedido = {}
    for item in items:
        por_pedido.setdefault(str(item.get("pedido_id")), []).append(item)
    for pedido in pedidos:
        detalle = por_pedido.get(str(pedido.get("id")), [])
        pedido["cantidad_items"] = sum(int(item.get("cantidad") or 0) for item in detalle)
        if incluir_detalle:
            pedido["items"] = detalle
        pedido["fecha_local"] = _fecha_local_presentacion(pedido.get("created_at"))
        _agregar_cambio_presentacion(pedido)
    return pedidos


def _consulta_pedidos_periodo(comercio_id, inicio, fin, limite, db=None):
    db = db or supabase_admin
    return (
        db.table("pedidos").select("*")
        .eq("comercio_id", comercio_id)
        .gte("created_at", inicio.isoformat())
        .lt("created_at", fin.isoformat())
        .order("created_at", desc=True)
        .limit(limite)
        .execute().data
        or []
    )


def _fingerprint_pedido(pedido, items):
    contenido = {
        "comercio_id": pedido["comercio_id"],
        "nombre_cliente": pedido["nombre_cliente"],
        "whatsapp_cliente": pedido["whatsapp_cliente"],
        "whatsapp_normalizado": pedido["whatsapp_normalizado"],
        "tipo_entrega": pedido["tipo_entrega"],
        "direccion": pedido["direccion"],
        "referencia": pedido["referencia"],
        "forma_pago": pedido["forma_pago"],
        "paga_con": pedido["paga_con"],
        "subtotal": pedido["subtotal"],
        "costo_envio": pedido["costo_envio"],
        "total": pedido["total"],
        "distancia_delivery_m": pedido["distancia_delivery_m"],
        "observaciones": pedido["observaciones"],
        "items": sorted(items, key=lambda item: item["publicacion_id"]),
    }
    canonico = json.dumps(
        contenido, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _configuracion(comercio_id, db=None):
    db = db or supabase_admin
    respuesta = (
        db.table("pedidos_configuracion").select("*")
        .eq("comercio_id", comercio_id).limit(1).execute()
    )
    return {**CONFIG_DEFAULT, **((respuesta.data or [{}])[0])}


def _consultar_fidelizacion(comercio_id, cliente_claves, db=None):
    db = db or supabase_admin
    respuesta = db.rpc(
        "consultar_fidelizacion_clicklocal",
        {
            "p_comercio_id": comercio_id,
            "p_cliente_claves": cliente_claves,
        },
    ).execute().data
    if isinstance(respuesta, list) and len(respuesta) == 1:
        respuesta = respuesta[0]
    if not isinstance(respuesta, dict):
        raise RuntimeError("La consulta de fidelización no devolvió datos válidos.")
    return respuesta


def _adjuntar_fidelizacion(clientes, fidelizacion):
    por_clave = {
        str(cliente.get("cliente_clave") or ""): cliente
        for cliente in fidelizacion.get("clientes") or []
    }
    meta = fidelizacion.get("meta_sellos")
    activa = fidelizacion.get("activa") is True
    premio = fidelizacion.get("premio")
    for cliente in clientes:
        estado = por_clave.get(cliente["clave"])
        if not estado:
            continue
        estado["meta_sellos"] = meta
        estado["premio"] = premio
        estado["activa"] = activa
        estado["mostrar"] = bool(
            activa
            or estado.get("saldo")
            or estado.get("sellos_consumidos")
            or estado.get("canjes")
        )
        for canje in estado.get("canjes") or []:
            canje["fecha"] = _fecha_local_presentacion(canje.get("canjeado_at"))
        cliente["fidelizacion"] = estado
    return clientes


def _resolver_cliente(comercio_id, identificador):
    if len(identificador) != 64 or any(
        caracter not in "0123456789abcdef" for caracter in identificador
    ):
        return None, []
    pedidos = _consulta_pedidos_clientes(comercio_id)
    cliente = next(
        (
            agrupado for agrupado in _agrupar_clientes(pedidos, comercio_id)
            if agrupado["identificador"] == identificador
        ),
        None,
    )
    return cliente, pedidos


def _token_canje_fidelizacion(identificador):
    pendientes = session.get("fidelizacion_canjes_pendientes")
    if not isinstance(pendientes, dict):
        pendientes = {}
    token = _uuid(pendientes.get(identificador))
    if not token:
        token = str(uuid4())
        pendientes[identificador] = token
        session["fidelizacion_canjes_pendientes"] = pendientes
    return token


def _comercio_panel(db=None):
    db = db or supabase_admin
    comercio_id = session.get("gestor_comercio_id") if session.get("admin_logueado") else None
    consulta = db.table("comercios").select("*")
    if comercio_id:
        consulta = consulta.eq("id", comercio_id)
    elif session.get("user_id"):
        consulta = consulta.eq("user_id", session.get("user_id"))
    else:
        return None
    filas = consulta.limit(1).execute().data or []
    return filas[0] if filas else None


def _calcular_envio(config, comercio_id, entrega, direccion, whatsapp, token=None):
    if entrega == "retiro":
        if not config.get("acepta_retiro"):
            raise ValueError("Este comercio no ofrece retiro.")
        return Decimal("0.00"), None
    if entrega != "delivery" or not config.get("acepta_delivery"):
        raise ValueError("Este comercio no ofrece delivery.")
    if not direccion:
        raise ValueError("Ingresá la dirección y el número para el delivery.")
    if config.get("delivery_modo") == "fijo":
        costo = _dinero(config.get("delivery_costo_fijo"))
        if costo is None:
            raise ValueError("La configuración del delivery no es válida.")
        return costo, None
    if not token:
        raise ValueError("Calculá el envío antes de confirmar.")
    latitud, longitud = validar_coordenadas(
        config.get("delivery_origen_latitud"),
        config.get("delivery_origen_longitud"),
    )
    origen_clave = clave_cache_delivery(
        clave_origen_coordenadas(latitud, longitud)
    )
    try:
        cotizacion = validar_cotizacion(
            token, comercio_id, whatsapp, direccion, origen_clave,
        )
    except DeliveryError as error_nuevo:
        # Compatibilidad durante los 10 minutos de vida de cotizaciones
        # emitidas antes de vincular el token a las coordenadas del origen.
        try:
            cotizacion = validar_cotizacion(
                token, comercio_id, whatsapp, direccion,
                config.get("delivery_origen_direccion"),
            )
        except DeliveryError:
            raise error_nuevo
    return Decimal(str(cotizacion["costo_envio"])), cotizacion["distancia_m"]


def confirmar_publico(comercio_id, db=None, modulo_checker=None):
    db = db or supabase_admin
    modulo_checker = modulo_checker or modulo_activo
    comercio_id = _uuid(comercio_id)
    if not comercio_id:
        return jsonify(ok=False, error="El comercio no es válido."), 400
    if not modulo_checker(comercio_id, "carrito"):
        return jsonify(ok=False, error="Este comercio no tiene el carrito habilitado."), 403
    datos = request.get_json(silent=True) or {}
    idempotency_key = _uuid(datos.get("idempotency_key"))
    if not idempotency_key:
        return jsonify(ok=False, error="La clave del intento de pedido no es válida."), 400
    nombre = " ".join(str(datos.get("nombre") or "").split())[:49]
    apellido = " ".join(str(datos.get("apellido") or "").split())[:50]
    nombre_cliente = (nombre + " " + apellido).strip()
    telefono_raw = str(datos.get("whatsapp") or "").strip()
    telefono = limpiar_numero_whatsapp(telefono_raw)
    entrega = str(datos.get("entrega") or "").lower()
    pago = str(datos.get("pago") or "").lower()
    direccion = " ".join(str(datos.get("direccion") or "").split())[:300]
    numero = " ".join(str(datos.get("numero") or "").split())[:30]
    domicilio = (direccion + " " + numero).strip()
    referencia = str(datos.get("referencia") or "").strip()[:300]
    observaciones = str(datos.get("observaciones") or "").strip()[:500]
    items_cliente = datos.get("items")
    if not nombre:
        return jsonify(ok=False, error="Ingresá tu nombre."), 400
    if not apellido:
        return jsonify(ok=False, error="Ingresá tu apellido."), 400
    if not telefono or len("".join(c for c in telefono_raw if c.isdigit())) < 10:
        return jsonify(ok=False, error="Ingresá un WhatsApp válido con característica."), 400
    if not isinstance(items_cliente, list) or not items_cliente or len(items_cliente) > 50:
        return jsonify(ok=False, error="El carrito no es válido."), 400
    cantidades = {}
    for item in items_cliente:
        item_id = _uuid(item.get("id") if isinstance(item, dict) else None)
        cantidad_raw = item.get("cantidad") if isinstance(item, dict) else None
        try:
            cantidad = int(cantidad_raw)
            valida = Decimal(str(cantidad_raw)) == cantidad and not isinstance(cantidad_raw, bool)
        except (InvalidOperation, TypeError, ValueError):
            valida = False
            cantidad = 0
        if not item_id or not valida or not 1 <= cantidad <= 99 or item_id in cantidades:
            return jsonify(ok=False, error="La cantidad o producto no es válido."), 400
        cantidades[item_id] = cantidad
    try:
        comercio_rows = (db.table("comercios").select("id,nombre_negocio,whatsapp,activo")
            .eq("id", comercio_id).eq("activo", True).limit(1).execute().data or [])
        if not comercio_rows:
            return jsonify(ok=False, error="El comercio no está disponible."), 404
        comercio = comercio_rows[0]
        config = _configuracion(comercio_id, db)
        publicaciones = (db.table("publicaciones")
            .select("id,comercio_id,nombre,precio,activa,eliminada")
            .eq("comercio_id", comercio_id).in_("id", list(cantidades)).execute().data or [])
    except Exception as error:
        print("ERROR VALIDANDO PEDIDO COMERCIO:", error, flush=True)
        return jsonify(ok=False, error="No pudimos validar el pedido."), 500
    por_id = {str(p.get("id")): p for p in publicaciones}
    if set(por_id) != set(cantidades):
        return jsonify(ok=False, error="Un producto no pertenece a este comercio."), 400
    detalle, subtotal = [], Decimal("0.00")
    for item_id, cantidad in cantidades.items():
        pub = por_id[item_id]
        if str(pub.get("comercio_id")) != comercio_id:
            return jsonify(ok=False, error="Un producto no pertenece a este comercio."), 400
        if pub.get("activa") is not True or pub.get("eliminada") is True:
            return jsonify(ok=False, error="Un producto ya no está disponible."), 400
        precio = _precio(pub.get("precio"))
        if precio is None:
            return jsonify(ok=False, error="Un producto no tiene precio válido."), 400
        item_subtotal = precio * cantidad
        subtotal += item_subtotal
        detalle.append({"publicacion_id": item_id, "nombre_snapshot": str(pub.get("nombre") or "Producto"),
            "precio_unitario_snapshot": float(precio), "cantidad": cantidad, "subtotal": float(item_subtotal)})
    minimo = _dinero(config.get("pedido_minimo"), permite_none=True)
    if entrega == "delivery" and minimo is not None and subtotal < minimo:
        return jsonify(ok=False, error=f"El pedido mínimo es {_formatear(minimo)}."), 400
    if pago == "efectivo" and not config.get("acepta_efectivo"):
        return jsonify(ok=False, error="Este comercio no acepta efectivo."), 400
    if pago == "transferencia" and not config.get("acepta_transferencia"):
        return jsonify(ok=False, error="Este comercio no acepta transferencia."), 400
    if pago not in {"efectivo", "transferencia"}:
        return jsonify(ok=False, error="La forma de pago no es válida."), 400
    try:
        costo_envio, distancia = _calcular_envio(config, comercio_id, entrega, domicilio, telefono, datos.get("cotizacion_token"))
    except (ValueError, DeliveryError) as error:
        return jsonify(ok=False, error=str(error)), getattr(error, "status_code", 400)
    total = subtotal + costo_envio
    paga_con = _dinero(datos.get("paga_con"), permite_none=True) if pago == "efectivo" else None
    if pago == "efectivo" and datos.get("paga_con") not in (None, "") and (paga_con is None or paga_con < total):
        return jsonify(ok=False, error="El importe con el que pagás es menor al total."), 400
    # El texto se completa con el número retornado por la transacción.
    base = [f'{i["cantidad"]} x {i["nombre_snapshot"]} — {_formatear(i["subtotal"])}' for i in detalle]
    texto_provisorio = "\n".join(base)
    pedido_db = {"comercio_id": comercio_id, "nombre_cliente": nombre_cliente,
        "whatsapp_cliente": telefono_raw, "whatsapp_normalizado": telefono,
        "tipo_entrega": entrega, "direccion": domicilio, "referencia": referencia,
        "forma_pago": pago, "paga_con": float(paga_con) if paga_con is not None else None,
        "subtotal": float(subtotal), "costo_envio": float(costo_envio), "total": float(total),
        "distancia_delivery_m": distancia, "observaciones": observaciones, "texto_pedido": texto_provisorio}
    pedido_db["idempotency_key"] = idempotency_key
    pedido_db["idempotency_fingerprint"] = _fingerprint_pedido(pedido_db, detalle)
    try:
        creado_raw = db.rpc("crear_pedido_clicklocal", {"p_pedido": pedido_db, "p_items": detalle}).execute().data
        creado = creado_raw[0] if isinstance(creado_raw, list) else creado_raw
        if not creado or not creado.get("id"):
            raise RuntimeError("La base no devolvió el pedido creado")
    except Exception as error:
        print("ERROR GUARDANDO PEDIDO COMERCIO:", error, flush=True)
        return jsonify(ok=False, error="No pudimos guardar el pedido."), 500
    numero_pedido = creado.get("numero")
    lineas = [f"Pedido ClickLocal #{numero_pedido}", "", *base, "",
        "Subtotal: " + _formatear(subtotal), "Envío: " + _formatear(costo_envio),
        "TOTAL: " + _formatear(total), "", "Entrega: " + entrega.capitalize()]
    if entrega == "delivery":
        lineas.append("Dirección: " + domicilio)
        if referencia: lineas.append("Referencia: " + referencia)
    lineas.append("Pago: " + pago.capitalize())
    if paga_con is not None: lineas.append("Paga con: " + _formatear(paga_con))
    lineas.extend(["", "Cliente:", nombre_cliente, telefono_raw])
    if observaciones: lineas.extend(["", "Observaciones:", observaciones])
    texto = "\n".join(lineas)
    # Actualización posterior sólo enriquece el snapshot; el pedido ya existe.
    try:
        db.table("pedidos").update({"texto_pedido": texto}).eq("id", creado["id"]).eq("comercio_id", comercio_id).execute()
    except Exception:
        pass
    whatsapp_url = construir_url_whatsapp(comercio.get("whatsapp"), texto)
    if not whatsapp_url:
        return jsonify(ok=True, pedido_id=creado["id"], numero=numero_pedido, total=float(total),
            whatsapp_url=None, aviso="Pedido guardado; el comercio no tiene WhatsApp válido."), 200
    return jsonify(ok=True, pedido_id=creado["id"], numero=numero_pedido, total=float(total),
        subtotal=float(subtotal), costo_envio=float(costo_envio), whatsapp_url=whatsapp_url)


@pedidos_comercio_bp.get("/comercio/<comercio_id>/carrito/configuracion")
def configuracion_publica(comercio_id):
    comercio_id = _uuid(comercio_id)
    if not comercio_id or not modulo_activo(comercio_id, "carrito"):
        return jsonify(ok=False, error="Carrito no disponible."), 404
    try:
        config = _configuracion(comercio_id)
    except Exception:
        return jsonify(ok=False, error="Falta aplicar la migración de pedidos."), 503
    return jsonify(ok=True, configuracion={k: config.get(k) for k in CONFIG_DEFAULT})


@pedidos_comercio_bp.post("/comercio/<comercio_id>/carrito/cotizar-envio")
def cotizar_envio(comercio_id):
    comercio_id = _uuid(comercio_id)
    if not comercio_id or not modulo_activo(comercio_id, "carrito"):
        return jsonify(ok=False, error="Carrito no disponible."), 404
    datos = request.get_json(silent=True) or {}
    telefono = limpiar_numero_whatsapp(datos.get("whatsapp"))
    direccion = (str(datos.get("direccion") or "").strip() + " " + str(datos.get("numero") or "").strip()).strip()
    try:
        config = _configuracion(comercio_id)
        if not config.get("acepta_delivery"):
            raise DeliveryError("Este comercio no ofrece delivery.")
        if config.get("delivery_modo") == "fijo":
            costo = _dinero(config.get("delivery_costo_fijo"))
            return jsonify(ok=True, costo_envio=float(costo), distancia_m=None, cotizacion_token=None)
        origen = config.get("delivery_origen_direccion")
        destino = completar_direccion_destino(direccion, origen, config.get("delivery_ciudad"))
        lat, lon = validar_coordenadas(config.get("delivery_origen_latitud"), config.get("delivery_origen_longitud"))
        origen_clave = clave_cache_delivery(clave_origen_coordenadas(lat, lon))
        identidad_cache = construir_identidad_cache(
            comercio_id,
            normalizar_direccion(destino),
            origen_clave,
        )
        distancia = buscar_distancia_cache(supabase_admin, identidad_cache)
        if distancia is None:
            distancia = consultar_distancia_osm(
                lat, lon, destino, config.get("delivery_ciudad")
            )
            guardar_distancia_cache(
                supabase_admin,
                identidad_cache,
                distancia,
            )
        franja = seleccionar_franja(config.get("delivery_franjas"), distancia)
        if not franja:
            raise DeliveryError("La dirección está fuera del radio de entrega.", 422)
        costo = Decimal(str(franja["precio"]))
        token = firmar_cotizacion(
            comercio_id, telefono, direccion, distancia, costo, origen_clave
        )
        return jsonify(ok=True, costo_envio=float(costo), distancia_m=distancia, cotizacion_token=token)
    except DeliveryError as error:
        return jsonify(ok=False, error=error.mensaje), error.status_code


@pedidos_comercio_bp.route("/panel/pedidos", methods=["GET", "POST"])
def panel_pedidos():
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        return redirect(url_for("panel"))
    if request.method == "POST":
        f = request.form
        current_app.logger.info(
            "DELIVERY_DIAG entrada usar_costo_fijo=%r",
            f.getlist("usar_costo_fijo"),
        )
        current_app.logger.info(
            "DELIVERY_DIAG entrada acepta_delivery=%r",
            f.get("acepta_delivery"),
        )
        current_app.logger.info(
            "DELIVERY_DIAG entrada km=%r", f.getlist("franja_km")
        )
        current_app.logger.info(
            "DELIVERY_DIAG entrada precios=%r", f.getlist("franja_precio")
        )
        current_app.logger.info(
            "DELIVERY_DIAG entrada origen=%r",
            f.get("delivery_origen_direccion"),
        )
        current_app.logger.info(
            "DELIVERY_DIAG entrada ciudad=%r", f.get("delivery_ciudad")
        )
        try:
            valores_usar_costo_fijo = f.getlist("usar_costo_fijo")
            if any(valor != "1" for valor in valores_usar_costo_fijo):
                raise ValueError("La opción de costo fijo no es válida.")
            delivery_modo = "fijo" if valores_usar_costo_fijo else "distancia"
            configuracion_actual = _configuracion(comercio_id)
            limites_recibidos = f.getlist("franja_km")
            precios_recibidos = f.getlist("franja_precio")
            franjas_guardadas = configuracion_actual.get("delivery_franjas")
            franjas = franjas_guardadas if isinstance(franjas_guardadas, list) else []
            costo_fijo_guardado = _dinero(
                configuracion_actual.get("delivery_costo_fijo")
            )
            delivery_costo_fijo = float(costo_fijo_guardado or 0)
            acepta_delivery = f.get("acepta_delivery") == "1"
            if acepta_delivery and delivery_modo == "fijo":
                costo_fijo = _dinero(f.get("delivery_costo_fijo"))
                if costo_fijo is None:
                    raise ValueError("Ingresá un costo de envío válido.")
                delivery_costo_fijo = float(costo_fijo)
            if acepta_delivery and delivery_modo == "distancia":
                if not str(f.get("delivery_ciudad") or "").strip():
                    raise ValueError("Ingresá una ciudad válida.")
                limites_franjas, precios_franjas = _filtrar_franjas_formulario(
                    limites_recibidos,
                    precios_recibidos,
                )
                current_app.logger.info(
                    "DELIVERY_DIAG filtrado km=%r", limites_franjas
                )
                current_app.logger.info(
                    "DELIVERY_DIAG filtrado precios=%r", precios_franjas
                )
                franjas = validar_configuracion_delivery(
                    f.get("acepta_delivery") == "1",
                    f.get("delivery_origen_direccion"),
                    limites_franjas,
                    precios_franjas,
                )
            current_app.logger.info("DELIVERY_DIAG franjas=%r", franjas)
            valores = {"comercio_id": comercio_id, "acepta_retiro": f.get("acepta_retiro") == "1",
                "acepta_delivery": acepta_delivery, "acepta_efectivo": f.get("acepta_efectivo") == "1",
                "acepta_transferencia": f.get("acepta_transferencia") == "1",
                "pedido_minimo": float(_dinero(f.get("pedido_minimo"), True)) if _dinero(f.get("pedido_minimo"), True) is not None else None,
                "delivery_modo": delivery_modo,
                "delivery_costo_fijo": delivery_costo_fijo,
                "delivery_origen_direccion": f.get("delivery_origen_direccion") or None,
                "delivery_ciudad": f.get("delivery_ciudad") or comercio.get("ciudad"), "delivery_franjas": franjas}
            if valores["acepta_delivery"] and valores["delivery_modo"] == "distancia":
                latitud, longitud = geocodificar_direccion_nominatim(
                    valores["delivery_origen_direccion"],
                    valores["delivery_ciudad"],
                )
                valores["delivery_origen_latitud"] = latitud
                valores["delivery_origen_longitud"] = longitud
            if not valores["acepta_retiro"] and not valores["acepta_delivery"]: raise ValueError("Habilitá al menos una entrega.")
            if not valores["acepta_efectivo"] and not valores["acepta_transferencia"]: raise ValueError("Habilitá al menos un pago.")
            current_app.logger.info(
                "DELIVERY_DIAG upsert modo=%r",
                valores.get("delivery_modo"),
            )
            current_app.logger.info(
                "DELIVERY_DIAG upsert costo_fijo=%r",
                valores.get("delivery_costo_fijo"),
            )
            current_app.logger.info(
                "DELIVERY_DIAG upsert origen=%r",
                valores.get("delivery_origen_direccion"),
            )
            current_app.logger.info(
                "DELIVERY_DIAG upsert ciudad=%r",
                valores.get("delivery_ciudad"),
            )
            current_app.logger.info(
                "DELIVERY_DIAG upsert acepta_delivery=%r",
                valores.get("acepta_delivery"),
            )
            current_app.logger.info(
                "DELIVERY_DIAG upsert franjas=%r",
                valores.get("delivery_franjas"),
            )
            current_app.logger.info(
                "DELIVERY_DIAG upsert lat=%r lon=%r",
                valores.get("delivery_origen_latitud"),
                valores.get("delivery_origen_longitud"),
            )
            supabase_admin.table("pedidos_configuracion").upsert(valores, on_conflict="comercio_id").execute()
            return redirect(url_for("pedidos_comercio.panel_pedidos", guardado="1"))
        except (ValueError, DeliveryError) as error:
            return redirect(url_for("pedidos_comercio.panel_pedidos", error=str(error)))
    config = _configuracion(comercio_id)
    fidelizacion = None
    fidelizacion_error = None
    try:
        fidelizacion = _consultar_fidelizacion(comercio_id, [])
    except Exception:
        current_app.logger.exception("ERROR CONSULTANDO FIDELIZACION")
        fidelizacion_error = "No pudimos consultar la configuración de fidelización."
    inicio, fin, _periodo = _limites_periodo("hoy")
    pedidos = _consulta_pedidos_periodo(comercio_id, inicio, fin, 100)
    _adjuntar_resumen_items(pedidos, incluir_detalle=True)
    plan_actual = str(comercio.get("plan") or "gratis").strip().lower()
    if plan_actual != "premium":
        plan_actual = "gratis"
    plan_nombre_panel = "Premium" if plan_actual == "premium" else "Gratis"
    return render_template("pedidos_comercio/panel.html", comercio=comercio, config=config,
        pedidos=pedidos, estados=ESTADOS, modo_gestor=bool(session.get("gestor_comercio_id")),
        plan_nombre_panel=plan_nombre_panel, fidelizacion=fidelizacion,
        fidelizacion_error=fidelizacion_error)


@pedidos_comercio_bp.post("/panel/pedidos/fidelizacion")
def guardar_fidelizacion():
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    activa_valores = request.form.getlist("fidelizacion_activa")
    try:
        if len(activa_valores) > 1 or any(valor != "1" for valor in activa_valores):
            raise ValueError("La opción de fidelización no es válida.")
        meta_raw = str(request.form.get("fidelizacion_meta_sellos") or "").strip()
        try:
            meta_sellos = int(meta_raw)
        except (TypeError, ValueError):
            raise ValueError("Ingresá una cantidad de sellos válida.") from None
        if meta_sellos < 1 or str(meta_sellos) != meta_raw:
            raise ValueError("Ingresá una cantidad de sellos válida.")
        premio = str(request.form.get("fidelizacion_premio") or "").strip()
        if not premio:
            raise ValueError("Ingresá una descripción para el premio.")
        supabase_admin.rpc(
            "configurar_fidelizacion_clicklocal",
            {
                "p_comercio_id": comercio_id,
                "p_activa": activa_valores == ["1"],
                "p_meta_sellos": meta_sellos,
                "p_premio": premio,
            },
        ).execute()
        return redirect(url_for(
            "pedidos_comercio.panel_pedidos",
            fidelizacion_guardada="1",
            _anchor="configuracion",
        ))
    except ValueError as error:
        return redirect(url_for(
            "pedidos_comercio.panel_pedidos",
            fidelizacion_error=str(error),
            _anchor="configuracion",
        ))
    except Exception:
        current_app.logger.exception("ERROR GUARDANDO FIDELIZACION")
        return redirect(url_for(
            "pedidos_comercio.panel_pedidos",
            fidelizacion_error="No pudimos guardar la fidelización. Intentá nuevamente.",
            _anchor="configuracion",
        ))


@pedidos_comercio_bp.get("/panel/pedidos/historial")
def historial_pedidos():
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    inicio, fin, periodo = _limites_periodo(request.args.get("periodo", "hoy"))
    busqueda = " ".join(str(request.args.get("q") or "").split())[:100]
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except (TypeError, ValueError):
        pagina = 1
    candidatos = _consulta_pedidos_periodo(
        comercio_id, inicio, fin, HISTORIAL_MAX_CANDIDATOS
    )
    limite_alcanzado = len(candidatos) >= HISTORIAL_MAX_CANDIDATOS
    if busqueda:
        termino = busqueda.casefold()
        candidatos = [
            pedido for pedido in candidatos
            if termino in str(pedido.get("numero") or "").casefold()
            or termino in str(pedido.get("nombre_cliente") or "").casefold()
            or termino in str(pedido.get("whatsapp_cliente") or "").casefold()
            or termino in str(pedido.get("whatsapp_normalizado") or "").casefold()
        ]
    inicio_pagina = (pagina - 1) * HISTORIAL_POR_PAGINA
    pedidos = candidatos[inicio_pagina:inicio_pagina + HISTORIAL_POR_PAGINA]
    _adjuntar_resumen_items(pedidos)
    return render_template(
        "pedidos_comercio/historial.html",
        comercio=comercio,
        pedidos=pedidos,
        periodo=periodo,
        busqueda=busqueda,
        pagina=pagina,
        hay_anterior=pagina > 1,
        hay_siguiente=inicio_pagina + HISTORIAL_POR_PAGINA < len(candidatos),
        limite_alcanzado=limite_alcanzado,
    )


@pedidos_comercio_bp.get("/panel/pedidos/clientes")
def clientes_pedidos():
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    pedidos = _consulta_pedidos_clientes(comercio_id)
    limite_alcanzado = len(pedidos) >= CLIENTES_MAX_PEDIDOS
    clientes = _agrupar_clientes(pedidos, comercio_id)
    busqueda = " ".join(str(request.args.get("q") or "").split())[:100]
    if busqueda:
        termino = busqueda.casefold()
        clientes = [
            cliente for cliente in clientes
            if termino in cliente["nombre"].casefold()
            or termino in cliente["whatsapp"].casefold()
            or termino in cliente["clave"].casefold()
        ]
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except (TypeError, ValueError):
        pagina = 1
    inicio = (pagina - 1) * CLIENTES_POR_PAGINA
    clientes_pagina = clientes[inicio:inicio + CLIENTES_POR_PAGINA]
    fidelizacion = None
    fidelizacion_error = None
    try:
        fidelizacion = _consultar_fidelizacion(
            comercio_id,
            [cliente["clave"] for cliente in clientes_pagina],
        )
        _adjuntar_fidelizacion(clientes_pagina, fidelizacion)
    except Exception:
        current_app.logger.exception("ERROR CONSULTANDO FIDELIZACION DE CLIENTES")
        fidelizacion_error = "No pudimos consultar los sellos de los clientes."
    return render_template(
        "pedidos_comercio/clientes.html",
        comercio=comercio,
        clientes=clientes_pagina,
        busqueda=busqueda,
        pagina=pagina,
        hay_anterior=pagina > 1,
        hay_siguiente=inicio + CLIENTES_POR_PAGINA < len(clientes),
        limite_alcanzado=limite_alcanzado,
        fidelizacion=fidelizacion,
        fidelizacion_error=fidelizacion_error,
    )


@pedidos_comercio_bp.get("/panel/pedidos/clientes/<identificador>")
def cliente_pedidos(identificador):
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    cliente, pedidos = _resolver_cliente(comercio_id, identificador)
    if not cliente:
        abort(404)
    fidelizacion = None
    fidelizacion_error = None
    try:
        fidelizacion = _consultar_fidelizacion(comercio_id, [cliente["clave"]])
        _adjuntar_fidelizacion([cliente], fidelizacion)
    except Exception:
        current_app.logger.exception("ERROR CONSULTANDO FIDELIZACION DEL CLIENTE")
        fidelizacion_error = "No pudimos consultar los sellos de este cliente."
    token_canje = _token_canje_fidelizacion(identificador)
    return render_template(
        "pedidos_comercio/cliente.html",
        comercio=comercio,
        cliente=cliente,
        limite_alcanzado=len(pedidos) >= CLIENTES_MAX_PEDIDOS,
        token_canje=token_canje,
        fidelizacion_error=fidelizacion_error,
    )


@pedidos_comercio_bp.post(
    "/panel/pedidos/clientes/<identificador>/canjear-premio"
)
def canjear_premio_fidelizacion(identificador):
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    cliente, _pedidos = _resolver_cliente(comercio_id, identificador)
    if not cliente:
        abort(404)
    pendientes = session.get("fidelizacion_canjes_pendientes")
    token_sesion = (
        _uuid(pendientes.get(identificador))
        if isinstance(pendientes, dict)
        else None
    )
    token_formulario = _uuid(request.form.get("idempotency_key"))
    if not token_sesion or token_formulario != token_sesion:
        return redirect(url_for(
            "pedidos_comercio.cliente_pedidos",
            identificador=identificador,
            canje_error="El intento de canje no es válido. Recargá la ficha.",
        ))
    try:
        resultado = supabase_admin.rpc(
            "canjear_premio_fidelizacion_clicklocal",
            {
                "p_comercio_id": comercio_id,
                "p_cliente_clave": cliente["clave"],
                "p_idempotency_key": token_sesion,
            },
        ).execute().data
        if not resultado:
            raise RuntimeError("La RPC de canje no devolvió un resultado.")
    except Exception:
        current_app.logger.exception("ERROR CANJEANDO PREMIO DE FIDELIZACION")
        return redirect(url_for(
            "pedidos_comercio.cliente_pedidos",
            identificador=identificador,
            canje_error="No pudimos registrar el canje. Intentá nuevamente.",
        ))
    pendientes = dict(pendientes)
    pendientes.pop(identificador, None)
    if pendientes:
        session["fidelizacion_canjes_pendientes"] = pendientes
    else:
        session.pop("fidelizacion_canjes_pendientes", None)
    return redirect(url_for(
        "pedidos_comercio.cliente_pedidos",
        identificador=identificador,
        canje_guardado="1",
    ))


@pedidos_comercio_bp.get("/panel/pedidos/metricas")
def metricas_pedidos():
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    periodo_solicitado = request.args.get("periodo", "hoy")
    if periodo_solicitado not in {"hoy", "7d", "30d"}:
        periodo_solicitado = "hoy"
    inicio, fin, periodo = _limites_periodo(periodo_solicitado)
    pedidos, items = _consulta_datos_metricas(comercio_id, inicio, fin)
    metricas = _calcular_metricas(pedidos, items, comercio_id)
    return render_template(
        "pedidos_comercio/metricas.html",
        comercio=comercio,
        metricas=metricas,
        periodo=periodo,
        limite_alcanzado=len(pedidos) >= METRICAS_MAX_PEDIDOS,
    )


@pedidos_comercio_bp.get("/panel/pedidos/<pedido_id>/imprimir")
def imprimir_pedido(pedido_id):
    comercio = _comercio_panel()
    if not comercio:
        return redirect(url_for("login"))
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        abort(403)
    filas = (
        supabase_admin.table("pedidos")
        .select("*")
        .eq("id", pedido_id)
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not filas:
        abort(404)
    pedido = filas[0]
    pedido["items"] = (
        supabase_admin.table("pedido_items")
        .select("*")
        .eq("pedido_id", pedido_id)
        .execute()
        .data
        or []
    )
    _agregar_cambio_presentacion(pedido)
    return render_template(
        "pedidos_comercio/imprimir.html",
        comercio=comercio,
        pedido=pedido,
    )


@pedidos_comercio_bp.get("/panel/pedidos/actualizacion")
def actualizacion_pedidos():
    comercio = _comercio_panel()
    if not comercio: return jsonify(ok=False, error="No autorizado."), 401
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        return jsonify(ok=False, error="Este comercio no tiene el carrito habilitado."), 403
    inicio, fin, _periodo = _limites_periodo("hoy")
    pedidos = _consulta_pedidos_periodo(comercio_id, inicio, fin, 100)
    nuevos = sum(1 for p in pedidos if p.get("estado") == "nuevo")
    ultima = max((str(p.get("created_at") or "") for p in pedidos), default="")
    return jsonify(ok=True, nuevos=nuevos, ultima=ultima)


@pedidos_comercio_bp.post("/panel/pedidos/<pedido_id>/estado")
def cambiar_estado(pedido_id):
    comercio = _comercio_panel()
    if not comercio: return jsonify(ok=False, error="No autorizado."), 401
    comercio_id = str(comercio.get("id"))
    if not modulo_activo(comercio_id, "carrito"):
        return jsonify(ok=False, error="Este comercio no tiene el carrito habilitado."), 403
    nuevo = str((request.get_json(silent=True) or {}).get("estado") or "")
    filas = (supabase_admin.table("pedidos").select("id,estado").eq("id", pedido_id)
        .eq("comercio_id", comercio_id).limit(1).execute().data or [])
    if not filas: return jsonify(ok=False, error="Pedido no encontrado."), 404
    actual = filas[0].get("estado")
    if nuevo not in TRANSICIONES.get(actual, set()):
        return jsonify(ok=False, error="El cambio de estado no es válido."), 400
    cambio = {"estado": nuevo, "updated_at": datetime.now(timezone.utc).isoformat()}
    resultado = (supabase_admin.table("pedidos").update(cambio).eq("id", pedido_id)
        .eq("comercio_id", comercio_id).eq("estado", actual).execute().data or [])
    if not resultado: return jsonify(ok=False, error="El pedido cambió; actualizá la pantalla."), 409
    return jsonify(ok=True, estado=nuevo)
