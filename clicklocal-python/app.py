from flask import Flask, render_template, send_from_directory, request, redirect, url_for, session
from werkzeug.utils import secure_filename
import os
from decimal import Decimal, InvalidOperation
import uuid
from PIL import Image, ImageOps
from io import BytesIO
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    print("ClickLocal fotos: soporte HEIC/HEIF activo", flush=True)
except Exception as e:
    print("ClickLocal fotos: soporte HEIC/HEIF no disponible:", e, flush=True)

import json
import datetime
from config.supabase_config import supabase_auth, supabase_admin


def normalizar_precio(valor):
    """
    Acepta precios escritos por humanos:
    3500, 3.500, $3500, $ 3.500, 3500,50, $ 3.500,50.
    Devuelve int/float limpio para guardar en Supabase, o None.
    """
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    texto = (
        texto.replace("$", "")
        .replace("ARS", "")
        .replace("ars", "")
        .replace(" ", "")
        .strip()
    )

    limpio = "".join(ch for ch in texto if ch.isdigit() or ch in ".,")
    if not limpio or not any(ch.isdigit() for ch in limpio):
        return None

    # Si tiene punto y coma, el último separador se toma como decimal.
    if "," in limpio and "." in limpio:
        ultima_coma = limpio.rfind(",")
        ultimo_punto = limpio.rfind(".")

        if ultima_coma > ultimo_punto:
            entero = limpio[:ultima_coma].replace(".", "").replace(",", "")
            decimal = limpio[ultima_coma + 1:]
        else:
            entero = limpio[:ultimo_punto].replace(".", "").replace(",", "")
            decimal = limpio[ultimo_punto + 1:]

        numero_texto = f"{entero}.{decimal}" if decimal else entero

    elif "," in limpio:
        partes = limpio.split(",")

        # En Argentina, la coma suele ser decimal: 3500,50
        if len(partes) == 2 and 1 <= len(partes[1]) <= 2:
            entero = partes[0].replace(".", "").replace(",", "")
            decimal = partes[1]
            numero_texto = f"{entero}.{decimal}"
        else:
            numero_texto = limpio.replace(",", "").replace(".", "")

    elif "." in limpio:
        partes = limpio.split(".")

        # 42.000 => miles. 3500.50 => decimal.
        if len(partes) == 2 and 1 <= len(partes[1]) <= 2 and len(partes[0]) > 1:
            numero_texto = f"{partes[0].replace(',', '')}.{partes[1]}"
        else:
            numero_texto = limpio.replace(".", "").replace(",", "")

    else:
        numero_texto = limpio

    try:
        numero = Decimal(numero_texto)
    except InvalidOperation:
        return None

    if numero < 0:
        return None

    if numero == numero.to_integral_value():
        return int(numero)

    return float(numero.quantize(Decimal("0.01")))


def formatear_precio(valor):
    """
    Muestra el precio con formato argentino.
    Ej: 3500 -> $ 3.500
    Ej: 3500.5 -> $ 3.500,50
    """
    if valor is None or valor == "":
        return "Consultar precio"

    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        return "Consultar precio"

    if numero == numero.to_integral_value():
        entero = int(numero)
        return "$ " + f"{entero:,}".replace(",", ".")

    entero, decimal = f"{numero:.2f}".split(".")
    entero_formateado = f"{int(entero):,}".replace(",", ".")
    return f"$ {entero_formateado},{decimal}"


app = Flask(__name__)
app.jinja_env.filters["precio_arg"] = formatear_precio


# Clave temporal para session en desarrollo local
app.secret_key = os.getenv("FLASK_SECRET_KEY", "clicklocal-mvp-dev")

# Carpeta donde guardamos fotos subidas en esta etapa local
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/sw.js")
def service_worker():
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")



def procesar_imagen_clicklocal(f, contexto="foto"):
    """
    Motor central de procesamiento de fotos de ClickLocal.
    Recibe una imagen de celular/cámara y devuelve un JPG optimizado.
    """
    nombre = getattr(f, "filename", "") or ""
    content_type = getattr(f, "content_type", "") or ""
    mimetype = getattr(f, "mimetype", "") or ""

    try:
        f.stream.seek(0)
        img = Image.open(f.stream)
    except Exception as e_stream:
        try:
            img = Image.open(f)
        except Exception as e_file:
            print("\nERROR ABRIENDO FOTO CLICKLOCAL:", flush=True)
            print(f"contexto={contexto}", flush=True)
            print(f"archivo={nombre} content_type={content_type} mimetype={mimetype}", flush=True)
            print("stream:", type(e_stream), e_stream, flush=True)
            print("file:", type(e_file), e_file, flush=True)
            raise

    try:
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        print("AVISO FOTO CLICKLOCAL: no se pudo corregir EXIF:", e, flush=True)

    formato_original = getattr(img, "format", "") or ""
    modo_original = getattr(img, "mode", "") or ""
    ancho_original = getattr(img, "width", 0)
    alto_original = getattr(img, "height", 0)

    if img.mode != "RGB":
        img = img.convert("RGB")

    max_width = 1200
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80, optimize=True)
    buf.seek(0)

    print(
        f"FOTO CLICKLOCAL OK | contexto={contexto} | archivo={nombre} | "
        f"tipo={content_type} | formato={formato_original} | modo={modo_original} | "
        f"original={ancho_original}x{alto_original} | final={img.width}x{img.height} | "
        f"bytes_final={len(buf.getvalue())}",
        flush=True
    )

    return buf


def comercio_default():
    return {
        "nombre_negocio": "Deck Bazar",
        "email": "deckbazar@test.com",
        "whatsapp": "3430000000",
        "direccion": "",
        "direccion_mostrar": "",
        "venta_online": False,
        "ciudad": "Paraná",
        "categoria": "Hogar",
        "descripcion": "Bazar, regalos, mates y productos para el hogar.",
        "plan": "gratis",
    }




# ============================================================
# ANALYTICS MVP
# ============================================================

def normalizar_texto_analytics(valor):
    import unicodedata

    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
    return " ".join(texto.split())


def uuid_o_none(valor):
    if not valor:
        return None

    try:
        from uuid import UUID
        return str(UUID(str(valor)))
    except (ValueError, TypeError, AttributeError):
        return None


def limpiar_numero_whatsapp(numero_raw):
    """
    Normaliza números para WhatsApp Argentina.

    Casos esperados:
    - 3434150049      -> 5493434150049
    - 03434150049     -> 5493434150049
    - 543434150049    -> 5493434150049
    - 5493434150049   -> 5493434150049

    Nota: para celulares argentinos WhatsApp requiere 54 + 9 + característica + número.
    """
    numero = "".join(ch for ch in str(numero_raw or "") if ch.isdigit())

    if numero.startswith("00"):
        numero = numero[2:]

    while numero.startswith("0"):
        numero = numero[1:]

    if not numero:
        return ""

    if numero.startswith("549"):
        return numero

    if numero.startswith("54"):
        resto = numero[2:]
        if resto.startswith("9"):
            return numero
        return f"549{resto}"

    return f"549{numero}"


def construir_url_whatsapp(numero_raw, mensaje):
    from urllib.parse import quote

    numero = limpiar_numero_whatsapp(numero_raw)

    if not numero:
        return ""

    return f"https://wa.me/{numero}?text={quote(mensaje)}"


def analytics_crear_busqueda(
    consulta,
    total_publicaciones=0,
    total_listas=0,
    ciudad="Paraná",
    origen="buscador_home"
):
    consulta_limpia = str(consulta or "").strip()

    if not consulta_limpia:
        return None

    total_publicaciones = int(total_publicaciones or 0)
    total_listas = int(total_listas or 0)
    total_resultados = total_publicaciones + total_listas

    nueva_busqueda = {
        "consulta": consulta_limpia,
        "consulta_normalizada": normalizar_texto_analytics(consulta_limpia),
        "ciudad": ciudad,
        "origen": origen,
        "total_resultados_publicaciones": total_publicaciones,
        "total_resultados_listas": total_listas,
        "total_resultados": total_resultados,
        "tuvo_resultados": total_resultados > 0,
    }

    try:
        res = supabase_admin.table("busquedas_publicas").insert(nueva_busqueda).execute()
        filas = res.data or []

        if filas and filas[0].get("id"):
            return filas[0].get("id")

    except Exception as e:
        print("ERROR analytics_crear_busqueda:", e, flush=True)

    return None


def analytics_registrar_evento(
    tipo_evento,
    comercio_id=None,
    publicacion_id=None,
    lista_buscable_id=None,
    busqueda_id=None,
    consulta_origen=None,
    origen="web",
    metadata=None
):
    if not tipo_evento:
        return False

    evento = {
        "tipo_evento": tipo_evento,
        "comercio_id": uuid_o_none(comercio_id),
        "publicacion_id": uuid_o_none(publicacion_id),
        "lista_buscable_id": uuid_o_none(lista_buscable_id),
        "busqueda_id": uuid_o_none(busqueda_id),
        "consulta_origen": str(consulta_origen or "").strip() or None,
        "consulta_normalizada": normalizar_texto_analytics(consulta_origen),
        "origen": origen,
        "metadata": metadata or {},
    }

    try:
        supabase_admin.table("eventos_analytics").insert(evento).execute()
        return True

    except Exception as e:
        print("ERROR analytics_registrar_evento:", e, flush=True)
        return False


@app.route("/analytics/whatsapp/<comercio_id>")
def analytics_whatsapp(comercio_id):
    comercio_id = uuid_o_none(comercio_id)

    if not comercio_id:
        return redirect(url_for("inicio"))

    ultima_busqueda = session.get("ultima_busqueda_publica") or {}

    publicacion_id = uuid_o_none(request.args.get("publicacion_id"))
    lista_buscable_id = uuid_o_none(request.args.get("lista_buscable_id"))
    busqueda_id = uuid_o_none(request.args.get("busqueda_id") or ultima_busqueda.get("busqueda_id"))

    consulta_origen = (
        request.args.get("consulta")
        or ultima_busqueda.get("consulta")
        or ""
    ).strip()

    origen = (request.args.get("origen") or "click_whatsapp").strip()

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,nombre_negocio,whatsapp,activo")
            .eq("id", comercio_id)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return redirect(url_for("inicio"))

        comercio = comercios[0]

        if comercio.get("activo") is False:
            return redirect(url_for("inicio"))

        nombre_negocio = comercio.get("nombre_negocio") or "Comercio local"

        analytics_registrar_evento(
            "click_whatsapp",
            comercio_id=comercio_id,
            publicacion_id=publicacion_id,
            lista_buscable_id=lista_buscable_id,
            busqueda_id=busqueda_id,
            consulta_origen=consulta_origen,
            origen=origen,
            metadata={
                "nombre_negocio": nombre_negocio,
            }
        )

        mensaje = (
            "Hola, vengo de ClickLocal Paraná. "
            f"Quiero consultar por: {consulta_origen or nombre_negocio}."
        )

        whatsapp_final = construir_url_whatsapp(comercio.get("whatsapp"), mensaje)

        if whatsapp_final:
            return redirect(whatsapp_final)

    except Exception as e:
        print("ERROR analytics_whatsapp:", e, flush=True)

    return redirect(url_for("inicio"))


# INICIO / PLATAFORMA
@app.route("/")
@app.route("/index.html")
def inicio():
    from urllib.parse import quote
    import unicodedata

    comercio = session.get("comercio") or comercio_default()
    publicaciones_finales = []
    comercios_relacionados = []
    busqueda_id = None

    busqueda = request.args.get("q", "").strip()

    def normalizar_texto(valor):
        import re

        texto = str(valor or "").strip().lower()
        texto = unicodedata.normalize("NFD", texto)
        texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
        texto = re.sub(r"[^a-z0-9ñ\s]", " ", texto)
        return " ".join(texto.split())

    busqueda_normalizada = normalizar_texto(busqueda)

    PALABRAS_IGNORADAS_BUSCADOR = {
        "a", "al", "algo", "aca", "ahi", "ante", "con", "como", "comprar",
        "consigo", "conseguir", "cuanto", "de", "del", "donde", "el", "en",
        "encuentro", "encontrar", "hay", "ir", "la", "las", "lo", "los",
        "me", "mi", "para", "por", "puedo", "que", "quiero", "se", "si",
        "sin", "sobre", "te", "tener", "tenes", "tiene", "tienen", "un",
        "una", "unas", "unos", "venden", "vende", "ver", "y"
    }

    def extraer_palabras_clave(texto):
        texto_normalizado = normalizar_texto(texto)
        palabras = []

        for token in texto_normalizado.split():
            if len(token) < 2:
                continue

            if token in PALABRAS_IGNORADAS_BUSCADOR:
                continue

            if token not in palabras:
                palabras.append(token)

        return palabras

    palabras_clave_busqueda = extraer_palabras_clave(busqueda)

    def variantes_token(token):
        variantes = {token}

        # MVP: permite que "hamburguesas" encuentre "hamburguesa",
        # "mates" encuentre "mate", "cierres" encuentre "cierre", etc.
        if len(token) > 3 and token.endswith("s"):
            variantes.add(token[:-1])

        return variantes

    def calcular_score_busqueda(texto, peso=1):
        if not busqueda_normalizada:
            return 1, []

        texto_normalizado = normalizar_texto(texto)
        score = 0
        coincidencias = []

        for token in palabras_clave_busqueda:
            if any(variante in texto_normalizado for variante in variantes_token(token)):
                score += peso
                coincidencias.append(token)

        return score, coincidencias

    def unir_coincidencias(*listas):
        resultado = []

        for lista in listas:
            for item in lista or []:
                if item not in resultado:
                    resultado.append(item)

        return resultado

    def imagen_publica_de_publicacion(pub):
        imagenes = pub.get("imagenes") or []
        primera_imagen = ""

        if isinstance(imagenes, list) and imagenes:
            primera_imagen = imagenes[0]

        return (
            pub.get("imagen_principal")
            or pub.get("imagen_url")
            or primera_imagen
            or ""
        )

    def ubicacion_publica(comercio_data, direccion_publicacion=None):
        direccion_base = (
            direccion_publicacion
            or comercio_data.get("direccion_mostrar")
            or comercio_data.get("direccion")
            or comercio_data.get("ciudad")
            or ""
        )

        venta_online = bool(comercio_data.get("venta_online"))

        if direccion_base and venta_online:
            return f"{direccion_base} · Venta online"
        elif direccion_base:
            return direccion_base
        elif venta_online:
            return "Venta online"

        return "Consultar ubicación"

    def whatsapp_url(comercio_data, consulta, busqueda_id=None, lista_buscable_id=None):
        comercio_id = comercio_data.get("id")

        if not comercio_id:
            return ""

        return url_for(
            "analytics_whatsapp",
            comercio_id=comercio_id,
            busqueda_id=busqueda_id or "",
            lista_buscable_id=lista_buscable_id or "",
            consulta=consulta or "",
            origen="buscador_lista"
        )

    def cargar_comercios_por_id(comercio_ids):
        comercio_ids = [
            comercio_id for comercio_id in comercio_ids
            if comercio_id
        ]

        if not comercio_ids:
            return {}

        comercios_res = (
            supabase_admin
            .table("comercios")
            .select("id,nombre_negocio,direccion,direccion_mostrar,venta_online,ciudad,categoria,whatsapp,activo,plan")
            .in_("id", list(set(comercio_ids)))
            .execute()
        )

        comercios = comercios_res.data or []

        return {
            com.get("id"): com
            for com in comercios
            if com.get("id") and com.get("activo") is not False
        }

    try:
        # ====================================================
        # 1) PRIMER NIVEL: PUBLICACIONES ACTIVAS
        # ====================================================
        limite = 200 if busqueda else 80

        publicaciones_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,nombre,precio,descripcion,imagenes,imagen_principal,imagen_url,activa,comercio_id,direccion_mostrar,created_at")
            .eq("activa", True)
            .order("created_at", desc=True)
            .limit(limite)
            .execute()
        )

        publicaciones = publicaciones_res.data or []

        comercio_ids_publicaciones = [
            pub.get("comercio_id")
            for pub in publicaciones
            if pub.get("comercio_id")
        ]

        comercios_por_id = cargar_comercios_por_id(comercio_ids_publicaciones)

        imagen_por_comercio = {}

        for pub in publicaciones:
            comercio_id = pub.get("comercio_id")
            comercio_pub = comercios_por_id.get(comercio_id, {})

            if not comercio_pub or comercio_pub.get("activo") is False:
                continue

            texto_para_buscar = " ".join([
                str(pub.get("nombre") or ""),
                str(pub.get("descripcion") or ""),
                str(comercio_pub.get("nombre_negocio") or ""),
                str(comercio_pub.get("categoria") or ""),
                str(comercio_pub.get("ciudad") or ""),
            ])

            imagen_publica = imagen_publica_de_publicacion(pub)

            if comercio_id and imagen_publica and comercio_id not in imagen_por_comercio:
                imagen_por_comercio[comercio_id] = imagen_publica

            score_nombre, coincidencias_nombre = calcular_score_busqueda(pub.get("nombre"), peso=5)
            score_descripcion, coincidencias_descripcion = calcular_score_busqueda(pub.get("descripcion"), peso=2)
            score_comercio, coincidencias_comercio = calcular_score_busqueda(
                " ".join([
                    str(comercio_pub.get("nombre_negocio") or ""),
                    str(comercio_pub.get("categoria") or ""),
                    str(comercio_pub.get("ciudad") or ""),
                ]),
                peso=1
            )

            score_total = score_nombre + score_descripcion + score_comercio

            # Bonus simple: si la frase limpia completa aparece en el título,
            # esa publicación es más exacta que una grupal o genérica.
            nombre_normalizado = normalizar_texto(pub.get("nombre"))
            if busqueda_normalizada and busqueda_normalizada in nombre_normalizado:
                score_total += 10

            coincidencias = unir_coincidencias(
                coincidencias_nombre,
                coincidencias_descripcion,
                coincidencias_comercio
            )

            if busqueda_normalizada and score_total <= 0:
                continue

            publicaciones_finales.append({
                "id": pub.get("id"),
                "nombre": pub.get("nombre"),
                "precio": pub.get("precio"),
                "descripcion": pub.get("descripcion"),
                "imagen_url": imagen_publica,
                "comercio": comercio_pub.get("nombre_negocio", "Comercio local"),
                "direccion_mostrar": ubicacion_publica(comercio_pub, pub.get("direccion_mostrar")),
                "categoria": comercio_pub.get("categoria"),
                "created_at": pub.get("created_at"),
                "_score_busqueda": score_total,
                "_plan": str(comercio_pub.get("plan") or "gratis").lower(),
                "_coincidencias": coincidencias,
            })

        if busqueda_normalizada:
            publicaciones_finales.sort(
                key=lambda item: (
                    item.get("_score_busqueda", 0),
                    1 if item.get("_plan") == "premium" else 0,
                    item.get("created_at") or ""
                ),
                reverse=True
            )

        # ====================================================
        # 2) SEGUNDO NIVEL: LISTAS BUSCABLES
        # ====================================================
        if busqueda_normalizada:
            listas_res = (
                supabase_admin
                .table("listas_buscables")
                .select("id,comercio_id,producto_categoria,atributos_texto,activa,created_at")
                .eq("activa", True)
                .order("created_at", desc=True)
                .limit(300)
                .execute()
            )

            listas = listas_res.data or []

            listas_filtradas = []

            for lista in listas:
                texto_producto = str(lista.get("producto_categoria") or "")
                texto_atributos = str(lista.get("atributos_texto") or "")

                score_producto, coincidencias_producto = calcular_score_busqueda(texto_producto, peso=4)
                score_atributos, coincidencias_atributos = calcular_score_busqueda(texto_atributos, peso=3)

                score_lista = score_producto + score_atributos
                coincidencias_lista = unir_coincidencias(coincidencias_producto, coincidencias_atributos)

                if score_lista <= 0:
                    continue

                lista["_score_busqueda"] = score_lista
                lista["_coincidencias"] = coincidencias_lista

                listas_filtradas.append(lista)

            listas_filtradas.sort(
                key=lambda item: (
                    item.get("_score_busqueda", 0),
                    item.get("created_at") or ""
                ),
                reverse=True
            )

            comercio_ids_listas = [
                lista.get("comercio_id")
                for lista in listas_filtradas
                if lista.get("comercio_id")
            ]

            comercios_listas_por_id = cargar_comercios_por_id(comercio_ids_listas)

            comercios_vistos = set()

            for lista in listas_filtradas:
                comercio_id = lista.get("comercio_id")

                if not comercio_id or comercio_id in comercios_vistos:
                    continue

                comercio_lista = comercios_listas_por_id.get(comercio_id, {})

                if comercio_lista.get("activo") is False:
                    continue

                comercios_vistos.add(comercio_id)

                comercios_relacionados.append({
                    "comercio_id": comercio_id,
                    "lista_buscable_id": lista.get("id"),
                    "nombre_negocio": comercio_lista.get("nombre_negocio") or "Comercio local",
                    "producto_categoria": lista.get("producto_categoria") or "Producto relacionado",
                    "atributos_texto": lista.get("atributos_texto") or "",
                    "ubicacion_mostrar": ubicacion_publica(comercio_lista),
                    "whatsapp_url": whatsapp_url(comercio_lista, busqueda),
                    "imagen_url": imagen_por_comercio.get(comercio_id, ""),
                    "plan": str(comercio_lista.get("plan") or "gratis").lower(),
                    "_score_busqueda": lista.get("_score_busqueda", 0),
                    "_coincidencias": lista.get("_coincidencias") or [],
                })

            # Regla ClickLocal:
            # la búsqueda manda. Premium solo desempata dentro de resultados relevantes.
            comercios_relacionados.sort(
                key=lambda item: (
                    item.get("_score_busqueda", 0),
                    1 if item.get("plan") == "premium" else 0,
                    item.get("nombre_negocio") or ""
                ),
                reverse=True
            )

        if busqueda_normalizada:
            busqueda_id = analytics_crear_busqueda(
                consulta=busqueda,
                total_publicaciones=len(publicaciones_finales),
                total_listas=len(comercios_relacionados),
                ciudad="Paraná",
                origen="buscador_home"
            )

            if busqueda_id:
                session["ultima_busqueda_publica"] = {
                    "busqueda_id": busqueda_id,
                    "consulta": busqueda,
                }

                for item in comercios_relacionados:
                    item["whatsapp_url"] = whatsapp_url(
                        {"id": item.get("comercio_id")},
                        busqueda,
                        busqueda_id=busqueda_id,
                        lista_buscable_id=item.get("lista_buscable_id")
                    )

    except Exception as e:
        print("ERROR cargando galería pública / buscador:", e, flush=True)
        publicaciones_finales = []
        comercios_relacionados = []

    return render_template(
        "index.html",
        comercio=comercio,
        publicaciones=publicaciones_finales,
        comercios_relacionados=comercios_relacionados,
        busqueda=busqueda
    )



@app.route("/cartelera-demo")
def cartelera_demo():
    return render_template("cartelera_demo.html")


# REGISTRO COMERCIO
@app.route("/registro", methods=["GET", "POST"])
@app.route("/registro.html", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre_negocio = request.form.get("nombre_negocio", "").strip()
        email = request.form.get("email", "").strip().lower()
        whatsapp = request.form.get("whatsapp", "").strip()
        direccion = request.form.get("direccion", "").strip()
        venta_online = request.form.get("venta_online") == "on"
        ciudad = request.form.get("ciudad", "Paraná").strip()
        categoria = request.form.get("categoria", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        password = request.form.get("password", "").strip()
        repetir_password = request.form.get("repetir_password", "").strip()

        if not nombre_negocio or not email or not whatsapp or not direccion or not password:
            return "Faltan datos obligatorios: nombre del negocio, email, WhatsApp, dirección o contraseña.", 400

        if not categoria:
            return "Tenés que seleccionar una categoría.", 400

        if password != repetir_password:
            return "Las contraseñas no coinciden.", 400

        if len(password) < 6:
            return "La contraseña debe tener al menos 6 caracteres.", 400

        direccion_mostrar = direccion

        try:
            auth_res = supabase_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True
            })

            user = auth_res.user

            if not user:
                return "No se pudo crear el usuario en Supabase Auth.", 400

            comercio_nuevo = {
                "user_id": user.id,
                "nombre_negocio": nombre_negocio,
                "email": email,
                "whatsapp": whatsapp,
                "direccion": direccion,
                "direccion_mostrar": direccion_mostrar,
                "venta_online": venta_online,
                "ciudad": ciudad,
                "categoria": categoria,
                "descripcion": descripcion,
                "plan": "gratis",
            }

            insert_res = supabase_admin.table("comercios").insert(comercio_nuevo).execute()

            comercio_guardado = insert_res.data[0] if insert_res.data else comercio_nuevo

            session["user_id"] = user.id
            session["comercio"] = comercio_guardado
            session["publicaciones"] = []

            return redirect(url_for("panel"))

        except Exception as e:
            return f"Error registrando comercio: {e}", 400

    return render_template("registro.html")


# LOGIN COMERCIO
@app.route("/login", methods=["GET", "POST"])
@app.route("/login.html", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            return "Falta email o contraseña.", 400

        try:
            auth_res = supabase_auth.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            user = auth_res.user

            if not user:
                return "No se pudo iniciar sesión.", 400

            comercio_res = (
                supabase_admin
                .table("comercios")
                .select("*")
                .eq("user_id", user.id)
                .single()
                .execute()
            )

            comercio = comercio_res.data

            if not comercio:
                return "Usuario válido, pero no se encontró comercio asociado.", 404

            if comercio.get("activo") is False:
                return "Esta cuenta fue bloqueada por administración.", 403

            session["user_id"] = user.id
            session["comercio"] = comercio
            session["publicaciones"] = []

            return redirect(url_for("panel"))

        except Exception as e:
            return f"Error iniciando sesión: {e}", 400

    return render_template("login.html")


# PANEL DEL COMERCIO
@app.route("/panel", methods=["GET", "POST"])
@app.route("/panel.html", methods=["GET", "POST"])
def panel():
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    comercio = session.get("comercio") or comercio_default()
    publicaciones = []
    comercio_id = comercio.get("id") or user_id

    # Refrescar datos reales del comercio desde Supabase.
    # Esto permite que si desde el admin pasamos un comercio a Premium,
    # el panel del comercio lo vea reflejado.
    if user_id:
        try:
            comercio_res = (
                supabase_admin
                .table("comercios")
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

            if comercio_res.data:
                comercio = comercio_res.data[0]
                session["comercio"] = comercio
                comercio_id = comercio.get("id") or comercio.get("user_id") or comercio_id
        except Exception:
            pass

    if user_id and comercio.get("activo") is False:
        session.pop("user_id", None)
        session.pop("comercio", None)
        session.pop("publicaciones", None)
        return "Esta cuenta fue bloqueada por administración.", 403

    if request.method == "POST" and request.form.get("accion") == "actualizar_mis_datos":
        nombre_negocio = request.form.get("nombre_negocio", "").strip()
        whatsapp = request.form.get("whatsapp", "").strip()
        direccion = request.form.get("direccion", "").strip()
        descripcion = request.form.get("descripcion", "").strip()

        if not nombre_negocio or not whatsapp or not direccion:
            return "Faltan datos obligatorios: nombre del comercio, WhatsApp o dirección.", 400

        whatsapp_limpio = limpiar_numero_whatsapp(whatsapp)

        if not whatsapp_limpio:
            return "El WhatsApp no es válido. Ingresá solo números, por ejemplo 3430000000.", 400

        datos_actualizados = {
            "nombre_negocio": nombre_negocio,
            "whatsapp": whatsapp_limpio,
            "direccion": direccion,
            "direccion_mostrar": direccion,
            "descripcion": descripcion,
        }

        try:
            supabase_admin.table("comercios").update(datos_actualizados).eq("id", comercio_id).execute()

            # Mantener sincronizada la dirección visible de las publicaciones existentes.
            # Si el comercio corrige su dirección, la galería pública no debe seguir mostrando la vieja.
            supabase_admin.table("publicaciones").update({
                "direccion_mostrar": direccion
            }).eq("comercio_id", comercio_id).execute()

            comercio.update(datos_actualizados)
            session["comercio"] = comercio
            return redirect(url_for("panel", datos_actualizados="1"))
        except Exception as e:
            return f"Error actualizando datos del comercio: {e}", 400

    plan_actual = str(comercio.get("plan") or "gratis").strip().lower()

    if plan_actual != "premium":
        plan_actual = "gratis"

    comercio["plan_actual"] = plan_actual
    comercio["plan_nombre"] = "Premium" if plan_actual == "premium" else "Gratis"

    # Traer publicaciones desde Supabase si hay usuario
    if user_id:
        try:
            publicaciones_res = (
                supabase_admin
                .table("publicaciones")
                .select("*")
                .eq("comercio_id", comercio_id)
                .order("created_at", desc=True)
                .execute()
            )
            publicaciones = publicaciones_res.data or []
        except Exception:
            publicaciones = session.get("publicaciones", [])
    else:
        publicaciones = session.get("publicaciones", [])

    # Traer listas buscables reales desde Supabase
    listas_buscables = []
    if user_id:
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
        except Exception as e:
            print("ERROR leyendo listas_buscables:", e, flush=True)
            listas_buscables = []

    if request.method == "POST":
        nombre = request.form.get("nombre_publicacion", "").strip()
        precio = normalizar_precio(request.form.get("precio", ""))
        descripcion = request.form.get("descripcion_publicacion", "").strip()
        activa = request.form.get("activa") == "on"
        publicacion_id = request.form.get("publicacion_id", "").strip()
        imagenes_existentes_raw = request.form.get("imagenes_existentes", "[]").strip()

        if not nombre:
            return render_template(
                "panel.html",
                comercio=comercio,
                publicaciones=publicaciones,
                error="Falta el nombre de la publicación."
            )

        # MODO EDICIÓN: actualiza datos básicos, PORTADA, permite borrar fotos existentes
        # y permite agregar fotos nuevas hasta un máximo de 6.
        if publicacion_id:
            try:
                imagenes_existentes = json.loads(imagenes_existentes_raw) if imagenes_existentes_raw else []
                if not isinstance(imagenes_existentes, list):
                    imagenes_existentes = []
            except Exception:
                imagenes_existentes = []

            imagenes_existentes = [
                url.strip()
                for url in imagenes_existentes
                if isinstance(url, str) and url.strip()
            ]

            fotos_nuevas_edicion = []
            for slot in range(6):
                archivo = request.files.get(f"foto_{slot}")
                if archivo and archivo.filename:
                    fotos_nuevas_edicion.append({
                        "slot": slot,
                        "archivo": archivo
                    })

            if len(imagenes_existentes) + len(fotos_nuevas_edicion) > 6:
                return render_template(
                    "panel.html",
                    comercio=comercio,
                    publicaciones=publicaciones,
                    listas_buscables=listas_buscables,
                    error="Máximo 6 fotos permitidas por publicación."
                )

            try:
                principal_slot = int(request.form.get("foto_principal", "0") or 0)
            except Exception:
                principal_slot = 0

            urls_nuevas_por_slot = {}

            fotos_nuevas_edicion_procesadas = 0
            for item in fotos_nuevas_edicion:
                slot = item["slot"]
                f = item["archivo"]

                try:
                    buf = procesar_imagen_clicklocal(f, contexto="edicion")
                except Exception:
                    continue
                fotos_nuevas_edicion_procesadas += 1

                nombre_final = f"{uuid.uuid4().hex}.jpg"
                ruta_objeto = f"publicaciones/{nombre_final}"

                try:
                    supabase_admin.storage.from_("publicaciones").upload(
                        ruta_objeto,
                        buf.getvalue(),
                        {"content-type": "image/jpeg"}
                    )
                except TypeError:
                    supabase_admin.storage.from_("publicaciones").upload(
                        ruta_objeto,
                        buf.getvalue(),
                        file_options={"content-type": "image/jpeg"}
                    )

                public_url = supabase_admin.storage.from_("publicaciones").get_public_url(ruta_objeto)

                if isinstance(public_url, dict):
                    public_url = (
                        public_url.get("publicUrl")
                        or public_url.get("publicURL")
                        or public_url.get("signedURL")
                        or ""
                    )

                public_url = str(public_url).strip()

                if public_url:
                    urls_nuevas_por_slot[slot] = public_url

            if fotos_nuevas_edicion and fotos_nuevas_edicion_procesadas == 0:
                return render_template(
                    "panel.html",
                    publicaciones=publicaciones,
                    listas_buscables=locals().get("listas_buscables", []),
                    comercio=locals().get("comercio"),
                    error="No se pudo procesar una de las fotos. Probá con una imagen JPG, PNG, WEBP o una captura de pantalla."
                )

            imagenes_por_slot = {}

            for index, url in enumerate(imagenes_existentes[:6]):
                imagenes_por_slot[index] = url

            for slot, url in urls_nuevas_por_slot.items():
                imagenes_por_slot[slot] = url

            imagenes_finales = [
                imagenes_por_slot[i]
                for i in range(6)
                if imagenes_por_slot.get(i)
            ]

            if not imagenes_finales:
                return render_template(
                    "panel.html",
                    comercio=comercio,
                    publicaciones=publicaciones,
                    listas_buscables=listas_buscables,
                    error="La publicación debe tener al menos 1 foto."
                )

            imagen_principal_editada = imagenes_por_slot.get(principal_slot) or imagenes_finales[0]

            cambios_publicacion = {
                "nombre": nombre,
                "precio": precio,
                "descripcion": descripcion,
                "activa": activa,
                "imagenes": imagenes_finales,
                "imagen_principal": imagen_principal_editada,
                "imagen_url": imagen_principal_editada
            }

            try:
                supabase_admin.table("publicaciones").update(cambios_publicacion).eq("id", publicacion_id).eq("comercio_id", comercio_id).execute()
            except Exception as e:
                print("\nERROR REAL AL EDITAR PUBLICACION EN SUPABASE:", flush=True)
                print(type(e), flush=True)
                print(e, flush=True)
                print("DATOS QUE SE INTENTARON ACTUALIZAR:", flush=True)
                print(cambios_publicacion, flush=True)

                return render_template(
                    "panel.html",
                    comercio=comercio,
                    publicaciones=publicaciones,
                    listas_buscables=listas_buscables,
                    error=f"Error editando publicación: {e}"
                )

            return redirect(url_for("panel"))



        # Procesar archivos: hasta 6, conservando el casillero original
        fotos_a_procesar = []

        for slot in range(6):
            archivo = request.files.get(f"foto_{slot}")
            if archivo and archivo.filename:
                fotos_a_procesar.append({
                    "slot": slot,
                    "archivo": archivo
                })

        if len(fotos_a_procesar) == 0:
            return render_template(
                "panel.html",
                comercio=comercio,
                publicaciones=publicaciones,
                error="Tenés que subir al menos 1 foto."
            )

        if len(fotos_a_procesar) > 6:
            return render_template(
                "panel.html",
                comercio=comercio,
                publicaciones=publicaciones,
                error="Máximo 6 fotos permitidas."
            )

        try:
            principal_slot = int(request.form.get("foto_principal", "0") or 0)
        except Exception:
            principal_slot = 0

        imagenes_urls = []
        imagen_principal = ""

        fotos_carga_procesadas = 0
        for item in fotos_a_procesar:
            slot = item["slot"]
            f = item["archivo"]

            try:
                buf = procesar_imagen_clicklocal(f, contexto="carga_nueva")
            except Exception:
                continue
            fotos_carga_procesadas += 1

            nombre_final = f"{uuid.uuid4().hex}.jpg"
            ruta_objeto = f"publicaciones/{nombre_final}"

            # Subir a Supabase Storage
            file_options = {
                "content-type": "image/jpeg"
            }

            try:
                supabase_admin.storage.from_("publicaciones").upload(
                    ruta_objeto,
                    buf.getvalue(),
                    file_options=file_options
                )
            except Exception:
                # Algunos clients aceptan file-like
                buf.seek(0)
                supabase_admin.storage.from_("publicaciones").upload(
                    ruta_objeto,
                    buf,
                    file_options=file_options
                )

            # Obtener URL pública
            public_url = None
            try:
                url_res = supabase_admin.storage.from_("publicaciones").get_public_url(ruta_objeto)
                if isinstance(url_res, dict):
                    public_url = url_res.get("publicURL") or url_res.get("publicUrl") or url_res.get("public_url")
                else:
                    public_url = url_res
            except Exception:
                public_url = None

            if not public_url:
                # Fallback: construir URL pública si es posible
                try:
                    base = supabase_admin._client.url
                    public_url = f"{base}/storage/v1/object/public/publicaciones/{ruta_objeto}"
                except Exception:
                    public_url = f"/static/uploads/{nombre_final}"

            imagenes_urls.append({
                "slot": slot,
                "url": public_url,
                "es_principal": slot == principal_slot
            })

        if fotos_a_procesar and fotos_carga_procesadas == 0:
            return render_template(
                "panel.html",
                publicaciones=publicaciones,
                listas_buscables=locals().get("listas_buscables", []),
                comercio=locals().get("comercio"),
                error="No se pudo procesar una de las fotos. Probá con una imagen JPG, PNG, WEBP o una captura de pantalla."
            )

        if imagenes_urls:
            principal = next((img for img in imagenes_urls if img["es_principal"]), None)

            if principal:
                imagen_principal = principal["url"]
            else:
                imagenes_urls[0]["es_principal"] = True
                imagen_principal = imagenes_urls[0]["url"]

        imagenes_urls = [img["url"] for img in imagenes_urls]

        nueva_publicacion = {
            "id": uuid.uuid4().hex,
            "nombre": nombre,
            "precio": precio,
            "descripcion": descripcion,
            "imagenes": imagenes_urls,
            "imagen_principal": imagen_principal,
            "imagen_url": imagen_principal,
            "activa": activa,
            "comercio_id": comercio_id,
            "direccion_mostrar": comercio.get("direccion_mostrar"),
            "created_at": datetime.datetime.utcnow().isoformat()
        }

        try:
            insert_res = supabase_admin.table("publicaciones").insert(nueva_publicacion).execute()
        except Exception as e:
            print("\nERROR REAL AL INSERTAR PUBLICACION EN SUPABASE:", flush=True)
            print(type(e), flush=True)
            print(e, flush=True)
            print("DATOS QUE SE INTENTARON INSERTAR:", flush=True)
            print(nueva_publicacion, flush=True)
            return render_template(
                "panel.html",
                comercio=comercio,
                publicaciones=publicaciones,
                error=f"Error guardando publicación: {e}"
            )

        # Refrescar listado desde DB
        try:
            publicaciones_res = (
                supabase_admin
                .table("publicaciones")
                .select("*")
                .eq("comercio_id", comercio_id)
                .order("created_at", desc=True)
                .execute()
            )
            publicaciones = publicaciones_res.data or []
            session["publicaciones"] = publicaciones
            session.modified = True
        except Exception:
            pass

        return redirect(url_for("panel"))

    # ============================================================
    # CARTELERA - SOLO PARA COMERCIOS CINE Y TEATRO
    # Paso 5B: lectura para mostrar en panel, sin guardar todavía.
    # ============================================================
    es_cine_teatro = (comercio.get("categoria") or "").strip().lower() == "cine y teatro"
    carteleras = []

    if es_cine_teatro:
        dias_cartelera = {
            1: "Lunes",
            2: "Martes",
            3: "Miércoles",
            4: "Jueves",
            5: "Viernes",
            6: "Sábado",
            7: "Domingo",
        }

        try:
            carteleras_res = (
                supabase_admin
                .table("carteleras")
                .select("*")
                .eq("comercio_id", comercio_id)
                .order("created_at", desc=True)
                .execute()
            )

            carteleras = carteleras_res.data or []

            for cartelera in carteleras:
                funciones_res = (
                    supabase_admin
                    .table("cartelera_funciones")
                    .select("*")
                    .eq("cartelera_id", cartelera.get("id"))
                    .eq("activa", True)
                    .order("dia_semana")
                    .execute()
                )

                funciones = funciones_res.data or []

                for funcion in funciones:
                    dia = funcion.get("dia_semana")
                    funcion["dia_nombre"] = dias_cartelera.get(dia, f"Día {dia}")

                    horarios = funcion.get("horarios") or []
                    if isinstance(horarios, str):
                        horarios = [horarios]
                    funcion["horarios"] = horarios

                cartelera["funciones"] = funciones

        except Exception as e:
            print("\nERROR CARGANDO CARTELERAS EN PANEL:", e, flush=True)
            carteleras = []

    return render_template(
        "panel.html",
        comercio=comercio,
        publicaciones=publicaciones,
        listas_buscables=listas_buscables,
        es_cine_teatro=es_cine_teatro,
        carteleras=carteleras
    )


@app.route("/panel/cartelera/crear", methods=["POST"])
def crear_cartelera_panel():
    user_id = session.get("user_id")
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not user_id or not comercio_id:
        return redirect(url_for("login"))

    es_cine_teatro = (comercio.get("categoria") or "").strip().lower() == "cine y teatro"

    if not es_cine_teatro:
        return redirect(url_for("panel"))

    def texto_form(nombre):
        return (request.form.get(nombre) or "").strip()

    def precio_a_float(valor):
        texto = (valor or "").strip()

        if not texto:
            return None

        texto = (
            texto
            .replace("$", "")
            .replace(" ", "")
            .replace("\xa0", "")
        )

        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        else:
            partes = texto.split(".")
            if len(partes) > 1 and all(len(p) == 3 for p in partes[1:]):
                texto = "".join(partes)

        try:
            return float(texto)
        except Exception:
            raise ValueError("precio_general_invalido")

    titulo = texto_form("cartelera_titulo")

    if not titulo:
        return redirect(url_for("panel", cartelera_error="titulo"))

    try:
        precio_general = precio_a_float(texto_form("cartelera_precio_general"))
    except ValueError:
        return redirect(url_for("panel", cartelera_error="precio"))

    ahora = datetime.datetime.utcnow().isoformat()
    cartelera_id = str(uuid.uuid4())

    nueva_cartelera = {
        "id": cartelera_id,
        "comercio_id": comercio_id,
        "titulo": titulo,
        "imagen_url": None,
        "descripcion": texto_form("cartelera_descripcion") or None,
        "genero": texto_form("cartelera_genero") or None,
        "clasificacion": texto_form("cartelera_clasificacion") or None,
        "direccion_mostrar": texto_form("cartelera_direccion_mostrar") or comercio.get("direccion_mostrar") or comercio.get("direccion"),
        "activa": True,
        "precio_general": precio_general,
        "precios_detalle": texto_form("cartelera_precios_detalle") or None,
        "promociones": texto_form("cartelera_promociones") or None,
        "created_at": ahora,
        "updated_at": ahora,
    }

    dias = [
        (1, "lunes"),
        (2, "martes"),
        (3, "miercoles"),
        (4, "jueves"),
        (5, "viernes"),
        (6, "sabado"),
        (7, "domingo"),
    ]

    funciones = []

    for numero_dia, clave_dia in dias:
        horarios_texto = texto_form(f"cartelera_{clave_dia}_horarios")
        promo = texto_form(f"cartelera_{clave_dia}_promo")

        horarios = [
            h.strip()
            for h in horarios_texto.replace(";", ",").split(",")
            if h.strip()
        ]

        if horarios or promo:
            funciones.append({
                "id": str(uuid.uuid4()),
                "cartelera_id": cartelera_id,
                "dia_semana": numero_dia,
                "horarios": horarios,
                "promo": promo or None,
                "activa": True,
                "created_at": ahora,
                "updated_at": ahora,
            })

    try:
        supabase_admin.table("carteleras").insert(nueva_cartelera).execute()

        if funciones:
            supabase_admin.table("cartelera_funciones").insert(funciones).execute()

    except Exception as e:
        print("\nERROR GUARDANDO CARTELERA EN PANEL:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)
        print("DATOS CARTELERA:", nueva_cartelera, flush=True)
        print("FUNCIONES:", funciones, flush=True)
        return redirect(url_for("panel", cartelera_error="guardar"))

    return redirect(url_for("panel", cartelera_ok="1"))






# GUARDAR LISTA BUSCABLE
@app.route("/listas/guardar", methods=["POST"])
def guardar_lista_buscable():
    comercio = session.get("comercio") or {}
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id") or user_id

    # Refrescar comercio por seguridad para obtener id real y ciudad
    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if comercio_res.data:
            comercio = comercio_res.data[0]
            session["comercio"] = comercio
            comercio_id = comercio.get("id") or comercio.get("user_id") or comercio_id
    except Exception as e:
        print("ERROR refrescando comercio para lista buscable:", e, flush=True)

    producto_categoria = request.form.get("producto_categoria", "").strip()
    atributos_texto = request.form.get("atributos_texto", "").strip()

    if not producto_categoria or not atributos_texto:
        return redirect(url_for("panel") + "?lista_error=1#listas")

    nueva_lista = {
        "id": str(uuid.uuid4()),
        "comercio_id": comercio_id,
        "producto_categoria": producto_categoria,
        "atributos_texto": atributos_texto,
        "variantes_procesadas": [],
        "ciudad": comercio.get("ciudad") or "Paraná",
        "activa": True,
        "created_at": datetime.datetime.utcnow().isoformat(),
        "updated_at": datetime.datetime.utcnow().isoformat()
    }

    try:
        supabase_admin.table("listas_buscables").insert(nueva_lista).execute()
    except Exception as e:
        print("ERROR guardando lista buscable:", e, flush=True)
        print("DATOS:", nueva_lista, flush=True)
        return redirect(url_for("panel") + "?lista_error=1#listas")

    return redirect(url_for("panel") + "?lista_guardada=1#listas")



# EDITAR LISTA BUSCABLE
@app.route("/listas/editar/<lista_id>", methods=["POST"])
def editar_lista_buscable(lista_id):
    comercio = session.get("comercio") or {}
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    try:
        uuid.UUID(str(lista_id))
    except Exception:
        return redirect(url_for("panel") + "?lista_error=1#listas")

    comercio_id = comercio.get("id") or user_id

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if comercio_res.data:
            comercio = comercio_res.data[0]
            session["comercio"] = comercio
            comercio_id = comercio.get("id") or comercio.get("user_id") or comercio_id
    except Exception as e:
        print("ERROR refrescando comercio para editar lista:", e, flush=True)

    producto_categoria = request.form.get("producto_categoria", "").strip()
    atributos_texto = request.form.get("atributos_texto", "").strip()

    if not producto_categoria or not atributos_texto:
        return redirect(url_for("panel") + "?lista_error=1#listas")

    cambios = {
        "producto_categoria": producto_categoria,
        "atributos_texto": atributos_texto,
        "updated_at": datetime.datetime.utcnow().isoformat(),
        "activa": True
    }

    try:
        (
            supabase_admin
            .table("listas_buscables")
            .update(cambios)
            .eq("id", lista_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )
    except Exception as e:
        print("ERROR editando lista buscable:", e, flush=True)
        print("DATOS:", cambios, flush=True)
        return redirect(url_for("panel") + "?lista_error=1#listas")

    return redirect(url_for("panel") + "?lista_editada=1#listas")


# ELIMINAR LISTA BUSCABLE
@app.route("/listas/eliminar/<lista_id>", methods=["POST"])
def eliminar_lista_buscable(lista_id):
    comercio = session.get("comercio") or {}
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    try:
        uuid.UUID(str(lista_id))
    except Exception:
        return redirect(url_for("panel") + "?lista_error=1#listas")

    comercio_id = comercio.get("id") or user_id

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if comercio_res.data:
            comercio = comercio_res.data[0]
            session["comercio"] = comercio
            comercio_id = comercio.get("id") or comercio.get("user_id") or comercio_id
    except Exception as e:
        print("ERROR refrescando comercio para eliminar lista:", e, flush=True)

    try:
        (
            supabase_admin
            .table("listas_buscables")
            .delete()
            .eq("id", lista_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )
    except Exception as e:
        print("ERROR eliminando lista buscable:", e, flush=True)
        return redirect(url_for("panel") + "?lista_error=1#listas")

    return redirect(url_for("panel") + "?lista_eliminada=1#listas")


# ELIMINAR PUBLICACIÓN COMPLETA
@app.route("/publicacion/eliminar/<publicacion_id>", methods=["POST"])
def eliminar_publicacion(publicacion_id):
    comercio = session.get("comercio") or comercio_default()
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    comercio_id = comercio.get("id") or user_id

    def extraer_ruta_storage(url):
        if not url or not isinstance(url, str):
            return None

        url = url.strip()

        # Sólo intentamos borrar archivos públicos del bucket "publicaciones"
        marcador = "/storage/v1/object/public/publicaciones/"
        if marcador not in url:
            return None

        ruta = url.split(marcador, 1)[1].split("?", 1)[0].strip("/")

        # Seguridad básica: evitar rutas vacías o raras
        if not ruta or ".." in ruta:
            return None

        return ruta

    try:
        # Seguridad: sólo permite borrar publicaciones del comercio logueado
        existente_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,imagenes,imagen_principal,imagen_url")
            .eq("id", publicacion_id)
            .eq("comercio_id", comercio_id)
            .execute()
        )

        if not existente_res.data:
            print("Intento de borrar publicación inexistente o de otro comercio:", publicacion_id, flush=True)
            return redirect(url_for("panel"))

        publicacion = existente_res.data[0]

        urls = []

        imagenes = publicacion.get("imagenes") or []
        if isinstance(imagenes, list):
            urls.extend(imagenes)

        urls.append(publicacion.get("imagen_principal"))
        urls.append(publicacion.get("imagen_url"))

        rutas_storage = []
        for url in urls:
            ruta = extraer_ruta_storage(url)
            if ruta and ruta not in rutas_storage:
                rutas_storage.append(ruta)

        # Primero borramos la publicación de la base.
        # Si luego falla Storage, el panel ya queda limpio igual.
        supabase_admin.table("publicaciones").delete().eq("id", publicacion_id).eq("comercio_id", comercio_id).execute()

        # Después intentamos limpiar archivos de Storage.
        if rutas_storage:
            try:
                supabase_admin.storage.from_("publicaciones").remove(rutas_storage)
                print("Imagenes eliminadas de Storage:", rutas_storage, flush=True)
            except Exception as e_storage:
                print("\nAVISO: La publicación se eliminó, pero falló el borrado de imágenes en Storage:", flush=True)
                print(type(e_storage), flush=True)
                print(e_storage, flush=True)
                print("Rutas que se intentaron borrar:", rutas_storage, flush=True)

    except Exception as e:
        print("\nERROR REAL AL ELIMINAR PUBLICACION EN SUPABASE:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)

    return redirect(url_for("panel"))


# DETALLE DE PUBLICACIÓN
@app.route("/detalle")
@app.route("/detalle.html")
def detalle_sin_id():
    publicaciones = session.get("publicaciones", [])

    if publicaciones:
        primera_publicacion = publicaciones[0]
        return redirect(url_for("detalle", publicacion_id=primera_publicacion["id"]))

    return redirect(url_for("inicio"))


@app.route("/detalle/<publicacion_id>")
def detalle(publicacion_id):
    comercio = comercio_default()
    ultima_busqueda_publica = session.get("ultima_busqueda_publica") or {}
    busqueda_id_origen = uuid_o_none(ultima_busqueda_publica.get("busqueda_id"))
    consulta_origen = str(ultima_busqueda_publica.get("consulta") or "").strip()

    from uuid import UUID

    try:
        UUID(str(publicacion_id))
    except (ValueError, TypeError, AttributeError):
        return redirect(url_for("inicio"))

    try:
        publicacion_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,nombre,precio,descripcion,imagenes,imagen_principal,imagen_url,activa,comercio_id,direccion_mostrar,created_at")
            .eq("id", publicacion_id)
            .eq("activa", True)
            .limit(1)
            .execute()
        )

        publicaciones = publicacion_res.data or []

        if not publicaciones:
            return redirect(url_for("inicio"))

        publicacion_encontrada = publicaciones[0]

        imagenes = publicacion_encontrada.get("imagenes") or []
        primera_imagen = ""

        if isinstance(imagenes, list) and imagenes:
            primera_imagen = imagenes[0]

        imagen_publica = (
            publicacion_encontrada.get("imagen_principal")
            or publicacion_encontrada.get("imagen_url")
            or primera_imagen
            or ""
        )

        publicacion_encontrada["imagen_url"] = imagen_publica

        comercio_id = publicacion_encontrada.get("comercio_id")

        if comercio_id:
            comercio_res = (
                supabase_admin
                .table("comercios")
                .select("id,nombre_negocio,direccion,direccion_mostrar,venta_online,ciudad,categoria,whatsapp")
                .eq("id", comercio_id)
                .limit(1)
                .execute()
            )

            comercios = comercio_res.data or []

            if comercios:
                comercio = comercios[0]

        if not comercio.get("direccion_mostrar"):
            comercio["direccion_mostrar"] = (
                comercio.get("direccion")
                or comercio.get("ciudad")
                or "Consultar ubicación"
            )

        from urllib.parse import quote

        whatsapp_numero = limpiar_numero_whatsapp(comercio.get("whatsapp"))

        mensaje_whatsapp = (
            "Hola, vengo de ClickLocal Paraná. "
            f"Quiero consultar por: {publicacion_encontrada.get('nombre') or 'esta publicación'}"
        )

        if whatsapp_numero and comercio_id:
            comercio["whatsapp_url"] = url_for(
                "analytics_whatsapp",
                comercio_id=comercio_id,
                publicacion_id=publicacion_id,
                busqueda_id=busqueda_id_origen or "",
                consulta=publicacion_encontrada.get("nombre") or consulta_origen or "",
                origen="detalle_publicacion"
            )
        else:
            comercio["whatsapp_url"] = ""

        if comercio_id:
            analytics_registrar_evento(
                "visita_publicacion",
                comercio_id=comercio_id,
                publicacion_id=publicacion_id,
                busqueda_id=busqueda_id_origen,
                consulta_origen=consulta_origen,
                origen="detalle_publicacion",
                metadata={
                    "publicacion_nombre": publicacion_encontrada.get("nombre"),
                }
            )

    except Exception as e:
        print("ERROR cargando detalle de publicación:", e, flush=True)
        return redirect(url_for("inicio"))

    return render_template(
        "detalle.html",
        comercio=comercio,
        publicacion=publicacion_encontrada
    )


# PERFIL PÚBLICO DEL COMERCIO

@app.route("/comercio/<comercio_id>")
def perfil_comercio(comercio_id):
    from uuid import UUID
    from urllib.parse import quote

    ultima_busqueda_publica = session.get("ultima_busqueda_publica") or {}
    busqueda_id_origen = uuid_o_none(ultima_busqueda_publica.get("busqueda_id"))
    consulta_origen = str(ultima_busqueda_publica.get("consulta") or "").strip()

    ultima_busqueda_publica = session.get("ultima_busqueda_publica") or {}
    busqueda_id_origen = uuid_o_none(ultima_busqueda_publica.get("busqueda_id"))
    consulta_origen = str(ultima_busqueda_publica.get("consulta") or "").strip()

    try:
        UUID(str(comercio_id))
    except (ValueError, TypeError, AttributeError):
        return redirect(url_for("inicio"))

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,user_id,nombre_negocio,direccion,direccion_mostrar,venta_online,ciudad,categoria,descripcion,logo_url,whatsapp,activo")
            .eq("id", comercio_id)
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        comercio_data = comercio_res.data or []

        if not comercio_data:
            return redirect(url_for("inicio"))

        comercio = comercio_data[0]

        nombre_negocio = comercio.get("nombre_negocio") or "Comercio"
        palabras_nombre = nombre_negocio.split()
        iniciales = "".join(p[0].upper() for p in palabras_nombre[:2] if p)
        comercio["iniciales"] = iniciales or "CL"

        direccion_base = (
            comercio.get("direccion_mostrar")
            or comercio.get("direccion")
            or comercio.get("ciudad")
            or "Consultar ubicación"
        )

        ciudad = comercio.get("ciudad") or ""

        if ciudad and ciudad not in direccion_base:
            direccion_base = f"{direccion_base} · {ciudad}"

        if comercio.get("venta_online"):
            comercio["ubicacion_perfil"] = f"{direccion_base} · Venta online"
        else:
            comercio["ubicacion_perfil"] = direccion_base

        whatsapp_numero = limpiar_numero_whatsapp(comercio.get("whatsapp"))

        mensaje_whatsapp = (
            "Hola, vengo de ClickLocal Paraná. "
            f"Quiero consultar por {nombre_negocio}."
        )

        if whatsapp_numero:
            comercio["whatsapp_url"] = url_for(
                "analytics_whatsapp",
                comercio_id=comercio_id,
                busqueda_id=busqueda_id_origen or "",
                consulta=consulta_origen or nombre_negocio,
                origen="perfil_comercio"
            )
        else:
            comercio["whatsapp_url"] = ""

        analytics_registrar_evento(
            "visita_comercio",
            comercio_id=comercio_id,
            busqueda_id=busqueda_id_origen,
            consulta_origen=consulta_origen,
            origen="perfil_comercio",
            metadata={
                "nombre_negocio": nombre_negocio,
            }
        )

        publicaciones_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,nombre,precio,descripcion,imagenes,imagen_principal,imagen_url,activa,eliminada,comercio_id,direccion_mostrar,created_at")
            .eq("comercio_id", comercio_id)
            .eq("activa", True)
            .eq("eliminada", False)
            .order("created_at", desc=True)
            .execute()
        )

        publicaciones = publicaciones_res.data or []

        for publicacion in publicaciones:
            imagenes = publicacion.get("imagenes") or []
            primera_imagen = ""

            if isinstance(imagenes, list) and imagenes:
                primera_imagen = imagenes[0]

            imagen_publica = (
                publicacion.get("imagen_principal")
                or publicacion.get("imagen_url")
                or primera_imagen
                or ""
            )

            publicacion["imagen_url"] = imagen_publica

            publicacion["precio_mostrar"] = formatear_precio(publicacion.get("precio"))

            publicacion["ubicacion_mostrar"] = (
                publicacion.get("direccion_mostrar")
                or comercio.get("ubicacion_perfil")
                or "Consultar ubicación"
            )

    except Exception as e:
        print("ERROR cargando perfil de comercio:", e, flush=True)
        return redirect(url_for("inicio"))

    return render_template(
        "perfil.html",
        comercio=comercio,
        publicaciones=publicaciones
    )


@app.route("/perfil")
@app.route("/perfil.html")
def perfil():
    return redirect(url_for("inicio"))


@app.route("/solicitar-premium", methods=["POST"])
def solicitar_premium():
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if not comercio_res.data:
            return redirect(url_for("panel", premium_error="1") + "#plan")

        comercio = comercio_res.data[0]
        plan_actual = str(comercio.get("plan") or "gratis").strip().lower()

        # Si ya es Premium, no pedimos nada.
        if plan_actual == "premium":
            return redirect(url_for("panel") + "#plan")

        # El plan NO cambia todavía. Solo marcamos solicitud Premium pendiente.
        supabase_admin.table("comercios").update({
            "solicitud_premium": True
        }).eq("user_id", user_id).execute()

        comercio_sesion = session.get("comercio") or {}
        comercio_sesion["solicitud_premium"] = True
        session["comercio"] = comercio_sesion

        return redirect(url_for("panel", premium_solicitado="1") + "#plan")

    except Exception as e:
        print("\nERROR SOLICITANDO PREMIUM:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)
        return redirect(url_for("panel", premium_error="1") + "#plan")



# ============================================================
# PANEL DE CONTROL ADMIN - ACCESO SEGURO
# ============================================================

ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


def admin_requerido(func):
    def wrapper(*args, **kwargs):
        if not session.get("admin_logueado"):
            return redirect(url_for("admin_login"))
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        password = request.form.get("password", "").strip()

        if not ADMIN_USER or not ADMIN_PASSWORD:
            error = "Credenciales de administrador no configuradas."
        elif usuario == ADMIN_USER and password == ADMIN_PASSWORD:
            session["admin_logueado"] = True
            session["admin_user"] = usuario
            return redirect(url_for("admin"))
        else:
            error = "Usuario o contraseña de administrador incorrectos."

    return render_template("admin_login.html", error=error)



@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("comercio", None)
    session.pop("publicaciones", None)
    return redirect("/index.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logueado", None)
    session.pop("admin_user", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@app.route("/admin.html")
@admin_requerido
def admin():
    error = None
    comercios_raw = []
    publicaciones_raw = []

    try:
        comercios_res = (
            supabase_admin
            .table("comercios")
            .select("*")
            .execute()
        )
        comercios_raw = comercios_res.data or []
    except Exception as e:
        error = f"No se pudieron cargar los comercios: {e}"

    try:
        publicaciones_res = (
            supabase_admin
            .table("publicaciones")
            .select("*")
            .execute()
        )
        publicaciones_raw = publicaciones_res.data or []
    except Exception as e:
        if error:
            error += f" | No se pudieron cargar las publicaciones: {e}"
        else:
            error = f"No se pudieron cargar las publicaciones: {e}"

    def es_publicacion_activa(pub):
        if "activa" in pub:
            return bool(pub.get("activa"))

        if "activo" in pub:
            return bool(pub.get("activo"))

        estado = str(
            pub.get("estado")
            or pub.get("estado_publicacion")
            or ""
        ).strip().lower()

        if estado:
            return estado in ["activa", "activo", "publicada", "publicado"]

        return True

    publicaciones_por_comercio = {}
    publicaciones_restaurables_por_comercio = {}
    publicaciones_activas_por_comercio = {}

    for pub in publicaciones_raw:
        comercio_id = pub.get("comercio_id")

        if comercio_id:
            publicaciones_por_comercio[comercio_id] = publicaciones_por_comercio.get(comercio_id, 0) + 1

            if pub.get("eliminada") is not True:
                publicaciones_restaurables_por_comercio[comercio_id] = publicaciones_restaurables_por_comercio.get(comercio_id, 0) + 1

            if es_publicacion_activa(pub):
                publicaciones_activas_por_comercio[comercio_id] = publicaciones_activas_por_comercio.get(comercio_id, 0) + 1

    comercios = []

    for c in comercios_raw:
        comercio_id = c.get("id") or c.get("comercio_id") or c.get("user_id")

        plan = str(c.get("plan") or "gratis").strip().lower()
        if plan != "premium":
            plan = "gratis"

        estado_plan = str(c.get("estado_plan") or "").strip().lower()
        solicitud_premium = bool(c.get("solicitud_premium"))

        whatsapp = c.get("whatsapp") or "-"
        whatsapp_numero = limpiar_numero_whatsapp(whatsapp)

        cuenta_habilitada = c.get("activo") is not False
        publicaciones_total = publicaciones_por_comercio.get(comercio_id, 0)
        publicaciones_restaurables = publicaciones_restaurables_por_comercio.get(comercio_id, 0)
        publicaciones_activas = publicaciones_activas_por_comercio.get(comercio_id, 0)

        necesita_restaurar = (
            cuenta_habilitada
            and publicaciones_restaurables > 0
            and publicaciones_activas == 0
        )

        contenido_restaurado = (
            cuenta_habilitada
            and publicaciones_activas > 0
        )

        comercios.append({
            "id": comercio_id,
            "nombre": c.get("nombre_negocio") or c.get("nombre") or "Sin nombre",
            "email": c.get("email") or "-",
            "whatsapp": whatsapp,
            "whatsapp_url": construir_url_whatsapp(
                whatsapp,
                "Hola, vengo de ClickLocal Paraná. Quiero consultar por ClickLocal."
            ) if whatsapp_numero else None,
            "ciudad": c.get("ciudad") or "Paraná",
            "categoria": c.get("categoria") or c.get("rubro") or "-",
            "plan": plan,
            "estado_plan": estado_plan,
            "solicitud_premium": solicitud_premium,
            "activo": cuenta_habilitada,
            "estado_cuenta": "Habilitada" if cuenta_habilitada else "Bloqueada",
            "necesita_restaurar": necesita_restaurar,
            "contenido_restaurado": contenido_restaurado,
            "publicaciones_restaurables": publicaciones_restaurables,
            "created_at": c.get("created_at") or "-",
            "publicaciones_total": publicaciones_total,
            "publicaciones_activas": publicaciones_activas,
            "perfil_url": url_for("perfil_comercio", comercio_id=comercio_id) if comercio_id else None,
        })

    comercios.sort(
        key=lambda c: (
            0 if c.get("necesita_restaurar") else 1 if not c.get("activo") else 2,
            str(c.get("nombre") or "").lower()
        )
    )

    solicitudes_premium = [
        c for c in comercios
        if c.get("solicitud_premium") and c.get("plan") != "premium"
    ]

    total_publicaciones_activas = sum(
        1 for pub in publicaciones_raw
        if es_publicacion_activa(pub)
    )

    total_premium = sum(
        1 for c in comercios
        if c["plan"] == "premium"
    )

    total_bloqueados = sum(
        1 for c in comercios
        if not c.get("activo")
    )

    resumen = {
        "total_comercios": len(comercios),
        "total_premium": total_premium,
        "total_publicaciones": len(publicaciones_raw),
        "total_publicaciones_activas": total_publicaciones_activas,
        "total_solicitudes_premium": len(solicitudes_premium),
        "total_bloqueados": total_bloqueados,
    }

    return render_template(
        "admin.html",
        resumen=resumen,
        comercios=comercios,
        solicitudes_premium=solicitudes_premium,
        error=error,
        admin_user=session.get("admin_user")
    )



@app.route("/admin/activar-premium/<comercio_id>", methods=["POST"])
@admin_requerido
def admin_activar_premium(comercio_id):
    import datetime

    hoy = datetime.date.today()
    vencimiento = hoy + datetime.timedelta(days=30)

    try:
        supabase_admin.table("comercios").update({
            "plan": "premium",
            "estado_plan": "activo",
            "solicitud_premium": False,
            "fecha_inicio_plan": hoy.isoformat(),
            "fecha_vencimiento_plan": vencimiento.isoformat()
        }).eq("id", comercio_id).execute()

        return redirect(url_for("admin", premium_activado="1"))

    except Exception as e:
        print("\nERROR ACTIVANDO PREMIUM:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)
        return redirect(url_for("admin", premium_error="1"))



@app.route("/admin/bloquear-comercio/<comercio_id>", methods=["POST"])
@admin_requerido
def admin_bloquear_comercio(comercio_id):
    """
    Bloqueo de emergencia:
    - desactiva el comercio
    - oculta sus publicaciones
    - oculta sus listas buscables
    No borra datos ni imágenes.
    """
    try:
        supabase_admin.table("comercios").update({
            "activo": False
        }).eq("id", comercio_id).execute()

        supabase_admin.table("publicaciones").update({
            "activa": False
        }).eq("comercio_id", comercio_id).execute()

        try:
            supabase_admin.table("listas_buscables").update({
                "activa": False
            }).eq("comercio_id", comercio_id).execute()
        except Exception as e:
            print("AVISO: no se pudieron desactivar listas_buscables:", e, flush=True)

        return redirect(url_for("admin", comercio_bloqueado="1"))

    except Exception as e:
        print("\nERROR BLOQUEANDO COMERCIO:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)
        return redirect(url_for("admin", bloqueo_error="1"))


@app.route("/admin/reactivar-comercio/<comercio_id>", methods=["POST"])
@admin_requerido
def admin_reactivar_comercio(comercio_id):
    """
    Reactiva la cuenta del comercio.
    Por seguridad NO reactiva automáticamente publicaciones/listas,
    porque podrían haber sido el motivo del bloqueo.
    """
    try:
        supabase_admin.table("comercios").update({
            "activo": True
        }).eq("id", comercio_id).execute()

        return redirect(url_for("admin", comercio_reactivado="1"))

    except Exception as e:
        print("\nERROR REACTIVANDO COMERCIO:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)
        return redirect(url_for("admin", reactivar_error="1"))



@app.route("/admin/restaurar-contenido/<comercio_id>", methods=["POST"])
@admin_requerido
def admin_restaurar_contenido_comercio(comercio_id):
    """
    Restaura contenido de un comercio:
    - reactiva publicaciones no eliminadas
    - reactiva listas buscables
    No desbloquea la cuenta.
    """
    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,nombre_negocio,activo")
            .eq("id", comercio_id)
            .limit(1)
            .execute()
        )

        comercio_data = comercio_res.data or []

        if not comercio_data:
            return redirect(url_for("admin", restaurar_contenido_error="1"))

        if comercio_data[0].get("activo") is False:
            return redirect(url_for("admin", restaurar_bloqueado="1"))

        supabase_admin.table("publicaciones").update({
            "activa": True
        }).eq("comercio_id", comercio_id).eq("eliminada", False).execute()

        try:
            supabase_admin.table("listas_buscables").update({
                "activa": True
            }).eq("comercio_id", comercio_id).execute()
        except Exception as e:
            print("AVISO: no se pudieron restaurar listas_buscables:", e, flush=True)

        return redirect(url_for("admin", contenido_restaurado="1"))

    except Exception as e:
        print("\nERROR RESTAURANDO CONTENIDO:", flush=True)
        print(type(e), flush=True)
        print(e, flush=True)
        return redirect(url_for("admin", restaurar_contenido_error="1"))



# CSS
@app.route("/styles.css")
def styles():
    return send_from_directory("static", "styles.css")


# ============================================================
# PANEL DE CONTROL ADMIN - ACCESO SEGURO
# ============================================================

ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# IMÁGENES DE LA MAQUETA
@app.route("/img/<path:filename>")
def imagenes(filename):
    return send_from_directory("static/img", filename)



# ============================================================
# ANALYTICS ADMIN V1
# Lee datos desde Supabase:
# - busquedas_publicas
# - eventos_analytics
# ============================================================

@app.route("/admin/analytics")
@app.route("/admin/analytics.html")
@admin_requerido
def admin_analytics():
    from flask import request, render_template_string
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    def _safe_select(tabla, limit=1000):
        """
        Lectura defensiva: intenta ordenar por created_at.
        Si falla por alguna columna/permiso, intenta lectura simple.
        """
        try:
            res = (
                supabase_admin
                .table(tabla)
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return res.data or [], None
        except Exception as e1:
            try:
                res = (
                    supabase_admin
                    .table(tabla)
                    .select("*")
                    .limit(limit)
                    .execute()
                )
                return res.data or [], None
            except Exception as e2:
                return [], f"{tabla}: {e2}"

    def _parse_fecha(valor):
        if not valor:
            return None
        try:
            texto = str(valor).replace("Z", "+00:00")
            fecha = datetime.fromisoformat(texto)
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            return fecha
        except Exception:
            return None

    def _filtrar_por_dias(registros, dias):
        if dias is None:
            return registros

        desde = datetime.now(timezone.utc) - timedelta(days=dias)
        filtrados = []

        for r in registros:
            fecha = _parse_fecha(r.get("created_at"))
            if fecha and fecha >= desde:
                filtrados.append(r)

        return filtrados

    def _consulta_busqueda(b):
        return (
            b.get("consulta")
            or b.get("q")
            or b.get("busqueda")
            or b.get("texto")
            or ""
        ).strip()

    def _tipo_evento(e):
        return (
            e.get("tipo_evento")
            or e.get("evento")
            or e.get("tipo")
            or ""
        ).strip().lower()

    def _es_whatsapp(e):
        tipo = _tipo_evento(e)
        return "whatsapp" in tipo or "wa_" in tipo

    def _es_telefono(e):
        tipo = _tipo_evento(e)
        return "telefono" in tipo or "phone" in tipo or "llamada" in tipo

    def _es_vista(e):
        tipo = _tipo_evento(e)
        return "vista" in tipo or "view" in tipo or "perfil" in tipo or "publicacion" in tipo

    def _cantidad_resultados(b):
        for key in ("cantidad_resultados", "total_resultados", "resultados_count", "resultados"):
            valor = b.get(key)
            if valor is None:
                continue
            try:
                return int(valor)
            except Exception:
                continue
        return None

    def _es_sin_resultados(b):
        for key in ("sin_resultados", "sin_resultado"):
            if b.get(key) is True:
                return True

        cantidad = _cantidad_resultados(b)
        return cantidad == 0

    def _nombre_comercio(comercios_por_id, comercio_id):
        if not comercio_id:
            return "Comercio sin identificar"

        comercio = comercios_por_id.get(str(comercio_id))
        if not comercio:
            return "Comercio sin identificar"

        return (
            comercio.get("nombre_negocio")
            or comercio.get("nombre")
            or comercio.get("razon_social")
            or "Comercio sin nombre"
        )

    dias_raw = request.args.get("dias", "30")

    if dias_raw == "todos":
        dias = None
        periodo_label = "Todo el historial disponible"
    else:
        try:
            dias = int(dias_raw)
        except Exception:
            dias = 30
            dias_raw = "30"
        periodo_label = f"Últimos {dias} días"

    busquedas, err_busquedas = _safe_select("busquedas_publicas", limit=1500)
    eventos, err_eventos = _safe_select("eventos_analytics", limit=3000)
    comercios, err_comercios = _safe_select("comercios", limit=3000)

    errores = [e for e in [err_busquedas, err_eventos, err_comercios] if e]

    busquedas = _filtrar_por_dias(busquedas, dias)
    eventos = _filtrar_por_dias(eventos, dias)

    comercios_por_id = {
        str(c.get("id")): c
        for c in comercios
        if c.get("id")
    }

    busquedas_por_id = {
        str(b.get("id")): b
        for b in busquedas
        if b.get("id")
    }

    total_busquedas = len(busquedas)
    total_eventos = len(eventos)

    eventos_whatsapp = [e for e in eventos if _es_whatsapp(e)]
    eventos_telefono = [e for e in eventos if _es_telefono(e)]
    eventos_vista = [e for e in eventos if _es_vista(e)]

    top_busquedas_counter = Counter()
    top_sin_resultados_counter = Counter()

    for b in busquedas:
        consulta = _consulta_busqueda(b)
        if consulta:
            top_busquedas_counter[consulta] += 1

            if _es_sin_resultados(b):
                top_sin_resultados_counter[consulta] += 1

    eventos_por_tipo_counter = Counter()
    clicks_por_comercio_counter = Counter()
    whatsapp_por_consulta_counter = Counter()

    for e in eventos:
        tipo = _tipo_evento(e) or "sin_tipo"
        eventos_por_tipo_counter[tipo] += 1

        if _es_whatsapp(e) or _es_telefono(e):
            comercio_id = e.get("comercio_id")
            nombre = _nombre_comercio(comercios_por_id, comercio_id)
            clicks_por_comercio_counter[nombre] += 1

        if _es_whatsapp(e):
            consulta_origen = (
                e.get("consulta_origen")
                or e.get("consulta")
                or e.get("query")
                or ""
            )

            if not consulta_origen and e.get("busqueda_id"):
                b = busquedas_por_id.get(str(e.get("busqueda_id")))
                if b:
                    consulta_origen = _consulta_busqueda(b)

            consulta_origen = str(consulta_origen).strip()

            if consulta_origen:
                whatsapp_por_consulta_counter[consulta_origen] += 1

    def _top(counter, limite=15):
        return [
            {"nombre": nombre, "cantidad": cantidad}
            for nombre, cantidad in counter.most_common(limite)
        ]

    ctr_whatsapp = 0
    if total_busquedas:
        ctr_whatsapp = round((len(eventos_whatsapp) / total_busquedas) * 100, 1)

    stats = {
        "total_busquedas": total_busquedas,
        "total_eventos": total_eventos,
        "total_whatsapp": len(eventos_whatsapp),
        "total_telefono": len(eventos_telefono),
        "total_vistas": len(eventos_vista),
        "ctr_whatsapp": ctr_whatsapp,
    }

    top_busquedas = _top(top_busquedas_counter, 20)
    top_sin_resultados = _top(top_sin_resultados_counter, 20)
    eventos_por_tipo = _top(eventos_por_tipo_counter, 20)
    clicks_por_comercio = _top(clicks_por_comercio_counter, 20)
    whatsapp_por_consulta = _top(whatsapp_por_consulta_counter, 20)

    eventos_recientes = []
    for e in eventos[:40]:
        eventos_recientes.append({
            "fecha": e.get("created_at", ""),
            "tipo": _tipo_evento(e) or "sin_tipo",
            "comercio": _nombre_comercio(comercios_por_id, e.get("comercio_id")),
            "consulta": (
                e.get("consulta_origen")
                or e.get("consulta")
                or e.get("query")
                or ""
            ),
        })

    template = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Analytics admin V1 - ClickLocal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f6f7fb;
      color: #222;
    }

    .wrap {
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 18px;
    }

    h1 {
      margin: 0 0 6px;
      font-size: 28px;
    }

    .muted {
      color: #666;
      font-size: 14px;
    }

    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .btn {
      display: inline-block;
      padding: 9px 13px;
      border-radius: 999px;
      background: #fff;
      color: #222;
      text-decoration: none;
      border: 1px solid #ddd;
      font-size: 14px;
    }

    .btn.active {
      background: #ff7a00;
      color: white;
      border-color: #ff7a00;
      font-weight: bold;
    }

    .cards {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }

    .card {
      background: white;
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(0,0,0,.06);
      border: 1px solid #eee;
    }

    .card .num {
      font-size: 28px;
      font-weight: bold;
      margin-bottom: 4px;
    }

    .card .label {
      color: #666;
      font-size: 13px;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-top: 14px;
    }

    .panel {
      background: white;
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(0,0,0,.06);
      border: 1px solid #eee;
      overflow: hidden;
    }

    .panel h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }

    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid #eee;
      vertical-align: top;
    }

    th {
      color: #555;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }

    .empty {
      color: #777;
      padding: 10px 0;
      font-size: 14px;
    }

    .warn {
      background: #fff7e6;
      border: 1px solid #ffd58a;
      color: #5c3b00;
      border-radius: 12px;
      padding: 12px 14px;
      margin: 14px 0;
      font-size: 14px;
    }

    .full {
      grid-column: 1 / -1;
    }

    @media (max-width: 900px) {
      .cards {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .grid {
        grid-template-columns: 1fr;
      }

      .topbar {
        flex-direction: column;
      }

      .actions {
        justify-content: flex-start;
      }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>Analytics admin V1</h1>
        <div class="muted">{{ periodo_label }} · ClickLocal Paraná</div>
      </div>

      <div class="actions">
        <a class="btn" href="{{ url_for('admin') }}">← Volver al admin</a>
        <a class="btn {% if dias_raw == '7' %}active{% endif %}" href="{{ url_for('admin_analytics', dias=7) }}">7 días</a>
        <a class="btn {% if dias_raw == '30' %}active{% endif %}" href="{{ url_for('admin_analytics', dias=30) }}">30 días</a>
        <a class="btn {% if dias_raw == '90' %}active{% endif %}" href="{{ url_for('admin_analytics', dias=90) }}">90 días</a>
        <a class="btn {% if dias_raw == 'todos' %}active{% endif %}" href="{{ url_for('admin_analytics', dias='todos') }}">Todo</a>
      </div>
    </div>

    {% if errores %}
      <div class="warn">
        <strong>Atención:</strong> hubo problemas leyendo alguna tabla.
        {% for error in errores %}
          <div>{{ error }}</div>
        {% endfor %}
      </div>
    {% endif %}

    <div class="cards">
      <div class="card">
        <div class="num">{{ stats.total_busquedas }}</div>
        <div class="label">Búsquedas públicas</div>
      </div>

      <div class="card">
        <div class="num">{{ stats.total_eventos }}</div>
        <div class="label">Eventos registrados</div>
      </div>

      <div class="card">
        <div class="num">{{ stats.total_whatsapp }}</div>
        <div class="label">Clicks WhatsApp</div>
      </div>

      <div class="card">
        <div class="num">{{ stats.total_telefono }}</div>
        <div class="label">Clicks teléfono</div>
      </div>

      <div class="card">
        <div class="num">{{ stats.total_vistas }}</div>
        <div class="label">Vistas / aperturas</div>
      </div>

      <div class="card">
        <div class="num">{{ stats.ctr_whatsapp }}%</div>
        <div class="label">WhatsApp / búsquedas</div>
      </div>
    </div>

    <div class="grid">
      <div class="panel">
        <h2>Búsquedas más repetidas</h2>
        {% if top_busquedas %}
          <table>
            <thead>
              <tr>
                <th>Búsqueda</th>
                <th>Cantidad</th>
              </tr>
            </thead>
            <tbody>
              {% for item in top_busquedas %}
                <tr>
                  <td>{{ item.nombre }}</td>
                  <td>{{ item.cantidad }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="empty">Todavía no hay búsquedas para este período.</div>
        {% endif %}
      </div>

      <div class="panel">
        <h2>Búsquedas sin resultados</h2>
        {% if top_sin_resultados %}
          <table>
            <thead>
              <tr>
                <th>Búsqueda</th>
                <th>Cantidad</th>
              </tr>
            </thead>
            <tbody>
              {% for item in top_sin_resultados %}
                <tr>
                  <td>{{ item.nombre }}</td>
                  <td>{{ item.cantidad }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="empty">No hay búsquedas sin resultados detectadas en este período.</div>
        {% endif %}
      </div>

      <div class="panel">
        <h2>Clicks por comercio</h2>
        {% if clicks_por_comercio %}
          <table>
            <thead>
              <tr>
                <th>Comercio</th>
                <th>Clicks</th>
              </tr>
            </thead>
            <tbody>
              {% for item in clicks_por_comercio %}
                <tr>
                  <td>{{ item.nombre }}</td>
                  <td>{{ item.cantidad }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="empty">Todavía no hay clicks asociados a comercios.</div>
        {% endif %}
      </div>

      <div class="panel">
        <h2>WhatsApp por búsqueda de origen</h2>
        {% if whatsapp_por_consulta %}
          <table>
            <thead>
              <tr>
                <th>Consulta de origen</th>
                <th>Clicks WhatsApp</th>
              </tr>
            </thead>
            <tbody>
              {% for item in whatsapp_por_consulta %}
                <tr>
                  <td>{{ item.nombre }}</td>
                  <td>{{ item.cantidad }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="empty">Todavía no hay atribución de WhatsApp por búsqueda.</div>
        {% endif %}
      </div>

      <div class="panel">
        <h2>Eventos por tipo</h2>
        {% if eventos_por_tipo %}
          <table>
            <thead>
              <tr>
                <th>Tipo</th>
                <th>Cantidad</th>
              </tr>
            </thead>
            <tbody>
              {% for item in eventos_por_tipo %}
                <tr>
                  <td>{{ item.nombre }}</td>
                  <td>{{ item.cantidad }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="empty">Todavía no hay eventos registrados.</div>
        {% endif %}
      </div>

      <div class="panel">
        <h2>Eventos recientes</h2>
        {% if eventos_recientes %}
          <table>
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Tipo</th>
                <th>Comercio</th>
                <th>Consulta</th>
              </tr>
            </thead>
            <tbody>
              {% for item in eventos_recientes %}
                <tr>
                  <td>{{ item.fecha }}</td>
                  <td>{{ item.tipo }}</td>
                  <td>{{ item.comercio }}</td>
                  <td>{{ item.consulta }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="empty">No hay eventos recientes para mostrar.</div>
        {% endif %}
      </div>
    </div>
  </div>
</body>
</html>
"""

    return render_template_string(
        template,
        periodo_label=periodo_label,
        dias_raw=dias_raw,
        errores=errores,
        stats=stats,
        top_busquedas=top_busquedas,
        top_sin_resultados=top_sin_resultados,
        eventos_por_tipo=eventos_por_tipo,
        clicks_por_comercio=clicks_por_comercio,
        whatsapp_por_consulta=whatsapp_por_consulta,
        eventos_recientes=eventos_recientes,
    )

if __name__ == "__main__":
    app.run(debug=True)
