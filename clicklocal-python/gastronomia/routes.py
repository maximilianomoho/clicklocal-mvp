from flask import abort, redirect, render_template, request, session, url_for

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
