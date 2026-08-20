from flask import Flask, render_template, send_from_directory, send_file, request, redirect, url_for, session, g, has_request_context
from werkzeug.utils import secure_filename
import os
from decimal import Decimal, InvalidOperation
import uuid
import hashlib
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
from gastronomia import gastronomia_bp


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
app.register_blueprint(gastronomia_bp)


# Clave temporal para session en desarrollo local
app.secret_key = os.getenv("FLASK_SECRET_KEY", "clicklocal-mvp-dev")


# ============================================================
# CLICKLOCAL: IDENTIDAD ANALYTICS ANONIMA V1
# ============================================================
ANALYTICS_COOKIE_VISITANTE = "clicklocal_visitante_id"
ANALYTICS_COOKIE_SESION = "clicklocal_sesion_id"
ANALYTICS_COOKIE_ULTIMA_ACTIVIDAD = "clicklocal_sesion_ultima"
ANALYTICS_COOKIE_MODO = "clicklocal_modo_acceso"

ANALYTICS_VISITANTE_MAX_AGE = 365 * 24 * 60 * 60
ANALYTICS_SESION_INACTIVIDAD = 30 * 60
ANALYTICS_SESION_COOKIE_MAX_AGE = 2 * 60 * 60


def _analytics_uuid_valido_o_nuevo(valor):
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid4())


def _analytics_entero_o_none(valor):
    try:
        return int(str(valor))
    except (ValueError, TypeError):
        return None


@app.before_request
def analytics_preparar_identidad_anonima():
    """
    Prepara una identidad analítica anónima por navegador y sesión.

    No utiliza nombre, correo, teléfono, IP ni fingerprint.
    """
    if request.path.startswith("/static/"):
        return None

    ahora = int(time.time())

    visitante_id = _analytics_uuid_valido_o_nuevo(
        request.cookies.get(ANALYTICS_COOKIE_VISITANTE)
    )

    sesion_cookie = request.cookies.get(
        ANALYTICS_COOKIE_SESION
    )

    sesion_id = _analytics_uuid_valido_o_nuevo(
        sesion_cookie
    )

    ultima_actividad = _analytics_entero_o_none(
        request.cookies.get(
            ANALYTICS_COOKIE_ULTIMA_ACTIVIDAD
        )
    )

    sesion_cookie_valida = False

    try:
        uuid.UUID(str(sesion_cookie))
        sesion_cookie_valida = True
    except (ValueError, TypeError, AttributeError):
        sesion_cookie_valida = False

    sesion_vencida = (
        ultima_actividad is None
        or ahora < ultima_actividad
        or ahora - ultima_actividad
        > ANALYTICS_SESION_INACTIVIDAD
    )

    if not sesion_cookie_valida or sesion_vencida:
        sesion_id = str(uuid.uuid4())

    modo_acceso = str(
        request.cookies.get(ANALYTICS_COOKIE_MODO)
        or "web"
    ).strip().lower()

    if modo_acceso not in {"web", "pwa"}:
        modo_acceso = "web"

    g.analytics_visitante_id = visitante_id
    g.analytics_sesion_id = sesion_id
    g.analytics_modo_acceso = modo_acceso

    g.analytics_cookies_pendientes = {
        ANALYTICS_COOKIE_VISITANTE: {
            "value": visitante_id,
            "max_age": ANALYTICS_VISITANTE_MAX_AGE,
        },
        ANALYTICS_COOKIE_SESION: {
            "value": sesion_id,
            "max_age": ANALYTICS_SESION_COOKIE_MAX_AGE,
        },
        ANALYTICS_COOKIE_ULTIMA_ACTIVIDAD: {
            "value": str(ahora),
            "max_age": ANALYTICS_SESION_COOKIE_MAX_AGE,
        },
    }

    return None


@app.after_request
def analytics_persistir_identidad_anonima(respuesta):
    pendientes = getattr(
        g,
        "analytics_cookies_pendientes",
        None,
    )

    if not pendientes:
        return respuesta

    for nombre, configuracion in pendientes.items():
        respuesta.set_cookie(
            nombre,
            configuracion["value"],
            max_age=configuracion["max_age"],
            httponly=True,
            secure=request.is_secure,
            samesite="Lax",
            path="/",
        )

    return respuesta


def analytics_contexto_actual():
    if not has_request_context():
        return {
            "visitante_id": None,
            "sesion_id": None,
            "modo_acceso": "web",
        }

    return {
        "visitante_id": getattr(
            g,
            "analytics_visitante_id",
            None,
        ),
        "sesion_id": getattr(
            g,
            "analytics_sesion_id",
            None,
        ),
        "modo_acceso": getattr(
            g,
            "analytics_modo_acceso",
            "web",
        ),
    }


# ============================================================
# CLICKLOCAL: VISITAS UNICAS POR SESION V1
# ============================================================
ANALYTICS_VISITAS_SESION_CLAVE = "analytics_visitas_unicas"
ANALYTICS_VISITAS_SESION_MAX = 240


def _analytics_clave_visita(tipo_recurso, recurso_id):
    tipo = str(tipo_recurso or "").strip().lower()
    recurso = uuid_o_none(recurso_id)

    if tipo not in {"publicacion", "comercio"} or not recurso:
        return None

    texto = f"{tipo}:{recurso}"

    return hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()[:24]


def analytics_visita_ya_registrada(tipo_recurso, recurso_id):
    contexto = analytics_contexto_actual()
    sesion_id = contexto.get("sesion_id")
    clave = _analytics_clave_visita(
        tipo_recurso,
        recurso_id,
    )

    if not sesion_id or not clave:
        return False

    estado = session.get(
        ANALYTICS_VISITAS_SESION_CLAVE
    ) or {}

    if estado.get("sesion_id") != sesion_id:
        return False

    claves = estado.get("claves") or []

    return clave in claves


def analytics_marcar_visita_registrada(
    tipo_recurso,
    recurso_id,
):
    contexto = analytics_contexto_actual()
    sesion_id = contexto.get("sesion_id")
    clave = _analytics_clave_visita(
        tipo_recurso,
        recurso_id,
    )

    if not sesion_id or not clave:
        return False

    estado = session.get(
        ANALYTICS_VISITAS_SESION_CLAVE
    ) or {}

    if estado.get("sesion_id") != sesion_id:
        claves = []
    else:
        claves = [
            item
            for item in (estado.get("claves") or [])
            if item
        ]

    if clave not in claves:
        claves.append(clave)

    claves = claves[-ANALYTICS_VISITAS_SESION_MAX:]

    session[ANALYTICS_VISITAS_SESION_CLAVE] = {
        "sesion_id": sesion_id,
        "claves": claves,
    }

    session.modified = True
    return True


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
    contexto_analytics = analytics_contexto_actual()

    nueva_busqueda = {
        "consulta": consulta_limpia,
        "consulta_normalizada": normalizar_texto_analytics(consulta_limpia),
        "ciudad": ciudad,
        "origen": origen,
        "total_resultados_publicaciones": total_publicaciones,
        "total_resultados_listas": total_listas,
        "total_resultados": total_resultados,
        "tuvo_resultados": total_resultados > 0,
        "visitante_id": contexto_analytics["visitante_id"],
        "sesion_id": contexto_analytics["sesion_id"],
        "modo_acceso": contexto_analytics["modo_acceso"],
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

    contexto_analytics = analytics_contexto_actual()

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
        "visitante_id": contexto_analytics["visitante_id"],
        "sesion_id": contexto_analytics["sesion_id"],
        "modo_acceso": contexto_analytics["modo_acceso"],
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


# ============================================================
# CLICKLOCAL: CACHE PORTADA CARTELERAS E HISTORIAS V1
#
# Guarda temporalmente los resultados brutos de las consultas.
# Los filtros, búsquedas y armado visual siguen ejecutándose
# normalmente en cada apertura de la portada.
# ============================================================


CACHE_PORTADA_HISTORIAS = {
    "actualizado_en": 0.0,
    "datos": [],
}

CACHE_PORTADA_HISTORIAS_LOCK = Lock()

CACHE_PORTADA_HISTORIAS_TTL = 60




def invalidar_cache_portada_historias():
    with CACHE_PORTADA_HISTORIAS_LOCK:
        CACHE_PORTADA_HISTORIAS["actualizado_en"] = 0.0
        CACHE_PORTADA_HISTORIAS["datos"] = []




def obtener_historias_publicas_cache():
    ahora = time.monotonic()

    with CACHE_PORTADA_HISTORIAS_LOCK:
        actualizado_en = CACHE_PORTADA_HISTORIAS["actualizado_en"]

        if (
            actualizado_en
            and ahora - actualizado_en
            < CACHE_PORTADA_HISTORIAS_TTL
        ):
            return list(CACHE_PORTADA_HISTORIAS["datos"]), True

    respuesta = (
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

    datos = list(respuesta.data or [])

    with CACHE_PORTADA_HISTORIAS_LOCK:
        CACHE_PORTADA_HISTORIAS["actualizado_en"] = ahora
        CACHE_PORTADA_HISTORIAS["datos"] = list(datos)

    return list(datos), False



# ============================================================
# CLICKLOCAL: CACHE PUBLICACIONES PORTADA V1
#
# Reduce consultas repetidas a Supabase para la portada y
# búsquedas públicas. Se invalida cuando cambia una publicación.
# ============================================================

import threading as _clicklocal_cache_threading
import time as _clicklocal_cache_time


CACHE_PORTADA_PUBLICACIONES_TTL_SEGUNDOS = 60

CACHE_PORTADA_PUBLICACIONES = {
    "version": 0,
    "por_limite": {},
}

CACHE_PORTADA_PUBLICACIONES_LOCK = (
    _clicklocal_cache_threading.Lock()
)


def invalidar_cache_publicaciones_portada():
    with CACHE_PORTADA_PUBLICACIONES_LOCK:
        CACHE_PORTADA_PUBLICACIONES["version"] += 1
        CACHE_PORTADA_PUBLICACIONES["por_limite"].clear()


def obtener_publicaciones_portada_cache(limite):
    ahora = _clicklocal_cache_time.monotonic()

    with CACHE_PORTADA_PUBLICACIONES_LOCK:
        entrada = (
            CACHE_PORTADA_PUBLICACIONES["por_limite"]
            .get(limite)
        )

        if entrada:
            antiguedad = ahora - entrada["actualizado_en"]

            if (
                antiguedad
                < CACHE_PORTADA_PUBLICACIONES_TTL_SEGUNDOS
            ):
                return list(entrada["datos"]), True

        version_consulta = (
            CACHE_PORTADA_PUBLICACIONES["version"]
        )

    campos_publicacion = (
        "id,nombre,precio,descripcion,imagenes,"
        "imagen_principal,imagen_url,activa,"
        "comercio_id,direccion_mostrar,created_at,"
        "orden_grilla_at"
    )

    if limite <= 80:
        # La portada necesita candidatos recientes y antiguos.
        # Se consultan ambos extremos y se eliminan duplicados.
        respuesta_recientes = (
            supabase_admin
            .table("publicaciones")
            .select(campos_publicacion)
            .eq("activa", True)
            .order("orden_grilla_at", desc=True)
            .limit(limite)
            .execute()
        )

        respuesta_antiguas = (
            supabase_admin
            .table("publicaciones")
            .select(campos_publicacion)
            .eq("activa", True)
            .order("orden_grilla_at", desc=False)
            .limit(limite)
            .execute()
        )

        datos_por_id = {}

        for publicacion in (
            list(respuesta_recientes.data or [])
            + list(respuesta_antiguas.data or [])
        ):
            publicacion_id = str(
                publicacion.get("id") or ""
            ).strip()

            if not publicacion_id:
                continue

            datos_por_id[publicacion_id] = publicacion

        datos = list(datos_por_id.values())

        datos.sort(
            key=lambda item: (
                item.get("orden_grilla_at")
                or item.get("created_at")
                or ""
            ),
            reverse=True
        )
    else:
        # Las búsquedas conservan la consulta actual:
        # los 200 candidatos más recientes o editados.
        respuesta = (
            supabase_admin
            .table("publicaciones")
            .select(campos_publicacion)
            .eq("activa", True)
            .order("orden_grilla_at", desc=True)
            .limit(limite)
            .execute()
        )

        datos = list(respuesta.data or [])

    with CACHE_PORTADA_PUBLICACIONES_LOCK:
        if (
            CACHE_PORTADA_PUBLICACIONES["version"]
            == version_consulta
        ):
            CACHE_PORTADA_PUBLICACIONES[
                "por_limite"
            ][limite] = {
                "actualizado_en": ahora,
                "datos": list(datos),
            }

    return list(datos), False


# ============================================================
# CLICKLOCAL: MEZCLA SIMPLE PUBLICACIONES ANTIGUAS V1
#
# La portada combina:
# - 50 % recientes o editadas;
# - 30 % de antigüedad intermedia;
# - 20 % antiguas.
#
# No registra exposiciones ni modifica datos.
# ============================================================

def mezclar_publicaciones_portada(
    publicaciones,
    limite=80,
):
    ordenadas = list(publicaciones or [])

    if not ordenadas or limite <= 0:
        return []

    ordenadas.sort(
        key=lambda item: (
            item.get("orden_grilla_at")
            or item.get("created_at")
            or ""
        ),
        reverse=True
    )

    cantidad_salida = min(
        limite,
        len(ordenadas),
    )

    cantidad_recientes = max(
        1,
        round(cantidad_salida * 0.50),
    )

    cantidad_antiguas = round(
        cantidad_salida * 0.20
    )

    cantidad_intermedias = (
        cantidad_salida
        - cantidad_recientes
        - cantidad_antiguas
    )

    recientes = ordenadas[:cantidad_recientes]

    ids_elegidos = {
        str(item.get("id") or "")
        for item in recientes
    }

    antiguas = []

    for item in reversed(ordenadas):
        item_id = str(item.get("id") or "")

        if item_id in ids_elegidos:
            continue

        antiguas.append(item)
        ids_elegidos.add(item_id)

        if len(antiguas) >= cantidad_antiguas:
            break

    antiguas.reverse()

    candidatas_intermedias = [
        item
        for item in ordenadas
        if str(item.get("id") or "")
        not in ids_elegidos
    ]

    intermedias = []

    if (
        cantidad_intermedias > 0
        and candidatas_intermedias
    ):
        if (
            len(candidatas_intermedias)
            <= cantidad_intermedias
        ):
            intermedias = list(
                candidatas_intermedias
            )
        else:
            paso = (
                len(candidatas_intermedias)
                / cantidad_intermedias
            )

            indices_usados = set()

            for posicion in range(
                cantidad_intermedias
            ):
                indice = min(
                    int(posicion * paso),
                    len(candidatas_intermedias) - 1,
                )

                while (
                    indice in indices_usados
                    and indice + 1
                    < len(candidatas_intermedias)
                ):
                    indice += 1

                indices_usados.add(indice)

                intermedias.append(
                    candidatas_intermedias[indice]
                )

    resultado = []

    grupos = (
        (recientes, 5),
        (intermedias, 3),
        (antiguas, 2),
    )

    posiciones = {
        id(lista): 0
        for lista, _ in grupos
    }

    while len(resultado) < cantidad_salida:
        agregada_en_vuelta = False

        for lista, cantidad in grupos:
            posicion = posiciones[id(lista)]

            for _ in range(cantidad):
                if posicion >= len(lista):
                    break

                resultado.append(lista[posicion])
                posicion += 1
                agregada_en_vuelta = True

                if len(resultado) >= cantidad_salida:
                    break

            posiciones[id(lista)] = posicion

            if len(resultado) >= cantidad_salida:
                break

        if not agregada_en_vuelta:
            break

    ids_resultado = {
        str(item.get("id") or "")
        for item in resultado
    }

    for item in ordenadas:
        if len(resultado) >= cantidad_salida:
            break

        item_id = str(item.get("id") or "")

        if item_id in ids_resultado:
            continue

        resultado.append(item)
        ids_resultado.add(item_id)

    # Distribuir mejor los comercios dentro de cada tramo.
    #
    # Regla:
    # - bloques visuales de hasta 10 publicaciones;
    # - máximo ideal de 2 publicaciones por comercio;
    # - si no existen suficientes alternativas, completar el
    #   bloque sin ocultar ni eliminar publicaciones.
    pendientes = list(resultado)
    resultado_equilibrado = []

    TAMANO_BLOQUE_VISUAL = 10
    MAXIMO_POR_COMERCIO_EN_BLOQUE = 2

    while pendientes:
        bloque = []
        conteo_por_comercio = {}

        while (
            pendientes
            and len(bloque) < TAMANO_BLOQUE_VISUAL
        ):
            indice_elegido = None

            comercio_anterior = (
                str(
                    bloque[-1].get("comercio_id")
                    or ""
                )
                if bloque
                else ""
            )

            # Primera pasada:
            # respetar el máximo y evitar repetición inmediata.
            for indice, item in enumerate(pendientes):
                comercio_id = str(
                    item.get("comercio_id")
                    or f"sin-comercio:{item.get('id')}"
                )

                cantidad_actual = (
                    conteo_por_comercio.get(
                        comercio_id,
                        0,
                    )
                )

                if (
                    cantidad_actual
                    >= MAXIMO_POR_COMERCIO_EN_BLOQUE
                ):
                    continue

                if (
                    comercio_anterior
                    and comercio_id == comercio_anterior
                ):
                    continue

                indice_elegido = indice
                break

            # Segunda pasada:
            # respetar el máximo aunque no pueda evitarse
            # una repetición inmediata.
            if indice_elegido is None:
                for indice, item in enumerate(pendientes):
                    comercio_id = str(
                        item.get("comercio_id")
                        or f"sin-comercio:{item.get('id')}"
                    )

                    cantidad_actual = (
                        conteo_por_comercio.get(
                            comercio_id,
                            0,
                        )
                    )

                    if (
                        cantidad_actual
                        < MAXIMO_POR_COMERCIO_EN_BLOQUE
                    ):
                        indice_elegido = indice
                        break

            # Último recurso:
            # si quedan pocos comercios y todos alcanzaron el
            # máximo ideal, continuar sin perder publicaciones.
            if indice_elegido is None:
                indice_elegido = 0

            item_elegido = pendientes.pop(
                indice_elegido
            )

            comercio_elegido = str(
                item_elegido.get("comercio_id")
                or f"sin-comercio:{item_elegido.get('id')}"
            )

            bloque.append(item_elegido)

            conteo_por_comercio[comercio_elegido] = (
                conteo_por_comercio.get(
                    comercio_elegido,
                    0,
                )
                + 1
            )

        resultado_equilibrado.extend(bloque)

    return resultado_equilibrado


# ============================================================
# CLICKLOCAL: CINES Y TEATROS
#
# Sección preparada pero apagada hasta contar con
# las autorizaciones correspondientes.
# ============================================================

MOSTRAR_CINES_TEATROS = False


@app.context_processor
def exponer_estado_cines_teatros():
    return {
        "mostrar_cines_teatros": MOSTRAR_CINES_TEATROS,
    }


@app.route("/cines-y-teatros")
def cines_y_teatros():
    if not MOSTRAR_CINES_TEATROS:
        return "Página no disponible", 404

    return render_template("cines_teatros.html")


# INICIO / PLATAFORMA
@app.route("/")
@app.route("/index.html")
def inicio():
    # CLICKLOCAL: DIAGNOSTICO VELOCIDAD PORTADA V1
    import time as _clicklocal_time
    _clicklocal_portada_inicio = _clicklocal_time.perf_counter()
    _clicklocal_etapa_inicio = _clicklocal_portada_inicio

    from urllib.parse import quote
    import unicodedata

    comercio = session.get("comercio") or comercio_default()
    publicaciones_finales = []
    publicaciones_mas_vistas = []
    comercios_relacionados = []
    historias_publicas = []
    busqueda_id = None

    busqueda = request.args.get("q", "").strip()

    categoria_seleccionada = request.args.get(
        "categoria",
        ""
    ).strip()

    if categoria_seleccionada not in CATEGORIAS_HOME:
        categoria_seleccionada = ""

    # CLICKLOCAL: MACROCATEGORIA INICIAL POR DEFECTO V1
    # Sin búsqueda ni macro explícita, la portada abre
    # automáticamente la primera macrocategoría disponible.
    macro_slug = request.args.get(
        "macro",
        ""
    ).strip()

    if (
        not macro_slug
        and not busqueda
        and not categoria_seleccionada
    ):
        macro_slug = MACROCATEGORIAS_HOME[0]["slug"]

    macro_activa = MACROCATEGORIAS_POR_SLUG.get(
        macro_slug
    )

    if not macro_activa:
        if busqueda or categoria_seleccionada:
            macro_slug = ""
        else:
            macro_slug = MACROCATEGORIAS_HOME[0]["slug"]
            macro_activa = MACROCATEGORIAS_POR_SLUG.get(
                macro_slug
            )

    def normalizar_texto(valor):
        import re

        texto = str(valor or "").strip().lower()
        texto = unicodedata.normalize("NFD", texto)
        texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
        texto = re.sub(r"[^a-z0-9ñ\s]", " ", texto)
        return " ".join(texto.split())

    busqueda_normalizada = normalizar_texto(busqueda)

    PALABRAS_IGNORADAS_BUSCADOR = {
        "a", "al", "algo", "aca", "ahi", "algun", "alguna", "algunos",
        "algunas", "ante", "busco", "buscar", "con", "como", "comprar",
        "consigo", "conseguir", "cuanto", "de", "del", "donde", "el", "en",
        "encuentro", "encontrar", "esta", "estas", "este", "estos",
        "hacer", "hacen", "hago", "hay", "ir", "la", "las", "lo", "los",
        "lugar", "lugares", "me", "mi", "necesita", "necesito", "para",
        "parana", "por", "puedo", "que", "quiero", "se", "si", "sin",
        "sobre", "te",
        "tener", "tenes", "tiene", "tienen", "un", "una", "unas", "unos",
        "venden", "vende", "ver", "y"
    }

    # Equivalencias acotadas. No determinan rubros ni comercios:
    # solo permiten reconocer distintas maneras de expresar
    # un mismo concepto.
    EQUIVALENCIAS_BUSCADOR = (
        {
            "quinceanera",
            "quince",
            "15 anos",
            "cumple de 15",
        },
        {
            "recuerdo",
            "recuerdos",
            "souvenir",
            "souvenirs",
            "regional",
            "artesania",
            "artesanias",
        },
        {
            "romantica",
            "romantico",
            "pareja",
        },
        {
            "partido",
            "futbol",
        },
        {
            "chicos",
            "ninos",
            "ninas",
            "familia",
            "familiar",
        },
    )

    REEMPLAZOS_FRASES_BUSCADOR = {
        "cumple de 15": "quinceanera",
        "cumple 15": "quinceanera",
        "15 anos": "quinceanera",
        "quince anos": "quinceanera",
    }

    def extraer_palabras_clave(texto):
        import re

        texto_normalizado = normalizar_texto(texto)

        # Convierte algunas expresiones compuestas en un único
        # concepto antes de separar la consulta en palabras.
        for frase, concepto in sorted(
            REEMPLAZOS_FRASES_BUSCADOR.items(),
            key=lambda item: len(item[0]),
            reverse=True
        ):
            texto_normalizado = re.sub(
                r"\b" + re.escape(frase) + r"\b",
                concepto,
                texto_normalizado
            )

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

        # Plurales simples y controlados:
        # mates -> mate
        # hamburguesas -> hamburguesa
        # collares -> collar
        # animales -> animal
        if len(token) > 3 and token.endswith("s"):
            variantes.add(token[:-1])

        if len(token) > 4 and token.endswith("es"):
            variantes.add(token[:-2])

        variantes_base = set(variantes)

        for grupo in EQUIVALENCIAS_BUSCADOR:
            if variantes_base.intersection(grupo):
                variantes.update(grupo)

        return {
            variante
            for variante in variantes
            if len(variante) >= 2
        }

    def calcular_score_busqueda(texto, peso=1):
        import re

        if not busqueda_normalizada:
            return 1, []

        texto_normalizado = normalizar_texto(texto)
        palabras_texto = texto_normalizado.split()
        variantes_texto = set()

        for palabra in palabras_texto:
            variantes_texto.update(
                variantes_token(palabra)
            )

        score = 0
        coincidencias = []

        for token in palabras_clave_busqueda:
            variantes_busqueda = variantes_token(token)

            encontro_token = bool(
                variantes_busqueda.intersection(
                    variantes_texto
                )
            )

            if encontro_token:
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

    def bonus_cobertura_busqueda(coincidencias):
        total_conceptos = len(palabras_clave_busqueda)
        conceptos_encontrados = len(set(coincidencias or []))

        if total_conceptos <= 1 or conceptos_encontrados <= 1:
            return 0

        # Premia que un mismo resultado responda a varios
        # conceptos de la consulta.
        bonus = (conceptos_encontrados - 1) * 3

        # Si responde a todos los conceptos importantes,
        # recibe un bonus adicional.
        if conceptos_encontrados >= total_conceptos:
            bonus += 5

        return bonus

    def cumple_minimo_coincidencias_busqueda(coincidencias):
        if not busqueda_normalizada:
            return True

        conceptos_encontrados = len(
            set(coincidencias or [])
        )

        # Una coincidencia importante alcanza para mostrar
        # un posible resultado. Las coincidencias adicionales
        # mejoran el puntaje y el orden.
        return conceptos_encontrados >= 1

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

    def _cargar_comercios_por_id_sin_cache(comercio_ids):
        comercio_ids = [
            comercio_id for comercio_id in comercio_ids
            if comercio_id
        ]

        if not comercio_ids:
            return {}

        # CLICKLOCAL: DIAGNOSTICO CONSULTAS PORTADA V2 - línea 1492
        _clicklocal_consulta_inicio_1492 = _clicklocal_time.perf_counter()
        comercios_res = (
            supabase_admin
            .table("comercios")
            .select("id,nombre_negocio,direccion,direccion_mostrar,venta_online,ciudad,categoria,logo_url,whatsapp,activo,plan")
            .in_("id", list(set(comercio_ids)))
            .execute()
        )
        print("PORTADA CONSULTA L1492 comercios_res | tabla=comercios: "f"{_clicklocal_time.perf_counter() - _clicklocal_consulta_inicio_1492:.3f} s", flush=True)

        comercios = comercios_res.data or []

        comercios_por_id = {
            com.get("id"): com
            for com in comercios
            if com.get("id") and com.get("activo") is not False
        }

        categorias_por_comercio = (
            obtener_categorias_secundarias_por_comercio(
                list(comercios_por_id.keys())
            )
        )

        for comercio_id, comercio_data in (
            comercios_por_id.items()
        ):
            categorias_secundarias = (
                categorias_por_comercio.get(
                    comercio_id,
                    []
                )
            )

            comercio_data["categorias_secundarias"] = (
                categorias_secundarias
            )
            comercio_data[
                "_categorias_secundarias_texto"
            ] = " ".join(categorias_secundarias)

        return comercios_por_id

    comercios_cache_peticion = {}


    def cargar_comercios_por_id(comercio_ids):
        ids_solicitados = list({
            comercio_id
            for comercio_id in comercio_ids
            if comercio_id
        })

        ids_faltantes = [
            comercio_id
            for comercio_id in ids_solicitados
            if comercio_id not in comercios_cache_peticion
        ]

        if ids_faltantes:
            nuevos = (
                _cargar_comercios_por_id_sin_cache(
                    ids_faltantes
                )
            )

            comercios_cache_peticion.update(nuevos)

        return {
            comercio_id: comercios_cache_peticion[comercio_id]
            for comercio_id in ids_solicitados
            if comercio_id in comercios_cache_peticion
        }



    try:
        # ====================================================
        print("PORTADA preparación inicial: "f"{_clicklocal_time.perf_counter() - _clicklocal_etapa_inicio:.3f} s", flush=True)
        _clicklocal_etapa_inicio = _clicklocal_time.perf_counter()

        # ====================================================
        # CLICKLOCAL - BUSCADOR UNIVERSAL GASTRONOMIA V1
        #
        # Un comercio con Gastronomía activa conserva sus datos
        # tradicionales en la base, pero en una búsqueda pública
        # su representación oficial pasa a ser Gastronomía.
        # ====================================================

        gastronomia_ids = set()

        if busqueda_normalizada:
            try:
                gastronomia_config_res = (
                    supabase_admin
                    .table("gastronomia_configuracion")
                    .select("comercio_id")
                    .eq("activo", True)
                    .execute()
                )

                gastronomia_ids = {
                    str(item.get("comercio_id"))
                    for item in (
                        gastronomia_config_res.data or []
                    )
                    if item.get("comercio_id")
                }

            except Exception as error:
                print(
                    "AVISO BUSCADOR UNIVERSAL - "
                    "gastronomia_configuracion:",
                    type(error),
                    error,
                    flush=True
                )
                gastronomia_ids = set()

        # ====================================================
        # 1) PRIMER NIVEL: PUBLICACIONES ACTIVAS
        # ====================================================
        limite = 200 if busqueda else 80

        # CLICKLOCAL: CACHE PUBLICACIONES PORTADA V1
        _clicklocal_consulta_inicio_1542 = (
            _clicklocal_time.perf_counter()
        )

        (
            publicaciones,
            _clicklocal_publicaciones_desde_cache,
        ) = obtener_publicaciones_portada_cache(limite)

        print(
            "PORTADA CACHE publicaciones "
            f"{'HIT' if _clicklocal_publicaciones_desde_cache else 'MISS'}: "
            f"{_clicklocal_time.perf_counter() - _clicklocal_consulta_inicio_1542:.3f} s",
            flush=True
        )

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

            # En búsquedas públicas, un comercio gastronómico
            # activo no se muestra mediante publicaciones viejas.
            # Se incorporará más abajo desde gastronomia_productos.
            if (
                busqueda_normalizada
                and str(comercio_id) in gastronomia_ids
            ):
                continue

            if (
                macro_slug
                and not comercio_pertenece_a_macro(
                    comercio_pub,
                    macro_slug
                )
            ):
                continue

            if (
                categoria_seleccionada
                and not comercio_pertenece_a_categoria(
                    comercio_pub,
                    categoria_seleccionada
                )
            ):
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

            score_total += bonus_cobertura_busqueda(coincidencias)

            if (
                busqueda_normalizada
                and not cumple_minimo_coincidencias_busqueda(
                    coincidencias
                )
            ):
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

            publicaciones_finales = (
                mezclar_publicaciones_portada(
                    publicaciones_finales,
                    limite=limite,
                )
            )

        # ====================================================
        # 2) SEGUNDO NIVEL: LISTAS BUSCABLES
        # ====================================================
        if busqueda_normalizada:
            # CLICKLOCAL: DIAGNOSTICO CONSULTAS PORTADA V2 - línea 1808
            _clicklocal_consulta_inicio_1808 = _clicklocal_time.perf_counter()
            listas_res = (
                supabase_admin
                .table("listas_buscables")
                .select("id,comercio_id,producto_categoria,atributos_texto,activa,created_at")
                .eq("activa", True)
                .order("created_at", desc=True)
                .limit(300)
                .execute()
            )
            print("PORTADA CONSULTA L1808 listas_res | tabla=listas_buscables: "f"{_clicklocal_time.perf_counter() - _clicklocal_consulta_inicio_1808:.3f} s", flush=True)

            listas = listas_res.data or []

            listas_filtradas = []

            for lista in listas:
                texto_producto = str(lista.get("producto_categoria") or "")
                texto_atributos = str(lista.get("atributos_texto") or "")

                score_producto, coincidencias_producto = calcular_score_busqueda(texto_producto, peso=4)
                score_atributos, coincidencias_atributos = calcular_score_busqueda(texto_atributos, peso=3)

                score_lista = score_producto + score_atributos
                coincidencias_lista = unir_coincidencias(
                    coincidencias_producto,
                    coincidencias_atributos
                )

                score_lista += bonus_cobertura_busqueda(
                    coincidencias_lista
                )

                if not cumple_minimo_coincidencias_busqueda(
                    coincidencias_lista
                ):
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

                es_gastronomia = (
                    str(comercio_id) in gastronomia_ids
                )

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
                    "es_gastronomia": es_gastronomia,
                    "destino_url": (
                        f"/gastronomia/comercio/{comercio_id}"
                        if es_gastronomia
                        else f"/comercio/{comercio_id}"
                    ),
                    "_score_busqueda": lista.get("_score_busqueda", 0),
                    "_coincidencias": lista.get("_coincidencias") or [],
                })

            # ====================================================
            # 3) PRODUCTOS GASTRONOMICOS
            # ====================================================

            if gastronomia_ids:

                try:
                    productos_gastro_res = (
                        supabase_admin
                        .table("gastronomia_productos")
                        .select(
                            "id,comercio_id,nombre,descripcion,"
                            "imagen_url,precio,precio_promocional,"
                            "activo,disponible"
                        )
                        .in_(
                            "comercio_id",
                            list(gastronomia_ids)
                        )
                        .eq("activo", True)
                        .eq("disponible", True)
                        .execute()
                    )

                    productos_gastro = (
                        productos_gastro_res.data or []
                    )

                    comercio_ids_gastro = {
                        producto.get("comercio_id")
                        for producto in productos_gastro
                        if producto.get("comercio_id")
                    }

                    comercios_gastro_por_id = (
                        cargar_comercios_por_id(
                            comercio_ids_gastro
                        )
                    )

                    mejores_gastro_por_comercio = {}

                    for producto in productos_gastro:

                        comercio_id = producto.get(
                            "comercio_id"
                        )

                        if not comercio_id:
                            continue

                        comercio_gastro = (
                            comercios_gastro_por_id.get(
                                comercio_id,
                                {}
                            )
                        )

                        if (
                            not comercio_gastro
                            or comercio_gastro.get(
                                "activo"
                            ) is False
                        ):
                            continue

                        score_nombre, coincidencias_nombre = (
                            calcular_score_busqueda(
                                producto.get("nombre"),
                                peso=5
                            )
                        )

                        score_descripcion, coincidencias_descripcion = (
                            calcular_score_busqueda(
                                producto.get(
                                    "descripcion"
                                ),
                                peso=2
                            )
                        )

                        score_comercio, coincidencias_comercio = (
                            calcular_score_busqueda(
                                " ".join([
                                    str(
                                        comercio_gastro.get(
                                            "nombre_negocio"
                                        ) or ""
                                    ),
                                    str(
                                        comercio_gastro.get(
                                            "categoria"
                                        ) or ""
                                    ),
                                    str(
                                        comercio_gastro.get(
                                            "ciudad"
                                        ) or ""
                                    ),
                                ]),
                                peso=1
                            )
                        )

                        score_total = (
                            score_nombre
                            + score_descripcion
                            + score_comercio
                        )

                        nombre_normalizado_producto = (
                            normalizar_texto(
                                producto.get("nombre")
                            )
                        )

                        if (
                            busqueda_normalizada
                            and busqueda_normalizada
                            in nombre_normalizado_producto
                        ):
                            score_total += 10

                        coincidencias = unir_coincidencias(
                            coincidencias_nombre,
                            coincidencias_descripcion,
                            coincidencias_comercio
                        )

                        score_total += (
                            bonus_cobertura_busqueda(
                                coincidencias
                            )
                        )

                        if not (
                            cumple_minimo_coincidencias_busqueda(
                                coincidencias
                            )
                        ):
                            continue

                        actual = (
                            mejores_gastro_por_comercio.get(
                                comercio_id
                            )
                        )

                        if (
                            actual
                            and actual.get(
                                "_score_busqueda",
                                0
                            ) >= score_total
                        ):
                            continue

                        precio_producto = (
                            producto.get(
                                "precio_promocional"
                            )
                            if producto.get(
                                "precio_promocional"
                            ) is not None
                            else producto.get("precio")
                        )

                        mejores_gastro_por_comercio[
                            comercio_id
                        ] = {
                            "comercio_id": comercio_id,
                            "lista_buscable_id": None,
                            "gastronomia_producto_id": (
                                producto.get("id")
                            ),
                            "nombre_negocio": (
                                comercio_gastro.get(
                                    "nombre_negocio"
                                )
                                or "Comercio local"
                            ),
                            "producto_categoria": (
                                producto.get("nombre")
                                or "Producto gastronómico"
                            ),
                            "atributos_texto": (
                                producto.get(
                                    "descripcion"
                                )
                                or ""
                            ),
                            "ubicacion_mostrar": (
                                ubicacion_publica(
                                    comercio_gastro
                                )
                            ),
                            "whatsapp_url": "",
                            "imagen_url": (
                                producto.get("imagen_url")
                                or imagen_por_comercio.get(
                                    comercio_id,
                                    ""
                                )
                            ),
                            "precio": precio_producto,
                            "plan": str(
                                comercio_gastro.get(
                                    "plan"
                                )
                                or "gratis"
                            ).lower(),
                            "es_gastronomia": True,
                            "destino_url": (
                                f"/gastronomia/comercio/"
                                f"{comercio_id}"
                            ),
                            "_score_busqueda": score_total,
                            "_coincidencias": coincidencias,
                        }

                    # =================================================
                    # CLICKLOCAL - DESTINO DIRECTO GASTRONOMIA V1
                    #
                    # Si la consulta coincide con productos de Gastronomía,
                    # no mostramos una tarjeta intermedia en el home.
                    # Abrimos directamente la galería gastronómica filtrada.
                    # =================================================

                    comercio_exacto_id = None

                    for (
                        comercio_id_gastro,
                        comercio_data_gastro
                    ) in comercios_gastro_por_id.items():

                        nombre_gastro_normalizado = normalizar_texto(
                            comercio_data_gastro.get(
                                "nombre_negocio"
                            )
                        )

                        if (
                            nombre_gastro_normalizado
                            == busqueda_normalizada
                        ):
                            comercio_exacto_id = (
                                comercio_id_gastro
                            )
                            break

                    if comercio_exacto_id:
                        return redirect(
                            url_for(
                                "gastronomia.comercio_gastronomico",
                                comercio_id=comercio_exacto_id
                            )
                        )

                    if mejores_gastro_por_comercio:
                        return redirect(
                            url_for(
                                "gastronomia.inicio",
                                q=busqueda
                            )
                        )

                    comercios_relacionados.extend(
                        mejores_gastro_por_comercio.values()
                    )

                except Exception as error:
                    print(
                        "ERROR BUSCANDO PRODUCTOS "
                        "GASTRONOMICOS:",
                        type(error),
                        error,
                        flush=True
                    )


            # ====================================================
            # CONSOLIDAR RESULTADOS POR COMERCIO
            #
            # Un negocio puede coincidir por lista tradicional y
            # por producto gastronómico. Mostramos una sola vez.
            # Gastronomía tiene prioridad como experiencia pública.
            # ====================================================

            mejores_por_comercio = {}

            for item in comercios_relacionados:

                comercio_id = item.get("comercio_id")

                if not comercio_id:
                    continue

                actual = mejores_por_comercio.get(
                    comercio_id
                )

                if not actual:
                    mejores_por_comercio[
                        comercio_id
                    ] = item
                    continue

                item_gastro = bool(
                    item.get("es_gastronomia")
                )

                actual_gastro = bool(
                    actual.get("es_gastronomia")
                )

                if item_gastro and not actual_gastro:
                    mejores_por_comercio[
                        comercio_id
                    ] = item
                    continue

                if (
                    item_gastro == actual_gastro
                    and item.get(
                        "_score_busqueda",
                        0
                    )
                    > actual.get(
                        "_score_busqueda",
                        0
                    )
                ):
                    mejores_por_comercio[
                        comercio_id
                    ] = item

            comercios_relacionados = list(
                mejores_por_comercio.values()
            )


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

            for item in comercios_relacionados:
                comercio_id = item.get("comercio_id")

                if str(comercio_id) in gastronomia_ids:

                    item["es_gastronomia"] = True

                    nombre_comercio_normalizado = (
                        normalizar_texto(
                            item.get("nombre_negocio")
                        )
                    )

                    # Si el usuario escribió exactamente el nombre
                    # del comercio, entra directamente a su menú.
                    if (
                        busqueda_normalizada
                        == nombre_comercio_normalizado
                    ):
                        item["destino_url"] = (
                            f"/gastronomia/comercio/"
                            f"{comercio_id}"
                        )
                        item["destino_tipo"] = "menu"

                    # Si buscó un producto/comida, abrimos la
                    # galería gastronómica con TODAS las opciones.
                    else:
                        item["destino_url"] = url_for(
                            "gastronomia.inicio",
                            q=busqueda
                        )
                        item["destino_tipo"] = "galeria"

                    # Primero debe elegir comercio/producto.
                    item["whatsapp_url"] = ""

                elif not item.get("destino_url"):

                    item["destino_url"] = (
                        f"/comercio/{comercio_id}"
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
    print("PORTADA publicaciones/listas: "f"{_clicklocal_time.perf_counter() - _clicklocal_etapa_inicio:.3f} s", flush=True)
    _clicklocal_etapa_inicio = _clicklocal_time.perf_counter()

    # 3) HISTORIAS PREMIUM PÚBLICAS
    # Un círculo por comercio, con hasta 2 historias vigentes.
    # Si existe logo_url se usa el logo; de lo contrario,
    # se muestran las iniciales del comercio.
    # ====================================================
    try:
        # CLICKLOCAL: DIAGNOSTICO CONSULTAS PORTADA V2 - HISTORIAS CACHE
        _clicklocal_consulta_inicio_1943 = _clicklocal_time.perf_counter()

        (
            historias_candidatas,
            _clicklocal_historias_desde_cache,
        ) = obtener_historias_publicas_cache()

        print(
            "PORTADA CACHE historias "
            f"{'HIT' if _clicklocal_historias_desde_cache else 'MISS'}: "
            f"{_clicklocal_time.perf_counter() - _clicklocal_consulta_inicio_1943:.3f} s",
            flush=True
        )

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
            # CLICKLOCAL: DIAGNOSTICO CONSULTAS PORTADA V2 - línea 1984
            _clicklocal_consulta_inicio_1984 = _clicklocal_time.perf_counter()
            publicaciones_vinculadas_res = (
                supabase_admin
                .table("publicaciones")
                .select("id,comercio_id,activa,eliminada")
                .in_("id", publicacion_ids_historias)
                .eq("activa", True)
                .eq("eliminada", False)
                .execute()
            )
            print("PORTADA CONSULTA L1984 publicaciones_vinculadas_res | tabla=publicaciones: "f"{_clicklocal_time.perf_counter() - _clicklocal_consulta_inicio_1984:.3f} s", flush=True)

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
    # CLICKLOCAL: COMERCIOS PARA DESCUBRIR B1 V1
    #
    # - solo portada sin búsqueda;
    # - comercios con publicación e imagen;
    # - un registro por comercio;
    # - excluye Cine y Teatro;
    # - intenta mostrar categorías diferentes;
    # - rotación diaria estable;
    # - Gratis y Premium participan por igual;
    # - reutiliza datos ya cargados;
    # - no agrega consultas a Supabase.
    # ====================================================
    comercios_para_descubrir = []

    if not busqueda_normalizada:
        import datetime as _clicklocal_datetime
        import hashlib as _clicklocal_hashlib

        try:
            from zoneinfo import ZoneInfo as _ClickLocalZoneInfo

            clave_dia = (
                _clicklocal_datetime.datetime.now(
                    _ClickLocalZoneInfo(
                        "America/Argentina/Buenos_Aires"
                    )
                )
                .date()
                .isoformat()
            )
        except Exception:
            clave_dia = (
                _clicklocal_datetime.date.today().isoformat()
            )

        candidatos_por_comercio = {}

        for item in publicaciones_finales:
            if item.get("tipo") != "publicacion":
                continue

            comercio_id = str(
                item.get("comercio_id") or ""
            ).strip()

            if not comercio_id:
                continue

            imagenes_item = item.get("imagenes") or []
            primera_imagen = ""

            if (
                isinstance(imagenes_item, list)
                and imagenes_item
            ):
                primera_imagen = str(
                    imagenes_item[0] or ""
                ).strip()

            imagen_mostrar = str(
                item.get("imagen_mostrar")
                or item.get("imagen_principal")
                or item.get("imagen_url")
                or primera_imagen
                or ""
            ).strip()

            if not imagen_mostrar:
                continue

            comercio_datos = item.get("comercio")

            if isinstance(comercio_datos, dict):
                comercio_dict = comercio_datos
                comercio_texto = ""
            else:
                comercio_dict = {}
                comercio_texto = str(
                    comercio_datos or ""
                ).strip()

            nombre_negocio = str(
                item.get("nombre_negocio")
                or item.get("comercio_nombre")
                or item.get("nombre_comercio")
                or comercio_dict.get("nombre_negocio")
                or comercio_dict.get("nombre")
                or comercio_texto
                or "Comercio local"
            ).strip()

            categoria = str(
                item.get("categoria")
                or item.get("comercio_categoria")
                or comercio_dict.get("categoria")
                or "Comercio local"
            ).strip()

            if categoria.casefold() == "cine y teatro":
                continue

            logo_url = str(
                item.get("logo_url")
                or item.get("comercio_logo_url")
                or comercio_dict.get("logo_url")
                or ""
            ).strip()

            palabras_nombre = [
                palabra
                for palabra in nombre_negocio.split()
                if palabra
            ]

            iniciales = "".join(
                palabra[0].upper()
                for palabra in palabras_nombre[:2]
            ) or "CL"

            if comercio_id not in candidatos_por_comercio:
                candidatos_por_comercio[comercio_id] = {
                    "id": comercio_id,
                    "nombre_negocio": nombre_negocio,
                    "categoria": categoria,
                    "imagen_url": imagen_mostrar,
                    "logo_url": logo_url,
                    "iniciales": iniciales,
                    "perfil_url": url_for(
                        "perfil_comercio",
                        comercio_id=comercio_id,
                    ),
                }

        candidatos_ordenados = sorted(
            candidatos_por_comercio.values(),
            key=lambda candidato: (
                _clicklocal_hashlib.sha256(
                    (
                        clave_dia
                        + "|"
                        + candidato["id"]
                    ).encode("utf-8")
                ).hexdigest(),
                candidato["id"],
            ),
        )

        categorias_elegidas = set()
        ids_elegidos = set()

        for candidato in candidatos_ordenados:
            categoria_clave = str(
                candidato.get("categoria")
                or "Comercio local"
            ).strip().casefold()

            if categoria_clave in categorias_elegidas:
                continue

            comercios_para_descubrir.append(
                candidato
            )

            categorias_elegidas.add(
                categoria_clave
            )

            ids_elegidos.add(
                candidato["id"]
            )

            if len(comercios_para_descubrir) >= 3:
                break

        if len(comercios_para_descubrir) < 3:
            for candidato in candidatos_ordenados:
                if candidato["id"] in ids_elegidos:
                    continue

                comercios_para_descubrir.append(
                    candidato
                )

                ids_elegidos.add(
                    candidato["id"]
                )

                if len(comercios_para_descubrir) >= 3:
                    break

    # ====================================================
    # 4) LO MÁS VISTO
    #
    # Solo se calcula en la portada sin búsqueda.
    # No usa orden_grilla_at y no incluye carteleras.
    # Una edición no puede modificar este ranking.
    # ====================================================
    if not busqueda_normalizada:
        print("PORTADA historias: "f"{_clicklocal_time.perf_counter() - _clicklocal_etapa_inicio:.3f} s", flush=True)
        _clicklocal_etapa_inicio = _clicklocal_time.perf_counter()

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

    print("PORTADA más visto y preparación final: "f"{_clicklocal_time.perf_counter() - _clicklocal_etapa_inicio:.3f} s", flush=True)
    _clicklocal_render_inicio = _clicklocal_time.perf_counter()
    _clicklocal_html = render_template(
        "index.html",
        comercio=comercio,
        publicaciones=publicaciones_finales,
        publicaciones_mas_vistas=publicaciones_mas_vistas,
        comercios_relacionados=comercios_relacionados,
        historias_publicas=historias_publicas,
        comercios_para_descubrir=comercios_para_descubrir,
        busqueda=busqueda,
        categoria_seleccionada=categoria_seleccionada,
        macrocategorias=MACROCATEGORIAS_HOME,
        macro_slug=macro_slug,
        macro_activa=macro_activa
    )
    print("PORTADA render HTML: "f"{_clicklocal_time.perf_counter() - _clicklocal_render_inicio:.3f} s", flush=True)
    print("PORTADA TOTAL: "f"{_clicklocal_time.perf_counter() - _clicklocal_portada_inicio:.3f} s", flush=True)
    return _clicklocal_html



@app.route("/api/publicaciones-recientes")
def publicaciones_recientes_api():
    try:
        TAMANO_BLOQUE = 40

        try:
            offset = max(
                0,
                int(request.args.get("offset", 80))
            )
        except (TypeError, ValueError):
            offset = 80

        macro_slug = request.args.get(
            "macro",
            ""
        ).strip()

        if macro_slug not in MACROCATEGORIAS_POR_SLUG:
            macro_slug = ""

        categoria_seleccionada = request.args.get(
            "categoria",
            ""
        ).strip()

        if categoria_seleccionada not in CATEGORIAS_HOME:
            categoria_seleccionada = ""

        publicaciones_res = (
            supabase_admin
            .table("publicaciones")
            .select(
                "id,nombre,precio,descripcion,imagenes,"
                "imagen_principal,imagen_url,activa,comercio_id,"
                "direccion_mostrar,created_at,orden_grilla_at"
            )
            .eq("activa", True)
            .order("orden_grilla_at", desc=True)
            .range(
                offset,
                offset + TAMANO_BLOQUE - 1
            )
            .execute()
        )

        publicaciones = publicaciones_res.data or []

        comercio_ids = list({
            pub.get("comercio_id")
            for pub in publicaciones
            if pub.get("comercio_id")
        })

        comercios_por_id = {}

        if comercio_ids:
            comercios_res = (
                supabase_admin
                .table("comercios")
                .select(
                    "id,nombre_negocio,direccion,"
                    "direccion_mostrar,venta_online,ciudad,"
                    "categoria,activo,plan"
                )
                .in_("id", comercio_ids)
                .execute()
            )

            comercios_por_id = {
                comercio.get("id"): comercio
                for comercio in (comercios_res.data or [])
                if comercio.get("id")
                and comercio.get("activo") is not False
            }

        categorias_por_comercio = (
            obtener_categorias_secundarias_por_comercio(
                list(comercios_por_id.keys())
            )
            if comercios_por_id
            else {}
        )

        for comercio_id, comercio_data in (
            comercios_por_id.items()
        ):
            comercio_data["categorias_secundarias"] = (
                categorias_por_comercio.get(
                    comercio_id,
                    []
                )
            )

        def imagen_publica(pub):
            imagenes = pub.get("imagenes") or []
            primera = ""

            if isinstance(imagenes, list) and imagenes:
                primera = imagenes[0]

            return (
                pub.get("imagen_principal")
                or pub.get("imagen_url")
                or primera
                or ""
            )

        def ubicacion_publica(comercio, direccion_publicacion=None):
            direccion = (
                direccion_publicacion
                or comercio.get("direccion_mostrar")
                or comercio.get("direccion")
                or comercio.get("ciudad")
                or ""
            )

            venta_online = bool(comercio.get("venta_online"))

            if direccion and venta_online:
                return f"{direccion} · Venta online"

            if direccion:
                return direccion

            if venta_online:
                return "Venta online"

            return "Consultar ubicación"

        items = []

        for pub in publicaciones:
            comercio_id = pub.get("comercio_id")
            comercio = comercios_por_id.get(comercio_id)

            if not comercio:
                continue

            if (
                macro_slug
                and not comercio_pertenece_a_macro(
                    comercio,
                    macro_slug
                )
            ):
                continue

            if (
                categoria_seleccionada
                and not comercio_pertenece_a_categoria(
                    comercio,
                    categoria_seleccionada
                )
            ):
                continue

            items.append({
                "id": pub.get("id"),
                "comercio_id": comercio_id,
                "nombre": pub.get("nombre") or "",
                "precio": formatear_precio(pub.get("precio")),
                "imagen_url": imagen_publica(pub),
                "comercio": (
                    comercio.get("nombre_negocio")
                    or "Comercio local"
                ),
                "direccion_mostrar": ubicacion_publica(
                    comercio,
                    pub.get("direccion_mostrar")
                ),
                "categoria": comercio.get("categoria") or "",
            })

        return {
            "items": items,
            "siguiente_offset": offset + len(publicaciones),
            "hay_mas": len(publicaciones) == TAMANO_BLOQUE,
        }

    except Exception as e:
        print(
            "ERROR cargando publicaciones recientes:",
            e,
            flush=True
        )

        return {
            "items": [],
            "hay_mas": False,
            "error": True,
        }, 500


@app.route("/cartelera-demo")
def cartelera_demo():
    return render_template("cartelera_demo.html")



# ============================================================
# CLICKLOCAL: CATEGORÍAS MÚLTIPLES V1
#
# La categoría principal continúa en comercios.categoria.
# Las categorías opcionales se guardan en comercio_categorias.
# ============================================================

CATEGORIA_CINE_TEATRO = "Cine y Teatro"

CATEGORIAS_COMERCIO = (
    "Gastronomía",
    "Alimentos y bebidas",
    "Indumentaria",
    "Calzado y accesorios",
    "Joyería, relojería y accesorios",
    "Hogar, bazar y decoración",
    "Mueblería",
    "Viveros y jardinería",
    "Veterinaria y mascotas",
    "Tecnología",
    "Autos y motos",
    "Salud y bienestar",
    "Belleza y cuidado personal",
    "Servicios para el hogar",
    "Gráfica, diseño y personalizados",
    "Educación y cursos",
    "Turismo y excursiones",
    "Regalos, juguetes y artesanías",
    "Mercería y manualidades",
    "Librería, papelería e insumos comerciales",
    "Deportes",
    "Ferretería, Sanitarios y Electricidad",
    CATEGORIA_CINE_TEATRO,
    "Otros",
)

CATEGORIAS_SECUNDARIAS_PERMITIDAS = tuple(
    categoria
    for categoria in CATEGORIAS_COMERCIO
    if categoria != CATEGORIA_CINE_TEATRO
)

CATEGORIAS_AUTOGESTION = tuple(
    categoria
    for categoria in CATEGORIAS_COMERCIO
    if categoria != CATEGORIA_CINE_TEATRO
)

CATEGORIAS_HOME = tuple(
    categoria
    for categoria in CATEGORIAS_COMERCIO
    if categoria != "Otros"
)


# ============================================================
# CLICKLOCAL: MACROCATEGORÍAS DE LA PORTADA
#
# No reemplazan las categorías actuales.
# Una categoría puede aparecer en más de una macro.
# ============================================================

MACROCATEGORIAS_HOME = (
    {
        "slug": "moda-belleza-bienestar",
        "nombre": "Moda, Belleza y Bienestar",
        "icono": "✦",
        "categorias": (
            "Indumentaria",
            "Calzado y accesorios",
            "Joyería, relojería y accesorios",
            "Salud y bienestar",
            "Belleza y cuidado personal",
            "Deportes",
        ),
    },
    {
        "slug": "hogar-deco-regalos",
        "nombre": "Hogar, Deco y Regalos",
        "icono": "⌂",
        "categorias": (
            "Hogar, bazar y decoración",
            "Mueblería",
            "Viveros y jardinería",
            "Veterinaria y mascotas",
            "Joyería, relojería y accesorios",
            "Gráfica, diseño y personalizados",
            "Regalos, juguetes y artesanías",
            "Mercería y manualidades",
            "Librería, papelería e insumos comerciales",
        ),
    },
    {
        "slug": "gastronomia-alimentos",
        "nombre": "Gastronomía y Alimentos",
        "icono": "◆",
        "categorias": (
            "Gastronomía",
            "Alimentos y bebidas",
        ),
    },
    {
        "slug": "tecnologia-servicios",
        "nombre": "Tecnología y Servicios",
        "icono": "▣",
        "categorias": (
            "Tecnología",
            "Veterinaria y mascotas",
            "Servicios para el hogar",
            "Gráfica, diseño y personalizados",
            "Educación y cursos",
            "Librería, papelería e insumos comerciales",
        ),
    },
    {
        "slug": "autos-motos-movilidad",
        "nombre": "Autos, Motos y Movilidad",
        "icono": "●",
        "categorias": (
            "Autos y motos",
        ),
    },
    {
        "slug": "construccion-ferreteria",
        "nombre": "Construcción y Ferretería",
        "icono": "◆",
        "categorias": (
            "Ferretería, Sanitarios y Electricidad",
        ),
    },
    {
        "slug": "ocio-experiencias",
        "nombre": "Ocio y Experiencias",
        "icono": "★",
        "categorias": (
            "Cine y Teatro",
            "Deportes",
            "Educación y cursos",
            "Turismo y excursiones",
            "Regalos, juguetes y artesanías",
        ),
    },
)

MACROCATEGORIAS_POR_SLUG = {
    macro["slug"]: macro
    for macro in MACROCATEGORIAS_HOME
}


def comercio_pertenece_a_macro(comercio, macro_slug):
    macro = MACROCATEGORIAS_POR_SLUG.get(
        str(macro_slug or "").strip()
    )

    if not macro:
        return True

    categoria_principal = str(
        comercio.get("categoria") or ""
    ).strip()

    return categoria_principal in set(macro["categorias"])


def comercio_pertenece_a_categoria(
    comercio,
    categoria_seleccionada,
):
    categoria_buscada = str(
        categoria_seleccionada or ""
    ).strip()

    if not categoria_buscada:
        return True

    categoria_principal = str(
        comercio.get("categoria") or ""
    ).strip()

    return categoria_principal == categoria_buscada


app.jinja_env.globals.update({
    "CATEGORIAS_COMERCIO": CATEGORIAS_COMERCIO,
    "CATEGORIAS_AUTOGESTION": CATEGORIAS_AUTOGESTION,
    "CATEGORIAS_SECUNDARIAS_PERMITIDAS": (
        CATEGORIAS_SECUNDARIAS_PERMITIDAS
    ),
    "CATEGORIAS_HOME": CATEGORIAS_HOME,
    "CATEGORIA_CINE_TEATRO": CATEGORIA_CINE_TEATRO,
})


def validar_categorias_comercio(
    categoria_principal,
    categoria_secundaria_2="",
    categoria_secundaria_3="",
):
    principal = str(
        categoria_principal or ""
    ).strip()

    secundarias_recibidas = [
        str(categoria_secundaria_2 or "").strip(),
        str(categoria_secundaria_3 or "").strip(),
    ]

    secundarias = [
        categoria
        for categoria in secundarias_recibidas
        if categoria
    ]

    if not principal:
        return None, (
            "Tenés que seleccionar una categoría principal."
        )

    if principal not in CATEGORIAS_COMERCIO:
        return None, (
            "La categoría principal seleccionada no es válida."
        )

    for categoria in secundarias:
        if (
            categoria
            not in CATEGORIAS_SECUNDARIAS_PERMITIDAS
        ):
            return None, (
                "Una de las categorías secundarias "
                "seleccionadas no es válida."
            )

    categorias_elegidas = [principal] + secundarias

    if len(set(categorias_elegidas)) != len(
        categorias_elegidas
    ):
        return None, (
            "No se puede repetir la misma categoría."
        )

    if (
        principal == CATEGORIA_CINE_TEATRO
        and secundarias
    ):
        return None, (
            "Cine y Teatro no admite categorías secundarias."
        )

    return {
        "principal": principal,
        "secundarias": secundarias,
    }, None



def validar_categorias_registro(
    categoria_principal,
    categoria_secundaria_2="",
    categoria_secundaria_3="",
):
    principal = str(
        categoria_principal or ""
    ).strip()

    if principal == CATEGORIA_CINE_TEATRO:
        return None, (
            "Cine y Teatro es una categoría reservada. "
            "Debe ser habilitada por administración."
        )

    return validar_categorias_comercio(
        principal,
        categoria_secundaria_2,
        categoria_secundaria_3,
    )


def validar_categorias_panel(
    comercio,
    categoria_principal,
    categoria_secundaria_2="",
    categoria_secundaria_3="",
):
    categoria_actual = str(
        (comercio or {}).get("categoria") or ""
    ).strip()

    principal = str(
        categoria_principal or ""
    ).strip()

    secundaria_2 = str(
        categoria_secundaria_2 or ""
    ).strip()

    secundaria_3 = str(
        categoria_secundaria_3 or ""
    ).strip()

    if categoria_actual == CATEGORIA_CINE_TEATRO:
        if (
            principal != CATEGORIA_CINE_TEATRO
            or secundaria_2
            or secundaria_3
        ):
            return None, (
                "Cine y Teatro es una categoría especial "
                "y no puede modificarse desde el panel."
            )

        return {
            "principal": CATEGORIA_CINE_TEATRO,
            "secundarias": [],
        }, None

    if principal == CATEGORIA_CINE_TEATRO:
        return None, (
            "Cine y Teatro es una categoría reservada. "
            "Debe ser habilitada por administración."
        )

    return validar_categorias_comercio(
        principal,
        secundaria_2,
        secundaria_3,
    )


def obtener_categorias_secundarias_por_comercio(
    comercio_ids
):
    ids = list(dict.fromkeys(
        comercio_id
        for comercio_id in (comercio_ids or [])
        if comercio_id
    ))

    if not ids:
        return {}

    try:
        respuesta = (
            supabase_admin
            .table("comercio_categorias")
            .select(
                "id,comercio_id,categoria,orden,created_at"
            )
            .in_("comercio_id", ids)
            .order("orden")
            .execute()
        )

        filas = respuesta.data or []

    except Exception as error:
        print(
            "AVISO CARGANDO CATEGORÍAS SECUNDARIAS:",
            error,
            flush=True
        )
        return {}

    resultado = {}

    for fila in filas:
        comercio_id = fila.get("comercio_id")
        categoria = str(
            fila.get("categoria") or ""
        ).strip()

        if not comercio_id or not categoria:
            continue

        resultado.setdefault(
            comercio_id,
            []
        ).append(categoria)

    return resultado


def obtener_filas_categorias_secundarias(
    comercio_id
):
    if not comercio_id:
        return []

    respuesta = (
        supabase_admin
        .table("comercio_categorias")
        .select(
            "id,comercio_id,categoria,orden,created_at"
        )
        .eq("comercio_id", comercio_id)
        .order("orden")
        .execute()
    )

    return respuesta.data or []


def reemplazar_categorias_secundarias(
    comercio_id,
    categorias_secundarias,
):
    categorias = [
        str(categoria or "").strip()
        for categoria in (
            categorias_secundarias or []
        )
        if str(categoria or "").strip()
    ]

    filas_anteriores = (
        obtener_filas_categorias_secundarias(
            comercio_id
        )
    )

    nuevas_filas = [
        {
            "comercio_id": comercio_id,
            "categoria": categoria,
            "orden": posicion,
        }
        for posicion, categoria in enumerate(
            categorias,
            start=2
        )
    ]

    try:
        (
            supabase_admin
            .table("comercio_categorias")
            .delete()
            .eq("comercio_id", comercio_id)
            .execute()
        )

        if nuevas_filas:
            (
                supabase_admin
                .table("comercio_categorias")
                .insert(nuevas_filas)
                .execute()
            )

    except Exception:
        try:
            (
                supabase_admin
                .table("comercio_categorias")
                .delete()
                .eq("comercio_id", comercio_id)
                .execute()
            )

            if filas_anteriores:
                (
                    supabase_admin
                    .table("comercio_categorias")
                    .insert(filas_anteriores)
                    .execute()
                )

        except Exception as error_restaurando:
            print(
                "ERROR RESTAURANDO CATEGORÍAS "
                "SECUNDARIAS:",
                error_restaurando,
                flush=True
            )

        raise



# VERSIÓN VIGENTE DE LOS TÉRMINOS Y CONDICIONES
TERMINOS_VERSION = "2026-07-22"


@app.context_processor
def contexto_terminos():
    return {
        "TERMINOS_VERSION": TERMINOS_VERSION,
    }


# ============================================================
# CLICKLOCAL — CONTACTO / SOPORTE
# ============================================================

@app.route("/contacto", methods=["GET", "POST"])
def contacto():
    motivo_inicial = (
        request.args.get("motivo")
        or request.form.get("motivo")
        or "Consulta general"
    ).strip()

    whatsapp_inicial = (
        request.args.get("whatsapp")
        or request.form.get("whatsapp")
        or ""
    ).strip()

    origen = (
        request.args.get("origen")
        or request.form.get("origen")
        or "contacto"
    ).strip()

    comercio_id = (
        request.args.get("comercio_id")
        or request.form.get("comercio_id")
        or ""
    ).strip()

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip().lower()
        whatsapp = request.form.get("whatsapp", "").strip()
        motivo = request.form.get("motivo", "").strip()
        mensaje = request.form.get("mensaje", "").strip()

        if not nombre or not mensaje:
            return render_template(
                "contacto.html",
                error="Completá tu nombre y el mensaje.",
                nombre=nombre,
                email=email,
                whatsapp=whatsapp,
                motivo=motivo or motivo_inicial,
                mensaje=mensaje,
                origen=origen,
                comercio_id=comercio_id,
            ), 400

        whatsapp_guardar = (
            limpiar_numero_whatsapp(whatsapp)
            if whatsapp
            else None
        )

        try:
            supabase_admin.table("consultas_soporte").insert({
                "nombre": nombre,
                "email": email or None,
                "whatsapp": whatsapp_guardar,
                "motivo": motivo or "Consulta general",
                "mensaje": mensaje,
                "origen": origen or "contacto",
                "comercio_id": comercio_id or None,
                "estado": "pendiente",
            }).execute()

            return render_template(
                "contacto.html",
                enviado=True,
                nombre="",
                email="",
                whatsapp="",
                motivo="Consulta general",
                mensaje="",
                origen="contacto",
                comercio_id="",
            )

        except Exception as e:
            print(
                "ERROR GUARDANDO CONSULTA SOPORTE:",
                type(e),
                e,
                flush=True
            )

            return render_template(
                "contacto.html",
                error=(
                    "No pudimos enviar tu consulta en este momento. "
                    "Intentá nuevamente."
                ),
                nombre=nombre,
                email=email,
                whatsapp=whatsapp,
                motivo=motivo or motivo_inicial,
                mensaje=mensaje,
                origen=origen,
                comercio_id=comercio_id,
            ), 500

    return render_template(
        "contacto.html",
        enviado=False,
        nombre="",
        email="",
        whatsapp=whatsapp_inicial,
        motivo=motivo_inicial,
        mensaje="",
        origen=origen,
        comercio_id=comercio_id,
    )


# TÉRMINOS Y CONDICIONES
@app.route("/terminos")
@app.route("/terminos.html")
def terminos():
    return render_template("terminos.html")


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
        categoria_secundaria_2 = request.form.get(
            "categoria_secundaria_2",
            ""
        ).strip()
        categoria_secundaria_3 = request.form.get(
            "categoria_secundaria_3",
            ""
        ).strip()
        descripcion = request.form.get("descripcion", "").strip()
        password = request.form.get("password", "").strip()
        repetir_password = request.form.get("repetir_password", "").strip()
        acepta_terminos = (
            request.form.get("acepta_terminos") == "on"
        )

        if not acepta_terminos:
            return (
                "Debés leer y aceptar los Términos y Condiciones "
                "para crear una cuenta.",
                400,
            )

        if not nombre_negocio or not email or not whatsapp or not direccion or not password:
            return "Faltan datos obligatorios: nombre del negocio, email, WhatsApp, dirección o contraseña.", 400

        whatsapp_limpio = limpiar_numero_whatsapp(whatsapp)

        if not whatsapp_limpio:
            return render_template(
                "registro.html",
                error=(
                    "El WhatsApp ingresado no es válido. "
                    "Ingresá un número de celular, por ejemplo 343 000 0000."
                )
            ), 400

        categorias_validadas, error_categorias = (
            validar_categorias_registro(
                categoria,
                categoria_secundaria_2,
                categoria_secundaria_3,
            )
        )

        if error_categorias:
            return error_categorias, 400

        categoria = categorias_validadas["principal"]
        categorias_secundarias = (
            categorias_validadas["secundarias"]
        )

        if password != repetir_password:
            return "Las contraseñas no coinciden.", 400

        if len(password) < 6:
            return "La contraseña debe tener al menos 6 caracteres.", 400

        direccion_mostrar = direccion

        # ========================================================
        # CLICKLOCAL — PROTECCION ANTI DUPLICADOS POR WHATSAPP
        #
        # Compara números normalizados para detectar como iguales:
        # 3434547410
        # 543434547410
        # 5493434547410
        #
        # Se revisan también cuentas bloqueadas para evitar crear
        # una nueva cuenta encima de una cuenta ya existente.
        # ========================================================
        try:
            comercios_whatsapp_res = (
                supabase_admin
                .table("comercios")
                .select(
                    "id,nombre_negocio,email,whatsapp,activo"
                )
                .execute()
            )

            for comercio_existente in (
                comercios_whatsapp_res.data or []
            ):
                whatsapp_existente = limpiar_numero_whatsapp(
                    comercio_existente.get("whatsapp")
                )

                if (
                    whatsapp_existente
                    and whatsapp_existente == whatsapp_limpio
                ):
                    nombre_existente = (
                        comercio_existente.get("nombre_negocio")
                        or "un comercio existente"
                    )

                    print(
                        "REGISTRO BLOQUEADO POR WHATSAPP DUPLICADO:",
                        nombre_existente,
                        whatsapp_limpio,
                        flush=True
                    )

                    return render_template(
                        "registro.html",
                        error=(
                            "Ya existe un comercio registrado con "
                            "este WhatsApp. Iniciá sesión con la "
                            "cuenta existente."
                        ),
                        soporte_url=url_for(
                            "contacto",
                            motivo=(
                                "Problema para acceder a una "
                                "cuenta existente"
                            ),
                            whatsapp=whatsapp,
                            origen="registro_duplicado",
                        )
                    ), 409

        except Exception as e:
            print(
                "ERROR VERIFICANDO WHATSAPP DUPLICADO:",
                type(e),
                e,
                flush=True
            )

            return render_template(
                "registro.html",
                error=(
                    "No pudimos verificar el WhatsApp en este "
                    "momento. Intentá nuevamente."
                )
            ), 500

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
                "whatsapp": whatsapp_limpio,
                "direccion": direccion,
                "direccion_mostrar": direccion_mostrar,
                "venta_online": venta_online,
                "ciudad": ciudad,
                "categoria": categoria,
                "descripcion": descripcion,
                "plan": "gratis",
                "terminos_aceptados_at": (
                    datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat()
                ),
                "terminos_version": TERMINOS_VERSION,
            }

            insert_res = supabase_admin.table("comercios").insert(comercio_nuevo).execute()

            comercio_guardado = insert_res.data[0] if insert_res.data else comercio_nuevo

            if categorias_secundarias:
                comercio_id_nuevo = comercio_guardado.get("id")

                if not comercio_id_nuevo:
                    comercio_creado_res = (
                        supabase_admin
                        .table("comercios")
                        .select("*")
                        .eq("user_id", user.id)
                        .limit(1)
                        .execute()
                    )

                    if comercio_creado_res.data:
                        comercio_guardado = (
                            comercio_creado_res.data[0]
                        )
                        comercio_id_nuevo = (
                            comercio_guardado.get("id")
                        )

                if not comercio_id_nuevo:
                    raise RuntimeError(
                        "El comercio se creó, pero no se pudo "
                        "obtener su identificador para guardar "
                        "las categorías secundarias."
                    )

                try:
                    reemplazar_categorias_secundarias(
                        comercio_id_nuevo,
                        categorias_secundarias,
                    )

                except Exception:
                    try:
                        (
                            supabase_admin
                            .table("comercios")
                            .delete()
                            .eq("id", comercio_id_nuevo)
                            .execute()
                        )
                    except Exception:
                        pass

                    try:
                        supabase_admin.auth.admin.delete_user(
                            str(user.id)
                        )
                    except Exception:
                        pass

                    raise

            session["user_id"] = user.id
            session["comercio"] = comercio_guardado
            session["publicaciones"] = []

            categoria_registro = str(
                comercio_guardado.get("categoria") or ""
            ).strip().lower()

            if categoria_registro in {
                "gastronomía",
                "gastronomia",
            }:
                return redirect(
                    url_for(
                        "gastronomia.configuracion_inicial"
                    )
                )

            return redirect(url_for("panel"))

        except Exception as e:
            return f"Error registrando comercio: {e}", 400

    return render_template("registro.html")



# ACEPTAR TÉRMINOS DESDE EL PANEL
@app.route("/terminos/aceptar", methods=["POST"])
def aceptar_terminos():
    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    confirmacion = (
        request.form.get("acepta_terminos_panel") == "on"
    )

    volver_a = str(
        request.form.get("volver_a") or ""
    ).strip()

    destino = (
        "gastronomia.panel_gastronomia"
        if volver_a == "gastronomia"
        else "panel"
    )

    if not confirmacion:
        return redirect(url_for(destino))

    fecha_aceptacion = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    try:
        respuesta = (
            supabase_admin
            .table("comercios")
            .update({
                "terminos_aceptados_at": fecha_aceptacion,
                "terminos_version": TERMINOS_VERSION,
            })
            .eq("user_id", user_id)
            .execute()
        )

        filas = respuesta.data or []

        if filas:
            comercio_actualizado = filas[0]
        else:
            comercio_res = (
                supabase_admin
                .table("comercios")
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

            if not comercio_res.data:
                return "No se encontró el comercio.", 404

            comercio_actualizado = comercio_res.data[0]

        session["comercio"] = comercio_actualizado

        return redirect(url_for(destino))

    except Exception as error:
        print(
            "ERROR aceptando términos:",
            error,
            flush=True,
        )

        return (
            "No se pudo registrar la aceptación. "
            "Intentá nuevamente.",
            500,
        )


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

            categoria_login = str(
                comercio.get("categoria") or ""
            ).strip().lower()

            if categoria_login in {
                "gastronomía",
                "gastronomia",
            }:
                return redirect(
                    url_for(
                        "gastronomia.configuracion_inicial"
                    )
                )

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
        publicaciones_modificadas = False

        for pub in publicaciones_activas[limite_publicaciones:]:
            supabase_admin.table("publicaciones").update({
                "activa": False,
                "pausada_por_limite_plan": True
            }).eq("id", pub.get("id")).eq("comercio_id", comercio_id).execute()

            publicaciones_modificadas = True

        if publicaciones_modificadas:
            invalidar_cache_publicaciones_portada()

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



# ============================================================
# CLICKLOCAL — CUENTA GESTOR MVP
#
# El administrador conserva su propia sesión.
# session["gestor_comercio_id"] identifica únicamente el
# comercio que está siendo administrado.
#
# session["user_id"] NO se suplanta ni se modifica.
# ============================================================

def _modo_gestor_activo():
    return bool(
        session.get("admin_logueado")
        and session.get("gestor_comercio_id")
    )


def _user_id_panel_efectivo():
    """
    Devuelve el user_id propietario del comercio que puede operar
    actualmente en el panel.

    Modo normal:
        devuelve session["user_id"].

    Modo gestor:
        valida la sesión admin y obtiene el user_id real del dueño
        mediante gestor_comercio_id.

    Nunca escribe ni reemplaza session["user_id"].
    """

    if _modo_gestor_activo():
        comercio_id = session.get("gestor_comercio_id")

        try:
            comercio_res = (
                supabase_admin
                .table("comercios")
                .select("id,user_id,activo")
                .eq("id", comercio_id)
                .limit(1)
                .execute()
            )

            comercios = comercio_res.data or []

            if not comercios:
                return None

            comercio = comercios[0]

            user_id_propietario = comercio.get("user_id")

            if not user_id_propietario:
                return None

            return user_id_propietario

        except Exception as e:
            print(
                "ERROR RESOLVIENDO CUENTA GESTOR:",
                type(e),
                e,
                flush=True
            )
            return None

    return session.get("user_id")


@app.route("/panel/subir-foto-publicacion", methods=["POST"])
def subir_foto_publicacion_secuencial():
    user_id = _user_id_panel_efectivo()

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
    user_id = _user_id_panel_efectivo()
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
    user_id = _user_id_panel_efectivo()
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
    user_id = _user_id_panel_efectivo()

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

    # ==========================================================
    # CLICKLOCAL - REDIRECCION PANEL SEGUN BLOQUE
    # Un comercio con Gastronomía activa no debe entrar al
    # panel tradicional de publicaciones.
    # ==========================================================

    if request.method == "GET":
        try:
            gastronomia_res = (
                supabase_admin
                .table("gastronomia_configuracion")
                .select("comercio_id,activo")
                .eq("comercio_id", comercio_id)
                .eq("activo", True)
                .limit(1)
                .execute()
            )

            if gastronomia_res.data:
                return redirect("/gastronomia/panel")

        except Exception as error:
            print(
                "AVISO DETECTANDO PANEL GASTRONOMIA:",
                type(error),
                error,
                flush=True
            )

    if user_id and comercio.get("activo") is False:
        if _modo_gestor_activo():
            session.pop("gestor_comercio_id", None)
            session.pop("comercio", None)
            session.pop("publicaciones", None)
            return redirect(
                url_for("admin", gestor_error="comercio_bloqueado")
            )

        session.pop("user_id", None)
        session.pop("comercio", None)
        session.pop("publicaciones", None)
        return "Esta cuenta fue bloqueada por administración.", 403

    categorias_secundarias_panel = (
        obtener_categorias_secundarias_por_comercio(
            [comercio_id]
        ).get(comercio_id, [])
    )

    comercio["categoria_secundaria_2"] = (
        categorias_secundarias_panel[0]
        if len(categorias_secundarias_panel) >= 1
        else ""
    )
    comercio["categoria_secundaria_3"] = (
        categorias_secundarias_panel[1]
        if len(categorias_secundarias_panel) >= 2
        else ""
    )

    if request.method == "POST" and request.form.get("accion") == "actualizar_mis_datos":
        invalidar_cache_publicaciones_portada()

        nombre_negocio = request.form.get("nombre_negocio", "").strip()
        whatsapp = request.form.get("whatsapp", "").strip()
        direccion = request.form.get("direccion", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        categoria = request.form.get("categoria", "").strip()
        categoria_secundaria_2 = (
            categorias_secundarias_panel[0]
            if len(categorias_secundarias_panel) >= 1
            else ""
        )
        categoria_secundaria_3 = (
            categorias_secundarias_panel[1]
            if len(categorias_secundarias_panel) >= 2
            else ""
        )

        if not nombre_negocio or not whatsapp or not direccion:
            return "Faltan datos obligatorios: nombre del comercio, WhatsApp o dirección.", 400

        whatsapp_limpio = limpiar_numero_whatsapp(whatsapp)

        if not whatsapp_limpio:
            return "El WhatsApp no es válido. Ingresá solo números, por ejemplo 3430000000.", 400

        categorias_validadas, error_categorias = (
            validar_categorias_panel(
                comercio,
                categoria,
                categoria_secundaria_2,
                categoria_secundaria_3,
            )
        )

        if error_categorias:
            return error_categorias, 400

        categoria = categorias_validadas["principal"]
        categorias_secundarias = (
            categorias_validadas["secundarias"]
        )

        datos_actualizados = {
            "nombre_negocio": nombre_negocio,
            "whatsapp": whatsapp_limpio,
            "direccion": direccion,
            "direccion_mostrar": direccion,
            "descripcion": descripcion,
            "categoria": categoria,
        }

        datos_anteriores = {
            "nombre_negocio": comercio.get("nombre_negocio"),
            "whatsapp": comercio.get("whatsapp"),
            "direccion": comercio.get("direccion"),
            "direccion_mostrar": comercio.get(
                "direccion_mostrar"
            ),
            "descripcion": comercio.get("descripcion"),
            "categoria": comercio.get("categoria"),
        }

        categorias_anteriores = list(
            categorias_secundarias_panel
        )

        try:
            (
                supabase_admin
                .table("comercios")
                .update(datos_actualizados)
                .eq("id", comercio_id)
                .execute()
            )

            reemplazar_categorias_secundarias(
                comercio_id,
                categorias_secundarias,
            )

            # Mantener sincronizada la dirección visible
            # de las publicaciones existentes.
            (
                supabase_admin
                .table("publicaciones")
                .update({
                    "direccion_mostrar": direccion
                })
                .eq("comercio_id", comercio_id)
                .execute()
            )

            comercio.update(datos_actualizados)
            comercio["categoria_secundaria_2"] = (
                categorias_secundarias[0]
                if len(categorias_secundarias) >= 1
                else ""
            )
            comercio["categoria_secundaria_3"] = (
                categorias_secundarias[1]
                if len(categorias_secundarias) >= 2
                else ""
            )

            session["comercio"] = comercio
            session.modified = True

            return redirect(
                url_for(
                    "panel",
                    datos_actualizados="1"
                ) + "#datos"
            )

        except Exception as error:
            errores_restauracion = []

            try:
                (
                    supabase_admin
                    .table("comercios")
                    .update(datos_anteriores)
                    .eq("id", comercio_id)
                    .execute()
                )
            except Exception as error_restaurando:
                errores_restauracion.append(
                    f"comercio: {error_restaurando}"
                )

            try:
                reemplazar_categorias_secundarias(
                    comercio_id,
                    categorias_anteriores,
                )
            except Exception as error_restaurando:
                errores_restauracion.append(
                    f"categorías: {error_restaurando}"
                )

            try:
                (
                    supabase_admin
                    .table("publicaciones")
                    .update({
                        "direccion_mostrar": (
                            datos_anteriores.get(
                                "direccion_mostrar"
                            )
                            or datos_anteriores.get(
                                "direccion"
                            )
                            or ""
                        )
                    })
                    .eq("comercio_id", comercio_id)
                    .execute()
                )
            except Exception as error_restaurando:
                errores_restauracion.append(
                    f"publicaciones: {error_restaurando}"
                )

            print(
                "ERROR ACTUALIZANDO DATOS Y CATEGORÍAS:",
                error,
                flush=True
            )

            if errores_restauracion:
                print(
                    "ERRORES DURANTE LA RESTAURACIÓN:",
                    errores_restauracion,
                    flush=True
                )

            return (
                "No se pudieron actualizar los datos del "
                "comercio. Se conservaron los datos "
                "anteriores.",
                400
            )

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
        invalidar_cache_publicaciones_portada()

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
                        "activa_solicitada,"
                        "pausada_por_limite_plan,imagenes,"
                        "imagen_principal,imagen_url,"
                        "created_at,orden_grilla_at,"
                        "estado_revision,revisada_at,revisada_por,"
                        "ocultada_por_moderacion"
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

            estaba_oculta_por_moderacion = (
                publicacion_actual.get(
                    "ocultada_por_moderacion"
                ) is True
                or publicacion_actual.get(
                    "estado_revision"
                ) == "oculta"
            )

            if hubo_cambio_real:
                cambios_publicacion[
                    "activa_solicitada"
                ] = activa

                if estaba_oculta_por_moderacion:
                    # Una edición no libera contenido que
                    # fue ocultado expresamente por el admin.
                    cambios_publicacion.update({
                        "estado_revision": "oculta",
                        "ocultada_por_moderacion": True,
                        "activa": False,
                    })

                else:
                    modo_moderacion = (
                        obtener_modo_moderacion_publicaciones()
                    )

                    cambios_publicacion.update({
                        "estado_revision": "pendiente",
                        "revisada_at": None,
                        "revisada_por": None,
                        "ocultada_por_moderacion": False,
                        "activa": (
                            activa
                            if modo_moderacion == "suave"
                            else False
                        ),
                    })

            if (
                hubo_cambio_real
                and activa
                and not estaba_oculta_por_moderacion
            ):
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

        modo_moderacion = (
            obtener_modo_moderacion_publicaciones()
        )

        nueva_publicacion = {
            "id": uuid.uuid4().hex,
            "nombre": nombre,
            "precio": precio,
            "descripcion": descripcion,
            "imagenes": imagenes_urls,
            "imagen_principal": imagen_principal,
            "imagen_url": imagen_principal,
            "activa": (
                activa
                if modo_moderacion == "suave"
                else False
            ),
            "activa_solicitada": activa,
            "comercio_id": comercio_id,
            "direccion_mostrar": comercio.get("direccion_mostrar"),
            "created_at": datetime.datetime.utcnow().isoformat(),
            "estado_revision": "pendiente",
            "revisada_at": None,
            "revisada_por": None,
            "ocultada_por_moderacion": False
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
    # ============================================================
    # CINE Y TEATRO
    # Se conserva la categoría para personalizar el panel,
    # pero ya no se consultan carteleras ni funciones internas.
    # ============================================================
    es_cine_teatro = (comercio.get("categoria") or "").strip().lower() == "cine y teatro"

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

            # La lista principal muestra solamente historias que
            # todavía están dentro de sus 24 horas de vigencia.
            # Las vencidas permanecen guardadas para conservar
            # su historial y sus métricas.
            historias = [
                historia
                for historia in historias
                if historia.get("vigente") is True
            ]

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
        es_premium=es_premium,
        historias=historias,
        historias_activas=historias_activas,
        limite_historias_activas=2,
        modo_gestor=_modo_gestor_activo(),
        admin_user=session.get("admin_user")
    )



# ============================================================
# HISTORIAL DE HISTORIAS PREMIUM
# Página privada, paginada y solamente informativa.
# ============================================================

@app.route("/panel/historias/historial")
def historial_historias_panel():
    comercio, comercio_id, error_contexto = (
        _contexto_comercio_para_historias()
    )

    if error_contexto == "login":
        return redirect(url_for("login"))

    if error_contexto == "bloqueado":
        return "Esta cuenta fue bloqueada por administración.", 403

    if error_contexto == "premium":
        return redirect(
            url_for("panel", historia_error="solo_premium")
            + "#historias-premium"
        )

    if error_contexto or not comercio_id:
        return redirect(
            url_for("panel", historia_error="servidor")
            + "#historias-premium"
        )

    pagina_raw = str(
        request.args.get("pagina", "1") or "1"
    ).strip()

    try:
        pagina = max(1, int(pagina_raw))
    except (TypeError, ValueError):
        pagina = 1

    historias_por_pagina = 12
    desde = (pagina - 1) * historias_por_pagina

    # PostgREST usa un rango inclusivo. Se solicita un registro
    # adicional para saber si existe una página siguiente.
    hasta = desde + historias_por_pagina

    historias = []
    hay_siguiente = False

    try:
        ahora_utc = datetime.datetime.now(
            datetime.timezone.utc
        )

        historias_res = (
            supabase_admin
            .table("historias")
            .select(
                "id,comercio_id,imagen_url,texto,"
                "publicacion_id,activa,eliminada,"
                "expires_at,created_at"
            )
            .eq("comercio_id", comercio_id)
            .eq("eliminada", False)
            .lt("expires_at", ahora_utc.isoformat())
            .order("expires_at", desc=True)
            .range(desde, hasta)
            .execute()
        )

        historias_consultadas = historias_res.data or []
        hay_siguiente = (
            len(historias_consultadas)
            > historias_por_pagina
        )

        historias = historias_consultadas[
            :historias_por_pagina
        ]

        for historia in historias:
            historia["metricas_vistas"] = 0
            historia["metricas_click_publicacion"] = 0
            historia["metricas_click_comercio"] = 0

        ids_historias = [
            historia.get("id")
            for historia in historias
            if historia.get("id")
        ]

        if ids_historias:
            metricas_por_historia = {
                str(historia_id): {
                    "vista_historia": 0,
                    "click_historia_publicacion": 0,
                    "click_historia_comercio": 0,
                }
                for historia_id in ids_historias
            }

            inicio_metricas = 0
            tamanio_pagina_metricas = 1000

            while True:
                eventos_res = (
                    supabase_admin
                    .table("eventos_analytics")
                    .select("historia_id,tipo_evento")
                    .in_("historia_id", ids_historias)
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

        zona_argentina = datetime.timezone(
            datetime.timedelta(hours=-3)
        )

        def fecha_historia_mostrar(valor):
            valor_raw = str(valor or "").strip()

            if not valor_raw:
                return "Sin fecha"

            try:
                fecha = datetime.datetime.fromisoformat(
                    valor_raw.replace("Z", "+00:00")
                )

                if fecha.tzinfo is None:
                    fecha = fecha.replace(
                        tzinfo=datetime.timezone.utc
                    )

                fecha = fecha.astimezone(zona_argentina)

                return fecha.strftime("%d/%m/%Y · %H:%M")

            except Exception:
                return valor_raw

        for historia in historias:
            historia["created_at_mostrar"] = (
                fecha_historia_mostrar(
                    historia.get("created_at")
                )
            )

            historia["expires_at_mostrar"] = (
                fecha_historia_mostrar(
                    historia.get("expires_at")
                )
            )

    except Exception as e:
        print(
            "ERROR CARGANDO HISTORIAL DE HISTORIAS:",
            e,
            flush=True
        )

        historias = []
        hay_siguiente = False

    return render_template(
        "historial_historias.html",
        comercio=comercio,
        historias=historias,
        pagina=pagina,
        hay_anterior=pagina > 1,
        hay_siguiente=hay_siguiente
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
    user_id = _user_id_panel_efectivo()

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

        invalidar_cache_portada_historias()
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

        invalidar_cache_portada_historias()
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

        invalidar_cache_portada_historias()
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

        invalidar_cache_portada_historias()
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

        invalidar_cache_portada_historias()
        return _volver_historias(historia_ok="eliminada")

    except Exception as e:
        print("ERROR ELIMINANDO HISTORIA:", e, flush=True)
        return _volver_historias(historia_error="eliminar")


@app.route("/panel/cartelera/crear", methods=["POST"])
def crear_cartelera_panel():
    user_id = _user_id_panel_efectivo()
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
    user_id = _user_id_panel_efectivo()
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
    user_id = _user_id_panel_efectivo()

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
    user_id = _user_id_panel_efectivo()

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
    user_id = _user_id_panel_efectivo()

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
    invalidar_cache_publicaciones_portada()

    comercio = session.get("comercio") or comercio_default()
    user_id = _user_id_panel_efectivo()

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

    categoria_origen = request.args.get(
        "categoria",
        ""
    ).strip()

    if categoria_origen not in CATEGORIAS_HOME:
        categoria_origen = ""

    macro_origen = request.args.get(
        "macro",
        ""
    ).strip()

    if macro_origen not in MACROCATEGORIAS_POR_SLUG:
        macro_origen = ""

    parametros_regreso = {}

    if categoria_origen:
        parametros_regreso["categoria"] = (
            categoria_origen
        )

    if macro_origen:
        parametros_regreso["macro"] = macro_origen

    if parametros_regreso:
        regreso_galeria_url = url_for(
            "inicio",
            _anchor="publicaciones-recientes",
            **parametros_regreso,
        )
    else:
        regreso_galeria_url = url_for("inicio")

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

        if (
            comercio_id
            and not analytics_visita_ya_registrada(
                "publicacion",
                publicacion_id,
            )
        ):
            visita_registrada = analytics_registrar_evento(
                "visita_publicacion",
                comercio_id=comercio_id,
                publicacion_id=publicacion_id,
                busqueda_id=busqueda_id_origen,
                consulta_origen=consulta_origen,
                origen="detalle_publicacion",
                metadata={
                    "publicacion_nombre": publicacion_encontrada.get("nombre"),
                    "medicion": "una_visita_por_publicacion_y_sesion",
                }
            )

            if visita_registrada:
                analytics_marcar_visita_registrada(
                    "publicacion",
                    publicacion_id,
                )

    except Exception as e:
        print("ERROR cargando detalle de publicación:", e, flush=True)
        return redirect(url_for("inicio"))

    return render_template(
        "detalle.html",
        comercio=comercio,
        publicacion=publicacion_encontrada,
        regreso_galeria_url=regreso_galeria_url,
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

        if not analytics_visita_ya_registrada(
            "comercio",
            comercio_id,
        ):
            visita_registrada = analytics_registrar_evento(
                "visita_comercio",
                comercio_id=comercio_id,
                busqueda_id=busqueda_id_origen,
                consulta_origen=consulta_origen,
                origen="perfil_comercio",
                metadata={
                    "nombre_negocio": nombre_negocio,
                    "medicion": "una_visita_por_comercio_y_sesion",
                }
            )

            if visita_registrada:
                analytics_marcar_visita_registrada(
                    "comercio",
                    comercio_id,
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
    if _modo_gestor_activo():
        return redirect(url_for("panel") + "#plan")

    user_id = _user_id_panel_efectivo()

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
    session.pop("gestor_comercio_id", None)
    session.pop("comercio", None)
    session.pop("publicaciones", None)
    return redirect(url_for("admin_login"))



@app.route(
    "/admin/gestionar/<comercio_id>",
    methods=["POST"]
)
@admin_requerido
def admin_gestionar_comercio(comercio_id):
    """
    Entra al panel de un comercio usando la sesión propia
    del administrador.

    No conoce ni modifica la contraseña del comercio.
    No reemplaza session["user_id"].
    """

    try:
        uuid.UUID(str(comercio_id))
    except Exception:
        return redirect(
            url_for("admin", gestor_error="id_invalido")
        )

    try:
        comercio_res = (
            supabase_admin
            .table("comercios")
            .select(
                "id,user_id,nombre_negocio,"
                "activo,categoria,logo_url"
            )
            .eq("id", comercio_id)
            .limit(1)
            .execute()
        )

        comercios = comercio_res.data or []

        if not comercios:
            return redirect(
                url_for("admin", gestor_error="no_encontrado")
            )

        comercio = comercios[0]

        if comercio.get("activo") is False:
            return redirect(
                url_for(
                    "admin",
                    gestor_error="comercio_bloqueado"
                )
            )

        if not comercio.get("user_id"):
            return redirect(
                url_for(
                    "admin",
                    gestor_error="sin_propietario"
                )
            )

        session["gestor_comercio_id"] = comercio["id"]

        # Evitamos conservar datos de otro comercio en caché
        # de sesión. El /panel volverá a cargar los datos reales.
        session.pop("comercio", None)
        session.pop("publicaciones", None)

        session.modified = True

        return redirect(
            url_for("panel", gestor="1")
        )

    except Exception as e:
        print(
            "ERROR INICIANDO CUENTA GESTOR:",
            type(e),
            e,
            flush=True
        )

        return redirect(
            url_for("admin", gestor_error="servidor")
        )


@app.route(
    "/admin/gestor/salir",
    methods=["POST"]
)
@admin_requerido
def admin_salir_gestor():
    session.pop("gestor_comercio_id", None)

    # commerce/publicaciones pertenecen al comercio gestionado.
    # Se limpian para evitar mezclar información.
    session.pop("comercio", None)
    session.pop("publicaciones", None)

    session.modified = True

    return redirect(
        url_for("admin", gestor_fin="1")
    )


@app.route("/admin")
@app.route("/admin.html")
@admin_requerido
def admin():
    revisar_premium_vencidos()

    error = None
    comercios_raw = []
    publicaciones_raw = []
    consultas_soporte = []
    consultas_soporte_resueltas = []

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

    try:
        soporte_res = (
            supabase_admin
            .table("consultas_soporte")
            .select("*")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        consultas_soporte = soporte_res.data or []

        consultas_soporte.sort(
            key=lambda consulta: (
                0
                if str(
                    consulta.get("estado") or ""
                ).strip().lower() == "pendiente"
                else 1,
                str(consulta.get("created_at") or "")
            ),
            reverse=False
        )

        pendientes = [
            c for c in consultas_soporte
            if str(c.get("estado") or "").strip().lower()
            == "pendiente"
        ]

        resueltas = [
            c for c in consultas_soporte
            if str(c.get("estado") or "").strip().lower()
            != "pendiente"
        ]

        pendientes.sort(
            key=lambda c: str(c.get("created_at") or ""),
            reverse=True
        )

        resueltas.sort(
            key=lambda c: str(c.get("created_at") or ""),
            reverse=True
        )

        consultas_soporte = pendientes
        consultas_soporte_resueltas = resueltas

    except Exception as e:
        if error:
            error += f" | No se pudieron cargar las consultas de soporte: {e}"
        else:
            error = f"No se pudieron cargar las consultas de soporte: {e}"

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
            "descripcion": c.get("descripcion") or "Sin descripción.",
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

    categorias_por_revisar = [
        c for c in comercios
        if str(c.get("categoria") or "").strip() == "Otros"
    ]

    categorias_reasignacion = [
        categoria
        for categoria in CATEGORIAS_COMERCIO
        if categoria != "Otros"
    ]

    # ========================================================
    # ULTIMOS MOVIMIENTOS DEL ADMIN
    # ========================================================

    comercios_por_id_admin = {
        str(c.get("id")): c
        for c in comercios
        if c.get("id")
    }

    ultimos_comercios = sorted(
        comercios,
        key=lambda c: str(c.get("created_at") or ""),
        reverse=True
    )[:10]

    for comercio_admin in ultimos_comercios:
        comercio_admin["created_at_mostrar"] = (
            formatear_fecha_argentina(
                comercio_admin.get("created_at")
            )
        )

    ultimas_publicaciones = []

    publicaciones_ordenadas = sorted(
        [
            pub for pub in publicaciones_raw
            if pub.get("eliminada") is not True
        ],
        key=lambda pub: str(pub.get("created_at") or ""),
        reverse=True
    )[:10]

    for pub in publicaciones_ordenadas:
        comercio_id_pub = str(
            pub.get("comercio_id") or ""
        )

        comercio_pub = comercios_por_id_admin.get(
            comercio_id_pub,
            {}
        )

        ultimas_publicaciones.append({
            "id": pub.get("id"),
            "nombre": (
                pub.get("nombre")
                or pub.get("titulo")
                or "Sin nombre"
            ),
            "comercio_nombre": (
                comercio_pub.get("nombre")
                or "Comercio no identificado"
            ),
            "categoria": (
                comercio_pub.get("categoria")
                or "-"
            ),
            "created_at": pub.get("created_at"),
            "created_at_mostrar": (
                formatear_fecha_argentina(
                    pub.get("created_at")
                )
            ),
            "activa": es_publicacion_activa(pub),
        })

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

    total_moderacion_pendiente = sum(
        1
        for publicacion in publicaciones_raw
        if (
            publicacion.get("estado_revision")
            == "pendiente"
            and publicacion.get("eliminada") is not True
        )
    )

    resumen = {
        "total_comercios": len(comercios),
        "total_premium": total_premium,
        "total_publicaciones": len(publicaciones_raw),
        "total_publicaciones_activas": total_publicaciones_activas,
        "total_solicitudes_premium": len(solicitudes_premium),
        "total_bloqueados": total_bloqueados,
        "total_moderacion_pendiente": total_moderacion_pendiente,
        "total_consultas_soporte": sum(
            1
            for consulta in consultas_soporte
            if str(consulta.get("estado") or "").lower()
            == "pendiente"
        ),
    }

    return render_template(
        "admin.html",
        resumen=resumen,
        comercios=comercios,
        solicitudes_premium=solicitudes_premium,
        categorias_por_revisar=categorias_por_revisar,
        categorias_reasignacion=categorias_reasignacion,
        ultimos_comercios=ultimos_comercios,
        ultimas_publicaciones=ultimas_publicaciones,
        consultas_soporte=consultas_soporte,
        consultas_soporte_resueltas=consultas_soporte_resueltas,
        error=error,
        admin_user=session.get("admin_user")
    )




# ============================================================
# CLICKLOCAL — SOPORTE ADMIN
# ============================================================

@app.route(
    "/admin/soporte/<consulta_id>/resolver",
    methods=["POST"]
)
@admin_requerido
def admin_resolver_consulta_soporte(consulta_id):
    try:
        from uuid import UUID
        consulta_id_valido = str(UUID(str(consulta_id)))
    except (ValueError, TypeError, AttributeError):
        return redirect(url_for("admin"))

    try:
        (
            supabase_admin
            .table("consultas_soporte")
            .update({
                "estado": "resuelta",
                "resuelta_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "resuelta_por": session.get("admin_user"),
            })
            .eq("id", consulta_id_valido)
            .execute()
        )

    except Exception as e:
        print(
            "ERROR RESOLVIENDO CONSULTA SOPORTE:",
            type(e),
            e,
            flush=True
        )

    return redirect(url_for("admin"))


@app.route(
    "/admin/soporte/<consulta_id>/eliminar",
    methods=["POST"]
)
@admin_requerido
def admin_eliminar_consulta_soporte(consulta_id):
    try:
        from uuid import UUID
        consulta_id_valido = str(UUID(str(consulta_id)))
    except (ValueError, TypeError, AttributeError):
        return redirect(url_for("admin"))

    try:
        (
            supabase_admin
            .table("consultas_soporte")
            .delete()
            .eq("id", consulta_id_valido)
            .execute()
        )

    except Exception as e:
        print(
            "ERROR ELIMINANDO CONSULTA SOPORTE:",
            type(e),
            e,
            flush=True
        )

    return redirect(url_for("admin"))


# ============================================================
# CLICKLOCAL: MODERACIÓN DE PUBLICACIONES V1
# ============================================================

def formatear_fecha_argentina(valor):
    """
    Convierte una fecha ISO de Supabase, normalmente UTC,
    a la hora local de Argentina.

    Ejemplo:
    2026-07-28T17:09:53+00:00
    -> 28/07/2026 a las 14:09
    """
    if not valor:
        return "-"

    try:
        from zoneinfo import ZoneInfo

        texto = str(valor).strip().replace(
            "Z",
            "+00:00"
        )

        fecha = datetime.datetime.fromisoformat(
            texto
        )

        if fecha.tzinfo is None:
            fecha = fecha.replace(
                tzinfo=datetime.timezone.utc
            )

        fecha_argentina = fecha.astimezone(
            ZoneInfo("America/Argentina/Cordoba")
        )

        return fecha_argentina.strftime(
            "%d/%m/%Y a las %H:%M"
        )

    except Exception:
        return "-"


def obtener_modo_moderacion_publicaciones():
    """
    Devuelve:
    - suave: pendiente pero visible
    - segura: pendiente e invisible
    """
    try:
        respuesta = (
            supabase_admin
            .table("configuracion_sistema")
            .select("valor")
            .eq("clave", "moderacion_publicaciones")
            .limit(1)
            .execute()
        )

        filas = respuesta.data or []

        if filas:
            valor = str(
                filas[0].get("valor") or ""
            ).strip().lower()

            if valor in {"suave", "segura"}:
                return valor

    except Exception as error:
        print(
            "AVISO leyendo modo de moderación:",
            error,
            flush=True
        )

    # El fallback suave evita bloquear publicaciones
    # accidentalmente si falla una lectura temporal.
    return "suave"


@app.route("/admin/moderacion")
@admin_requerido
def admin_moderacion():
    estado = str(
        request.args.get("estado") or "pendiente"
    ).strip().lower()

    estados_permitidos = {
        "pendiente",
        "aprobada",
        "oculta",
        "todas",
    }

    if estado not in estados_permitidos:
        estado = "pendiente"

    modo_actual = obtener_modo_moderacion_publicaciones()
    error = None
    publicaciones = []

    try:
        consulta = (
            supabase_admin
            .table("publicaciones")
            .select(
                "id,nombre,precio,descripcion,imagenes,"
                "imagen_principal,imagen_url,activa,eliminada,"
                "comercio_id,created_at,estado_revision,"
                "revisada_at,revisada_por,"
                "ocultada_por_moderacion"
            )
            .eq("eliminada", False)
        )

        if estado != "todas":
            consulta = consulta.eq(
                "estado_revision",
                estado
            )

        respuesta = (
            consulta
            .order("created_at", desc=True)
            .limit(250)
            .execute()
        )

        publicaciones = respuesta.data or []

        comercio_ids = sorted({
            publicacion.get("comercio_id")
            for publicacion in publicaciones
            if publicacion.get("comercio_id")
        })

        comercios_por_id = {}

        if comercio_ids:
            comercios_res = (
                supabase_admin
                .table("comercios")
                .select(
                    "id,nombre_negocio,categoria,ciudad,"
                    "activo,created_at"
                )
                .in_("id", comercio_ids)
                .execute()
            )

            comercios_por_id = {
                comercio.get("id"): comercio
                for comercio in comercios_res.data or []
            }

        ahora = datetime.datetime.now(
            datetime.timezone.utc
        )

        for publicacion in publicaciones:
            comercio = comercios_por_id.get(
                publicacion.get("comercio_id"),
                {}
            )

            imagenes = publicacion.get("imagenes") or []

            primera_imagen = (
                imagenes[0]
                if isinstance(imagenes, list) and imagenes
                else ""
            )

            publicacion["imagen_mostrar"] = (
                publicacion.get("imagen_principal")
                or publicacion.get("imagen_url")
                or primera_imagen
                or ""
            )

            publicacion["comercio"] = comercio
            publicacion["comercio_nombre"] = (
                comercio.get("nombre_negocio")
                or "Comercio sin nombre"
            )

            publicacion["perfil_url"] = url_for(
                "perfil_comercio",
                comercio_id=publicacion.get("comercio_id")
            )

            publicacion["detalle_url"] = url_for(
                "detalle",
                publicacion_id=publicacion.get("id")
            )

            comercio_nuevo = False
            created_at = str(
                comercio.get("created_at") or ""
            ).strip()

            if created_at:
                try:
                    fecha_comercio = datetime.datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )

                    if fecha_comercio.tzinfo is None:
                        fecha_comercio = fecha_comercio.replace(
                            tzinfo=datetime.timezone.utc
                        )

                    comercio_nuevo = (
                        ahora - fecha_comercio
                    ).days <= 7
                except Exception:
                    comercio_nuevo = False

            publicacion["comercio_nuevo"] = comercio_nuevo
            publicacion["created_at_mostrar"] = (
                formatear_fecha_argentina(
                    publicacion.get("created_at")
                )
            )

    except Exception as excepcion:
        print(
            "ERROR CARGANDO MODERACIÓN:",
            excepcion,
            flush=True
        )
        error = str(excepcion)

    conteos = {
        "pendiente": 0,
        "aprobada": 0,
        "oculta": 0,
    }

    try:
        estados_res = (
            supabase_admin
            .table("publicaciones")
            .select("estado_revision,eliminada")
            .eq("eliminada", False)
            .execute()
        )

        for fila in estados_res.data or []:
            estado_fila = fila.get("estado_revision")

            if estado_fila in conteos:
                conteos[estado_fila] += 1

    except Exception as excepcion:
        print(
            "AVISO CONTANDO MODERACIÓN:",
            excepcion,
            flush=True
        )

    return render_template(
        "admin_moderacion.html",
        publicaciones=publicaciones,
        estado=estado,
        conteos=conteos,
        modo_actual=modo_actual,
        error=error,
        admin_user=session.get("admin_user"),
    )


@app.route(
    "/admin/moderacion/cambiar-modo",
    methods=["POST"]
)
@admin_requerido
def admin_moderacion_cambiar_modo():
    nuevo_modo = str(
        request.form.get("modo") or ""
    ).strip().lower()

    if nuevo_modo not in {"suave", "segura"}:
        return redirect(
            url_for(
                "admin_moderacion",
                modo_error="1"
            )
        )

    try:
        (
            supabase_admin
            .table("configuracion_sistema")
            .update({
                "valor": nuevo_modo,
                "updated_at": (
                    datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat()
                ),
            })
            .eq("clave", "moderacion_publicaciones")
            .execute()
        )

        return redirect(
            url_for(
                "admin_moderacion",
                modo_actualizado="1"
            )
        )

    except Exception as excepcion:
        print(
            "ERROR CAMBIANDO MODO:",
            excepcion,
            flush=True
        )

        return redirect(
            url_for(
                "admin_moderacion",
                modo_error="1"
            )
        )


@app.route(
    "/admin/moderacion/aprobar/<publicacion_id>",
    methods=["POST"]
)
@admin_requerido
def admin_moderacion_aprobar(publicacion_id):
    invalidar_cache_publicaciones_portada()

    publicacion_id = uuid_o_none(publicacion_id)

    if not publicacion_id:
        return redirect(
            url_for(
                "admin_moderacion",
                accion_error="1"
            )
        )

    try:
        ahora = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()

        publicacion_res = (
            supabase_admin
            .table("publicaciones")
            .select("id,activa_solicitada")
            .eq("id", publicacion_id)
            .limit(1)
            .execute()
        )

        filas_publicacion = publicacion_res.data or []

        if not filas_publicacion:
            return redirect(
                url_for(
                    "admin_moderacion",
                    accion_error="1"
                )
            )

        activa_solicitada = (
            filas_publicacion[0].get(
                "activa_solicitada"
            ) is True
        )

        (
            supabase_admin
            .table("publicaciones")
            .update({
                "estado_revision": "aprobada",
                "revisada_at": ahora,
                "revisada_por": (
                    session.get("admin_user")
                    or "admin"
                ),
                "ocultada_por_moderacion": False,
                "activa": activa_solicitada,
            })
            .eq("id", publicacion_id)
            .execute()
        )

        return redirect(
            url_for(
                "admin_moderacion",
                aprobada="1"
            )
        )

    except Exception as excepcion:
        print(
            "ERROR APROBANDO PUBLICACIÓN:",
            excepcion,
            flush=True
        )

        return redirect(
            url_for(
                "admin_moderacion",
                accion_error="1"
            )
        )


@app.route(
    "/admin/moderacion/ocultar/<publicacion_id>",
    methods=["POST"]
)
@admin_requerido
def admin_moderacion_ocultar(publicacion_id):
    invalidar_cache_publicaciones_portada()

    publicacion_id = uuid_o_none(publicacion_id)

    if not publicacion_id:
        return redirect(
            url_for(
                "admin_moderacion",
                accion_error="1"
            )
        )

    try:
        ahora = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()

        (
            supabase_admin
            .table("publicaciones")
            .update({
                "estado_revision": "oculta",
                "revisada_at": ahora,
                "revisada_por": (
                    session.get("admin_user")
                    or "admin"
                ),
                "ocultada_por_moderacion": True,
                "activa": False,
            })
            .eq("id", publicacion_id)
            .execute()
        )

        return redirect(
            url_for(
                "admin_moderacion",
                ocultada="1"
            )
        )

    except Exception as excepcion:
        print(
            "ERROR OCULTANDO PUBLICACIÓN:",
            excepcion,
            flush=True
        )

        return redirect(
            url_for(
                "admin_moderacion",
                accion_error="1"
            )
        )


@app.route(
    "/admin/asignar-categoria/<comercio_id>",
    methods=["POST"]
)
@admin_requerido
def admin_asignar_categoria(comercio_id):
    invalidar_cache_publicaciones_portada()

    categoria_nueva = str(
        request.form.get("categoria", "") or ""
    ).strip()

    categorias_validas = {
        categoria
        for categoria in CATEGORIAS_COMERCIO
        if categoria != "Otros"
    }

    if categoria_nueva not in categorias_validas:
        return redirect(
            url_for(
                "admin",
                categoria_error="1"
            )
        )

    try:
        (
            supabase_admin
            .table("comercios")
            .update({
                "categoria": categoria_nueva
            })
            .eq("id", comercio_id)
            .execute()
        )

        return redirect(
            url_for(
                "admin",
                categoria_asignada="1"
            )
        )

    except Exception as e:
        print(
            "ERROR ASIGNANDO CATEGORIA DESDE ADMIN:",
            type(e),
            e,
            flush=True
        )

        return redirect(
            url_for(
                "admin",
                categoria_error="1"
            )
        )


@app.route("/admin/activar-premium/<comercio_id>", methods=["POST"])
@admin_requerido
def admin_activar_premium(comercio_id):
    invalidar_cache_publicaciones_portada()

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

        (
            supabase_admin
            .table("publicaciones")
            .update({
                "activa": True,
                "pausada_por_limite_plan": False
            })
            .eq("comercio_id", comercio_id)
            .eq("pausada_por_limite_plan", True)
            .eq("estado_revision", "aprobada")
            .eq("ocultada_por_moderacion", False)
            .eq("activa_solicitada", True)
            .execute()
        )

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
    invalidar_cache_publicaciones_portada()

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
    invalidar_cache_publicaciones_portada()

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

        (
            supabase_admin
            .table("publicaciones")
            .update({
                "activa": True
            })
            .eq("comercio_id", comercio_id)
            .eq("eliminada", False)
            .eq("estado_revision", "aprobada")
            .eq("ocultada_por_moderacion", False)
            .eq("activa_solicitada", True)
            .execute()
        )

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
    from collections import Counter, defaultdict
    from datetime import datetime, timedelta, timezone

    def _parse_fecha(valor):
        if not valor:
            return None

        try:
            texto = str(valor).replace("Z", "+00:00")
            fecha = datetime.fromisoformat(texto)

            if fecha.tzinfo is None:
                fecha = fecha.replace(
                    tzinfo=timezone.utc
                )

            return fecha

        except Exception:
            return None

    def _valor_presente(valor):
        return (
            valor is not None
            and str(valor).strip() != ""
        )

    def _leer_paginado(
        tabla,
        columnas,
        desde_iso=None,
        tamanio=1000,
        maximo=50000,
    ):
        registros = []
        inicio = 0

        while inicio < maximo:
            fin = min(
                inicio + tamanio - 1,
                maximo - 1,
            )

            consulta = (
                supabase_admin
                .table(tabla)
                .select(columnas)
            )

            if desde_iso:
                consulta = consulta.gte(
                    "created_at",
                    desde_iso,
                )

            respuesta = (
                consulta
                .order(
                    "created_at",
                    desc=True,
                )
                .range(inicio, fin)
                .execute()
            )

            lote = respuesta.data or []
            registros.extend(lote)

            if len(lote) < tamanio:
                break

            inicio += tamanio

        truncado = (
            len(registros) >= maximo
        )

        return registros, truncado

    def _leer_con_respaldo(
        tabla,
        columnas,
        desde_iso=None,
    ):
        try:
            return (
                *_leer_paginado(
                    tabla,
                    columnas,
                    desde_iso=desde_iso,
                ),
                None,
            )

        except Exception as error_especifico:
            try:
                datos, truncado = (
                    _leer_paginado(
                        tabla,
                        "*",
                        desde_iso=desde_iso,
                    )
                )

                return (
                    datos,
                    truncado,
                    (
                        f"{tabla}: se utilizó lectura "
                        f"general porque falló la "
                        f"selección específica: "
                        f"{error_especifico}"
                    ),
                )

            except Exception as error_general:
                return (
                    [],
                    False,
                    f"{tabla}: {error_general}",
                )

    def _nombre_comercio(
        comercios_por_id,
        comercio_id,
    ):
        comercio = comercios_por_id.get(
            str(comercio_id or "")
        )

        if not comercio:
            return "Comercio sin identificar"

        return (
            comercio.get("nombre_negocio")
            or comercio.get("nombre")
            or "Comercio sin nombre"
        )

    def _nombre_publicacion(
        publicaciones_por_id,
        publicacion_id,
    ):
        publicacion = publicaciones_por_id.get(
            str(publicacion_id or "")
        )

        if not publicacion:
            return "Publicación sin identificar"

        return (
            publicacion.get("nombre")
            or "Publicación sin nombre"
        )

    def _nombre_historia(
        historias_por_id,
        historia_id,
    ):
        historia = historias_por_id.get(
            str(historia_id or "")
        )

        if not historia:
            return "Historia sin identificar"

        texto = str(
            historia.get("texto")
            or ""
        ).strip()

        if texto:
            return texto[:80]

        return "Historia sin texto"

    def _top(
        counter,
        limite=15,
    ):
        return [
            {
                "nombre": nombre,
                "cantidad": cantidad,
            }
            for nombre, cantidad
            in counter.most_common(limite)
        ]

    dias_raw = request.args.get(
        "dias",
        "30",
    )

    if dias_raw == "todos":
        dias = None
        desde = None
        desde_iso = None
        periodo_label = (
            "Todo el historial disponible"
        )

    else:
        try:
            dias = int(dias_raw)

        except Exception:
            dias = 30
            dias_raw = "30"

        if dias not in (7, 30, 90):
            dias = 30
            dias_raw = "30"

        desde = (
            datetime.now(timezone.utc)
            - timedelta(days=dias)
        )

        desde_iso = desde.isoformat()
        periodo_label = (
            f"Últimos {dias} días"
        )

    errores = []
    advertencias = []

    eventos, truncado_eventos, error = (
        _leer_con_respaldo(
            "eventos_analytics",
            (
                "id,created_at,tipo_evento,"
                "publicacion_id,comercio_id,"
                "historia_id,busqueda_id,"
                "visitante_id,sesion_id,"
                "modo_acceso,consulta_origen"
            ),
            desde_iso=desde_iso,
        )
    )

    if error:
        errores.append(error)

    if truncado_eventos:
        advertencias.append(
            "Se alcanzó el límite interno "
            "de lectura de eventos."
        )

    busquedas, truncado_busquedas, error = (
        _leer_con_respaldo(
            "busquedas_publicas",
            (
                "id,created_at,consulta,"
                "total_resultados,"
                "visitante_id,sesion_id,"
                "modo_acceso"
            ),
            desde_iso=desde_iso,
        )
    )

    if error:
        errores.append(error)

    if truncado_busquedas:
        advertencias.append(
            "Se alcanzó el límite interno "
            "de lectura de búsquedas."
        )

    comercios, _, error = (
        _leer_con_respaldo(
            "comercios",
            (
                "id,created_at,nombre_negocio,"
                "categoria,ciudad,plan,activo"
            ),
        )
    )

    if error:
        errores.append(error)

    publicaciones, _, error = (
        _leer_con_respaldo(
            "publicaciones",
            (
                "id,created_at,nombre,precio,"
                "descripcion,comercio_id,"
                "activa,eliminada"
            ),
        )
    )

    if error:
        errores.append(error)

    historias, _, error = (
        _leer_con_respaldo(
            "historias",
            (
                "id,created_at,comercio_id,"
                "texto,publicacion_id,"
                "activa,eliminada"
            ),
        )
    )

    if error:
        errores.append(error)

    listas, _, error = (
        _leer_con_respaldo(
            "listas_buscables",
            (
                "id,created_at,comercio_id,"
                "producto_categoria,"
                "atributos_texto,activa"
            ),
        )
    )

    if error:
        errores.append(error)

    comercios_por_id = {
        str(item.get("id")): item
        for item in comercios
        if item.get("id")
    }

    publicaciones_por_id = {
        str(item.get("id")): item
        for item in publicaciones
        if item.get("id")
    }

    historias_por_id = {
        str(item.get("id")): item
        for item in historias
        if item.get("id")
    }

    busquedas_por_id = {
        str(item.get("id")): item
        for item in busquedas
        if item.get("id")
    }

    visitantes = set()
    sesiones = set()

    sesiones_web = set()
    sesiones_pwa = set()

    registros_identificados = 0
    registros_sin_identificar = 0

    primera_fecha_identificada = None

    for registro in eventos + busquedas:
        visitante_id = str(
            registro.get("visitante_id")
            or ""
        ).strip()

        sesion_id = str(
            registro.get("sesion_id")
            or ""
        ).strip()

        modo_acceso = str(
            registro.get("modo_acceso")
            or ""
        ).strip().lower()

        fecha = _parse_fecha(
            registro.get("created_at")
        )

        if visitante_id or sesion_id:
            registros_identificados += 1

            if (
                fecha
                and (
                    primera_fecha_identificada
                    is None
                    or fecha
                    < primera_fecha_identificada
                )
            ):
                primera_fecha_identificada = (
                    fecha
                )

        else:
            registros_sin_identificar += 1

        if visitante_id:
            visitantes.add(visitante_id)

        if sesion_id:
            sesiones.add(sesion_id)

            if modo_acceso == "pwa":
                sesiones_pwa.add(sesion_id)

            elif modo_acceso == "web":
                sesiones_web.add(sesion_id)

    tipos_counter = Counter(
        str(
            evento.get("tipo_evento")
            or "sin_tipo"
        ).strip().lower()
        for evento in eventos
    )

    total_visita_publicacion = (
        tipos_counter.get(
            "visita_publicacion",
            0,
        )
    )

    total_visita_comercio = (
        tipos_counter.get(
            "visita_comercio",
            0,
        )
    )

    total_whatsapp = (
        tipos_counter.get(
            "click_whatsapp",
            0,
        )
    )

    total_vista_historia = (
        tipos_counter.get(
            "vista_historia",
            0,
        )
    )

    total_click_historia_comercio = (
        tipos_counter.get(
            "click_historia_comercio",
            0,
        )
    )

    total_click_historia_publicacion = (
        tipos_counter.get(
            "click_historia_publicacion",
            0,
        )
    )

    total_busquedas = len(busquedas)

    busquedas_sin_resultados = []

    for busqueda in busquedas:
        try:
            if int(
                busqueda.get(
                    "total_resultados"
                )
            ) == 0:
                busquedas_sin_resultados.append(
                    busqueda
                )

        except Exception:
            pass

    total_sin_resultados = len(
        busquedas_sin_resultados
    )

    porcentaje_sin_resultados = (
        round(
            (
                total_sin_resultados
                / total_busquedas
            )
            * 100,
            1,
        )
        if total_busquedas
        else 0
    )

    whatsapp_por_sesion = (
        round(
            total_whatsapp
            / len(sesiones),
            2,
        )
        if sesiones
        else 0
    )

    whatsapp_por_perfil = (
        round(
            (
                total_whatsapp
                / total_visita_comercio
            )
            * 100,
            1,
        )
        if total_visita_comercio
        else 0
    )

    publicaciones_por_visitante = (
        round(
            (
                total_visita_publicacion
                / len(visitantes)
            ),
            2,
        )
        if visitantes
        else 0
    )

    sesiones_por_visitante = (
        round(
            len(sesiones)
            / len(visitantes),
            2,
        )
        if visitantes
        else 0
    )

    sesiones_con_modo = (
        sesiones_web
        | sesiones_pwa
    )

    porcentaje_pwa = (
        round(
            (
                len(sesiones_pwa)
                / len(sesiones_con_modo)
            )
            * 100,
            1,
        )
        if sesiones_con_modo
        else 0
    )

    cobertura_identidad = (
        round(
            (
                registros_identificados
                / (
                    registros_identificados
                    + registros_sin_identificar
                )
            )
            * 100,
            1,
        )
        if (
            registros_identificados
            + registros_sin_identificar
        )
        else 0
    )

    stats = {
        "visitantes_unicos": len(visitantes),
        "sesiones_unicas": len(sesiones),
        "total_busquedas": total_busquedas,
        "sin_resultados": total_sin_resultados,
        "visitas_publicaciones": (
            total_visita_publicacion
        ),
        "visitas_comercios": (
            total_visita_comercio
        ),
        "clicks_whatsapp": total_whatsapp,
        "historias_vistas": (
            total_vista_historia
        ),
        "sesiones_web": len(sesiones_web),
        "sesiones_pwa": len(sesiones_pwa),
        "click_historia_comercio": (
            total_click_historia_comercio
        ),
        "click_historia_publicacion": (
            total_click_historia_publicacion
        ),
        "porcentaje_sin_resultados": (
            porcentaje_sin_resultados
        ),
        "whatsapp_por_sesion": (
            whatsapp_por_sesion
        ),
        "whatsapp_por_perfil": (
            whatsapp_por_perfil
        ),
        "publicaciones_por_visitante": (
            publicaciones_por_visitante
        ),
        "sesiones_por_visitante": (
            sesiones_por_visitante
        ),
        "porcentaje_pwa": porcentaje_pwa,
        "cobertura_identidad": (
            cobertura_identidad
        ),
    }

    publicaciones_vistas_counter = Counter()
    comercios_visitados_counter = Counter()
    whatsapp_comercios_counter = Counter()
    historias_vistas_counter = Counter()
    actividad_categoria_counter = Counter()
    whatsapp_consulta_counter = Counter()

    for evento in eventos:
        tipo = str(
            evento.get("tipo_evento")
            or ""
        ).strip().lower()

        comercio_id = evento.get(
            "comercio_id"
        )

        comercio = comercios_por_id.get(
            str(comercio_id or "")
        )

        categoria = (
            comercio.get("categoria")
            if comercio
            else None
        )

        if categoria:
            actividad_categoria_counter[
                str(categoria)
            ] += 1

        if tipo == "visita_publicacion":
            publicacion_id = evento.get(
                "publicacion_id"
            )

            nombre_publicacion = (
                _nombre_publicacion(
                    publicaciones_por_id,
                    publicacion_id,
                )
            )

            nombre_comercio = (
                _nombre_comercio(
                    comercios_por_id,
                    comercio_id,
                )
            )

            clave = (
                f"{nombre_publicacion} "
                f"— {nombre_comercio}"
            )

            publicaciones_vistas_counter[
                clave
            ] += 1

        elif tipo == "visita_comercio":
            nombre = _nombre_comercio(
                comercios_por_id,
                comercio_id,
            )

            comercios_visitados_counter[
                nombre
            ] += 1

        elif tipo == "click_whatsapp":
            nombre = _nombre_comercio(
                comercios_por_id,
                comercio_id,
            )

            whatsapp_comercios_counter[
                nombre
            ] += 1

            consulta_origen = str(
                evento.get("consulta_origen")
                or ""
            ).strip()

            if (
                not consulta_origen
                and evento.get("busqueda_id")
            ):
                busqueda = (
                    busquedas_por_id.get(
                        str(
                            evento.get(
                                "busqueda_id"
                            )
                        )
                    )
                )

                if busqueda:
                    consulta_origen = str(
                        busqueda.get("consulta")
                        or ""
                    ).strip()

            if consulta_origen:
                whatsapp_consulta_counter[
                    consulta_origen.lower()
                ] += 1

        elif tipo == "vista_historia":
            historia_id = evento.get(
                "historia_id"
            )

            nombre_historia = (
                _nombre_historia(
                    historias_por_id,
                    historia_id,
                )
            )

            nombre_comercio = (
                _nombre_comercio(
                    comercios_por_id,
                    comercio_id,
                )
            )

            clave = (
                f"{nombre_historia} "
                f"— {nombre_comercio}"
            )

            historias_vistas_counter[
                clave
            ] += 1

    busquedas_counter = Counter()
    sin_resultados_counter = Counter()

    for busqueda in busquedas:
        consulta = str(
            busqueda.get("consulta")
            or ""
        ).strip().lower()

        if not consulta:
            continue

        busquedas_counter[consulta] += 1

        try:
            if int(
                busqueda.get(
                    "total_resultados"
                )
            ) == 0:
                sin_resultados_counter[
                    consulta
                ] += 1

        except Exception:
            pass

    productos_declarados_counter = Counter()

    for lista in listas:
        producto = str(
            lista.get("producto_categoria")
            or ""
        ).strip().lower()

        if producto:
            productos_declarados_counter[
                producto
            ] += 1

    categorias_comercios_counter = Counter(
        str(
            comercio.get("categoria")
            or "Sin categoría"
        ).strip()
        for comercio in comercios
        if comercio.get("activo") is not False
    )

    rankings = {
        "publicaciones_vistas": _top(
            publicaciones_vistas_counter,
            20,
        ),
        "comercios_visitados": _top(
            comercios_visitados_counter,
            20,
        ),
        "whatsapp_comercios": _top(
            whatsapp_comercios_counter,
            20,
        ),
        "historias_vistas": _top(
            historias_vistas_counter,
            20,
        ),
        "busquedas": _top(
            busquedas_counter,
            20,
        ),
        "sin_resultados": _top(
            sin_resultados_counter,
            20,
        ),
        "whatsapp_consultas": _top(
            whatsapp_consulta_counter,
            20,
        ),
        "actividad_categorias": _top(
            actividad_categoria_counter,
            20,
        ),
        "categorias_comercios": _top(
            categorias_comercios_counter,
            20,
        ),
        "productos_declarados": _top(
            productos_declarados_counter,
            20,
        ),
    }

    diario = defaultdict(
        lambda: {
            "visitantes": set(),
            "sesiones": set(),
            "busquedas": 0,
            "sin_resultados": 0,
            "visitas_publicaciones": 0,
            "visitas_comercios": 0,
            "whatsapp": 0,
            "historias": 0,
        }
    )

    for busqueda in busquedas:
        fecha = _parse_fecha(
            busqueda.get("created_at")
        )

        if not fecha:
            continue

        clave_fecha = fecha.date().isoformat()
        fila = diario[clave_fecha]

        fila["busquedas"] += 1

        visitante_id = str(
            busqueda.get("visitante_id")
            or ""
        ).strip()

        sesion_id = str(
            busqueda.get("sesion_id")
            or ""
        ).strip()

        if visitante_id:
            fila["visitantes"].add(
                visitante_id
            )

        if sesion_id:
            fila["sesiones"].add(
                sesion_id
            )

        try:
            if int(
                busqueda.get(
                    "total_resultados"
                )
            ) == 0:
                fila["sin_resultados"] += 1

        except Exception:
            pass

    for evento in eventos:
        fecha = _parse_fecha(
            evento.get("created_at")
        )

        if not fecha:
            continue

        clave_fecha = fecha.date().isoformat()
        fila = diario[clave_fecha]

        visitante_id = str(
            evento.get("visitante_id")
            or ""
        ).strip()

        sesion_id = str(
            evento.get("sesion_id")
            or ""
        ).strip()

        if visitante_id:
            fila["visitantes"].add(
                visitante_id
            )

        if sesion_id:
            fila["sesiones"].add(
                sesion_id
            )

        tipo = str(
            evento.get("tipo_evento")
            or ""
        ).strip().lower()

        if tipo == "visita_publicacion":
            fila[
                "visitas_publicaciones"
            ] += 1

        elif tipo == "visita_comercio":
            fila[
                "visitas_comercios"
            ] += 1

        elif tipo == "click_whatsapp":
            fila["whatsapp"] += 1

        elif tipo == "vista_historia":
            fila["historias"] += 1

    evolucion = []

    for fecha in sorted(diario):
        fila = diario[fecha]

        evolucion.append({
            "fecha": fecha,
            "visitantes": len(
                fila["visitantes"]
            ),
            "sesiones": len(
                fila["sesiones"]
            ),
            "busquedas": fila["busquedas"],
            "sin_resultados": (
                fila["sin_resultados"]
            ),
            "visitas_publicaciones": (
                fila[
                    "visitas_publicaciones"
                ]
            ),
            "visitas_comercios": (
                fila["visitas_comercios"]
            ),
            "whatsapp": fila["whatsapp"],
            "historias": fila["historias"],
        })

    fechas_eventos = [
        _parse_fecha(
            registro.get("created_at")
        )
        for registro in eventos + busquedas
    ]

    fechas_eventos = [
        fecha
        for fecha in fechas_eventos
        if fecha
    ]

    fecha_inicial = (
        min(fechas_eventos)
        if fechas_eventos
        else None
    )

    fecha_final = (
        max(fechas_eventos)
        if fechas_eventos
        else None
    )

    calidad = {
        "registros_identificados": (
            registros_identificados
        ),
        "registros_sin_identificar": (
            registros_sin_identificar
        ),
        "primera_fecha_identificada": (
            formatear_fecha_argentina(
                primera_fecha_identificada
                .isoformat()
            )
            if primera_fecha_identificada
            else "-"
        ),
        "fecha_inicial": (
            formatear_fecha_argentina(
                fecha_inicial.isoformat()
            )
            if fecha_inicial
            else "-"
        ),
        "fecha_final": (
            formatear_fecha_argentina(
                fecha_final.isoformat()
            )
            if fecha_final
            else "-"
        ),
    }

    resumen_comercial = {
        "total_comercios": len(comercios),
        "total_publicaciones": len(
            publicaciones
        ),
        "total_listas": len(listas),
        "publicaciones_con_precio": sum(
            1
            for publicacion in publicaciones
            if _valor_presente(
                publicacion.get("precio")
            )
        ),
        "publicaciones_con_descripcion": sum(
            1
            for publicacion in publicaciones
            if _valor_presente(
                publicacion.get(
                    "descripcion"
                )
            )
        ),
    }

    return render_template(
        "admin_analytics.html",
        periodo_label=periodo_label,
        dias_raw=dias_raw,
        stats=stats,
        rankings=rankings,
        evolucion=evolucion,
        errores=errores,
        advertencias=advertencias,
        calidad=calidad,
        resumen_comercial=resumen_comercial,
        admin_user=session.get("admin_user"),
    )

if __name__ == "__main__":
    app.run(debug=True)
