from flask import abort, jsonify, redirect, render_template, request, session, url_for

from config.supabase_config import supabase_admin

from . import gastronomia_bp


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
                "id,comercio_id,nombre,imagen_url,"
                "precio,precio_promocional,"
                "activo,disponible,orden"
            )
            .in_("comercio_id", comercio_ids)
            .eq("activo", True)
            .order("orden")
            .execute()
        )

        productos = productos_res.data or []

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

    promos_gastronomicas = []

    for producto in productos if comercio_ids else []:
        precio_promocional = producto.get("precio_promocional")

        if (
            precio_promocional is None
            or not producto.get("imagen_url")
        ):
            continue

        comercio_id = str(producto.get("comercio_id"))

        promos_gastronomicas.append({
            "id": producto.get("id"),
            "comercio_id": comercio_id,
            "comercio_nombre": nombre_comercio_por_id.get(
                comercio_id,
                ""
            ),
            "nombre": producto.get("nombre"),
            "imagen_url": producto.get("imagen_url"),
            "precio_mostrar": _formatear_precio(
                precio_promocional
            ),
            "precio_anterior_mostrar": _formatear_precio(
                producto.get("precio")
            ),
        })

    promos_gastronomicas = promos_gastronomicas[:8]

    return render_template(
        "gastronomia/inicio.html",
        comercios_gastronomicos=comercios_gastronomicos,
        promos_gastronomicas=promos_gastronomicas,
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
            "tiempo_estimado_min"
        )
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .limit(1)
        .execute()
    )

    configuraciones = config_res.data or []

    if not configuraciones:
        abort(404)

    configuracion = configuraciones[0]

    productos_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,descripcion,precio,"
            "precio_promocional,imagen_url,disponible,"
            "activo,destacado,orden"
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
                "direccion,categoria,descripcion,"
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
            "tiempo_estimado_min"
        )
        .eq("comercio_id", comercio_id)
        .limit(1)
        .execute()
    )

    configuraciones = configuracion_res.data or []

    if not configuraciones:
        abort(404)

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
            "activo,destacado,orden"
        )
        .eq("comercio_id", comercio_id)
        .order("orden", desc=True)
        .execute()
    )

    productos = productos_res.data or []

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
        es_premium=(
            str(comercio.get("plan_nombre") or "")
            .strip()
            .lower()
            == "premium"
        ),
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
    Normalización V1 para métricas internas.
    Conserva únicamente dígitos.
    """
    return "".join(
        caracter
        for caracter in str(valor or "")
        if caracter.isdigit()
    )


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

    if not telefono_normalizado:
        return jsonify({
            "ok": False,
            "error": "Ingresá tu WhatsApp."
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

    # ----------------------------------------------------------
    # Verificar comercio/configuración
    # ----------------------------------------------------------

    comercio_res = (
        supabase_admin
        .table("comercios")
        .select("id,nombre_negocio,whatsapp")
        .eq("id", comercio_id)
        .limit(1)
        .execute()
    )

    comercios = comercio_res.data or []

    if not comercios:
        return jsonify({
            "ok": False,
            "error": "Comercio no encontrado."
        }), 404

    config_res = (
        supabase_admin
        .table("gastronomia_configuracion")
        .select(
            "comercio_id,activo,acepta_delivery,"
            "acepta_retiro,costo_envio"
        )
        .eq("comercio_id", comercio_id)
        .eq("activo", True)
        .limit(1)
        .execute()
    )

    configuraciones = config_res.data or []

    if not configuraciones:
        return jsonify({
            "ok": False,
            "error": "El comercio no está recibiendo pedidos."
        }), 400

    configuracion = configuraciones[0]

    if (
        modalidad == "delivery"
        and not configuracion.get("acepta_delivery")
    ):
        return jsonify({
            "ok": False,
            "error": "El comercio no tiene Delivery habilitado."
        }), 400

    if (
        modalidad == "retiro"
        and not configuracion.get("acepta_retiro")
    ):
        return jsonify({
            "ok": False,
            "error": "El comercio no tiene Retiro habilitado."
        }), 400

    # ----------------------------------------------------------
    # Productos solicitados
    # El servidor vuelve a calcular precios desde Supabase.
    # No confía en el total enviado por el navegador.
    # ----------------------------------------------------------

    producto_ids = []

    for item in detalle_cliente:
        if not isinstance(item, dict):
            continue

        producto_id = str(
            item.get("id") or ""
        ).strip()

        if producto_id and producto_id not in producto_ids:
            producto_ids.append(producto_id)

    if not producto_ids:
        return jsonify({
            "ok": False,
            "error": "El pedido no contiene productos válidos."
        }), 400

    productos_res = (
        supabase_admin
        .table("gastronomia_productos")
        .select(
            "id,nombre,precio,precio_promocional,"
            "activo,disponible"
        )
        .eq("comercio_id", comercio_id)
        .in_("id", producto_ids)
        .execute()
    )

    productos_db = productos_res.data or []

    productos_por_id = {
        str(producto.get("id")): producto
        for producto in productos_db
        if producto.get("id")
    }

    # ----------------------------------------------------------
    # Opciones/extras solicitados
    # ----------------------------------------------------------

    opcion_ids = []

    for item in detalle_cliente:
        if not isinstance(item, dict):
            continue

        for opcion in item.get("opciones") or []:
            if not isinstance(opcion, dict):
                continue

            opcion_id = str(
                opcion.get("id") or ""
            ).strip()

            if opcion_id and opcion_id not in opcion_ids:
                opcion_ids.append(opcion_id)

    opciones_por_id = {}
    producto_por_grupo = {}

    if opcion_ids:

        opciones_res = (
            supabase_admin
            .table("gastronomia_opciones")
            .select(
                "id,grupo_id,nombre,precio_extra,"
                "activo,disponible"
            )
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
                supabase_admin
                .table("gastronomia_grupos_opciones")
                .select("id,producto_id,activo")
                .in_("id", grupo_ids)
                .execute()
            )

            grupos_db = grupos_res.data or []

            producto_por_grupo = {
                str(grupo.get("id")):
                    str(grupo.get("producto_id"))
                for grupo in grupos_db
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

    # ----------------------------------------------------------
    # Construir detalle canónico y subtotal
    # ----------------------------------------------------------

    detalle_final = []
    subtotal = 0.0

    for item in detalle_cliente:

        if not isinstance(item, dict):
            continue

        producto_id = str(
            item.get("id") or ""
        ).strip()

        producto = productos_por_id.get(producto_id)

        if not producto:
            return jsonify({
                "ok": False,
                "error": "Uno de los productos ya no está disponible."
            }), 400

        if (
            producto.get("activo") is False
            or producto.get("disponible") is False
        ):
            return jsonify({
                "ok": False,
                "error": (
                    f'{producto.get("nombre") or "Un producto"} '
                    "ya no está disponible."
                )
            }), 400

        try:
            cantidad = int(item.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0

        if cantidad <= 0 or cantidad > 99:
            return jsonify({
                "ok": False,
                "error": "Cantidad de producto inválida."
            }), 400

        precio_promocional = producto.get(
            "precio_promocional"
        )

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

            opcion_id = str(
                opcion_cliente.get("id") or ""
            ).strip()

            opcion = opciones_por_id.get(opcion_id)

            if not opcion:
                return jsonify({
                    "ok": False,
                    "error": "Una opción del producto ya no está disponible."
                }), 400

            grupo_id = str(
                opcion.get("grupo_id") or ""
            )

            if producto_por_grupo.get(grupo_id) != producto_id:
                return jsonify({
                    "ok": False,
                    "error": "Una opción no corresponde al producto."
                }), 400

            try:
                precio_extra = float(
                    opcion.get("precio_extra") or 0
                )
            except (TypeError, ValueError):
                precio_extra = 0.0

            precio_unitario += precio_extra

            opciones_finales.append({
                "id": opcion_id,
                "nombre": str(
                    opcion.get("nombre") or ""
                ),
                "precio": precio_extra,
            })

        nota = str(
            item.get("nota") or ""
        ).strip()[:180]

        subtotal_item = precio_unitario * cantidad
        subtotal += subtotal_item

        detalle_final.append({
            "id": producto_id,
            "nombre": str(
                producto.get("nombre") or ""
            ),
            "cantidad": cantidad,
            "precio_unitario": precio_unitario,
            "subtotal": subtotal_item,
            "opciones": opciones_finales,
            "nota": nota,
        })

    subtotal = round(subtotal, 2)

    # Por ahora mantenemos el comportamiento visual actual:
    # el envío no se suma automáticamente al carrito.
    costo_envio = 0.0
    descuento = 0.0
    total = subtotal

    # ----------------------------------------------------------
    # Efectivo
    # ----------------------------------------------------------

    paga_con = None

    if forma_pago == "efectivo":

        try:
            paga_con = float(
                payload.get("paga_con") or 0
            )
        except (TypeError, ValueError):
            paga_con = 0

        if paga_con < total:
            return jsonify({
                "ok": False,
                "error": (
                    "El importe con el que pagás "
                    "es menor al total."
                )
            }), 400

    # ----------------------------------------------------------
    # Texto base del pedido
    # ----------------------------------------------------------

    lineas = [
        f'Pedido para {comercios[0].get("nombre_negocio") or ""}',
        "",
        f"Cliente: {nombre} {apellido}",
        f"WhatsApp: {telefono}",
        "",
    ]

    def pesos(valor):
        return "$" + f"{round(float(valor)):,.0f}".replace(",", ".")

    for item in detalle_final:

        lineas.append(
            f'{item["cantidad"]}x '
            f'{item["nombre"]} - '
            f'{pesos(item["subtotal"])}'
        )

        for opcion in item["opciones"]:
            linea_opcion = "  + " + opcion["nombre"]

            if opcion["precio"] > 0:
                linea_opcion += (
                    " (" +
                    pesos(opcion["precio"]) +
                    ")"
                )

            lineas.append(linea_opcion)

        if item["nota"]:
            lineas.append(
                "  Aclaración: " + item["nota"]
            )

        lineas.append("")

    lineas.append(
        "Total: " + pesos(total)
    )

    lineas.append("")
    lineas.append(
        "Modalidad: " +
        (
            "Delivery"
            if modalidad == "delivery"
            else "Retiro"
        )
    )

    if modalidad == "delivery":
        lineas.append(
            "Dirección: " + direccion
        )

    lineas.append(
        "Forma de pago: " +
        (
            "Efectivo"
            if forma_pago == "efectivo"
            else "Transferencia"
        )
    )

    if forma_pago == "efectivo":
        lineas.append(
            "Paga con: " + pesos(paga_con)
        )

        lineas.append(
            "Cambio aproximado: " +
            pesos(paga_con - total)
        )

    if observaciones:
        lineas.append("")
        lineas.append(
            "Aclaración general: " +
            observaciones
        )

    texto_pedido = "\n".join(lineas)

    # ----------------------------------------------------------
    # Guardar pedido
    # ----------------------------------------------------------

    datos_pedido = {
        "numero_pedido": 0,
        "comercio_id": comercio_id,
        "nombre_cliente": nombre,
        "apellido_cliente": apellido,
        "telefono_cliente": telefono,
        "telefono_normalizado": telefono_normalizado,
        "tipo_entrega": modalidad,
        "direccion_entrega": (
            direccion
            if modalidad == "delivery"
            else None
        ),
        "forma_pago": forma_pago,
        "paga_con": paga_con,
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
        pedido_res = (
            supabase_admin
            .table("gastronomia_pedidos")
            .insert(datos_pedido)
            .execute()
        )
    except Exception as error:
        print(
            "ERROR REGISTRANDO PEDIDO GASTRONOMICO:",
            type(error),
            error,
            flush=True
        )

        return jsonify({
            "ok": False,
            "error": "No se pudo registrar el pedido."
        }), 500

    pedidos = pedido_res.data or []

    if not pedidos:
        return jsonify({
            "ok": False,
            "error": "No se pudo confirmar el pedido."
        }), 500

    pedido = pedidos[0]

    return jsonify({
        "ok": True,
        "pedido_id": pedido.get("id"),
        "numero_pedido": pedido.get("numero_pedido"),
        "created_at": pedido.get("created_at"),
        "texto_pedido": texto_pedido,
        "whatsapp_comercio": (
            comercios[0].get("whatsapp") or ""
        ),
    })

