import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from flask import Flask

from gastronomia import gastronomia_bp
from gastronomia.services.delivery import (
    DeliveryError,
    clave_cache_delivery,
    clave_origen_coordenadas,
    completar_direccion_destino,
    consultar_distancia_osm,
    consultar_distancia_osrm,
    extraer_altura_direccion,
    firmar_cotizacion,
    geocodificar_direccion_nominatim,
    normalizar_altura,
    normalizar_direccion,
    seleccionar_franja,
    validar_configuracion_delivery,
    validar_coordenadas,
    validar_cotizacion,
)


class RespuestaHTTPFalsa:
    def __init__(self, datos):
        self.contenido = json.dumps(datos).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.contenido


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.datos = list(db.datos.get(tabla, []))
        self.datos_upsert = None
        self.datos_update = None
        self.conflicto = None

    def select(self, *args):
        return self

    def eq(self, columna, valor):
        self.datos = [fila for fila in self.datos if fila.get(columna) == valor]
        return self

    def limit(self, *args):
        return self

    def upsert(self, datos, on_conflict=None):
        self.datos_upsert = datos
        self.conflicto = on_conflict
        return self

    def update(self, datos):
        self.datos_update = datos
        return self

    def execute(self):
        if self.datos_update is not None:
            self.db.updates.append((self.tabla, self.datos_update))
            for fila in self.datos:
                fila.update(self.datos_update)
            return SimpleNamespace(data=self.datos)
        if self.datos_upsert is not None:
            self.db.upserts.append((self.tabla, self.datos_upsert, self.conflicto))
            filas = self.db.datos.setdefault(self.tabla, [])
            claves = (self.conflicto or "").split(",")
            existente = next((
                fila for fila in filas
                if all(fila.get(clave) == self.datos_upsert.get(clave) for clave in claves)
            ), None)
            if existente is None:
                filas.append(dict(self.datos_upsert))
            else:
                existente.update(self.datos_upsert)
            return SimpleNamespace(data=[self.datos_upsert])
        return SimpleNamespace(data=self.datos)


class SupabaseFalso:
    def __init__(self, datos):
        self.datos = datos
        self.upserts = []
        self.updates = []

    def table(self, nombre):
        return ConsultaFalsa(self, nombre)


class DeliveryDistanciaTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "secreto-de-prueba"
        self.contexto = self.app.app_context()
        self.contexto.push()
        self.franjas = [
            {"hasta_km": 2, "precio": 1500},
            {"hasta_km": 4, "precio": 2500},
            {"hasta_km": 6, "precio": 3500},
        ]

    def tearDown(self):
        self.contexto.pop()

    def crear_db_cotizacion(self, comercio_id="comercio-1", origen=None, cache=None, franjas=None):
        origen = origen or "San Martín 1234, Paraná, Entre Ríos"
        return SupabaseFalso({
            "gastronomia_configuracion": [{
                "comercio_id": comercio_id,
                "activo": True,
                "acepta_delivery": True,
                "delivery_distancia_activo": True,
                "delivery_origen_direccion": origen,
                "delivery_origen_latitud": -31.731234,
                "delivery_origen_longitud": -60.523456,
                "delivery_franjas": franjas or self.franjas,
            }],
            "comercios": [{"id": comercio_id, "ciudad": "Paraná"}],
            "gastronomia_cliente_direcciones": cache or [],
        })

    def cotizar(self, db, comercio_id="comercio-1", telefono="343 6123456", direccion="Perú 50", distancia_osrm=3200):
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")
        with (
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.consultar_distancia_osm",
                return_value=distancia_osrm,
            ) as osm,
        ):
            respuesta = app.test_client().post(
                f"/gastronomia/comercio/{comercio_id}/delivery/cotizar",
                json={
                    "telefono_cliente": telefono,
                    "direccion_entrega": direccion,
                },
            )
        return respuesta, osm

    def geocodificar_respuesta(self, direccion, candidatos, ciudad="Paraná"):
        respuesta = RespuestaHTTPFalsa(candidatos)
        with patch(
            "gastronomia.services.delivery.urllib_request.urlopen",
            return_value=respuesta,
        ):
            return geocodificar_direccion_nominatim(direccion, ciudad)

    @staticmethod
    def candidato_altura(
        numero="150",
        ciudad="Paraná",
        pais="ar",
        **campos,
    ):
        candidato = {
            "lat": "-31.725",
            "lon": "-60.524",
            "category": "place",
            "type": "house",
            "addresstype": "place",
            "address": {
                "house_number": numero,
                "road": "Nogoyá",
                "city": ciudad,
                "country_code": pais,
            },
        }
        candidato.update(campos)
        return candidato

    def test_configuracion_activa_valida(self):
        resultado = validar_configuracion_delivery(
            True,
            "San Martín 1234, Paraná, Entre Ríos",
            ["2", "4", "6"],
            ["1500", "2500", "3500"],
        )
        self.assertEqual(resultado, self.franjas)

    def test_configuracion_apagada_no_exige_franjas(self):
        self.assertEqual(validar_configuracion_delivery(False, "", [], []), [])

    def test_rechaza_franjas_no_crecientes_duplicadas_o_mas_de_cinco(self):
        casos = (
            (["2", "2"], ["1", "2"]),
            (["4", "2"], ["1", "2"]),
            (["1", "2", "3", "4", "5", "6"], ["0"] * 6),
        )
        for limites, precios in casos:
            with self.subTest(limites=limites):
                with self.assertRaises(ValueError):
                    validar_configuracion_delivery(True, "Origen completo", limites, precios)

    def test_rechaza_valores_no_finitos_y_precio_negativo(self):
        for limite, precio in (("nan", "1"), ("inf", "1"), ("2", "-1")):
            with self.subTest(limite=limite, precio=precio):
                with self.assertRaises(ValueError):
                    validar_configuracion_delivery(True, "Origen completo", [limite], [precio])

    def test_franja_en_limite_y_entre_limites(self):
        self.assertEqual(seleccionar_franja(self.franjas, 2000)["precio"], 1500)
        self.assertEqual(seleccionar_franja(self.franjas, 3200)["precio"], 2500)
        self.assertIsNone(seleccionar_franja(self.franjas, 6001))

    def test_completa_contexto_desde_origen(self):
        self.assertEqual(
            completar_direccion_destino(
                "Perú 50",
                "San Martín 1234, Paraná, Entre Ríos",
                "Paraná",
            ),
            "Perú 50, Paraná, Entre Ríos",
        )

    def test_normalizacion_conservadora_de_direccion(self):
        self.assertEqual(
            normalizar_direccion("  PERÚ  50 ,  Paraná. "),
            "peru 50, parana",
        )
        self.assertNotEqual(
            normalizar_direccion("Perú 50, piso 2"),
            normalizar_direccion("Perú 50, piso 3"),
        )

    def test_nominatim_usa_busqueda_estructurada_y_contexto_del_comercio(self):
        respuesta = RespuestaHTTPFalsa([{
            "lat": "-31.72",
            "lon": "-60.53",
            "category": "place",
            "type": "house",
            "addresstype": "place",
            "address": {
                "house_number": "50",
                "city": "Paraná",
                "country_code": "ar",
            },
        }])
        with patch("gastronomia.services.delivery.urllib_request.urlopen", return_value=respuesta) as abrir:
            coordenadas = geocodificar_direccion_nominatim(
                "Perú 50",
                "Paraná",
                base_url="https://nominatim.test",
                user_agent="ClickLocal-Test/1.0",
            )
        self.assertEqual(coordenadas, (-31.72, -60.53))
        self.assertEqual(abrir.call_count, 1)
        peticion = abrir.call_args.args[0]
        self.assertIn("/search?", peticion.full_url)
        parametros = parse_qs(urlparse(peticion.full_url).query)
        self.assertEqual(parametros["street"], ["Perú 50"])
        self.assertEqual(parametros["city"], ["Paraná"])
        self.assertEqual(parametros["country"], ["Argentina"])
        self.assertEqual(parametros["countrycodes"], ["ar"])
        self.assertEqual(parametros["format"], ["jsonv2"])
        self.assertEqual(parametros["addressdetails"], ["1"])
        self.assertEqual(parametros["limit"], ["5"])
        self.assertNotIn("q", parametros)
        self.assertEqual(peticion.headers["User-agent"], "ClickLocal-Test/1.0")

    def test_altura_exacta_y_equivalencias_conservadoras(self):
        candidato = self.candidato_altura(numero="150")
        self.assertEqual(
            self.geocodificar_respuesta("Nogoyá 150", [candidato]),
            (-31.725, -60.524),
        )
        for direccion, devuelta in (
            ("Nogoyá 00150", "150"),
            ("Nogoyá 150 A", "00150a"),
            ("Nogoyá 150 bis", "150 BIS"),
            ("Nogoyá 150/1", "00150 / 01"),
        ):
            with self.subTest(direccion=direccion, devuelta=devuelta):
                resultado = self.geocodificar_respuesta(
                    direccion,
                    [self.candidato_altura(numero=devuelta)],
                )
                self.assertEqual(resultado, (-31.725, -60.524))

    def test_normalizacion_y_extraccion_de_altura(self):
        self.assertEqual(normalizar_altura(" 00150 A "), "150a")
        self.assertEqual(normalizar_altura("00150 / 01"), "150/1")
        self.assertEqual(extraer_altura_direccion("25 de Mayo 00150"), "150")
        self.assertIsNone(normalizar_altura("150-152"))
        self.assertIsNone(extraer_altura_direccion("San Martín 150-152"))
        self.assertIsNone(extraer_altura_direccion("San Martín sin número"))

    def test_rechaza_sin_house_number_road_ciudad_o_pais_incorrectos(self):
        sin_numero = self.candidato_altura()
        sin_numero["address"].pop("house_number")
        calle = self.candidato_altura(addresstype="road")
        highway = self.candidato_altura(category="highway")
        casos = (
            sin_numero,
            calle,
            highway,
            self.candidato_altura(ciudad="Crespo"),
            self.candidato_altura(pais="uy"),
        )
        for candidato in casos:
            with self.subTest(candidato=candidato):
                with self.assertRaisesRegex(
                    DeliveryError,
                    "No pudimos ubicar ese número",
                ):
                    self.geocodificar_respuesta("Nogoyá 150", [candidato])

    def test_interpolacion_y_amenity_con_numero_coincidente_se_aceptan(self):
        interpolado = self.candidato_altura(
            numero="150",
            osm_type="way",
            osm_id=261307459,
        )
        amenity = self.candidato_altura(
            numero="150",
            category="amenity",
            type="restaurant",
            addresstype="amenity",
        )
        for candidato in (interpolado, amenity):
            with self.subTest(candidato=candidato):
                self.assertEqual(
                    self.geocodificar_respuesta("Nogoyá 150", [candidato]),
                    (-31.725, -60.524),
                )

    def test_rechaza_interpolacion_distinta_numero_distinto_y_rango(self):
        casos = (
            ("Nogoyá 150", "152"),
            ("San Martín 1800", "180"),
            ("Nogoyá 151", "150-152"),
        )
        for direccion, devuelta in casos:
            with self.subTest(direccion=direccion, devuelta=devuelta):
                with self.assertRaisesRegex(
                    DeliveryError,
                    "No pudimos ubicar ese número",
                ):
                    self.geocodificar_respuesta(
                        direccion,
                        [self.candidato_altura(numero=devuelta, osm_type="way")],
                    )

    def test_varios_candidatos_elige_uno_posterior_con_altura_exacta(self):
        calle = self.candidato_altura(addresstype="road")
        calle["address"].pop("house_number")
        exacto = self.candidato_altura(numero="150", lat="-31.726", lon="-60.525")
        self.assertEqual(
            self.geocodificar_respuesta("Nogoyá 150", [calle, exacto]),
            (-31.726, -60.525),
        )

    def test_solo_calle_y_direccion_sin_altura_son_error(self):
        calle = self.candidato_altura(addresstype="road")
        calle["address"].pop("house_number")
        with self.assertRaisesRegex(DeliveryError, "No pudimos ubicar ese número"):
            self.geocodificar_respuesta("San Martín 1800", [calle])
        with self.assertRaisesRegex(DeliveryError, "No pudimos ubicar ese número"):
            geocodificar_direccion_nominatim("San Martín", "Paraná")

    def test_nominatim_revisa_candidatos_hasta_encontrar_la_ciudad(self):
        respuesta = RespuestaHTTPFalsa([
            {
                "lat": "-32.02",
                "lon": "-60.30",
                "address": {
                    "city": "Crespo",
                    "county": "Departamento Paraná",
                    "country_code": "ar",
                },
            },
            {
                "lat": "-31.72",
                "lon": "-60.53",
                "address": {
                    "municipality": "Municipio de Paraná",
                    "house_number": "1800",
                    "country_code": "ar",
                },
            },
        ])
        with patch(
            "gastronomia.services.delivery.urllib_request.urlopen",
            return_value=respuesta,
        ):
            coordenadas = geocodificar_direccion_nominatim(
                "San Martín 1800",
                "Paraná",
            )
        self.assertEqual(coordenadas, (-31.72, -60.53))

    def test_nominatim_no_acepta_county_estado_display_name_ni_otro_pais(self):
        respuesta = RespuestaHTTPFalsa([
            {
                "lat": "-32.02",
                "lon": "-60.30",
                "display_name": "Paraná, Argentina",
                "address": {
                    "city": "Crespo",
                    "county": "Departamento Paraná",
                    "state": "Entre Ríos",
                    "country_code": "ar",
                },
            },
            {
                "lat": "-31.72",
                "lon": "-60.53",
                "address": {
                    "city": "Paraná",
                    "country_code": "uy",
                },
            },
            {
                "lat": "-34.16",
                "lon": "-58.96",
                "display_name": "Parana, San Martín, Campana, Argentina",
                "address": {
                    "city": "Campana",
                    "municipality": "Municipio de Paraná",
                    "country_code": "ar",
                },
            },
        ])
        with patch(
            "gastronomia.services.delivery.urllib_request.urlopen",
            return_value=respuesta,
        ):
            with self.assertRaisesRegex(
                DeliveryError,
                "No pudimos ubicar ese número",
            ):
                geocodificar_direccion_nominatim(
                    "San Martín 800",
                    "Paraná",
                )

    def test_panel_guarda_ubicacion_geocodificada_sin_exponer_coordenadas(self):
        db = self.crear_db_cotizacion()
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-1", "ciudad": "Paraná"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.geocodificar_direccion_nominatim",
                return_value=(-31.7259377, -60.5243986),
            ) as geocodificar,
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/delivery/ubicacion",
                data={
                    "delivery_origen_direccion": (
                        "Nogoyá 150"
                    ),
                },
            )

        self.assertEqual(respuesta.status_code, 302)
        geocodificar.assert_called_once_with(
            "Nogoyá 150",
            "Paraná",
        )
        self.assertEqual(db.updates, [(
            "gastronomia_configuracion",
            {
                "delivery_origen_direccion": (
                    "Nogoyá 150"
                ),
                "delivery_origen_latitud": -31.7259377,
                "delivery_origen_longitud": -60.5243986,
            },
        )])

    def test_panel_no_guarda_ubicacion_si_nominatim_no_la_encuentra(self):
        db = self.crear_db_cotizacion()
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-1"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.geocodificar_direccion_nominatim",
                side_effect=DeliveryError("Dirección no encontrada", 422),
            ),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/delivery/ubicacion",
                data={"delivery_origen_direccion": "Dirección inexistente"},
            )

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("ubicacion_delivery_error=1", respuesta.location)
        self.assertEqual(db.updates, [])

    def test_panel_no_guarda_origen_sin_altura_interpretable(self):
        db = self.crear_db_cotizacion()
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")
        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-1", "ciudad": "Paraná"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/delivery/ubicacion",
                data={"delivery_origen_direccion": "Nogoyá"},
            )
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("ubicacion_delivery_error=1", respuesta.location)
        self.assertEqual(db.updates, [])

    def test_panel_guarda_origen_interpolado_con_numero_coincidente(self):
        db = self.crear_db_cotizacion()
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")
        respuesta_nominatim = RespuestaHTTPFalsa([self.candidato_altura(
            numero="150",
            osm_type="way",
            osm_id=261307459,
        )])
        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-1", "ciudad": "Paraná"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.services.delivery.urllib_request.urlopen",
                return_value=respuesta_nominatim,
            ),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/delivery/ubicacion",
                data={"delivery_origen_direccion": "Nogoyá 150"},
            )
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("ubicacion_delivery_ok=1", respuesta.location)
        self.assertEqual(len(db.updates), 1)
        self.assertEqual(
            db.updates[0][1]["delivery_origen_latitud"],
            -31.725,
        )

    def test_panel_muestra_mensaje_de_altura(self):
        from pathlib import Path

        plantilla = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "gastronomia"
            / "panel.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "No pudimos ubicar ese número. Revisá la calle y la altura.",
            plantilla,
        )

    def test_guardar_configuracion_preserva_ubicacion_geocodificada(self):
        db = self.crear_db_cotizacion()
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-1"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
        ):
            respuesta = app.test_client().post(
                "/gastronomia/panel/configuracion",
                data={
                    "acepta_delivery": "on",
                    "delivery_distancia_activo": "on",
                    "pedido_minimo": "",
                    "costo_envio": "0",
                    "tiempo_estimado_min": "30",
                    "descuento_efectivo_pct": "0",
                    "descuento_transferencia_pct": "0",
                    "delivery_hasta_km": ["2", "4", "6"],
                    "delivery_precio": ["1500", "2500", "3500"],
                },
            )

        self.assertEqual(respuesta.status_code, 302)
        tabla, datos = db.updates[-1]
        self.assertEqual(tabla, "gastronomia_configuracion")
        self.assertEqual(datos["delivery_franjas"], self.franjas)
        self.assertNotIn("delivery_origen_direccion", datos)
        self.assertNotIn("delivery_origen_latitud", datos)
        self.assertNotIn("delivery_origen_longitud", datos)

    def test_osrm_usa_driving_y_overview_false(self):
        respuesta = RespuestaHTTPFalsa({"code": "Ok", "routes": [{"distance": 3200.4}]})
        with patch("gastronomia.services.delivery.urllib_request.urlopen", return_value=respuesta) as abrir:
            distancia = consultar_distancia_osrm(
                -31.731234, -60.523456, -31.72, -60.53,
                base_url="https://osrm.test",
            )
        self.assertEqual(distancia, 3200)
        url = abrir.call_args.args[0].full_url
        self.assertIn("/route/v1/driving/", url)
        self.assertIn("?overview=false&alternatives=false&steps=false", url)

    def test_nominatim_y_osrm_sin_resultado_son_errores_controlados(self):
        respuesta = RespuestaHTTPFalsa([])
        with patch("gastronomia.services.delivery.urllib_request.urlopen", return_value=respuesta):
            with self.assertRaises(DeliveryError):
                geocodificar_direccion_nominatim(
                    "Dirección inexistente",
                    "Paraná",
                )
        respuesta = RespuestaHTTPFalsa({"code": "NoRoute", "routes": []})
        with patch("gastronomia.services.delivery.urllib_request.urlopen", return_value=respuesta):
            with self.assertRaises(DeliveryError):
                consultar_distancia_osrm(-31.7, -60.5, -31.8, -60.6)

    def test_osrm_no_se_llama_si_nominatim_no_valida_la_altura(self):
        with (
            patch(
                "gastronomia.services.delivery.geocodificar_direccion_nominatim",
                side_effect=DeliveryError(
                    "No pudimos ubicar ese número. Revisá la calle y la altura.",
                    422,
                ),
            ),
            patch(
                "gastronomia.services.delivery.consultar_distancia_osrm"
            ) as osrm,
        ):
            with self.assertRaises(DeliveryError):
                consultar_distancia_osm(
                    -31.731234,
                    -60.523456,
                    "San Martín 1800",
                    "Paraná",
                )
        osrm.assert_not_called()

    def test_coordenadas_y_clave_de_origen(self):
        self.assertEqual(validar_coordenadas("-31.7", "-60.5"), (-31.7, -60.5))
        self.assertEqual(
            clave_origen_coordenadas(-31.7312344, -60.5234564),
            "coord:-31.731234,-60.523456",
        )
        for coordenadas in ((None, None), (91, 0), (0, 181), (True, 1)):
            with self.assertRaises(DeliveryError):
                validar_coordenadas(*coordenadas)

    def test_token_valido_liga_comercio_direccion_distancia_y_costo(self):
        token = firmar_cotizacion(
            "comercio-1", "3436123456", "Perú 50", 3200, 2500,
            "coord:-31.731234,-60.523456",
        )
        self.assertEqual(
            validar_cotizacion(
                token, "comercio-1", "3436123456", "  PERÚ   50 ",
                "coord:-31.731234,-60.523456",
            ),
            {"distancia_m": 3200, "costo_envio": 2500.0},
        )
        with self.assertRaises(DeliveryError):
            validar_cotizacion(token, "comercio-2", "3436123456", "Perú 50", "coord:-31.731234,-60.523456")
        with self.assertRaises(DeliveryError):
            validar_cotizacion(token, "comercio-1", "3436123456", "Perú 51", "coord:-31.731234,-60.523456")
        with self.assertRaises(DeliveryError):
            validar_cotizacion(token + "alterado", "comercio-1", "3436123456", "Perú 50", "coord:-31.731234,-60.523456")
        with self.assertRaises(DeliveryError):
            validar_cotizacion(token, "comercio-1", "3430000000", "Perú 50", "coord:-31.731234,-60.523456")
        with self.assertRaises(DeliveryError):
            validar_cotizacion(token, "comercio-1", "3436123456", "Perú 50", "Otro origen 1, Paraná")

    def test_token_vencido_es_rechazado(self):
        token = firmar_cotizacion("comercio-1", "3436123456", "Perú 50", 3200, 2500, "Origen")
        with self.assertRaises(DeliveryError):
            validar_cotizacion(token, "comercio-1", "3436123456", "Perú 50", "Origen", max_age=-1)

    def test_endpoint_cotiza_sin_recibir_costo_del_navegador(self):
        db = self.crear_db_cotizacion()
        respuesta, osm = self.cotizar(db)
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.get_json()
        self.assertEqual(datos["distancia_m"], 3200)
        self.assertEqual(datos["costo_envio"], 2500)
        self.assertTrue(datos["cotizacion_token"])
        osm.assert_called_once_with(
            -31.731234,
            -60.523456,
            "Perú 50",
            "Paraná",
        )
        self.assertEqual(len(db.upserts), 1)
        cache = db.upserts[0][1]
        self.assertEqual(cache["telefono_normalizado"], "3436123456")
        self.assertEqual(cache["direccion_normalizada"], "peru 50")
        self.assertEqual(cache["distancia_m"], 3200)
        self.assertEqual(
            cache["origen_normalizado"],
            clave_cache_delivery("coord:-31.731234,-60.523456"),
        )
        self.assertNotIn("costo_envio", cache)

    def test_altura_invalida_no_crea_cache(self):
        db = self.crear_db_cotizacion()
        app = Flask(__name__)
        app.secret_key = "secreto-de-prueba"
        app.register_blueprint(gastronomia_bp, url_prefix="/gastronomia")
        with (
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.consultar_distancia_osm",
                side_effect=DeliveryError(
                    "No pudimos ubicar ese número. Revisá la calle y la altura.",
                    422,
                ),
            ) as osm,
        ):
            respuesta = app.test_client().post(
                "/gastronomia/comercio/comercio-1/delivery/cotizar",
                json={
                    "telefono_cliente": "343 6123456",
                    "direccion_entrega": "San Martín 1800",
                },
            )
        self.assertEqual(respuesta.status_code, 422)
        self.assertIn("ubicar ese número", respuesta.get_json()["error"])
        osm.assert_called_once()
        self.assertEqual(db.upserts, [])

    def test_sin_coordenadas_devuelve_error_y_no_intenta_osm(self):
        db = self.crear_db_cotizacion()
        configuracion = db.datos["gastronomia_configuracion"][0]
        configuracion["delivery_origen_latitud"] = None
        configuracion["delivery_origen_longitud"] = None
        respuesta, osm = self.cotizar(db)
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("configurar la ubicación", respuesta.get_json()["error"])
        osm.assert_not_called()

    def test_cache_valida_evita_servicios_externos_y_aplica_precio_actual(self):
        cache = [{
            "comercio_id": "comercio-1",
            "telefono_normalizado": "3436123456",
            "direccion_normalizada": "peru 50",
            "distancia_m": 3200,
            "origen_normalizado": clave_cache_delivery(
                "coord:-31.731234,-60.523456"
            ),
        }]
        franjas_actuales = [
            {"hasta_km": 2, "precio": 1700},
            {"hasta_km": 4, "precio": 2800},
        ]
        db = self.crear_db_cotizacion(cache=cache, franjas=franjas_actuales)
        respuesta, osm = self.cotizar(db, distancia_osrm=9999)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()["distancia_m"], 3200)
        self.assertEqual(respuesta.get_json()["costo_envio"], 2800)
        osm.assert_not_called()
        self.assertEqual(db.upserts, [])

    def test_cache_anterior_no_evade_la_validacion_nueva(self):
        cache = [{
            "comercio_id": "comercio-1",
            "telefono_normalizado": "3436123456",
            "direccion_normalizada": "peru 50",
            "distancia_m": 1000,
            "origen_normalizado": "coord:-31.731234,-60.523456",
        }]
        db = self.crear_db_cotizacion(cache=cache)
        respuesta, osm = self.cotizar(db, distancia_osrm=3200)
        self.assertEqual(respuesta.status_code, 200)
        osm.assert_called_once()
        self.assertEqual(respuesta.get_json()["distancia_m"], 3200)
        self.assertEqual(
            db.upserts[0][1]["origen_normalizado"],
            clave_cache_delivery("coord:-31.731234,-60.523456"),
        )

    def test_cambio_de_origen_invalida_cache_y_actualiza_distancia(self):
        cache = [{
            "comercio_id": "comercio-1",
            "telefono_normalizado": "3436123456",
            "direccion_normalizada": "peru 50",
            "distancia_m": 1000,
            "origen_normalizado": "origen anterior",
        }]
        db = self.crear_db_cotizacion(cache=cache)
        respuesta, osm = self.cotizar(db, distancia_osrm=3200)
        self.assertEqual(respuesta.status_code, 200)
        osm.assert_called_once()
        self.assertEqual(db.upserts[0][1]["distancia_m"], 3200)

    def test_otro_telefono_no_reutiliza_cache(self):
        cache = [{
            "comercio_id": "comercio-1",
            "telefono_normalizado": "3436123456",
            "direccion_normalizada": "peru 50",
            "distancia_m": 1000,
            "origen_normalizado": "coord:-31.731234,-60.523456",
        }]
        db = self.crear_db_cotizacion(cache=cache)
        respuesta, osm = self.cotizar(db, telefono="343 6999999")
        self.assertEqual(respuesta.status_code, 200)
        osm.assert_called_once()

    def test_otro_comercio_no_reutiliza_cache(self):
        cache = [{
            "comercio_id": "comercio-1",
            "telefono_normalizado": "3436123456",
            "direccion_normalizada": "peru 50",
            "distancia_m": 1000,
            "origen_normalizado": "coord:-31.731234,-60.523456",
        }]
        db = self.crear_db_cotizacion(comercio_id="comercio-2", cache=cache)
        respuesta, osm = self.cotizar(db, comercio_id="comercio-2")
        self.assertEqual(respuesta.status_code, 200)
        osm.assert_called_once()

    def test_gastronomia_no_conserva_google_routes(self):
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "gastronomia"
        contenido = "\n".join(
            archivo.read_text(encoding="utf-8")
            for archivo in raiz.rglob("*.py")
        )
        for texto in (
            "GOOGLE_MAPS_ROUTES_API_KEY",
            "consultar_distancia_routes",
            "routes.googleapis.com",
            "X-Goog-",
        ):
            self.assertNotIn(texto, contenido)


if __name__ == "__main__":
    unittest.main()
