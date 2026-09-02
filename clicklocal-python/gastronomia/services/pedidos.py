from config.supabase_config import supabase_admin


class PedidoError(Exception):
    def __init__(self, mensaje, status_code=400):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_code = status_code


def _pesos(valor):
    return "$" + f"{round(float(valor)):,.0f}".replace(",", ".")


def generar_texto_whatsapp(
    comercio,
    nombre,
    apellido,
    telefono,
    modalidad,
    direccion,
    forma_pago,
    paga_con,
    observaciones,
    detalle,
    subtotal,
    costo_envio,
    descuento,
    descuento_pct_aplicado,
    total,
):
    lineas = [
        f'Pedido para {comercio.get("nombre_negocio") or ""}',
        "",
        f"Cliente: {nombre} {apellido}",
        f"WhatsApp: {telefono}",
        "",
    ]

    for item in detalle:
        lineas.append(
            f'{item["cantidad"]}x {item["nombre"]} - '
            f'{_pesos(item["subtotal"])}'
        )

        for opcion in item["opciones"]:
            linea_opcion = "  + " + opcion["nombre"]
            if opcion["precio"] > 0:
                linea_opcion += " (" + _pesos(opcion["precio"]) + ")"
            lineas.append(linea_opcion)

        if item["nota"]:
            lineas.append("  Aclaración: " + item["nota"])
        lineas.append("")

    lineas.append("Productos: " + _pesos(subtotal))

    if descuento > 0:
        medio_descuento = (
            "efectivo" if forma_pago == "efectivo" else "transferencia"
        )
        lineas.append(
            "Descuento "
            + medio_descuento
            + " ("
            + f"{descuento_pct_aplicado:g}"
            + "%): -"
            + _pesos(descuento)
        )

    if modalidad == "delivery":
        lineas.append(
            "Envío: "
            + (_pesos(costo_envio) if costo_envio > 0 else "Gratis")
        )

    lineas.extend([
        "Total: " + _pesos(total),
        "",
        "Modalidad: " + ("Delivery" if modalidad == "delivery" else "Retiro"),
    ])

    if modalidad == "delivery":
        lineas.append("Dirección: " + direccion)

    lineas.append(
        "Forma de pago: "
        + ("Efectivo" if forma_pago == "efectivo" else "Transferencia")
    )

    if forma_pago == "efectivo":
        lineas.append("Paga con: " + _pesos(paga_con))
        lineas.append("Cambio aproximado: " + _pesos(paga_con - total))

    if observaciones:
        lineas.extend(["", "Aclaración general: " + observaciones])

    return "\n".join(lineas)


def crear_pedido(
    comercio_id,
    nombre,
    apellido,
    telefono,
    telefono_normalizado,
    modalidad,
    direccion,
    forma_pago,
    paga_con,
    observaciones,
    items,
    visitante_id=None,
    sesion_id=None,
    cliente_supabase=None,
):
    """Crea el pedido con las mismas reglas del endpoint publico actual."""
    db = cliente_supabase or supabase_admin

    comercio_res = (
        db.table("comercios")
        .select("id,nombre_negocio,whatsapp")
        .eq("id", comercio_id)
        .limit(1)
        .execute()
    )
    comercios = comercio_res.data or []
    if not comercios:
        raise PedidoError("Comercio no encontrado.", 404)
    comercio = comercios[0]

    config_res = (
        db.table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,acepta_retiro,"
            "pedido_minimo,costo_envio,descuento_efectivo_pct,"
            "descuento_transferencia_pct"
        )
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .limit(1)
        .execute()
    )
    configuraciones = config_res.data or []
    if not configuraciones:
        raise PedidoError("El comercio no está recibiendo pedidos.")
    configuracion = configuraciones[0]

    if modalidad == "delivery" and not configuracion.get("acepta_delivery"):
        raise PedidoError("El comercio no tiene Delivery habilitado.")
    if modalidad == "retiro" and not configuracion.get("acepta_retiro"):
        raise PedidoError("El comercio no tiene Retiro habilitado.")

    producto_ids = []
    for item in items:
        if not isinstance(item, dict):
            continue
        producto_id = str(item.get("id") or "").strip()
        if producto_id and producto_id not in producto_ids:
            producto_ids.append(producto_id)

    if not producto_ids:
        raise PedidoError("El pedido no contiene productos válidos.")

    productos_res = (
        db.table("gastronomia_productos")
        .select("id,nombre,precio,precio_promocional,activo,disponible")
        .eq("comercio_id", comercio_id)
        .in_("id", producto_ids)
        .execute()
    )
    productos_por_id = {
        str(producto.get("id")): producto
        for producto in (productos_res.data or [])
        if producto.get("id")
    }

    opcion_ids = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for opcion in item.get("opciones") or []:
            if not isinstance(opcion, dict):
                continue
            opcion_id = str(opcion.get("id") or "").strip()
            if opcion_id and opcion_id not in opcion_ids:
                opcion_ids.append(opcion_id)

    opciones_por_id = {}
    producto_por_grupo = {}
    if opcion_ids:
        opciones_res = (
            db.table("gastronomia_opciones")
            .select("id,grupo_id,nombre,precio_extra,activo,disponible")
            .in_("id", opcion_ids)
            .execute()
        )
        opciones_db = opciones_res.data or []
        grupo_ids = list({
            str(opcion.get("grupo_id"))
            for opcion in opciones_db
            if opcion.get("grupo_id")
        })
        if grupo_ids:
            grupos_res = (
                db.table("gastronomia_grupos_opciones")
                .select("id,producto_id,activo")
                .in_("id", grupo_ids)
                .execute()
            )
            producto_por_grupo = {
                str(grupo.get("id")): str(grupo.get("producto_id"))
                for grupo in (grupos_res.data or [])
                if (
                    grupo.get("id")
                    and grupo.get("producto_id")
                    and grupo.get("activo") is not False
                )
            }
        opciones_por_id = {
            str(opcion.get("id")): opcion
            for opcion in opciones_db
            if (
                opcion.get("id")
                and opcion.get("activo") is not False
                and opcion.get("disponible") is not False
            )
        }

    detalle_final = []
    subtotal = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        producto_id = str(item.get("id") or "").strip()
        producto = productos_por_id.get(producto_id)
        if not producto:
            raise PedidoError("Uno de los productos ya no está disponible.")
        if (
            producto.get("activo") is False
            or producto.get("disponible") is False
        ):
            raise PedidoError(
                f'{producto.get("nombre") or "Un producto"} '
                "ya no está disponible."
            )

        try:
            cantidad = int(item.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0
        if cantidad <= 0 or cantidad > 99:
            raise PedidoError("Cantidad de producto inválida.")

        precio_promocional = producto.get("precio_promocional")
        precio_base = (
            precio_promocional
            if precio_promocional is not None
            else producto.get("precio")
        )
        try:
            precio_unitario = float(precio_base or 0)
        except (TypeError, ValueError):
            precio_unitario = 0.0

        opciones_finales = []
        for opcion_cliente in item.get("opciones") or []:
            if not isinstance(opcion_cliente, dict):
                continue
            opcion_id = str(opcion_cliente.get("id") or "").strip()
            opcion = opciones_por_id.get(opcion_id)
            if not opcion:
                raise PedidoError("Una opción del producto ya no está disponible.")
            grupo_id = str(opcion.get("grupo_id") or "")
            if producto_por_grupo.get(grupo_id) != producto_id:
                raise PedidoError("Una opción no corresponde al producto.")
            try:
                precio_extra = float(opcion.get("precio_extra") or 0)
            except (TypeError, ValueError):
                precio_extra = 0.0
            precio_unitario += precio_extra
            opciones_finales.append({
                "id": opcion_id,
                "nombre": str(opcion.get("nombre") or ""),
                "precio": precio_extra,
            })

        nota = str(item.get("nota") or "").strip()[:180]
        subtotal_item = precio_unitario * cantidad
        subtotal += subtotal_item
        detalle_final.append({
            "id": producto_id,
            "nombre": str(producto.get("nombre") or ""),
            "cantidad": cantidad,
            "precio_unitario": precio_unitario,
            "subtotal": subtotal_item,
            "opciones": opciones_finales,
            "nota": nota,
        })

    subtotal = round(subtotal, 2)
    try:
        pedido_minimo = float(configuracion.get("pedido_minimo") or 0)
    except (TypeError, ValueError):
        pedido_minimo = 0.0
    pedido_minimo = round(pedido_minimo, 2)
    if pedido_minimo > 0 and subtotal < pedido_minimo:
        raise PedidoError(
            "El pedido mínimo de este comercio es "
            "$" + f"{round(pedido_minimo):,.0f}".replace(",", ".") + "."
        )

    costo_envio = 0.0
    if modalidad == "delivery":
        try:
            costo_envio = float(configuracion.get("costo_envio") or 0)
        except (TypeError, ValueError):
            costo_envio = 0.0
        costo_envio = max(round(costo_envio, 2), 0.0)

    descuento_pct_aplicado = 0.0
    try:
        if forma_pago == "efectivo":
            descuento_pct_aplicado = float(
                configuracion.get("descuento_efectivo_pct") or 0
            )
        elif forma_pago == "transferencia":
            descuento_pct_aplicado = float(
                configuracion.get("descuento_transferencia_pct") or 0
            )
    except (TypeError, ValueError):
        descuento_pct_aplicado = 0.0
    descuento_pct_aplicado = min(max(descuento_pct_aplicado, 0.0), 100.0)
    descuento = round(subtotal * descuento_pct_aplicado / 100, 2)
    total = round(subtotal + costo_envio - descuento, 2)

    paga_con_final = None
    if forma_pago == "efectivo":
        try:
            paga_con_final = float(paga_con or 0)
        except (TypeError, ValueError):
            paga_con_final = 0
        if paga_con_final < total:
            raise PedidoError("El importe con el que pagás es menor al total.")

    texto_pedido = generar_texto_whatsapp(
        comercio, nombre, apellido, telefono, modalidad, direccion,
        forma_pago, paga_con_final, observaciones, detalle_final, subtotal,
        costo_envio, descuento, descuento_pct_aplicado, total,
    )

    datos_pedido = {
        "numero_pedido": 0,
        "comercio_id": comercio_id,
        "visitante_id": visitante_id,
        "sesion_id": sesion_id,
        "nombre_cliente": nombre,
        "apellido_cliente": apellido,
        "telefono_cliente": telefono,
        "telefono_normalizado": telefono_normalizado,
        "tipo_entrega": modalidad,
        "direccion_entrega": direccion if modalidad == "delivery" else None,
        "forma_pago": forma_pago,
        "paga_con": paga_con_final,
        "subtotal": subtotal,
        "costo_envio": costo_envio,
        "descuento": descuento,
        "total": total,
        "observaciones": observaciones or None,
        "detalle": detalle_final,
        "texto_pedido": texto_pedido,
        "estado": "recibido",
    }

    try:
        pedido_res = db.table("gastronomia_pedidos").insert(datos_pedido).execute()
    except Exception as error:
        print(
            "ERROR REGISTRANDO PEDIDO GASTRONOMICO:",
            type(error),
            error,
            flush=True,
        )
        raise PedidoError("No se pudo registrar el pedido.", 500) from error

    pedidos = pedido_res.data or []
    if not pedidos:
        raise PedidoError("No se pudo confirmar el pedido.", 500)
    pedido = pedidos[0]
    return {
        "id": pedido.get("id"),
        "numero_pedido": pedido.get("numero_pedido"),
        "created_at": pedido.get("created_at"),
        "detalle": detalle_final,
        "subtotal": subtotal,
        "costo_envio": costo_envio,
        "descuento": descuento,
        "total": total,
        "texto_pedido": texto_pedido,
        "whatsapp_comercio": comercio.get("whatsapp") or "",
    }
