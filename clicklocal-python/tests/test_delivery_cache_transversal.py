from types import SimpleNamespace

import pytest

import app as app_module
from delivery_cache import (
    buscar_distancia_cache,
    construir_identidad_cache,
    guardar_distancia_cache,
)
from pedidos_comercio import routes
from pedidos_comercio.delivery import DeliveryError


COMERCIO_ID = "11111111-1111-4111-8111-111111111111"


class ConsultaCacheFalsa:
    def __init__(self, db):
        self.db = db
        self.filtros = {}
        self.datos_upsert = None
        self.on_conflict = None

    def select(self, *_args):
        return self

    def eq(self, campo, valor):
        self.filtros[campo] = valor
        return self

    def limit(self, *_args):
        return self

    def upsert(self, datos, on_conflict=None):
        self.datos_upsert = dict(datos)
        self.on_conflict = on_conflict
        return self

    def execute(self):
        if self.datos_upsert is None:
            if self.db.falla_lectura:
                raise RuntimeError("falló la lectura de caché")
            clave = (
                self.filtros.get("comercio_id"),
                self.filtros.get("direccion_normalizada"),
                self.filtros.get("origen_clave"),
            )
            distancia = self.db.distancias.get(clave)
            return SimpleNamespace(
                data=[] if distancia is None else [{"distancia_m": distancia}]
            )
        if self.db.falla_escritura:
            raise RuntimeError("falló la escritura de caché")
        assert self.on_conflict == (
            "comercio_id,direccion_normalizada,origen_clave"
        )
        clave = (
            self.datos_upsert["comercio_id"],
            self.datos_upsert["direccion_normalizada"],
            self.datos_upsert["origen_clave"],
        )
        self.db.distancias[clave] = self.datos_upsert["distancia_m"]
        self.db.upserts.append(self.datos_upsert)
        return SimpleNamespace(data=[self.datos_upsert])


class CacheFalsa:
    def __init__(self, falla_lectura=False, falla_escritura=False):
        self.distancias = {}
        self.upserts = []
        self.falla_lectura = falla_lectura
        self.falla_escritura = falla_escritura

    def table(self, nombre):
        assert nombre == "delivery_direcciones_cache"
        return ConsultaCacheFalsa(self)


def configuracion(**cambios):
    datos = {
        "acepta_delivery": True,
        "delivery_modo": "distancia",
        "delivery_origen_direccion": "Origen 123, Paraná",
        "delivery_ciudad": "Paraná",
        "delivery_origen_latitud": -31.731234,
        "delivery_origen_longitud": -60.523456,
        "delivery_franjas": [
            {"hasta_km": 5, "precio": 2500},
            {"hasta_km": 10, "precio": 4000},
        ],
    }
    datos.update(cambios)
    return datos


def cotizar(monkeypatch, db, config=None, direccion="Perú", numero="50", distancia=3200):
    config = config or configuracion()
    monkeypatch.setattr(routes, "supabase_admin", db)
    monkeypatch.setattr(routes, "modulo_activo", lambda *_args: True)
    monkeypatch.setattr(routes, "_configuracion", lambda *_args, **_kwargs: config)
    externo = SimpleNamespace(llamadas=[])

    def calcular(*args):
        externo.llamadas.append(args)
        return distancia

    monkeypatch.setattr(routes, "consultar_distancia_osm", calcular)
    respuesta = app_module.app.test_client().post(
        f"/comercio/{COMERCIO_ID}/carrito/cotizar-envio",
        json={
            "whatsapp": "3436123456",
            "direccion": direccion,
            "numero": numero,
        },
    )
    return respuesta, externo


def clave(config, direccion="peru 50, parana"):
    origen = routes.clave_cache_delivery(
        routes.clave_origen_coordenadas(
            config["delivery_origen_latitud"],
            config["delivery_origen_longitud"],
        )
    )
    return (COMERCIO_ID, direccion, origen)


def test_helper_upsert_idempotente_guarda_solo_distancia_e_identidad():
    db = CacheFalsa()
    identidad = construir_identidad_cache(COMERCIO_ID, "peru 50", "altura-v1|coord:1,2")

    assert guardar_distancia_cache(db, identidad, 3200) is True
    assert buscar_distancia_cache(db, identidad) == 3200
    assert set(db.upserts[0]) == {
        "comercio_id", "direccion_normalizada", "origen_clave",
        "distancia_m", "updated_at",
    }


def test_cache_hit_no_llama_servicios_y_usa_tarifa_actual(monkeypatch):
    db = CacheFalsa()
    config = configuracion()
    db.distancias[clave(config)] = 3200

    respuesta, externo = cotizar(monkeypatch, db, config)

    assert respuesta.status_code == 200
    assert respuesta.get_json()["distancia_m"] == 3200
    assert respuesta.get_json()["costo_envio"] == 2500
    assert externo.llamadas == []
    assert db.upserts == []

    config_nueva = configuracion(
        delivery_franjas=[{"hasta_km": 5, "precio": 3100}]
    )
    respuesta, externo = cotizar(monkeypatch, db, config_nueva)
    assert respuesta.get_json()["costo_envio"] == 3100
    assert externo.llamadas == []


def test_cache_miss_calcula_guarda_y_usa_tarifa_actual(monkeypatch):
    db = CacheFalsa()
    respuesta, externo = cotizar(monkeypatch, db)

    assert respuesta.status_code == 200
    assert respuesta.get_json()["costo_envio"] == 2500
    assert len(externo.llamadas) == 1
    assert db.distancias[clave(configuracion())] == 3200
    assert "costo_envio" not in db.upserts[0]


@pytest.mark.parametrize("cambio", ["origen", "direccion"])
def test_cambio_de_identidad_produce_miss(monkeypatch, cambio):
    db = CacheFalsa()
    config = configuracion()
    db.distancias[clave(config)] = 3200
    if cambio == "origen":
        config = configuracion(delivery_origen_latitud=-31.700001)
        direccion, numero = "Perú", "50"
    else:
        direccion, numero = "Perú", "51"

    respuesta, externo = cotizar(
        monkeypatch, db, config, direccion=direccion, numero=numero, distancia=4100
    )

    assert respuesta.status_code == 200
    assert respuesta.get_json()["distancia_m"] == 4100
    assert len(externo.llamadas) == 1


def test_fallo_al_leer_cache_hace_fallback_externo(monkeypatch):
    db = CacheFalsa(falla_lectura=True)
    respuesta, externo = cotizar(monkeypatch, db)

    assert respuesta.status_code == 200
    assert respuesta.get_json()["distancia_m"] == 3200
    assert len(externo.llamadas) == 1


def test_fallo_al_guardar_cache_no_bloquea_cotizacion(monkeypatch):
    db = CacheFalsa(falla_escritura=True)
    respuesta, externo = cotizar(monkeypatch, db)

    assert respuesta.status_code == 200
    assert respuesta.get_json()["costo_envio"] == 2500
    assert len(externo.llamadas) == 1


def test_token_es_valido_con_mismo_origen_e_invalido_si_cambia(monkeypatch):
    db = CacheFalsa()
    config = configuracion()
    respuesta, _ = cotizar(monkeypatch, db, config)
    token = respuesta.get_json()["cotizacion_token"]

    with app_module.app.app_context():
        costo, distancia = routes._calcular_envio(
            config, COMERCIO_ID, "delivery", "Perú 50",
            "5493436123456", token,
        )
        assert float(costo) == 2500
        assert distancia == 3200
        with pytest.raises(DeliveryError):
            routes._calcular_envio(
                configuracion(delivery_origen_latitud=-31.700001),
                COMERCIO_ID, "delivery", "Perú 50",
                "5493436123456", token,
            )


def test_token_legacy_sigue_valido_durante_su_ventana_actual():
    config = configuracion()
    with app_module.app.app_context():
        token = routes.firmar_cotizacion(
            COMERCIO_ID, "5493436123456", "Perú 50", 3200, 2500,
            config["delivery_origen_direccion"],
        )
        costo, distancia = routes._calcular_envio(
            config, COMERCIO_ID, "delivery", "Perú 50",
            "5493436123456", token,
        )

    assert float(costo) == 2500
    assert distancia == 3200
