from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import abort, g, jsonify, redirect, render_template, request, session, url_for

from config.supabase_config import supabase_admin

from . import gastronomia_bp
from .services.pedidos import PedidoError, crear_pedido


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


def _es_premium_gastronomia(comercio):
    return (
        str((comercio or {}).get("plan") or "gratis")
        .strip()
        .lower()
        == "premium"
    )


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
                "logo_url,direccion,whatsapp,plan"
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

    comercios_premium_ids = {
        str(comercio.get("id"))
        for comercio in comercios_gastronomicos
        if str(
            comercio.get("plan") or "gratis"
        ).strip().lower() == "premium"
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
            comercio_id in comercios_premium_ids
            and producto.get("destacado")
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
            comercio_id in comercios_premium_ids
            and precio_promocional is not None
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
            "id,nombre_negocio,whatsapp,direccion,"
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
            "descuento_transferencia_pct"
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

    productos_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,descripcion,precio,"
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

        opciones_por_grupo.setdefault(
            grupo_id,
            []
        ).append(opcion)

    grupos_por_producto = {}

    for grupo in grupos:
        grupo_id = grupo.get("id")

        grupo["opciones"] = opciones_por_grupo.get(
            grupo_id,
            []
        )

        grupos_por_producto.setdefault(
            grupo.get("producto_id"),
            []
        ).append(grupo)

    for producto in productos:
        precio = producto.get("precio")
        precio_promocional = producto.get(
            "precio_promocional"
        )

        producto["precio_mostrar"] = _formatear_precio(
            precio
        )

        producto["precio_promocional_mostrar"] = (
            _formatear_precio(precio_promocional)
            if precio_promocional is not None
            else ""
        )

        producto["grupos_opciones"] = (
            grupos_por_producto.get(
                producto.get("id"),
                []
            )
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
                "direccion,categoria,descripcion,"
                "logo_url,activo,plan,estado_plan,"
                "fecha_inicio_plan,fecha_vencimiento_plan,"
                "solicitud_premium,"
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
                "direccion,categoria,descripcion,"
                "logo_url,activo,plan,estado_plan,"
                "fecha_inicio_plan,fecha_vencimiento_plan,"
                "solicitud_premium,"
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

    # Si se intenta ACTIVAR un producto, respetar el límite Gratis.
    if nuevo_estado:
        plan_actual = str(
            comercio.get("plan") or "gratis"
        ).strip().lower()

        if plan_actual != "premium":
            activos_res = (
                supabase_admin
                .table("gastronomia_productos")
                .select("id")
                .eq("comercio_id", comercio_id)
                .eq("activo", True)
                .execute()
            )

            cantidad_activos = len(activos_res.data or [])

            if cantidad_activos >= 10:
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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="destacados"
            )
            + "#mis-productos"
        )

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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="destacados"
            )
            + "#mis-productos"
        )

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
        precio = float(
            precio_raw
            .replace("$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )

        if precio < 0:
            raise ValueError

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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="promociones"
            )
            + "#mis-productos"
        )

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
        precio_promocional = float(
            precio_raw
            .replace("$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )
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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="promociones"
            )
            + "#mis-productos"
        )

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


@gastronomia_bp.route("/panel", methods=["GET", "POST"])
def panel_gastronomia():
    comercio = _comercio_panel_gastronomia()

    if not comercio:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id")

    configuracion_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,"
            "acepta_retiro,pedido_minimo,costo_envio,"
            "tiempo_estimado_min,descuento_efectivo_pct,"
            "descuento_transferencia_pct"
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

    productos_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,descripcion,precio,"
            "precio_promocional,imagen_url,disponible,"
            "activo,destacado,destacado_hasta,"
            "promocion_desde,promocion_hasta,orden"
        )
        .eq("comercio_id", comercio_id)
        .order("orden", desc=True)
        .execute()
    )

    productos = productos_res.data or []

    # --------------------------------------------------------
    # PLAN GASTRONOMIA
    # Gratis: máximo 10 productos activos.
    # Premium: sin este límite reducido.
    # --------------------------------------------------------
    plan_actual = str(
        comercio.get("plan") or "gratis"
    ).strip().lower()

    if plan_actual != "premium":
        plan_actual = "gratis"

    comercio["plan_actual"] = plan_actual
    comercio["plan_nombre"] = (
        "Premium"
        if plan_actual == "premium"
        else "Gratis"
    )

    comercio["fecha_vencimiento_plan_mostrar"] = None
    comercio["dias_restantes_plan"] = None

    if (
        plan_actual == "premium"
        and comercio.get("fecha_vencimiento_plan")
    ):
        try:
            vencimiento_plan = date.fromisoformat(
                str(
                    comercio.get(
                        "fecha_vencimiento_plan"
                    )
                )[:10]
            )

            hoy_plan = date.today()

            comercio[
                "fecha_vencimiento_plan_mostrar"
            ] = vencimiento_plan.strftime(
                "%d/%m/%Y"
            )

            comercio["dias_restantes_plan"] = max(
                (vencimiento_plan - hoy_plan).days,
                0,
            )

        except (TypeError, ValueError):
            comercio[
                "fecha_vencimiento_plan_mostrar"
            ] = comercio.get(
                "fecha_vencimiento_plan"
            )

    es_premium = plan_actual == "premium"

    limite_productos_gratis = 10

    cantidad_productos_activos = sum(
        1
        for producto in productos
        if bool(producto.get("activo"))
    )

    for producto in productos:
        producto["precio_mostrar"] = _formatear_precio(
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

    error_producto = ""

    if request.args.get("limite_productos") == "1":
        error_producto = (
            "El plan Gratis permite hasta 10 productos activos. "
            "Pausá otro producto o pasá a Gastronomía Premium."
        )

    if request.method == "POST":
        nombre = str(
            request.form.get("nombre") or ""
        ).strip()

        descripcion = str(
            request.form.get("descripcion") or ""
        ).strip()

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
                precio = float(
                    precio_raw
                    .replace("$", "")
                    .replace(" ", "")
                    .replace(".", "")
                    .replace(",", ".")
                )

                if precio < 0:
                    raise ValueError

            except (TypeError, ValueError):
                error_producto = (
                    "El precio ingresado no es válido."
                )

        if (
            not error_producto
            and not es_premium
            and cantidad_productos_activos >= limite_productos_gratis
        ):
            error_producto = (
                "El plan Gratis permite hasta 10 productos activos. "
                "Pausá otro producto o pasá a Gastronomía Premium."
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

    if es_premium:
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

            pedidos_metricas_res = (
                supabase_admin
                .table("gastronomia_pedidos")
                .select(
                    "id,created_at,total,detalle,"
                    "visitante_id,sesion_id,"
                    "telefono_normalizado,"
                    "nombre_cliente,apellido_cliente,"
                    "direccion_entrega"
                )
                .eq("comercio_id", comercio_id)
                .gte(
                    "created_at",
                    desde_consulta
                )
                .execute()
            )

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

    listas_buscables = []

    try:
        listas_res = (
            supabase_admin
            .table("listas_buscables")
            .select("*")
            .eq("comercio_id", comercio_id)
            .order("created_at", desc=True)
            .execute()
        )

        listas_buscables = listas_res.data or []

    except Exception as error:
        print(
            "ERROR LEYENDO LISTAS EN GASTRONOMIA:",
            type(error),
            error,
            flush=True
        )

    return render_template(
        "gastronomia/panel.html",
        comercio=comercio,
        configuracion=configuracion,
        productos=productos,
        error_producto=error_producto,
        listas_buscables=listas_buscables,
        es_cine_teatro=False,
        es_premium=es_premium,
        plan_nombre=("Premium" if es_premium else "Gratis"),
        limite_productos_gratis=limite_productos_gratis,
        cantidad_productos_activos=cantidad_productos_activos,
        metricas_gastronomia=metricas_gastronomia,
        metricas_por_producto=metricas_por_producto,
        periodo_metricas=periodo_metricas,
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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="extras"
            )
            + "#mis-productos"
        )

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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="extras"
            )
            + "#mis-productos"
        )

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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="extras"
            )
            + "#mis-productos"
        )

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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="extras"
            )
            + "#mis-productos"
        )

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

    if not _es_premium_gastronomia(comercio):
        return redirect(
            url_for(
                "gastronomia.panel_gastronomia",
                premium_bloqueado="extras"
            )
            + "#mis-productos"
        )

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
    "/comercio/<comercio_id>/pedido",
    methods=["POST"]
)
def registrar_pedido(comercio_id):

    payload = request.get_json(silent=True) or {}

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
        )
    except PedidoError as error:
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
    })
