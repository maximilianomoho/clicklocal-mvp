from flask import Flask, render_template, send_from_directory, send_file, request, redirect, url_for, session
from werkzeug.utils import secure_filename
import os
from decimal import Decimal, InvalidOperation
import uuid
from PIL import Image, ImageOps, ImageDraw, ImageFont
from io import BytesIO
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    print("ClickLocal fotos: soporte HEIC/HEIF activo", flush=True)
except Exception as e:
    print("ClickLocal fotos: soporte HEIC/HEIF no disponible:", e, flush=True)

import json
import datetime
import time
from threading import Lock
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
# Límite de seguridad para cargas desde celulares.
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.errorhandler(413)
def carga_demasiado_grande(error):
    return (
        "Las fotos seleccionadas son demasiado pesadas. "
        "Volvé al panel y probá con menos fotos o imágenes más livianas.",
        413
    )


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



# ============================================================
# CLICKLOCAL: LOGO DEL NEGOCIO V1
#
# Recibe una foto, captura o archivo de imagen y la acomoda
# dentro de un cuadrado blanco sin recortarla ni deformarla.
# ============================================================

def procesar_logo_clicklocal(archivo):
    if not archivo or not getattr(archivo, "filename", "").strip():
        raise ValueError("No se recibió ninguna imagen.")

    try:
        archivo.stream.seek(0)
        imagen = Image.open(archivo.stream)
        imagen.load()
        imagen = ImageOps.exif_transpose(imagen)
    except Exception as e:
        raise ValueError(
            "No se pudo abrir la imagen seleccionada."
        ) from e

    if imagen.width < 20 or imagen.height < 20:
        raise ValueError("La imagen es demasiado pequeña.")

    tiene_transparencia = (
        imagen.mode in ("RGBA", "LA")
        or (
            imagen.mode == "P"
            and "transparency" in imagen.info
        )
    )

    if tiene_transparencia:
        imagen = imagen.convert("RGBA")

        fondo_original = Image.new(
            "RGBA",
            imagen.size,
            (255, 255, 255, 255)
        )

        fondo_original.alpha_composite(imagen)
        imagen = fondo_original.convert("RGB")
    else:
        imagen = imagen.convert("RGB")

    tamanio_canvas = 800

    remuestreo = getattr(Image, "Resampling", Image)

    # CLICKLOCAL: RECORTE SEGURO DE BORDE DE LOGO V1
    #
    # Solo recorta cuando el borde exterior es mayormente
    # blanco. Así evitamos cortar logos normales que utilizan
    # todo el espacio de la imagen.
    ancho_antes_recorte, alto_antes_recorte = imagen.size

    if ancho_antes_recorte >= 100 and alto_antes_recorte >= 100:
        muestra_borde = imagen.resize(
            (100, 100),
            remuestreo.LANCZOS
        )

        pixeles = muestra_borde.load()
        grosor_muestra = 6
        pixeles_borde = 0
        pixeles_blancos = 0

        for y in range(100):
            for x in range(100):
                esta_en_borde = (
                    x < grosor_muestra
                    or x >= 100 - grosor_muestra
                    or y < grosor_muestra
                    or y >= 100 - grosor_muestra
                )

                if not esta_en_borde:
                    continue

                pixeles_borde += 1
                rojo, verde, azul = pixeles[x, y]

                if rojo >= 235 and verde >= 235 and azul >= 235:
                    pixeles_blancos += 1

        proporcion_blanca = (
            pixeles_blancos / pixeles_borde
            if pixeles_borde
            else 0
        )

        if proporcion_blanca >= 0.70:
            recorte_x = max(
                1,
                round(ancho_antes_recorte * 0.08)
            )
            recorte_y = max(
                1,
                round(alto_antes_recorte * 0.08)
            )

            ancho_recortado = (
                ancho_antes_recorte - recorte_x * 2
            )
            alto_recortado = (
                alto_antes_recorte - recorte_y * 2
            )

            if ancho_recortado >= 20 and alto_recortado >= 20:
                imagen = imagen.crop((
                    recorte_x,
                    recorte_y,
                    ancho_antes_recorte - recorte_x,
                    alto_antes_recorte - recorte_y
                ))

                print(
                    "LOGO CLICKLOCAL: borde blanco detectado, "
                    "recorte suave aplicado",
                    flush=True
                )

    # Ajusta el logo permitiendo también agrandar imágenes
    # pequeñas, siempre sin deformarlas.
    escala_logo = min(
        tamanio_canvas / imagen.width,
        tamanio_canvas / imagen.height
    )

    nuevo_ancho = max(
        1,
        round(imagen.width * escala_logo)
    )
    nuevo_alto = max(
        1,
        round(imagen.height * escala_logo)
    )

    imagen = imagen.resize(
        (nuevo_ancho, nuevo_alto),
        remuestreo.LANCZOS
    )

    canvas = Image.new(
        "RGB",
        (tamanio_canvas, tamanio_canvas),
        (255, 255, 255)
    )

    posicion_x = (tamanio_canvas - imagen.width) // 2
    posicion_y = (tamanio_canvas - imagen.height) // 2

    canvas.paste(
        imagen,
        (posicion_x, posicion_y)
    )

    buffer_logo = BytesIO()

    canvas.save(
        buffer_logo,
        format="PNG",
        optimize=True
    )

    buffer_logo.seek(0)

    print(
        "LOGO CLICKLOCAL OK | "
        f"archivo={archivo.filename} | "
        f"original={imagen.width}x{imagen.height} | "
        f"final={tamanio_canvas}x{tamanio_canvas} | "
        f"bytes={len(buffer_logo.getvalue())}",
        flush=True
    )

    return buffer_logo



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
    historia_id=None,
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
        "historia_id": uuid_o_none(historia_id),
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



# ============================================================
# CLICKLOCAL: ANALYTICS DE HISTORIAS V1
# ============================================================

def _historia_publica_para_analytics(historia_id):
    historia_id = uuid_o_none(historia_id)

    if not historia_id:
        return None, None

    try:
        historia_res = (
            supabase_admin
            .table("historias")
            .select(
                "id,comercio_id,publicacion_id,"
                "activa,eliminada,expires_at"
            )
            .eq("id", historia_id)
            .limit(1)
            .execute()
        )

        historias = historia_res.data or []

        if not historias:
            return None, None

        historia = historias[0]

        if historia.get("activa") is not True:
            return None, None

        if historia.get("eliminada") is True:
            return None, None

        if not _historia_esta_vigente(historia):
            return None, None

        comercio_id = historia.get("comercio_id")

        if not comercio_id:
            return None, None

        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,activo,plan")
            .eq("id", comercio_id)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return None, None

        comercio = comercios[0]

        if comercio.get("activo") is False:
            return None, None

        plan = str(
            comercio.get("plan") or "gratis"
        ).strip().lower()

        if plan != "premium":
            return None, None

        return historia, comercio

    except Exception as e:
        print(
            "ERROR VALIDANDO HISTORIA PARA ANALYTICS:",
            e,
            flush=True
        )
        return None, None


@app.route(
    "/analytics/historias/vista/<historia_id>",
    methods=["POST"]
)
def analytics_historia_vista(historia_id):
    import hashlib

    historia, comercio = _historia_publica_para_analytics(
        historia_id
    )

    if not historia or not comercio:
        return "", 404

    historia_id_valida = str(historia.get("id"))

    clave_historia = hashlib.sha256(
        historia_id_valida.encode("utf-8")
    ).hexdigest()[:16]

    vistas_sesion_raw = str(
        session.get("historias_vistas_sesion") or ""
    )

    vistas_sesion = [
        item
        for item in vistas_sesion_raw.split(".")
        if item
    ]

    if clave_historia in vistas_sesion:
        return "", 204

    registrado = analytics_registrar_evento(
        "vista_historia",
        comercio_id=historia.get("comercio_id"),
        publicacion_id=historia.get("publicacion_id"),
        historia_id=historia.get("id"),
        origen="visor_historia",
        metadata={
            "medicion": "una_vista_por_historia_y_sesion"
        }
    )

    if registrado:
        vistas_sesion.append(clave_historia)

        # Máximo práctico: cubre todas las historias públicas
        # disponibles durante una sesión sin inflar la cookie.
        vistas_sesion = vistas_sesion[-120:]

        session["historias_vistas_sesion"] = ".".join(
            vistas_sesion
        )
        session.modified = True

    return "", 204


@app.route(
    "/analytics/historias/publicacion/<historia_id>"
)
def analytics_historia_publicacion(historia_id):
    historia, comercio = _historia_publica_para_analytics(
        historia_id
    )

    if not historia or not comercio:
        return redirect(url_for("inicio"))

    publicacion_id = uuid_o_none(
        historia.get("publicacion_id")
    )

    if not publicacion_id:
        return redirect(
            f"/comercio/{historia.get('comercio_id')}"
        )

    try:
        publicacion_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,comercio_id,activa,eliminada")
            .eq("id", publicacion_id)
            .eq("comercio_id", historia.get("comercio_id"))
            .eq("activa", True)
            .eq("eliminada", False)
            .limit(1)
            .execute()
        )

        if not (publicacion_res.data or []):
            return redirect(
                f"/comercio/{historia.get('comercio_id')}"
            )

        analytics_registrar_evento(
            "click_historia_publicacion",
            comercio_id=historia.get("comercio_id"),
            publicacion_id=publicacion_id,
            historia_id=historia.get("id"),
            origen="boton_historia_publicacion"
        )

        return redirect(f"/detalle/{publicacion_id}")

    except Exception as e:
        print(
            "ERROR REGISTRANDO CLIC DE HISTORIA A PUBLICACIÓN:",
            e,
            flush=True
        )

        return redirect(
            f"/comercio/{historia.get('comercio_id')}"
        )


@app.route(
    "/analytics/historias/comercio/<historia_id>"
)
def analytics_historia_comercio(historia_id):
    historia, comercio = _historia_publica_para_analytics(
        historia_id
    )

    if not historia or not comercio:
        return redirect(url_for("inicio"))

    comercio_id = historia.get("comercio_id")

    analytics_registrar_evento(
        "click_historia_comercio",
        comercio_id=comercio_id,
        publicacion_id=historia.get("publicacion_id"),
        historia_id=historia.get("id"),
        origen="boton_historia_comercio"
    )

    return redirect(f"/comercio/{comercio_id}")


# ============================================================
# CLICKLOCAL: LO MÁS VISTO POR VISITAS REALES
#
# Cuenta únicamente eventos "visita_publicacion"
# registrados durante los últimos 7 días.
# La caché evita consultar toda la tabla de Analytics
# en cada apertura de la portada.
# ============================================================

CACHE_MAS_VISTAS_PUBLICACIONES = {
    "actualizado_en": 0.0,
    "conteos": {},
}

CACHE_MAS_VISTAS_PUBLICACIONES_LOCK = Lock()
CACHE_MAS_VISTAS_PUBLICACIONES_TTL = 300


def obtener_conteos_visitas_publicaciones():
    from datetime import datetime, timedelta, timezone

    ahora = time.monotonic()

    desde_utc = (
        datetime.now(timezone.utc)
        - timedelta(days=7)
    ).isoformat()

    with CACHE_MAS_VISTAS_PUBLICACIONES_LOCK:
        actualizado_en = (
            CACHE_MAS_VISTAS_PUBLICACIONES["actualizado_en"]
        )

        if (
            actualizado_en
            and ahora - actualizado_en
            < CACHE_MAS_VISTAS_PUBLICACIONES_TTL
        ):
            return dict(
                CACHE_MAS_VISTAS_PUBLICACIONES["conteos"]
            )

    conteos = {}
    inicio = 0
    pagina = 1000

    try:
        while True:
            respuesta = (
                supabase_admin
                .table("eventos_analytics")
                .select("publicacion_id")
                .eq("tipo_evento", "visita_publicacion")
                .gte("created_at", desde_utc)
                .range(inicio, inicio + pagina - 1)
                .execute()
            )

            filas = respuesta.data or []

            for fila in filas:
                publicacion_id = fila.get("publicacion_id")

                if not publicacion_id:
                    continue

                publicacion_id = str(publicacion_id)

                conteos[publicacion_id] = (
                    conteos.get(publicacion_id, 0) + 1
                )

            if len(filas) < pagina:
                break

            inicio += pagina

    except Exception as error:
        print(
            "ERROR cargando visitas para Lo más visto:",
            error,
            flush=True
        )

        # Si ya existía una caché válida, conserva esos datos.
        # Si todavía no había caché, devuelve una lista vacía
        # y la portada continúa mostrando Publicaciones recientes.
        with CACHE_MAS_VISTAS_PUBLICACIONES_LOCK:
            return dict(
                CACHE_MAS_VISTAS_PUBLICACIONES["conteos"]
            )

    with CACHE_MAS_VISTAS_PUBLICACIONES_LOCK:
        CACHE_MAS_VISTAS_PUBLICACIONES["actualizado_en"] = ahora
        CACHE_MAS_VISTAS_PUBLICACIONES["conteos"] = dict(conteos)

    return dict(conteos)


# INICIO / PLATAFORMA
@app.route("/")
@app.route("/index.html")
def inicio():
    from urllib.parse import quote
    import unicodedata

    comercio = session.get("comercio") or comercio_default()
    publicaciones_finales = []
    publicaciones_mas_vistas = []
    comercios_relacionados = []
    historias_publicas = []
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

    def imagen_publica_de_cartelera(item):
        imagenes = item.get("imagenes") or []
        primera_imagen = ""

        if isinstance(imagenes, list) and imagenes:
            primera_imagen = imagenes[0]

        return (
            item.get("imagen_principal")
            or item.get("imagen_url")
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
            .select("id,nombre_negocio,direccion,direccion_mostrar,venta_online,ciudad,categoria,logo_url,whatsapp,activo,plan")
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
            .select("id,nombre,precio,descripcion,imagenes,imagen_principal,imagen_url,activa,comercio_id,direccion_mostrar,created_at,orden_grilla_at")
            .eq("activa", True)
            .order("orden_grilla_at", desc=True)
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
                "comercio_id": comercio_id,
                "tipo": "publicacion",
                "nombre": pub.get("nombre"),
                "precio": pub.get("precio"),
                "descripcion": pub.get("descripcion"),
                "imagen_url": imagen_publica,
                "comercio": comercio_pub.get("nombre_negocio", "Comercio local"),
                "direccion_mostrar": ubicacion_publica(comercio_pub, pub.get("direccion_mostrar")),
                "categoria": comercio_pub.get("categoria"),
                "created_at": pub.get("created_at"),
                "orden_grilla_at": (
                    pub.get("orden_grilla_at")
                    or pub.get("created_at")
                ),
                "_score_busqueda": score_total,
                "_plan": str(comercio_pub.get("plan") or "gratis").lower(),
                "_coincidencias": coincidencias,
            })

        # ====================================================
        # 1B) TIPO CARTELERA PUBLICA: CARTELERAS ACTIVAS
        # ====================================================
        carteleras_res = (
            supabase_admin
            .table("carteleras")
            .select("id,comercio_id,titulo,descripcion,genero,clasificacion,direccion_mostrar,precio_general,precios_detalle,promociones,imagen_url,imagenes,imagen_principal,activa,created_at")
            .eq("activa", True)
            .order("created_at", desc=True)
            .limit(limite)
            .execute()
        )

        carteleras_publicas = carteleras_res.data or []

        comercio_ids_carteleras = [
            item.get("comercio_id")
            for item in carteleras_publicas
            if item.get("comercio_id")
        ]

        comercios_carteleras_por_id = cargar_comercios_por_id(comercio_ids_carteleras)

        for item in carteleras_publicas:
            comercio_id = item.get("comercio_id")
            comercio_cartelera = comercios_carteleras_por_id.get(comercio_id, {})

            if not comercio_cartelera or comercio_cartelera.get("activo") is False:
                continue

            texto_para_buscar = " ".join([
                str(item.get("titulo") or ""),
                str(item.get("descripcion") or ""),
                str(item.get("genero") or ""),
                str(item.get("clasificacion") or ""),
                str(item.get("promociones") or ""),
                str(comercio_cartelera.get("nombre_negocio") or ""),
                str(comercio_cartelera.get("categoria") or ""),
                str(comercio_cartelera.get("ciudad") or ""),
            ])

            score_titulo, coincidencias_titulo = calcular_score_busqueda(item.get("titulo"), peso=5)
            score_descripcion, coincidencias_descripcion = calcular_score_busqueda(
                " ".join([
                    str(item.get("descripcion") or ""),
                    str(item.get("genero") or ""),
                    str(item.get("clasificacion") or ""),
                    str(item.get("promociones") or ""),
                ]),
                peso=2
            )
            score_comercio, coincidencias_comercio = calcular_score_busqueda(
                " ".join([
                    str(comercio_cartelera.get("nombre_negocio") or ""),
                    str(comercio_cartelera.get("categoria") or ""),
                    str(comercio_cartelera.get("ciudad") or ""),
                ]),
                peso=1
            )

            score_total = score_titulo + score_descripcion + score_comercio

            titulo_normalizado = normalizar_texto(item.get("titulo"))
            if busqueda_normalizada and busqueda_normalizada in titulo_normalizado:
                score_total += 10

            coincidencias = unir_coincidencias(
                coincidencias_titulo,
                coincidencias_descripcion,
                coincidencias_comercio
            )

            if busqueda_normalizada and score_total <= 0:
                continue

            imagen_publica = imagen_publica_de_cartelera(item)

            if comercio_id and imagen_publica and comercio_id not in imagen_por_comercio:
                imagen_por_comercio[comercio_id] = imagen_publica

            publicaciones_finales.append({
                "id": item.get("id"),
                "comercio_id": comercio_id,
                "tipo": "cartelera",
                "nombre": item.get("titulo"),
                "precio": item.get("precio_general"),
                "descripcion": item.get("descripcion"),
                "imagen_url": imagen_publica,
                "comercio": comercio_cartelera.get("nombre_negocio", "Comercio local"),
                "direccion_mostrar": ubicacion_publica(comercio_cartelera, item.get("direccion_mostrar")),
                "categoria": comercio_cartelera.get("categoria") or "Cine y Teatro",
                "created_at": item.get("created_at"),
                "orden_grilla_at": item.get("created_at"),
                "_score_busqueda": score_total,
                "_plan": str(comercio_cartelera.get("plan") or "gratis").lower(),
                "_coincidencias": coincidencias,
            })

        if busqueda_normalizada:
            publicaciones_finales.sort(
                key=lambda item: (
                    item.get("_score_busqueda", 0),
                    1 if item.get("_plan") == "premium" else 0,
                    item.get("orden_grilla_at")
                    or item.get("created_at")
                    or ""
                ),
                reverse=True
            )
        else:
            publicaciones_finales.sort(
                key=lambda item: (
                    item.get("orden_grilla_at")
                    or item.get("created_at")
                    or ""
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

    # ====================================================
    # 3) HISTORIAS PREMIUM PÚBLICAS
    # Un círculo por comercio, con hasta 2 historias vigentes.
    # Si existe logo_url se usa el logo; de lo contrario,
    # se muestran las iniciales del comercio.
    # ====================================================
    try:
        historias_res = (
            supabase_admin
            .table("historias")
            .select(
                "id,comercio_id,imagen_url,texto,publicacion_id,"
                "activa,eliminada,expires_at,created_at"
            )
            .eq("activa", True)
            .eq("eliminada", False)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )

        historias_candidatas = historias_res.data or []

        historias_vigentes = [
            historia
            for historia in historias_candidatas
            if _historia_esta_vigente(historia)
        ]

        comercio_ids_historias = [
            historia.get("comercio_id")
            for historia in historias_vigentes
            if historia.get("comercio_id")
        ]

        comercios_historias_por_id = cargar_comercios_por_id(
            comercio_ids_historias
        )

        publicacion_ids_historias = list({
            historia.get("publicacion_id")
            for historia in historias_vigentes
            if historia.get("publicacion_id")
        })

        publicaciones_vinculadas_validas = set()

        if publicacion_ids_historias:
            publicaciones_vinculadas_res = (
                supabase_admin
                .table("publicaciones")
                .select("id,comercio_id,activa,eliminada")
                .in_("id", publicacion_ids_historias)
                .eq("activa", True)
                .eq("eliminada", False)
                .execute()
            )

            publicaciones_vinculadas_validas = {
                fila.get("id")
                for fila in (publicaciones_vinculadas_res.data or [])
                if fila.get("id")
            }

        publicacion_ids_resultados = {
            item.get("id")
            for item in publicaciones_finales
            if item.get("tipo") == "publicacion" and item.get("id")
        }

        comercio_ids_resultados = {
            item.get("comercio_id")
            for item in publicaciones_finales
            if item.get("comercio_id")
        }

        comercio_ids_resultados.update({
            item.get("comercio_id")
            for item in comercios_relacionados
            if item.get("comercio_id")
        })

        def iniciales_comercio(nombre):
            palabras = [
                palabra
                for palabra in str(nombre or "").strip().split()
                if palabra
            ]

            if not palabras:
                return "CL"

            return "".join(
                palabra[0]
                for palabra in palabras[:2]
            ).upper()

        grupos_por_comercio = {}
        orden_comercios = []

        for historia in historias_vigentes:
            comercio_id = historia.get("comercio_id")
            comercio_historia = comercios_historias_por_id.get(
                comercio_id,
                {}
            )

            if not comercio_historia:
                continue

            if comercio_historia.get("activo") is False:
                continue

            plan_historia = str(
                comercio_historia.get("plan") or "gratis"
            ).strip().lower()

            if plan_historia != "premium":
                continue

            nombre_negocio = (
                comercio_historia.get("nombre_negocio")
                or "Comercio local"
            )

            if busqueda_normalizada:
                texto_historia_busqueda = " ".join([
                    str(historia.get("texto") or ""),
                    str(nombre_negocio),
                    str(comercio_historia.get("categoria") or ""),
                    str(comercio_historia.get("ciudad") or ""),
                ])

                score_historia, _ = calcular_score_busqueda(
                    texto_historia_busqueda,
                    peso=3
                )

                publicacion_id = historia.get("publicacion_id")

                relacionada = (
                    score_historia > 0
                    or publicacion_id in publicacion_ids_resultados
                    or comercio_id in comercio_ids_resultados
                )

                if not relacionada:
                    continue

            if comercio_id not in grupos_por_comercio:
                grupos_por_comercio[comercio_id] = {
                    "comercio_id": comercio_id,
                    "nombre_negocio": nombre_negocio,
                    "iniciales": iniciales_comercio(nombre_negocio),
                    "logo_url": str(
                        comercio_historia.get("logo_url") or ""
                    ).strip(),
                    "comercio_url": f"/comercio/{comercio_id}",
                    "historias": [],
                }

                orden_comercios.append(comercio_id)

            historia_id = historia.get("id")
            publicacion_id = historia.get("publicacion_id")
            publicacion_url = ""

            if (
                publicacion_id
                and publicacion_id in publicaciones_vinculadas_validas
            ):
                publicacion_url = (
                    f"/analytics/historias/publicacion/"
                    f"{historia_id}"
                )

            grupos_por_comercio[comercio_id]["historias"].append({
                "id": historia_id,
                "imagen_url": historia.get("imagen_url") or "",
                "texto": historia.get("texto") or "",
                "publicacion_id": publicacion_id,
                "publicacion_url": publicacion_url,
                "comercio_url": (
                    f"/analytics/historias/comercio/"
                    f"{historia_id}"
                ),
                "vista_url": (
                    f"/analytics/historias/vista/"
                    f"{historia_id}"
                ),
                "expires_at": historia.get("expires_at"),
            })

        historias_publicas = [
            grupos_por_comercio[comercio_id]
            for comercio_id in orden_comercios
            if grupos_por_comercio[comercio_id]["historias"]
        ][:30]

        comercios_con_historia = {
            grupo.get("comercio_id")
            for grupo in historias_publicas
            if grupo.get("comercio_id")
        }

        for item in publicaciones_finales:
            item["tiene_historia"] = (
                item.get("comercio_id") in comercios_con_historia
            )

    except Exception as e:
        print(
            "ERROR CARGANDO HISTORIAS PÚBLICAS:",
            e,
            flush=True
        )
        historias_publicas = []

        for item in publicaciones_finales:
            item["tiene_historia"] = False

    # ====================================================
    # 4) LO MÁS VISTO
    #
    # Solo se calcula en la portada sin búsqueda.
    # No usa orden_grilla_at y no incluye carteleras.
    # Una edición no puede modificar este ranking.
    # ====================================================
    if not busqueda_normalizada:
        conteos_visitas = (
            obtener_conteos_visitas_publicaciones()
        )

        publicaciones_con_visitas = []

        for item in publicaciones_finales:
            if item.get("tipo") != "publicacion":
                continue

            publicacion_id = str(item.get("id") or "")

            if not publicacion_id:
                continue

            visitas_reales = int(
                conteos_visitas.get(publicacion_id, 0) or 0
            )

            if visitas_reales <= 0:
                continue

            item_mas_visto = dict(item)
            item_mas_visto["visitas_reales"] = visitas_reales

            publicaciones_con_visitas.append(
                item_mas_visto
            )

        publicaciones_ordenadas_por_visitas = sorted(
            publicaciones_con_visitas,
            key=lambda item: (
                item.get("visitas_reales", 0),
                item.get("created_at") or "",
                str(item.get("id") or "")
            ),
            reverse=True
        )

        publicaciones_por_comercio = {}

        for item in publicaciones_ordenadas_por_visitas:
            clave_comercio = str(
                item.get("comercio_id")
                or f"sin-comercio:{item.get('id')}"
            )

            cantidad_del_comercio = (
                publicaciones_por_comercio.get(
                    clave_comercio,
                    0
                )
            )

            if cantidad_del_comercio >= 2:
                continue

            publicaciones_mas_vistas.append(item)

            publicaciones_por_comercio[clave_comercio] = (
                cantidad_del_comercio + 1
            )

            if len(publicaciones_mas_vistas) >= 12:
                break

    return render_template(
        "index.html",
        comercio=comercio,
        publicaciones=publicaciones_finales,
        publicaciones_mas_vistas=publicaciones_mas_vistas,
        comercios_relacionados=comercios_relacionados,
        historias_publicas=historias_publicas,
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

def limite_publicaciones_por_plan(comercio):
    plan = str((comercio or {}).get("plan") or "gratis").strip().lower()
    return 100 if plan == "premium" else 30


def limite_listas_por_plan(comercio):
    plan = str((comercio or {}).get("plan") or "gratis").strip().lower()
    return 300 if plan == "premium" else 50


def contar_publicaciones_activas(publicaciones):
    total = 0
    for pub in publicaciones or []:
        if pub.get("eliminada") is True:
            continue
        if pub.get("activa") is True:
            total += 1
    return total


def contar_listas_activas(listas):
    return sum(1 for lista in (listas or []) if lista.get("activa") is True)


def aplicar_limites_plan_comercio(comercio_id, comercio):
    limite_publicaciones = limite_publicaciones_por_plan(comercio)
    limite_listas = limite_listas_por_plan(comercio)

    try:
        publicaciones_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,activa,eliminada,created_at")
            .eq("comercio_id", comercio_id)
            .eq("eliminada", False)
            .order("created_at", desc=True)
            .execute()
        )
        publicaciones = publicaciones_res.data or []
        publicaciones_activas = [p for p in publicaciones if p.get("activa") is True]

        for pub in publicaciones_activas[limite_publicaciones:]:
            supabase_admin.table("publicaciones").update({
                "activa": False,
                "pausada_por_limite_plan": True
            }).eq("id", pub.get("id")).eq("comercio_id", comercio_id).execute()

    except Exception as e:
        print("ERROR aplicando límite de publicaciones:", e, flush=True)

    try:
        listas_res = (
            supabase_admin
            .table("listas_buscables")
            .select("id,activa,created_at")
            .eq("comercio_id", comercio_id)
            .order("created_at", desc=True)
            .execute()
        )
        listas = listas_res.data or []
        listas_activas = [l for l in listas if l.get("activa") is True]

        for lista in listas_activas[limite_listas:]:
            supabase_admin.table("listas_buscables").update({
                "activa": False,
                "pausada_por_limite_plan": True
            }).eq("id", lista.get("id")).eq("comercio_id", comercio_id).execute()

    except Exception as e:
        print("ERROR aplicando límite de listas:", e, flush=True)


def revisar_premium_vencidos():
    import datetime

    hoy = datetime.date.today()

    try:
        res = (
            supabase_admin
            .table("comercios")
            .select("id,plan,fecha_vencimiento_plan")
            .eq("plan", "premium")
            .execute()
        )
        comercios = res.data or []
    except Exception as e:
        print("ERROR buscando Premium vencidos:", e, flush=True)
        return

    for comercio in comercios:
        vencimiento_raw = comercio.get("fecha_vencimiento_plan")
        if not vencimiento_raw:
            continue

        try:
            vencimiento = datetime.date.fromisoformat(str(vencimiento_raw)[:10])
        except Exception:
            continue

        if vencimiento >= hoy:
            continue

        comercio_id = comercio.get("id")
        if not comercio_id:
            continue

        try:
            supabase_admin.table("comercios").update({
                "plan": "gratis",
                "estado_plan": "vencido"
            }).eq("id", comercio_id).execute()

            comercio_gratis = dict(comercio)
            comercio_gratis["plan"] = "gratis"
            aplicar_limites_plan_comercio(comercio_id, comercio_gratis)

        except Exception as e:
            print("ERROR venciendo Premium:", comercio_id, e, flush=True)


@app.route("/panel/subir-foto-publicacion", methods=["POST"])
def subir_foto_publicacion_secuencial():
    user_id = session.get("user_id")

    if not user_id:
        return {
            "ok": False,
            "error": "La sesión venció. Volvé a iniciar sesión."
        }, 401

    archivo = request.files.get("foto")

    if not archivo or not getattr(archivo, "filename", "").strip():
        return {
            "ok": False,
            "error": "No se recibió la foto."
        }, 400

    try:
        buf = procesar_imagen_clicklocal(
            archivo,
            contexto="carga_secuencial"
        )

        nombre_final = f"{uuid.uuid4().hex}.jpg"
        ruta_objeto = f"publicaciones/{nombre_final}"
        contenido = buf.getvalue()

        try:
            supabase_admin.storage.from_("publicaciones").upload(
                ruta_objeto,
                contenido,
                file_options={"content-type": "image/jpeg"}
            )
        except TypeError:
            supabase_admin.storage.from_("publicaciones").upload(
                ruta_objeto,
                contenido,
                {"content-type": "image/jpeg"}
            )

        url_res = (
            supabase_admin
            .storage
            .from_("publicaciones")
            .get_public_url(ruta_objeto)
        )

        if isinstance(url_res, dict):
            public_url = (
                url_res.get("publicURL")
                or url_res.get("publicUrl")
                or url_res.get("public_url")
                or ""
            )
        else:
            public_url = url_res

        public_url = str(public_url or "").strip()

        if not public_url:
            try:
                base = supabase_admin._client.url
                public_url = (
                    f"{base}/storage/v1/object/public/"
                    f"publicaciones/{ruta_objeto}"
                )
            except Exception:
                public_url = ""

        if not public_url:
            raise RuntimeError(
                "La foto se subió, pero no se pudo obtener su dirección."
            )

        return {
            "ok": True,
            "url": public_url
        }

    except Exception as e:
        print(
            "\nERROR SUBIENDO FOTO SECUENCIAL:",
            type(e),
            e,
            flush=True
        )

        return {
            "ok": False,
            "error": "No se pudo subir esta foto. Revisá la conexión e intentá nuevamente."
        }, 500




# ============================================================
# CLICKLOCAL: LOGO DEL NEGOCIO V1
# ============================================================

@app.route("/panel/logo/subir", methods=["POST"])
def subir_logo_negocio():
    user_id = session.get("user_id")
    comercio_sesion = dict(session.get("comercio") or {})

    if not user_id:
        return redirect(url_for("login"))

    comercio_id = comercio_sesion.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    archivo = request.files.get("logo")

    if not archivo or not getattr(
        archivo,
        "filename",
        ""
    ).strip():
        return redirect(
            url_for("panel", logo_error="sin_imagen")
            + "#datos"
        )

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,user_id,nombre_negocio,logo_url")
            .eq("id", comercio_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return redirect(url_for("login"))

        buffer_logo = procesar_logo_clicklocal(archivo)

        nombre_final = f"{uuid.uuid4().hex}.png"
        ruta_objeto = (
            f"logos/{comercio_id}/{nombre_final}"
        )

        contenido_logo = buffer_logo.getvalue()

        try:
            supabase_admin.storage.from_(
                "publicaciones"
            ).upload(
                ruta_objeto,
                contenido_logo,
                file_options={
                    "content-type": "image/png"
                }
            )
        except TypeError:
            supabase_admin.storage.from_(
                "publicaciones"
            ).upload(
                ruta_objeto,
                contenido_logo,
                {
                    "content-type": "image/png"
                }
            )

        url_res = (
            supabase_admin
            .storage
            .from_("publicaciones")
            .get_public_url(ruta_objeto)
        )

        if isinstance(url_res, dict):
            logo_url = (
                url_res.get("publicURL")
                or url_res.get("publicUrl")
                or url_res.get("public_url")
                or ""
            )
        else:
            logo_url = str(url_res or "").strip()

        logo_url = str(logo_url or "").strip()

        if not logo_url:
            try:
                base = supabase_admin._client.url
                logo_url = (
                    f"{base}/storage/v1/object/public/"
                    f"publicaciones/{ruta_objeto}"
                )
            except Exception:
                logo_url = ""

        if not logo_url:
            raise RuntimeError(
                "La imagen se subió, pero no se obtuvo su URL."
            )

        supabase_admin.table("comercios").update({
            "logo_url": logo_url
        }).eq(
            "id",
            comercio_id
        ).eq(
            "user_id",
            user_id
        ).execute()

        comercio_sesion["logo_url"] = logo_url
        session["comercio"] = comercio_sesion
        session.modified = True

        return redirect(
            url_for("panel", logo_ok="subido")
            + "#datos"
        )

    except ValueError as e:
        print(
            "ERROR PROCESANDO LOGO:",
            e,
            flush=True
        )

        return redirect(
            url_for("panel", logo_error="formato")
            + "#datos"
        )

    except Exception as e:
        print(
            "ERROR SUBIENDO LOGO:",
            type(e),
            e,
            flush=True
        )

        return redirect(
            url_for("panel", logo_error="subida")
            + "#datos"
        )


@app.route("/panel/logo/quitar", methods=["POST"])
def quitar_logo_negocio():
    user_id = session.get("user_id")
    comercio_sesion = dict(session.get("comercio") or {})

    if not user_id:
        return redirect(url_for("login"))

    comercio_id = comercio_sesion.get("id")

    if not comercio_id:
        return redirect(url_for("login"))

    try:
        supabase_admin.table("comercios").update({
            "logo_url": None
        }).eq(
            "id",
            comercio_id
        ).eq(
            "user_id",
            user_id
        ).execute()

        comercio_sesion["logo_url"] = None
        session["comercio"] = comercio_sesion
        session.modified = True

        return redirect(
            url_for("panel", logo_ok="quitado")
            + "#datos"
        )

    except Exception as e:
        print(
            "ERROR QUITANDO LOGO:",
            type(e),
            e,
            flush=True
        )

        return redirect(
            url_for("panel", logo_error="quitar")
            + "#datos"
        )


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
    comercio["fecha_vencimiento_plan_mostrar"] = None
    comercio["dias_restantes_plan"] = None

    if plan_actual == "premium" and comercio.get("fecha_vencimiento_plan"):
        try:
            vencimiento_plan = datetime.date.fromisoformat(str(comercio.get("fecha_vencimiento_plan"))[:10])
            hoy_plan = datetime.date.today()
            comercio["fecha_vencimiento_plan_mostrar"] = vencimiento_plan.strftime("%d/%m/%Y")
            comercio["dias_restantes_plan"] = max((vencimiento_plan - hoy_plan).days, 0)
        except Exception:
            comercio["fecha_vencimiento_plan_mostrar"] = comercio.get("fecha_vencimiento_plan")

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

    # ============================================================
    # CLICKLOCAL: MÉTRICAS PREMIUM POR PUBLICACIÓN V1
    #
    # Las visitas comenzaron a registrarse correctamente cuando
    # Supabase habilitó visita_publicacion el 14/07/2026.
    # No se mezclan clics antiguos con visitas que antes no se guardaban.
    # ============================================================
    metricas_publicaciones_desde_iso = (
        "2026-07-14T19:46:00+00:00"
    )
    metricas_publicaciones_desde_mostrar = "14/07/2026"

    for publicacion in publicaciones:
        publicacion["metricas_visitas"] = 0
        publicacion["metricas_whatsapp"] = 0
        publicacion["metricas_conversion"] = None
        publicacion["metricas_desde"] = (
            metricas_publicaciones_desde_mostrar
        )

    if plan_actual == "premium" and publicaciones:
        try:
            ids_publicaciones_metricas = [
                publicacion.get("id")
                for publicacion in publicaciones
                if publicacion.get("id")
            ]

            metricas_por_publicacion = {
                str(publicacion_id): {
                    "visita_publicacion": 0,
                    "click_whatsapp": 0,
                }
                for publicacion_id in ids_publicaciones_metricas
            }

            if ids_publicaciones_metricas:
                inicio_eventos = 0
                tamanio_pagina_eventos = 1000

                while True:
                    eventos_res = (
                        supabase_admin
                        .table("eventos_analytics")
                        .select(
                            "publicacion_id,tipo_evento,"
                            "created_at,origen"
                        )
                        .eq("comercio_id", comercio_id)
                        .in_(
                            "publicacion_id",
                            ids_publicaciones_metricas
                        )
                        .in_(
                            "tipo_evento",
                            [
                                "visita_publicacion",
                                "click_whatsapp",
                            ]
                        )
                        .gte(
                            "created_at",
                            metricas_publicaciones_desde_iso
                        )
                        .range(
                            inicio_eventos,
                            inicio_eventos
                            + tamanio_pagina_eventos
                            - 1
                        )
                        .execute()
                    )

                    eventos = eventos_res.data or []

                    for evento in eventos:
                        publicacion_id_evento = str(
                            evento.get("publicacion_id") or ""
                        )

                        tipo_evento = str(
                            evento.get("tipo_evento") or ""
                        )

                        if (
                            publicacion_id_evento
                            in metricas_por_publicacion
                            and tipo_evento
                            in metricas_por_publicacion[
                                publicacion_id_evento
                            ]
                        ):
                            metricas_por_publicacion[
                                publicacion_id_evento
                            ][tipo_evento] += 1

                    if len(eventos) < tamanio_pagina_eventos:
                        break

                    inicio_eventos += tamanio_pagina_eventos

            for publicacion in publicaciones:
                publicacion_id_actual = str(
                    publicacion.get("id") or ""
                )

                metricas = metricas_por_publicacion.get(
                    publicacion_id_actual,
                    {}
                )

                visitas = int(
                    metricas.get("visita_publicacion", 0)
                )

                clicks_whatsapp = int(
                    metricas.get("click_whatsapp", 0)
                )

                publicacion["metricas_visitas"] = visitas
                publicacion["metricas_whatsapp"] = clicks_whatsapp

                if visitas > 0:
                    publicacion["metricas_conversion"] = round(
                        (clicks_whatsapp / visitas) * 100
                    )
                else:
                    publicacion["metricas_conversion"] = None

        except Exception as e:
            print(
                "ERROR CARGANDO MÉTRICAS DE PUBLICACIONES:",
                e,
                flush=True
            )

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
        imagenes_subidas_secuencial_raw = request.form.get(
            "imagenes_subidas_secuencial",
            "[]"
        ).strip()

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

            try:
                publicacion_actual_res = (
                    supabase_admin
                    .table("publicaciones")
                    .select(
                        "id,nombre,precio,descripcion,activa,"
                        "pausada_por_limite_plan,imagenes,"
                        "imagen_principal,imagen_url,"
                        "created_at,orden_grilla_at"
                    )
                    .eq("id", publicacion_id)
                    .eq("comercio_id", comercio_id)
                    .limit(1)
                    .execute()
                )

                filas_publicacion_actual = (
                    publicacion_actual_res.data or []
                )

                publicacion_actual = (
                    filas_publicacion_actual[0]
                    if filas_publicacion_actual
                    else {}
                )

            except Exception as e:
                return render_template(
                    "panel.html",
                    comercio=comercio,
                    publicaciones=publicaciones,
                    listas_buscables=listas_buscables,
                    error=f"No se pudo leer la publicación: {e}"
                )

            if not publicacion_actual:
                return render_template(
                    "panel.html",
                    comercio=comercio,
                    publicaciones=publicaciones,
                    listas_buscables=listas_buscables,
                    error="La publicación que querés editar no existe."
                )

            estaba_activa = publicacion_actual.get("activa") is True

            if activa and not estaba_activa:
                limite_publicaciones = limite_publicaciones_por_plan(comercio)
                publicaciones_activas = contar_publicaciones_activas(publicaciones)

                if publicaciones_activas >= limite_publicaciones:
                    return render_template(
                        "panel.html",
                        comercio=comercio,
                        publicaciones=publicaciones,
                        listas_buscables=listas_buscables,
                        error=f"Tu plan permite hasta {limite_publicaciones} publicaciones activas. Para activar esta publicación, pausá otra activa o mejorá tu plan."
                    )

            cambios_publicacion = {
                "nombre": nombre,
                "precio": precio,
                "descripcion": descripcion,
                "activa": activa,
                "pausada_por_limite_plan": False if activa else publicacion_actual.get("pausada_por_limite_plan", False),
                "imagenes": imagenes_finales,
                "imagen_principal": imagen_principal_editada,
                "imagen_url": imagen_principal_editada
            }

            precio_actual = normalizar_precio(
                ""
                if publicacion_actual.get("precio") is None
                else str(publicacion_actual.get("precio"))
            )

            imagenes_actuales = (
                publicacion_actual.get("imagenes") or []
            )

            if not isinstance(imagenes_actuales, list):
                imagenes_actuales = []

            imagen_principal_actual = str(
                publicacion_actual.get("imagen_principal")
                or publicacion_actual.get("imagen_url")
                or ""
            ).strip()

            hubo_cambio_real = any([
                str(
                    publicacion_actual.get("nombre") or ""
                ).strip() != nombre,

                precio_actual != precio,

                str(
                    publicacion_actual.get("descripcion") or ""
                ).strip() != descripcion,

                (publicacion_actual.get("activa") is True)
                != activa,

                imagenes_actuales != imagenes_finales,

                imagen_principal_actual
                != imagen_principal_editada,
            ])

            if hubo_cambio_real and activa:
                ahora_utc = datetime.datetime.now(
                    datetime.timezone.utc
                )

                valor_orden_actual = (
                    publicacion_actual.get("orden_grilla_at")
                    or publicacion_actual.get("created_at")
                )

                fecha_orden_actual = None

                if valor_orden_actual:
                    try:
                        fecha_orden_actual = (
                            datetime.datetime.fromisoformat(
                                str(valor_orden_actual).replace(
                                    "Z",
                                    "+00:00"
                                )
                            )
                        )

                        if fecha_orden_actual.tzinfo is None:
                            fecha_orden_actual = (
                                fecha_orden_actual.replace(
                                    tzinfo=datetime.timezone.utc
                                )
                            )

                        fecha_orden_actual = (
                            fecha_orden_actual.astimezone(
                                datetime.timezone.utc
                            )
                        )

                    except Exception:
                        fecha_orden_actual = None

                puede_subir_en_grilla = (
                    fecha_orden_actual is None
                    or ahora_utc - fecha_orden_actual
                    >= datetime.timedelta(hours=24)
                )

                if puede_subir_en_grilla:
                    cambios_publicacion["orden_grilla_at"] = (
                        ahora_utc.isoformat()
                    )

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



        # Fotos ya subidas secuencialmente desde el navegador.
        imagenes_secuenciales = []

        try:
            datos_secuenciales = json.loads(
                imagenes_subidas_secuencial_raw
            ) if imagenes_subidas_secuencial_raw else []

            if not isinstance(datos_secuenciales, list):
                datos_secuenciales = []
        except Exception:
            datos_secuenciales = []

        slots_usados = set()

        for item in datos_secuenciales:
            if not isinstance(item, dict):
                continue

            try:
                slot = int(item.get("slot"))
            except Exception:
                continue

            url = str(item.get("url") or "").strip()

            if (
                slot < 0
                or slot > 5
                or not url
                or slot in slots_usados
            ):
                continue

            slots_usados.add(slot)
            imagenes_secuenciales.append({
                "slot": slot,
                "url": url
            })

        # Compatibilidad con el mecanismo tradicional.
        fotos_a_procesar = []

        if not imagenes_secuenciales:
            for slot in range(6):
                archivo = request.files.get(f"foto_{slot}")

                if archivo and archivo.filename:
                    fotos_a_procesar.append({
                        "slot": slot,
                        "archivo": archivo
                    })

        if activa:
            limite_publicaciones = limite_publicaciones_por_plan(comercio)
            publicaciones_activas = contar_publicaciones_activas(publicaciones)

            if publicaciones_activas >= limite_publicaciones:
                return render_template(
                    "panel.html",
                    comercio=comercio,
                    publicaciones=publicaciones,
                    listas_buscables=listas_buscables,
                    error=f"Tu plan permite hasta {limite_publicaciones} publicaciones activas. Para cargar otra, pausá alguna publicación activa o mejorá tu plan."
                )

        if len(fotos_a_procesar) == 0 and len(imagenes_secuenciales) == 0:
            return render_template(
                "panel.html",
                comercio=comercio,
                publicaciones=publicaciones,
                listas_buscables=listas_buscables,
                error="Tenés que subir al menos 1 foto."
            )

        if len(fotos_a_procesar) + len(imagenes_secuenciales) > 6:
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

        imagenes_urls = [
            {
                "slot": item["slot"],
                "url": item["url"],
                "es_principal": item["slot"] == principal_slot
            }
            for item in imagenes_secuenciales
        ]
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
                .eq("eliminada", False)
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

    # ============================================================
    # HISTORIAS PREMIUM - LECTURA PARA EL PANEL
    # Máximo 2 historias activas y vigentes por comercio.
    # ============================================================
    es_premium = str(comercio.get("plan_actual") or comercio.get("plan") or "").strip().lower() == "premium"
    historias = []
    historias_activas = 0

    if es_premium:
        try:
            historias_res = (
                supabase_admin
                .table("historias")
                .select("*")
                .eq("comercio_id", comercio_id)
                .eq("eliminada", False)
                .order("created_at", desc=True)
                .execute()
            )

            historias = historias_res.data or []
            ahora_utc = datetime.datetime.now(datetime.timezone.utc)

            for historia in historias:
                expires_at_raw = str(historia.get("expires_at") or "").strip()
                vigente = False

                if expires_at_raw:
                    try:
                        expires_at = datetime.datetime.fromisoformat(
                            expires_at_raw.replace("Z", "+00:00")
                        )

                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(
                                tzinfo=datetime.timezone.utc
                            )

                        vigente = expires_at > ahora_utc
                    except Exception:
                        vigente = False

                historia["vigente"] = vigente
                historia["activa_vigente"] = (
                    historia.get("activa") is True
                    and historia.get("eliminada") is not True
                    and vigente
                )

                historia["metricas_vistas"] = 0
                historia["metricas_click_publicacion"] = 0
                historia["metricas_click_comercio"] = 0

            ids_historias_metricas = [
                historia.get("id")
                for historia in historias
                if historia.get("id")
            ]

            if ids_historias_metricas:
                try:
                    metricas_por_historia = {
                        str(historia_id): {
                            "vista_historia": 0,
                            "click_historia_publicacion": 0,
                            "click_historia_comercio": 0,
                        }
                        for historia_id in ids_historias_metricas
                    }

                    inicio_metricas = 0
                    tamanio_pagina_metricas = 1000

                    while True:
                        eventos_res = (
                            supabase_admin
                            .table("eventos_analytics")
                            .select("historia_id,tipo_evento")
                            .in_(
                                "historia_id",
                                ids_historias_metricas
                            )
                            .range(
                                inicio_metricas,
                                inicio_metricas
                                + tamanio_pagina_metricas
                                - 1
                            )
                            .execute()
                        )

                        eventos = eventos_res.data or []

                        for evento in eventos:
                            historia_id_evento = str(
                                evento.get("historia_id") or ""
                            )

                            tipo_evento = str(
                                evento.get("tipo_evento") or ""
                            )

                            if (
                                historia_id_evento
                                in metricas_por_historia
                                and tipo_evento
                                in metricas_por_historia[
                                    historia_id_evento
                                ]
                            ):
                                metricas_por_historia[
                                    historia_id_evento
                                ][tipo_evento] += 1

                        if len(eventos) < tamanio_pagina_metricas:
                            break

                        inicio_metricas += tamanio_pagina_metricas

                    for historia in historias:
                        historia_id_actual = str(
                            historia.get("id") or ""
                        )

                        metricas = metricas_por_historia.get(
                            historia_id_actual,
                            {}
                        )

                        historia["metricas_vistas"] = int(
                            metricas.get("vista_historia", 0)
                        )

                        historia["metricas_click_publicacion"] = int(
                            metricas.get(
                                "click_historia_publicacion",
                                0
                            )
                        )

                        historia["metricas_click_comercio"] = int(
                            metricas.get(
                                "click_historia_comercio",
                                0
                            )
                        )

                except Exception as e:
                    print(
                        "ERROR CARGANDO MÉTRICAS DE HISTORIAS:",
                        e,
                        flush=True
                    )

            historias_activas = sum(
                1
                for historia in historias
                if historia.get("activa_vigente") is True
            )

        except Exception as e:
            print("\nERROR CARGANDO HISTORIAS EN PANEL:", e, flush=True)
            historias = []
            historias_activas = 0

    return render_template(
        "panel.html",
        comercio=comercio,
        publicaciones=publicaciones,
        listas_buscables=listas_buscables,
        es_cine_teatro=es_cine_teatro,
        carteleras=carteleras,
        es_premium=es_premium,
        historias=historias,
        historias_activas=historias_activas,
        limite_historias_activas=2
    )



# ============================================================
# HISTORIAS PREMIUM
# Máximo 2 historias activas y vigentes por comercio.
# ============================================================

def _historia_esta_vigente(historia):
    expires_at_raw = str((historia or {}).get("expires_at") or "").strip()

    if not expires_at_raw:
        return False

    try:
        expires_at = datetime.datetime.fromisoformat(
            expires_at_raw.replace("Z", "+00:00")
        )

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)

        return expires_at > datetime.datetime.now(datetime.timezone.utc)

    except Exception:
        return False


def _historias_activas_del_comercio(comercio_id):
    historias_res = (
        supabase_admin
        .table("historias")
        .select("id,activa,eliminada,expires_at")
        .eq("comercio_id", comercio_id)
        .eq("eliminada", False)
        .execute()
    )

    historias = historias_res.data or []

    return [
        historia
        for historia in historias
        if historia.get("activa") is True
        and _historia_esta_vigente(historia)
    ]


def _contexto_comercio_para_historias():
    user_id = session.get("user_id")

    if not user_id:
        return None, None, "login"

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return None, None, "comercio"

        comercio = comercios[0]
        comercio_id = comercio.get("id")

        if comercio.get("activo") is False:
            return comercio, comercio_id, "bloqueado"

        plan = str(
            comercio.get("plan_actual")
            or comercio.get("plan")
            or "gratis"
        ).strip().lower()

        if plan != "premium":
            return comercio, comercio_id, "premium"

        session["comercio"] = comercio
        session.modified = True

        return comercio, comercio_id, None

    except Exception as e:
        print("ERROR OBTENIENDO COMERCIO PARA HISTORIAS:", e, flush=True)
        return None, None, "servidor"


def _volver_historias(**parametros):
    return redirect(
        url_for("panel", **parametros) + "#historias-premium"
    )


@app.route("/panel/historias/crear", methods=["POST"])
def crear_historia_panel():
    comercio, comercio_id, error_contexto = _contexto_comercio_para_historias()

    if error_contexto == "login":
        return redirect(url_for("login"))

    if error_contexto == "bloqueado":
        return "Esta cuenta fue bloqueada por administración.", 403

    if error_contexto == "premium":
        return _volver_historias(historia_error="solo_premium")

    if error_contexto or not comercio_id:
        return _volver_historias(historia_error="servidor")

    try:
        historias_activas = _historias_activas_del_comercio(comercio_id)

        if len(historias_activas) >= 2:
            return _volver_historias(historia_error="limite")

    except Exception as e:
        print("ERROR CONTANDO HISTORIAS ACTIVAS:", e, flush=True)
        return _volver_historias(historia_error="servidor")

    texto_historia = request.form.get("historia_texto", "").strip()

    if len(texto_historia) > 180:
        return _volver_historias(historia_error="texto")

    publicacion_id = request.form.get(
        "historia_publicacion_id",
        ""
    ).strip()

    if publicacion_id:
        try:
            uuid.UUID(publicacion_id)

            publicacion_res = (
                supabase_admin
                .table("publicaciones")
                .select("id")
                .eq("id", publicacion_id)
                .eq("comercio_id", comercio_id)
                .eq("activa", True)
                .eq("eliminada", False)
                .limit(1)
                .execute()
            )

            if not publicacion_res.data:
                return _volver_historias(
                    historia_error="publicacion"
                )

        except Exception:
            return _volver_historias(historia_error="publicacion")
    else:
        publicacion_id = None

    foto = request.files.get("historia_foto")

    if not foto or not foto.filename:
        return _volver_historias(historia_error="foto")

    try:
        buf = procesar_imagen_clicklocal(
            foto,
            contexto="historia_premium"
        )

        nombre_final = f"{uuid.uuid4().hex}.jpg"
        ruta_objeto = f"historias/{nombre_final}"

        try:
            supabase_admin.storage.from_("publicaciones").upload(
                ruta_objeto,
                buf.getvalue(),
                file_options={"content-type": "image/jpeg"}
            )
        except Exception:
            buf.seek(0)
            supabase_admin.storage.from_("publicaciones").upload(
                ruta_objeto,
                buf,
                file_options={"content-type": "image/jpeg"}
            )

        url_res = (
            supabase_admin
            .storage
            .from_("publicaciones")
            .get_public_url(ruta_objeto)
        )

        if isinstance(url_res, dict):
            imagen_url = (
                url_res.get("publicURL")
                or url_res.get("publicUrl")
                or url_res.get("public_url")
                or ""
            )
        else:
            imagen_url = str(url_res or "").strip()

        if not imagen_url:
            base = supabase_admin._client.url
            imagen_url = (
                f"{base}/storage/v1/object/public/"
                f"publicaciones/{ruta_objeto}"
            )

        expires_at = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=24)
        ).isoformat()

        nueva_historia = {
            "comercio_id": comercio_id,
            "imagen_url": imagen_url,
            "texto": texto_historia or None,
            "publicacion_id": publicacion_id,
            "activa": True,
            "eliminada": False,
            "expires_at": expires_at,
        }

        supabase_admin.table("historias").insert(
            nueva_historia
        ).execute()

        return _volver_historias(historia_ok="creada")

    except Exception as e:
        print("\nERROR CREANDO HISTORIA PREMIUM:", flush=True)
        print(type(e), e, flush=True)
        return _volver_historias(historia_error="guardar")


@app.route(
    "/panel/historias/desactivar/<historia_id>",
    methods=["POST"]
)
def desactivar_historia_panel(historia_id):
    comercio, comercio_id, error_contexto = _contexto_comercio_para_historias()

    if error_contexto == "login":
        return redirect(url_for("login"))

    if error_contexto or not comercio_id:
        return _volver_historias(historia_error="permiso")

    try:
        uuid.UUID(str(historia_id))

        supabase_admin.table("historias").update({
            "activa": False
        }).eq(
            "id", historia_id
        ).eq(
            "comercio_id", comercio_id
        ).eq(
            "eliminada", False
        ).execute()

        return _volver_historias(historia_ok="desactivada")

    except Exception as e:
        print("ERROR DESACTIVANDO HISTORIA:", e, flush=True)
        return _volver_historias(historia_error="desactivar")


@app.route(
    "/panel/historias/activar/<historia_id>",
    methods=["POST"]
)
def activar_historia_panel(historia_id):
    comercio, comercio_id, error_contexto = _contexto_comercio_para_historias()

    if error_contexto == "login":
        return redirect(url_for("login"))

    if error_contexto or not comercio_id:
        return _volver_historias(historia_error="permiso")

    try:
        uuid.UUID(str(historia_id))

        historia_res = (
            supabase_admin
            .table("historias")
            .select("id,activa,eliminada,expires_at")
            .eq("id", historia_id)
            .eq("comercio_id", comercio_id)
            .eq("eliminada", False)
            .limit(1)
            .execute()
        )

        historias = historia_res.data or []

        if not historias:
            return _volver_historias(historia_error="no_existe")

        historia = historias[0]

        if not _historia_esta_vigente(historia):
            return _volver_historias(historia_error="vencida")

        if historia.get("activa") is not True:
            historias_activas = _historias_activas_del_comercio(
                comercio_id
            )

            if len(historias_activas) >= 2:
                return _volver_historias(historia_error="limite")

        supabase_admin.table("historias").update({
            "activa": True
        }).eq(
            "id", historia_id
        ).eq(
            "comercio_id", comercio_id
        ).execute()

        return _volver_historias(historia_ok="activada")

    except Exception as e:
        print("ERROR ACTIVANDO HISTORIA:", e, flush=True)
        return _volver_historias(historia_error="activar")



@app.route(
    "/panel/historias/editar/<historia_id>",
    methods=["POST"]
)
def editar_historia_panel(historia_id):
    comercio, comercio_id, error_contexto = _contexto_comercio_para_historias()

    if error_contexto == "login":
        return redirect(url_for("login"))

    if error_contexto == "bloqueado":
        return "Esta cuenta fue bloqueada por administración.", 403

    if error_contexto == "premium":
        return _volver_historias(historia_error="solo_premium")

    if error_contexto or not comercio_id:
        return _volver_historias(historia_error="permiso")

    try:
        uuid.UUID(str(historia_id))

        historia_res = (
            supabase_admin
            .table("historias")
            .select("id")
            .eq("id", historia_id)
            .eq("comercio_id", comercio_id)
            .eq("eliminada", False)
            .limit(1)
            .execute()
        )

        if not (historia_res.data or []):
            return _volver_historias(historia_error="no_existe")

        texto_historia = request.form.get(
            "historia_texto",
            ""
        ).strip()

        if len(texto_historia) > 180:
            return _volver_historias(historia_error="texto")

        publicacion_id = request.form.get(
            "historia_publicacion_id",
            ""
        ).strip()

        if publicacion_id:
            try:
                uuid.UUID(publicacion_id)

                publicacion_res = (
                    supabase_admin
                    .table("publicaciones")
                    .select("id")
                    .eq("id", publicacion_id)
                    .eq("comercio_id", comercio_id)
                    .eq("activa", True)
                    .eq("eliminada", False)
                    .limit(1)
                    .execute()
                )

                if not publicacion_res.data:
                    return _volver_historias(
                        historia_error="publicacion"
                    )

            except Exception:
                return _volver_historias(
                    historia_error="publicacion"
                )
        else:
            publicacion_id = None

        actualizacion = {
            "texto": texto_historia or None,
            "publicacion_id": publicacion_id,
        }

        foto = request.files.get("historia_foto")

        if foto and foto.filename:
            buf = procesar_imagen_clicklocal(
                foto,
                contexto="historia_premium"
            )

            nombre_final = f"{uuid.uuid4().hex}.jpg"
            ruta_objeto = f"historias/{nombre_final}"

            try:
                supabase_admin.storage.from_("publicaciones").upload(
                    ruta_objeto,
                    buf.getvalue(),
                    file_options={"content-type": "image/jpeg"}
                )
            except Exception:
                buf.seek(0)
                supabase_admin.storage.from_("publicaciones").upload(
                    ruta_objeto,
                    buf,
                    file_options={"content-type": "image/jpeg"}
                )

            url_res = (
                supabase_admin
                .storage
                .from_("publicaciones")
                .get_public_url(ruta_objeto)
            )

            if isinstance(url_res, dict):
                imagen_url = (
                    url_res.get("publicURL")
                    or url_res.get("publicUrl")
                    or url_res.get("public_url")
                    or ""
                )
            else:
                imagen_url = str(url_res or "").strip()

            if not imagen_url:
                base = supabase_admin._client.url
                imagen_url = (
                    f"{base}/storage/v1/object/public/"
                    f"publicaciones/{ruta_objeto}"
                )

            actualizacion["imagen_url"] = imagen_url

        supabase_admin.table("historias").update(
            actualizacion
        ).eq(
            "id", historia_id
        ).eq(
            "comercio_id", comercio_id
        ).eq(
            "eliminada", False
        ).execute()

        return _volver_historias(historia_ok="editada")

    except Exception as e:
        print("ERROR EDITANDO HISTORIA:", e, flush=True)
        return _volver_historias(historia_error="editar")


@app.route(
    "/panel/historias/eliminar/<historia_id>",
    methods=["POST"]
)
def eliminar_historia_panel(historia_id):
    comercio, comercio_id, error_contexto = _contexto_comercio_para_historias()

    if error_contexto == "login":
        return redirect(url_for("login"))

    if error_contexto == "bloqueado":
        return "Esta cuenta fue bloqueada por administración.", 403

    if error_contexto == "premium":
        return _volver_historias(historia_error="solo_premium")

    if error_contexto or not comercio_id:
        return _volver_historias(historia_error="permiso")

    try:
        uuid.UUID(str(historia_id))

        supabase_admin.table("historias").update({
            "activa": False,
            "eliminada": True
        }).eq(
            "id", historia_id
        ).eq(
            "comercio_id", comercio_id
        ).eq(
            "eliminada", False
        ).execute()

        return _volver_historias(historia_ok="eliminada")

    except Exception as e:
        print("ERROR ELIMINANDO HISTORIA:", e, flush=True)
        return _volver_historias(historia_error="eliminar")


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

    # Fotos de cartelera: hasta 6 imágenes.
    # Usamos el bucket existente "publicaciones", carpeta "carteleras/",
    # para no tocar políticas de Storage.
    try:
        foto_principal_slot = int(request.form.get("cartelera_foto_principal") or 1)
    except Exception:
        foto_principal_slot = 1

    if foto_principal_slot < 1 or foto_principal_slot > 6:
        foto_principal_slot = 1

    fotos_a_procesar = []

    for slot in range(1, 7):
        archivo = request.files.get(f"cartelera_foto_{slot}")

        if archivo and getattr(archivo, "filename", "").strip():
            fotos_a_procesar.append({
                "slot": slot,
                "archivo": archivo
            })

    imagenes_procesadas = []
    fotos_procesadas = 0

    for item in fotos_a_procesar:
        slot = item["slot"]
        archivo = item["archivo"]

        try:
            buf = procesar_imagen_clicklocal(archivo, contexto="cartelera")
        except Exception:
            continue

        fotos_procesadas += 1

        nombre_final = f"{uuid.uuid4().hex}.jpg"
        ruta_objeto = f"carteleras/{nombre_final}"

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
            buf.seek(0)
            supabase_admin.storage.from_("publicaciones").upload(
                ruta_objeto,
                buf,
                file_options=file_options
            )

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
            try:
                base = supabase_admin._client.url
                public_url = f"{base}/storage/v1/object/public/publicaciones/{ruta_objeto}"
            except Exception:
                public_url = f"/static/uploads/{nombre_final}"

        imagenes_procesadas.append({
            "slot": slot,
            "url": public_url
        })

    if fotos_a_procesar and fotos_procesadas == 0:
        return redirect(url_for("panel", cartelera_error="fotos"))

    imagen_principal = ""

    if imagenes_procesadas:
        principal = next(
            (img for img in imagenes_procesadas if img["slot"] == foto_principal_slot),
            None
        )

        if principal:
            imagen_principal = principal["url"]
        else:
            imagen_principal = imagenes_procesadas[0]["url"]

    imagenes_urls = [img["url"] for img in imagenes_procesadas]

    nueva_cartelera = {
        "id": cartelera_id,
        "comercio_id": comercio_id,
        "titulo": titulo,
        "imagen_url": imagen_principal or None,
        "imagenes": imagenes_urls,
        "imagen_principal": imagen_principal or None,
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



def _cartelera_panel_contexto(cartelera_id):
    user_id = session.get("user_id")
    comercio = session.get("comercio") or {}
    comercio_id = comercio.get("id")

    if not user_id or not comercio_id:
        return None, "login"

    es_cine_teatro = (comercio.get("categoria") or "").strip().lower() == "cine y teatro"

    if not es_cine_teatro:
        return None, "panel"

    try:
        cartelera_res = (
            supabase_admin
            .table("carteleras")
            .select("id,comercio_id,activa,eliminada")
            .eq("id", cartelera_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )

        filas = cartelera_res.data or []

        if not filas:
            return None, "panel"

        cartelera = filas[0]

        if str(cartelera.get("eliminada")).lower() == "true":
            return None, "panel"

        return {
            "comercio_id": comercio_id,
            "cartelera": cartelera,
        }, None

    except Exception as e:
        print("\nERROR VALIDANDO CARTELERA DEL PANEL:", e, flush=True)
        return None, "panel"


@app.route("/panel/cartelera/pausar/<cartelera_id>", methods=["POST"])
def pausar_cartelera_panel(cartelera_id):
    contexto, destino = _cartelera_panel_contexto(cartelera_id)

    if destino == "login":
        return redirect(url_for("login"))

    if not contexto:
        return redirect(url_for("panel"))

    ahora = datetime.datetime.utcnow().isoformat()

    try:
        (
            supabase_admin
            .table("carteleras")
            .update({
                "activa": False,
                "updated_at": ahora,
            })
            .eq("id", cartelera_id)
            .eq("comercio_id", contexto["comercio_id"])
            .execute()
        )
    except Exception as e:
        print("\nERROR PAUSANDO CARTELERA:", e, flush=True)

    return redirect(url_for("panel"))


@app.route("/panel/cartelera/activar/<cartelera_id>", methods=["POST"])
def activar_cartelera_panel(cartelera_id):
    contexto, destino = _cartelera_panel_contexto(cartelera_id)

    if destino == "login":
        return redirect(url_for("login"))

    if not contexto:
        return redirect(url_for("panel"))

    ahora = datetime.datetime.utcnow().isoformat()

    try:
        (
            supabase_admin
            .table("carteleras")
            .update({
                "activa": True,
                "updated_at": ahora,
            })
            .eq("id", cartelera_id)
            .eq("comercio_id", contexto["comercio_id"])
            .execute()
        )
    except Exception as e:
        print("\nERROR ACTIVANDO CARTELERA:", e, flush=True)

    return redirect(url_for("panel"))


@app.route("/panel/cartelera/eliminar/<cartelera_id>", methods=["POST"])
def eliminar_cartelera_panel(cartelera_id):
    contexto, destino = _cartelera_panel_contexto(cartelera_id)

    if destino == "login":
        return redirect(url_for("login"))

    if not contexto:
        return redirect(url_for("panel"))

    ahora = datetime.datetime.utcnow().isoformat()

    try:
        (
            supabase_admin
            .table("carteleras")
            .update({
                "activa": False,
                "eliminada": True,
                "deleted_at": ahora,
                "updated_at": ahora,
            })
            .eq("id", cartelera_id)
            .eq("comercio_id", contexto["comercio_id"])
            .execute()
        )
    except Exception as e:
        print("\nERROR ELIMINANDO CARTELERA:", e, flush=True)

    return redirect(url_for("panel"))






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

    try:
        listas_res = (
            supabase_admin
            .table("listas_buscables")
            .select("id,activa")
            .eq("comercio_id", comercio_id)
            .eq("activa", True)
            .execute()
        )
        listas_activas = len(listas_res.data or [])
    except Exception as e:
        print("ERROR contando listas activas:", e, flush=True)
        listas_activas = 0

    limite_listas = limite_listas_por_plan(comercio)

    if listas_activas >= limite_listas:
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

    try:
        lista_actual_res = (
            supabase_admin
            .table("listas_buscables")
            .select("id,activa")
            .eq("id", lista_id)
            .eq("comercio_id", comercio_id)
            .limit(1)
            .execute()
        )
        lista_actual = (lista_actual_res.data or [{}])[0]
    except Exception as e:
        print("ERROR leyendo lista actual:", e, flush=True)
        lista_actual = {}

    estaba_activa = lista_actual.get("activa") is True

    if not estaba_activa:
        try:
            listas_res = (
                supabase_admin
                .table("listas_buscables")
                .select("id,activa")
                .eq("comercio_id", comercio_id)
                .eq("activa", True)
                .execute()
            )
            listas_activas = len(listas_res.data or [])
        except Exception as e:
            print("ERROR contando listas activas al editar:", e, flush=True)
            listas_activas = 0

        limite_listas = limite_listas_por_plan(comercio)

        if listas_activas >= limite_listas:
            return redirect(url_for("panel") + "?lista_error=1#listas")

    cambios = {
        "producto_categoria": producto_categoria,
        "atributos_texto": atributos_texto,
        "updated_at": datetime.datetime.utcnow().isoformat(),
        "activa": True,
        "pausada_por_limite_plan": False
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



# Caché temporal en memoria para que la imagen compartida
# no tenga que descargarse y procesarse nuevamente.
CACHE_IMAGENES_COMPARTIR = {}
CACHE_IMAGENES_COMPARTIR_LOCK = Lock()
CACHE_IMAGENES_COMPARTIR_TTL = 3600


def respuesta_imagen_compartir_publicacion(
    datos_imagen,
    estado_cache
):
    respuesta = send_file(
        BytesIO(datos_imagen),
        mimetype="image/jpeg",
        max_age=3600
    )

    respuesta.headers["Cache-Control"] = (
        "public, max-age=3600, "
        "stale-while-revalidate=86400"
    )

    respuesta.headers["X-Content-Type-Options"] = (
        "nosniff"
    )

    respuesta.headers["X-ClickLocal-Preview-Cache"] = (
        estado_cache
    )

    return respuesta


# ============================================================
# CLICKLOCAL: IMAGEN PARA COMPARTIR PUBLICACIÓN V1
#
# Genera una imagen horizontal para WhatsApp, Facebook,
# Telegram y otras vistas previas:
# foto del producto + publicación + negocio + ClickLocal.
# ============================================================

@app.route(
    "/detalle/<publicacion_id>/imagen-compartir.jpg"
)
def imagen_compartir_publicacion(publicacion_id):
    publicacion_id = uuid_o_none(publicacion_id)

    if not publicacion_id:
        return "", 404

    version_imagen = str(
        request.args.get("v") or "3"
    ).strip()[:20]

    clave_cache = (
        f"{publicacion_id}:{version_imagen}"
    )

    ahora = time.monotonic()

    with CACHE_IMAGENES_COMPARTIR_LOCK:
        entrada_cache = CACHE_IMAGENES_COMPARTIR.get(
            clave_cache
        )

        if entrada_cache:
            momento_cache, datos_cache = entrada_cache

            if (
                ahora - momento_cache
                < CACHE_IMAGENES_COMPARTIR_TTL
            ):
                return respuesta_imagen_compartir_publicacion(
                    datos_cache,
                    "HIT"
                )

            CACHE_IMAGENES_COMPARTIR.pop(
                clave_cache,
                None
            )

    try:
        publicacion_res = (
            supabase_admin
            .table("publicaciones")
            .select(
                "id,nombre,imagenes,imagen_principal,"
                "imagen_url,activa,comercio_id"
            )
            .eq("id", publicacion_id)
            .eq("activa", True)
            .limit(1)
            .execute()
        )

        publicaciones = publicacion_res.data or []

        if not publicaciones:
            return "", 404

        publicacion = publicaciones[0]
        comercio_id = publicacion.get("comercio_id")

        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,nombre_negocio,activo")
            .eq("id", comercio_id)
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return "", 404

        nombre_publicacion = str(
            publicacion.get("nombre")
            or "Publicación"
        ).strip()

        nombre_negocio = str(
            comercios[0].get("nombre_negocio")
            or "Comercio local"
        ).strip()

        imagenes = publicacion.get("imagenes") or []
        primera_imagen = ""

        if isinstance(imagenes, list):
            primera_imagen = next(
                (
                    str(imagen).strip()
                    for imagen in imagenes
                    if imagen
                ),
                ""
            )

        imagen_url = str(
            publicacion.get("imagen_principal")
            or publicacion.get("imagen_url")
            or primera_imagen
            or ""
        ).strip()

        ancho = 1200
        alto = 630
        alto_foto = 630

        imagen_final = Image.new(
            "RGB",
            (ancho, alto),
            (255, 255, 255)
        )

        def cargar_fuente(tamanio, negrita=False):
            if negrita:
                rutas = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                ]
            else:
                rutas = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                ]

            for ruta_fuente in rutas:
                try:
                    return ImageFont.truetype(
                        ruta_fuente,
                        tamanio
                    )
                except Exception:
                    continue

            return ImageFont.load_default()

        fuente_fallback = cargar_fuente(
            110,
            negrita=True
        )

        imagen_producto = None

        if imagen_url:
            try:
                datos_imagen = None

                if imagen_url.startswith("/static/"):
                    import os

                    raiz_app = os.path.abspath(app.root_path)
                    ruta_local = os.path.abspath(
                        os.path.join(
                            raiz_app,
                            imagen_url.lstrip("/")
                        )
                    )

                    if not ruta_local.startswith(
                        raiz_app + os.sep
                    ):
                        raise ValueError(
                            "Ruta de imagen local inválida."
                        )

                    with open(ruta_local, "rb") as archivo:
                        datos_imagen = archivo.read(
                            15 * 1024 * 1024 + 1
                        )

                else:
                    from urllib.parse import urlparse
                    from urllib.request import Request, urlopen

                    url_analizada = urlparse(imagen_url)

                    if url_analizada.scheme != "https":
                        raise ValueError(
                            "La imagen debe usar HTTPS."
                        )

                    solicitud = Request(
                        imagen_url,
                        headers={
                            "User-Agent":
                            "ClickLocal-Preview/1.0"
                        }
                    )

                    with urlopen(
                        solicitud,
                        timeout=10
                    ) as respuesta_imagen:
                        datos_imagen = respuesta_imagen.read(
                            15 * 1024 * 1024 + 1
                        )

                if (
                    not datos_imagen
                    or len(datos_imagen) > 15 * 1024 * 1024
                ):
                    raise ValueError(
                        "La imagen supera el límite permitido."
                    )

                imagen_producto = Image.open(
                    BytesIO(datos_imagen)
                )

                imagen_producto = ImageOps.exif_transpose(
                    imagen_producto
                ).convert("RGB")

            except Exception as error_imagen:
                print(
                    "CLICKLOCAL: no se pudo cargar la foto "
                    "para compartir publicación:",
                    error_imagen,
                    flush=True
                )

        if imagen_producto is not None:
            remuestreo = getattr(
                getattr(Image, "Resampling", Image),
                "LANCZOS"
            )

            portada = ImageOps.fit(
                imagen_producto,
                (ancho, alto_foto),
                method=remuestreo,
                centering=(0.5, 0.5)
            )

            imagen_final.paste(portada, (0, 0))

        else:
            fondo = Image.new(
                "RGB",
                (ancho, alto_foto),
                (15, 23, 42)
            )

            dibujo_fondo = ImageDraw.Draw(fondo)

            texto_fallback = "ClickLocal"

            caja_fallback = dibujo_fondo.textbbox(
                (0, 0),
                texto_fallback,
                font=fuente_fallback
            )

            ancho_fallback = (
                caja_fallback[2] - caja_fallback[0]
            )

            alto_fallback = (
                caja_fallback[3] - caja_fallback[1]
            )

            dibujo_fondo.text(
                (
                    (ancho - ancho_fallback) / 2,
                    (alto_foto - alto_fallback) / 2
                    - caja_fallback[1]
                ),
                texto_fallback,
                font=fuente_fallback,
                fill=(255, 255, 255)
            )

            imagen_final.paste(fondo, (0, 0))

        buffer_imagen = BytesIO()

        imagen_final.save(
            buffer_imagen,
            format="JPEG",
            quality=88,
            optimize=True,
            progressive=True
        )

        datos_generados = buffer_imagen.getvalue()
        ahora = time.monotonic()

        with CACHE_IMAGENES_COMPARTIR_LOCK:
            claves_vencidas = [
                clave
                for clave, entrada
                in CACHE_IMAGENES_COMPARTIR.items()
                if (
                    ahora - entrada[0]
                    >= CACHE_IMAGENES_COMPARTIR_TTL
                )
            ]

            for clave_vencida in claves_vencidas:
                CACHE_IMAGENES_COMPARTIR.pop(
                    clave_vencida,
                    None
                )

            CACHE_IMAGENES_COMPARTIR[clave_cache] = (
                ahora,
                datos_generados
            )

        return respuesta_imagen_compartir_publicacion(
            datos_generados,
            "MISS"
        )

    except Exception as error:
        print(
            "ERROR GENERANDO IMAGEN PARA COMPARTIR "
            "PUBLICACIÓN:",
            error,
            flush=True
        )

        return "", 404


# ============================================================
# CLICKLOCAL: IMAGEN PARA COMPARTIR NEGOCIO V1
#
# Cuando el comercio todavía no tiene logo, genera una imagen
# cuadrada con sus iniciales para la vista previa del enlace.
# ============================================================

@app.route(
    "/comercio/<comercio_id>/imagen-compartir.png"
)
def imagen_compartir_negocio(comercio_id):
    comercio_id = uuid_o_none(comercio_id)

    if not comercio_id:
        return "", 404

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select("id,nombre_negocio,activo")
            .eq("id", comercio_id)
            .eq("activo", True)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return "", 404

        nombre_negocio = str(
            comercios[0].get("nombre_negocio")
            or "Comercio"
        ).strip()

        palabras = [
            palabra
            for palabra in nombre_negocio.split()
            if palabra
        ]

        iniciales = "".join(
            palabra[0]
            for palabra in palabras[:2]
        ).upper() or "CL"

        tamanio = 800

        imagen = Image.new(
            "RGB",
            (tamanio, tamanio),
            (15, 23, 42)
        )

        dibujo = ImageDraw.Draw(imagen)

        fuente = None

        rutas_fuente = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]

        for ruta_fuente in rutas_fuente:
            try:
                fuente = ImageFont.truetype(
                    ruta_fuente,
                    270 if len(iniciales) > 1 else 330
                )
                break
            except Exception:
                continue

        if fuente is None:
            fuente = ImageFont.load_default()

        caja_texto = dibujo.textbbox(
            (0, 0),
            iniciales,
            font=fuente
        )

        ancho_texto = caja_texto[2] - caja_texto[0]
        alto_texto = caja_texto[3] - caja_texto[1]

        posicion_x = (
            (tamanio - ancho_texto) / 2
            - caja_texto[0]
        )

        posicion_y = (
            (tamanio - alto_texto) / 2
            - caja_texto[1]
        )

        dibujo.text(
            (posicion_x, posicion_y),
            iniciales,
            font=fuente,
            fill=(255, 255, 255)
        )

        buffer_imagen = BytesIO()

        imagen.save(
            buffer_imagen,
            format="PNG",
            optimize=True
        )

        buffer_imagen.seek(0)

        respuesta = send_file(
            buffer_imagen,
            mimetype="image/png",
            max_age=86400
        )

        respuesta.headers["Cache-Control"] = (
            "public, max-age=86400"
        )

        return respuesta

    except Exception as e:
        print(
            "ERROR GENERANDO IMAGEN PARA COMPARTIR NEGOCIO:",
            e,
            flush=True
        )
        return "", 404


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

        # ====================================================
        # CARTELERA PUBLICA DEL COMERCIO
        # ====================================================
        carteleras = []
        dias_cartelera = {
            1: "Lunes",
            2: "Martes",
            3: "Miércoles",
            4: "Jueves",
            5: "Viernes",
            6: "Sábado",
            7: "Domingo",
        }

        carteleras_res = (
            supabase_admin
            .table("carteleras")
            .select("id,titulo,descripcion,genero,clasificacion,direccion_mostrar,precio_general,precios_detalle,promociones,imagen_url,imagenes,imagen_principal,activa,created_at")
            .eq("comercio_id", comercio_id)
            .eq("activa", True)
            .order("created_at", desc=True)
            .execute()
        )

        carteleras = carteleras_res.data or []

        cartelera_ids = [
            item.get("id")
            for item in carteleras
            if item.get("id")
        ]

        funciones_por_cartelera = {}

        if cartelera_ids:
            funciones_res = (
                supabase_admin
                .table("cartelera_funciones")
                .select("id,cartelera_id,dia_semana,horarios,promo,activa")
                .in_("cartelera_id", cartelera_ids)
                .eq("activa", True)
                .order("dia_semana", desc=False)
                .execute()
            )

            for funcion in funciones_res.data or []:
                cartelera_id_funcion = funcion.get("cartelera_id")
                dia = funcion.get("dia_semana")
                horarios = funcion.get("horarios") or []

                if not isinstance(horarios, list):
                    horarios = []

                funcion["dia_nombre"] = dias_cartelera.get(dia, f"Día {dia}")
                funcion["horarios_texto"] = ", ".join(
                    str(h).strip() for h in horarios if str(h).strip()
                )

                funciones_por_cartelera.setdefault(cartelera_id_funcion, []).append(funcion)

        for cartelera in carteleras:
            imagenes = cartelera.get("imagenes") or []
            primera_imagen = ""

            if isinstance(imagenes, list) and imagenes:
                primera_imagen = imagenes[0]

            cartelera["imagen_url"] = (
                cartelera.get("imagen_principal")
                or cartelera.get("imagen_url")
                or primera_imagen
                or ""
            )

            cartelera["precio_mostrar"] = formatear_precio(cartelera.get("precio_general"))

            cartelera["ubicacion_mostrar"] = (
                cartelera.get("direccion_mostrar")
                or comercio.get("ubicacion_perfil")
                or "Consultar ubicación"
            )

            cartelera["funciones"] = funciones_por_cartelera.get(cartelera.get("id"), [])

    except Exception as e:
        print("ERROR cargando perfil de comercio:", e, flush=True)
        return redirect(url_for("inicio"))

    return render_template(
        "perfil.html",
        comercio=comercio,
        publicaciones=publicaciones,
        carteleras=carteleras
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
    revisar_premium_vencidos()

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

    try:
        duracion_meses = int(request.form.get("duracion_meses", "1"))
    except ValueError:
        duracion_meses = 1

    if duracion_meses not in [1, 3, 6]:
        duracion_meses = 1

    vencimiento = hoy + datetime.timedelta(days=30 * duracion_meses)

    try:
        supabase_admin.table("comercios").update({
            "plan": "premium",
            "estado_plan": "activo",
            "solicitud_premium": False,
            "fecha_inicio_plan": hoy.isoformat(),
            "fecha_vencimiento_plan": vencimiento.isoformat()
        }).eq("id", comercio_id).execute()

        supabase_admin.table("publicaciones").update({
            "activa": True,
            "pausada_por_limite_plan": False
        }).eq("comercio_id", comercio_id).eq("pausada_por_limite_plan", True).execute()

        supabase_admin.table("listas_buscables").update({
            "activa": True,
            "pausada_por_limite_plan": False
        }).eq("comercio_id", comercio_id).eq("pausada_por_limite_plan", True).execute()

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
