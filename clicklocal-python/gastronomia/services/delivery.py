import json
import math
import re
import time
import unicodedata
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config.delivery import NOMINATIM_BASE_URL, OSRM_BASE_URL, OSM_USER_AGENT

MENSAJE_DIRECCION_NO_ENCONTRADA = (
    "No pudimos ubicar esa dirección. Revisá la calle y el número."
)
MENSAJE_ALTURA_NO_ENCONTRADA = (
    "No pudimos ubicar ese número. Revisá la calle y la altura."
)
MENSAJE_SERVICIO_TEMPORAL = (
    "No pudimos calcular el envío en este momento. "
    "Intentá nuevamente en unos segundos."
)
CACHE_PRECISION_VERSION = "altura-v1"
MAX_FRANJAS_DELIVERY = 5
MAX_DIRECCION_DELIVERY = 300
COTIZACION_MAX_AGE_SECONDS = 10 * 60


class DeliveryError(Exception):
    def __init__(self, mensaje, status_code=400, codigo="delivery_error"):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_code = status_code
        self.codigo = codigo


def normalizar_direccion(valor):
    texto = unicodedata.normalize("NFKC", str(valor or ""))
    texto = re.sub(r"\s+", " ", texto.strip())
    texto = re.sub(r"\s*,\s*", ", ", texto)
    texto = re.sub(r",(?:\s*,)+", ",", texto).strip(" ,")
    texto = re.sub(r"(?<=\w)\.(?=\s|,|$)", "", texto)
    texto = "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caracter)
    )
    return re.sub(r"\s+", " ", texto).casefold()


def normalizar_altura(valor):
    texto = unicodedata.normalize("NFKC", str(valor or "")).strip().casefold()
    coincidencia = re.fullmatch(
        r"0*(\d+)(?:(?:\s*[-._]?\s*)(bis|[a-z])|\s*/\s*0*(\d+))?",
        texto,
    )
    if not coincidencia:
        return None
    numero = str(int(coincidencia.group(1)))
    if coincidencia.group(2):
        return numero + coincidencia.group(2)
    if coincidencia.group(3):
        return numero + "/" + str(int(coincidencia.group(3)))
    return numero


def extraer_altura_direccion(direccion):
    texto = unicodedata.normalize("NFKC", str(direccion or "")).strip().casefold()
    coincidencia = re.search(
        r"(?<![\d/-])(\d+(?:(?:\s*[-._]?\s*)(?:bis|[a-z])|\s*/\s*\d+)?)\s*$",
        texto,
    )
    return normalizar_altura(coincidencia.group(1)) if coincidencia else None


def direccion_calle_altura(direccion):
    """Separa contexto escrito después de una coma sin inventar domicilios."""
    direccion = re.sub(r"\s+", " ", str(direccion or "").strip())
    primera_parte = direccion.split(",", 1)[0].strip()
    return primera_parte if extraer_altura_direccion(primera_parte) else direccion


def clave_cache_delivery(origen_normalizado):
    return CACHE_PRECISION_VERSION + "|" + str(origen_normalizado or "")


def validar_configuracion_delivery(activo, direccion, limites, precios):
    if not activo:
        return []

    direccion = re.sub(r"\s+", " ", str(direccion or "").strip())
    if not direccion or len(direccion) > MAX_DIRECCION_DELIVERY:
        raise ValueError("Ingresá una dirección de salida válida.")

    if not 1 <= len(limites) <= MAX_FRANJAS_DELIVERY:
        raise ValueError("Configurá entre 1 y 5 franjas de delivery.")
    if len(limites) != len(precios):
        raise ValueError("Completá el límite y el precio de cada franja.")

    franjas = []
    limite_anterior = 0.0
    for limite_raw, precio_raw in zip(limites, precios):
        try:
            limite = float(str(limite_raw).strip().replace(",", "."))
            precio = float(str(precio_raw).strip().replace(",", "."))
        except (TypeError, ValueError) as exc:
            raise ValueError("Las franjas contienen valores inválidos.") from exc
        if not math.isfinite(limite) or limite <= 0:
            raise ValueError("Cada distancia debe ser mayor que cero.")
        if not math.isfinite(precio) or precio < 0:
            raise ValueError("Cada precio debe ser mayor o igual que cero.")
        if limite <= limite_anterior:
            raise ValueError("Las distancias deben ser estrictamente crecientes.")
        franjas.append({
            "hasta_km": round(limite, 3),
            "precio": round(precio, 2),
        })
        limite_anterior = limite
    return franjas


def validar_franjas_guardadas(valor):
    if (
        not isinstance(valor, list)
        or not valor
        or any(not isinstance(franja, dict) for franja in valor)
    ):
        raise DeliveryError("La configuración de delivery no es válida.", 500)
    return validar_configuracion_delivery(
        True,
        "dirección configurada",
        [franja.get("hasta_km") for franja in valor if isinstance(franja, dict)],
        [franja.get("precio") for franja in valor if isinstance(franja, dict)],
    )


def completar_direccion_destino(direccion, origen, ciudad=None):
    destino = re.sub(r"\s+", " ", str(direccion or "").strip())
    if not destino:
        raise DeliveryError("Ingresá la dirección de entrega.")

    destino_normalizado = destino.casefold()
    ciudad = re.sub(r"\s+", " ", str(ciudad or "").strip())
    partes_origen = [parte.strip() for parte in str(origen or "").split(",")]
    contexto = [parte for parte in partes_origen[1:] if parte]

    if ciudad and ciudad.casefold() not in destino_normalizado:
        if contexto:
            return destino + ", " + ", ".join(contexto)
        return destino + ", " + ciudad
    return destino


def validar_coordenadas(latitud, longitud):
    try:
        if isinstance(latitud, bool) or isinstance(longitud, bool):
            raise ValueError
        latitud = float(latitud)
        longitud = float(longitud)
    except (TypeError, ValueError) as exc:
        raise DeliveryError("La ubicación de salida del delivery no es válida.", 409) from exc
    if (
        not math.isfinite(latitud)
        or not math.isfinite(longitud)
        or not -90 <= latitud <= 90
        or not -180 <= longitud <= 180
    ):
        raise DeliveryError("La ubicación de salida del delivery no es válida.", 409)
    return latitud, longitud


def clave_origen_coordenadas(latitud, longitud):
    latitud, longitud = validar_coordenadas(latitud, longitud)
    return f"coord:{latitud:.6f},{longitud:.6f}"


def _leer_json(peticion, timeout, servicio):
    for intento in range(2):
        codigo = f"{servicio.casefold()}_respuesta_invalida"
        transitorio = False
        try:
            with urllib_request.urlopen(peticion, timeout=timeout) as respuesta:
                return json.loads(respuesta.read().decode("utf-8"))
        except urllib_error.HTTPError as error:
            transitorio = 500 <= int(error.code or 0) <= 599
            codigo = (
                f"{servicio.casefold()}_http_5xx"
                if transitorio
                else f"{servicio.casefold()}_http_4xx"
            )
        except urllib_error.URLError as error:
            razon = getattr(error, "reason", None)
            es_timeout = isinstance(razon, TimeoutError)
            codigo = (
                f"{servicio.casefold()}_timeout"
                if es_timeout
                else f"{servicio.casefold()}_red"
            )
            transitorio = True
        except TimeoutError:
            codigo = f"{servicio.casefold()}_timeout"
            transitorio = True
        except json.JSONDecodeError:
            codigo = f"{servicio.casefold()}_json_invalido"

        if transitorio and intento == 0:
            current_app.logger.warning(
                "Fallo transitorio consultando %s; reintento único (%s)",
                servicio,
                codigo,
            )
            time.sleep(1)
            continue

        current_app.logger.warning(
            "Error controlado consultando %s (%s)",
            servicio,
            codigo,
        )
        raise DeliveryError(MENSAJE_SERVICIO_TEMPORAL, 503, codigo)


def geocodificar_direccion_nominatim(
    direccion,
    ciudad,
    base_url=None,
    user_agent=None,
    timeout=8,
):
    direccion = direccion_calle_altura(direccion)
    ciudad = re.sub(r"\s+", " ", str(ciudad or "").strip())
    if not direccion or not ciudad:
        raise DeliveryError(
            MENSAJE_DIRECCION_NO_ENCONTRADA,
            422,
            "direccion_no_encontrada",
        )
    altura_solicitada = extraer_altura_direccion(direccion)
    if not altura_solicitada:
        raise DeliveryError(
            MENSAJE_ALTURA_NO_ENCONTRADA,
            422,
            "altura_no_encontrada",
        )
    url = (base_url or NOMINATIM_BASE_URL).rstrip("/") + "/search?" + urllib_parse.urlencode({
        "street": direccion,
        "city": ciudad,
        "country": "Argentina",
        "countrycodes": "ar",
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 5,
    })
    peticion = urllib_request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent or OSM_USER_AGENT,
        },
    )
    datos = _leer_json(peticion, timeout, "Nominatim")
    if not isinstance(datos, list) or not datos:
        raise DeliveryError(
            MENSAJE_DIRECCION_NO_ENCONTRADA,
            422,
            "direccion_no_encontrada",
        )

    ciudad_esperada = normalizar_direccion(ciudad)
    prefijos_administrativos = (
        "municipio de ",
        "municipalidad de ",
        "ciudad de ",
    )

    for candidato in datos:
        if not isinstance(candidato, dict):
            continue
        detalles = candidato.get("address")
        if not isinstance(detalles, dict):
            continue
        if str(detalles.get("country_code") or "").casefold() != "ar":
            continue

        localidades_principales = []
        for campo in ("city", "town", "village"):
            localidad = normalizar_direccion(detalles.get(campo))
            for prefijo in prefijos_administrativos:
                if localidad.startswith(prefijo):
                    localidad = localidad[len(prefijo):].strip()
                    break
            if localidad:
                localidades_principales.append(localidad)

        localidades = localidades_principales
        if not localidades:
            municipio = normalizar_direccion(
                detalles.get("municipality")
            )
            for prefijo in prefijos_administrativos:
                if municipio.startswith(prefijo):
                    municipio = municipio[len(prefijo):].strip()
                    break
            if municipio:
                localidades = [municipio]

        if ciudad_esperada not in localidades:
            continue

        if str(candidato.get("addresstype") or "").casefold() == "road":
            continue
        categoria = str(
            candidato.get("category") or candidato.get("class") or ""
        ).casefold()
        if categoria == "highway":
            continue
        altura_devuelta = normalizar_altura(detalles.get("house_number"))
        if not altura_devuelta or altura_devuelta != altura_solicitada:
            continue

        try:
            return validar_coordenadas(
                candidato["lat"],
                candidato["lon"],
            )
        except (KeyError, DeliveryError):
            continue

    raise DeliveryError(
        MENSAJE_ALTURA_NO_ENCONTRADA,
        422,
        "altura_no_encontrada",
    )


def consultar_distancia_osrm(
    origen_latitud,
    origen_longitud,
    destino_latitud,
    destino_longitud,
    base_url=None,
    timeout=8,
):
    origen_latitud, origen_longitud = validar_coordenadas(
        origen_latitud, origen_longitud
    )
    try:
        destino_latitud, destino_longitud = validar_coordenadas(
            destino_latitud, destino_longitud
        )
    except DeliveryError as exc:
        raise DeliveryError(
            MENSAJE_DIRECCION_NO_ENCONTRADA,
            422,
            "destino_invalido",
        ) from exc
    coordenadas = (
        f"{origen_longitud:.6f},{origen_latitud:.6f};"
        f"{destino_longitud:.6f},{destino_latitud:.6f}"
    )
    url = (
        (base_url or OSRM_BASE_URL).rstrip("/")
        + "/route/v1/driving/"
        + coordenadas
        + "?overview=false&alternatives=false&steps=false"
    )
    peticion = urllib_request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": OSM_USER_AGENT},
    )
    datos = _leer_json(peticion, timeout, "OSRM")
    rutas = datos.get("routes") if isinstance(datos, dict) else None
    distancia = (
        rutas[0].get("distance")
        if isinstance(datos, dict) and datos.get("code") == "Ok" and rutas
        else None
    )
    if isinstance(distancia, bool) or not isinstance(distancia, (int, float)):
        codigo_osrm = str(datos.get("code") or "") if isinstance(datos, dict) else ""
        raise DeliveryError(
            MENSAJE_DIRECCION_NO_ENCONTRADA,
            422,
            "osrm_no_route" if codigo_osrm == "NoRoute" else "osrm_sin_ruta",
        )
    distancia = int(round(distancia))
    if distancia < 0:
        raise DeliveryError(
            MENSAJE_DIRECCION_NO_ENCONTRADA,
            422,
            "osrm_distancia_invalida",
        )
    return distancia


def consultar_distancia_osm(
    origen_latitud,
    origen_longitud,
    direccion_destino,
    ciudad,
):
    destino_latitud, destino_longitud = geocodificar_direccion_nominatim(
        direccion_destino,
        ciudad,
    )
    return consultar_distancia_osrm(
        origen_latitud,
        origen_longitud,
        destino_latitud,
        destino_longitud,
    )


def seleccionar_franja(franjas, distancia_m):
    for franja in validar_franjas_guardadas(franjas):
        if distancia_m <= round(float(franja["hasta_km"]) * 1000):
            return franja
    return None


def _serializador():
    return URLSafeTimedSerializer(
        current_app.secret_key,
        salt="gastronomia-delivery-cotizacion-v1",
    )


def firmar_cotizacion(
    comercio_id,
    telefono_normalizado,
    direccion,
    distancia_m,
    costo_envio,
    origen,
):
    return _serializador().dumps({
        "comercio_id": str(comercio_id),
        "telefono_normalizado": str(telefono_normalizado),
        "direccion": normalizar_direccion(direccion),
        "distancia_m": int(distancia_m),
        "costo_envio": round(float(costo_envio), 2),
        "origen_normalizado": normalizar_direccion(origen),
    })


def validar_cotizacion(
    token,
    comercio_id,
    telefono_normalizado,
    direccion,
    origen,
    max_age=COTIZACION_MAX_AGE_SECONDS,
):
    try:
        datos = _serializador().loads(str(token or ""), max_age=max_age)
    except SignatureExpired as exc:
        raise DeliveryError("La cotización venció. Calculá el envío nuevamente.") from exc
    except BadSignature as exc:
        raise DeliveryError("La cotización no es válida. Calculá el envío nuevamente.") from exc

    if (
        str(datos.get("comercio_id")) != str(comercio_id)
        or datos.get("telefono_normalizado") != str(telefono_normalizado)
        or datos.get("direccion") != normalizar_direccion(direccion)
        or datos.get("origen_normalizado") != normalizar_direccion(origen)
    ):
        raise DeliveryError(
            "Los datos de entrega cambiaron. Calculá el envío nuevamente."
        )
    try:
        distancia = int(datos["distancia_m"])
        costo = round(float(datos["costo_envio"]), 2)
    except (KeyError, TypeError, ValueError) as exc:
        raise DeliveryError("La cotización no es válida. Calculá el envío nuevamente.") from exc
    if distancia < 0 or not math.isfinite(costo) or costo < 0:
        raise DeliveryError("La cotización no es válida. Calculá el envío nuevamente.")
    return {"distancia_m": distancia, "costo_envio": costo}
