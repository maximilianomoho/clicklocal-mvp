from datetime import datetime, timedelta, timezone
import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import app
from juegos import services
from juegos import routes as juegos_routes


@pytest.fixture(autouse=True)
def analytics_mock():
    with (
        patch("juegos.routes._registrar_evento_clickjuegos") as registro,
        patch("juegos.routes.obtener_resumen_juegos", return_value={}),
        patch("juegos.routes.contar_partidas_validas", return_value=0),
    ):
        yield registro


class Query:
    def __init__(self, db, tabla):
        self.db, self.tabla = db, tabla
        self.filtros, self.ordenes, self.limite = [], [], None
        self.accion, self.payload = "select", None

    def select(self, *_): self.accion = "select"; return self
    def insert(self, payload): self.accion, self.payload = "insert", payload; return self
    def update(self, payload): self.accion, self.payload = "update", payload; return self
    def eq(self, campo, valor): self.filtros.append(("eq", campo, valor)); return self
    def neq(self, campo, valor): self.filtros.append(("neq", campo, valor)); return self
    def gte(self, campo, valor): self.filtros.append(("gte", campo, valor)); return self
    def gt(self, campo, valor): self.filtros.append(("gt", campo, valor)); return self
    def lt(self, campo, valor): self.filtros.append(("lt", campo, valor)); return self
    def in_(self, campo, valores): self.filtros.append(("in", campo, valores)); return self
    def order(self, campo, desc=False): self.ordenes.append((campo, desc)); return self
    def limit(self, valor): self.limite = valor; return self

    def execute(self):
        filas = self.db.datos[self.tabla]
        if self.accion == "insert":
            fila = dict(self.payload)
            fila.setdefault("id", f"{self.tabla}-{len(filas) + 1}")
            fila.setdefault("created_at", self.db.ahora.isoformat())
            filas.append(fila)
            return SimpleNamespace(data=[dict(fila)])
        candidatas = [fila for fila in filas if self._coincide(fila)]
        if self.accion == "update":
            for fila in candidatas: fila.update(self.payload)
            return SimpleNamespace(data=[dict(fila) for fila in candidatas])
        for campo, desc in reversed(self.ordenes):
            candidatas.sort(key=lambda fila: fila.get(campo), reverse=desc)
        if self.limite is not None: candidatas = candidatas[:self.limite]
        return SimpleNamespace(data=[dict(fila) for fila in candidatas])

    def _coincide(self, fila):
        for op, campo, valor in self.filtros:
            actual = fila.get(campo)
            if op == "eq" and actual != valor: return False
            if op == "neq" and actual == valor: return False
            if op == "in" and str(actual) not in {str(v) for v in valor}: return False
            if op == "gte" and str(actual) < str(valor): return False
            if op == "gt" and str(actual) <= str(valor): return False
            if op == "lt" and str(actual) >= str(valor): return False
        return True


class FakeDB:
    def __init__(self):
        self.ahora = datetime(2026, 9, 20, 15, tzinfo=timezone.utc)
        self.datos = {"juegos": [], "jugadores": [], "partidas": [], "desafios": []}
    def table(self, tabla): return Query(self, tabla)


def circulo(puntos=120, rx=0.32, ry=0.32, cierre=True):
    cantidad = puntos if cierre else puntos + 1
    tope = puntos if cierre else int(puntos * 0.72)
    return [[0.5 + rx * math.cos(2 * math.pi * i / (cantidad - 1)),
             0.5 + ry * math.sin(2 * math.pi * i / (cantidad - 1))]
            for i in range(tope)]


def test_identidad_nueva_guarda_solo_hash_y_token_no_esta_en_base():
    db = FakeDB()
    jugador, token = services.resolver_o_crear_jugador(db=db)
    assert jugador["id"]
    assert token not in str(db.datos["jugadores"])
    assert len(db.datos["jugadores"][0]["browser_token_hash"]) == 64


def test_identidad_persistente_resuelve_mismo_jugador():
    db = FakeDB()
    primero, token = services.resolver_o_crear_jugador(db=db)
    segundo, token_nuevo = services.resolver_o_crear_jugador(token, db=db)
    assert segundo["id"] == primero["id"]
    assert token_nuevo is None
    assert len(db.datos["jugadores"]) == 1


def test_cookie_invalida_crea_jugador_nuevo():
    db = FakeDB()
    services.resolver_o_crear_jugador(db=db)
    segundo, token = services.resolver_o_crear_jugador("invalida", db=db)
    assert token and segundo["id"] != db.datos["jugadores"][0]["id"]


def test_score_circulo_perfecto_supera_deformado():
    perfecto = services.calcular_score_circulo({"points": circulo()})["score"]
    deformado = services.calcular_score_circulo({"points": circulo(rx=0.32, ry=0.20)})["score"]
    assert perfecto > 94
    assert perfecto > deformado + 8


@pytest.mark.parametrize("points", [
    [[i / 30, 0.5] for i in range(24)],
    circulo(puntos=10),
    circulo(cierre=False),
])
def test_trazos_linea_pocos_puntos_y_abierto_no_logran_score_alto(points):
    try:
        score = services.calcular_score_circulo({"points": points})["score"]
        assert score < 55
    except services.JuegoError:
        pass


@pytest.mark.parametrize("payload", [None, {}, {"points": "x"}, {"points": [[float("nan"), 0]] * 24}, {"points": [[2, 0]] * 24}])
def test_payload_invalido_es_rechazado(payload):
    with pytest.raises(services.JuegoError): services.calcular_score_circulo(payload)


def test_nonce_valido_se_consume_y_no_puede_reutilizarse():
    db = FakeDB()
    db.datos["jugadores"] = [{"id": "j1"}]
    nonce = services.iniciar_intento("j1", "g1", db=db)
    assert nonce not in str(db.datos["jugadores"])
    assert services.consumir_intento("j1", "g1", nonce, db=db)
    assert db.datos["jugadores"][0]["intento_token_hash"] is None
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j1", "g1", nonce, db=db)


def test_nonce_vencido_es_rechazado():
    db = FakeDB()
    db.datos["jugadores"] = [{"id": "j2"}]
    ahora = datetime.now(timezone.utc)
    nonce = services.iniciar_intento("j2", "g1", ahora=ahora, db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento(
            "j2", "g1", nonce,
            ahora=ahora + timedelta(seconds=121), db=db,
        )


def test_nonce_incorrecto_no_consume_el_intento_valido():
    db = FakeDB()
    db.datos["jugadores"] = [{"id": "j3"}]
    nonce = services.iniciar_intento("j3", "g1", db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j3", "g1", "x" * 43, db=db)
    assert services.consumir_intento("j3", "g1", nonce, db=db)


def test_intento_nuevo_reemplaza_al_anterior():
    db = FakeDB()
    db.datos["jugadores"] = [{"id": "j4"}]
    anterior = services.iniciar_intento("j4", "g1", db=db)
    nuevo = services.iniciar_intento("j4", "g1", db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j4", "g1", anterior, db=db)
    assert services.consumir_intento("j4", "g1", nuevo, db=db)


def test_dos_consumos_no_pueden_ser_validos_y_no_dependen_de_memoria_local():
    db = FakeDB()
    db.datos["jugadores"] = [{"id": "j5"}]
    nonce = services.iniciar_intento("j5", "g1", db=db)
    assert not hasattr(services, "_intentos")
    assert services.consumir_intento("j5", "g1", nonce, db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j5", "g1", nonce, db=db)


def test_partida_recalcula_score_ignora_score_cliente_y_actualiza_record():
    db = FakeDB(); jugador = {"id": "j1"}; juego = {"id": "g1"}
    db.datos["partidas"].append({"id":"p0","jugador_id":"j1","juego_id":"g1","score":80,
                                  "valida":True,"created_at":"2026-09-19T10:00:00+00:00"})
    resultado = services.registrar_partida(jugador, juego, {"points": circulo(), "score": 1}, db=db, ahora=db.ahora)
    assert resultado["score"] > 94
    assert resultado["score"] == db.datos["partidas"][-1]["score"]
    assert resultado["is_new_record"] is True
    assert resultado["personal_best"] == resultado["score"]


def test_record_anterior_se_conserva_si_es_mejor():
    db = FakeDB(); jugador = {"id":"j1"}; juego = {"id":"g1"}
    db.datos["partidas"].append({"id":"p0","jugador_id":"j1","juego_id":"g1","score":99,
                                  "valida":True,"created_at":"2026-09-19T10:00:00+00:00"})
    resultado = services.registrar_partida(jugador, juego, {"points": circulo(rx=.32, ry=.2)}, db=db, ahora=db.ahora)
    assert resultado["personal_best"] == 99
    assert resultado["is_new_record"] is False


def test_alias_se_normaliza_y_alias_duplicado_se_rechaza():
    db = FakeDB(); db.datos["jugadores"] = [
        {"id":"j1","alias":None,"alias_normalizado":None,"activo":True},
        {"id":"j2","alias":"Máxi","alias_normalizado":"maxi","activo":True},
    ]
    assert services.guardar_alias("j1", " Laura ", db=db)["alias"] == "Laura"
    with pytest.raises(services.JuegoError) as error: services.guardar_alias("j1", "MAXI", db=db)
    assert error.value.status == 409


def _db_ranking():
    db = FakeDB(); db.datos["jugadores"] = [
        {"id":"a","alias":"Ana","activo":True}, {"id":"b","alias":"Beto","activo":True},
        {"id":"c","alias":None,"activo":True}, {"id":"d","alias":"Fuera","activo":False},
    ]
    db.datos["partidas"] = [
        {"id":"1","jugador_id":"a","juego_id":"g","score":90,"valida":True,"created_at":"2026-09-15T10:00:00+00:00"},
        {"id":"2","jugador_id":"a","juego_id":"g","score":95,"valida":True,"created_at":"2026-09-16T10:00:00+00:00"},
        {"id":"3","jugador_id":"b","juego_id":"g","score":95,"valida":True,"created_at":"2026-09-15T09:00:00+00:00"},
        {"id":"4","jugador_id":"c","juego_id":"g","score":99,"valida":True,"created_at":"2026-09-15T08:00:00+00:00"},
        {"id":"5","jugador_id":"d","juego_id":"g","score":100,"valida":True,"created_at":"2026-09-15T07:00:00+00:00"},
        {"id":"6","jugador_id":"a","juego_id":"g","score":100,"valida":True,"created_at":"2026-08-01T07:00:00+00:00"},
    ]; return db


@pytest.mark.parametrize("periodo", ["semana", "mes", "historico"])
def test_rankings_periodos_una_entrada_excluyen_sin_alias_e_inactivos(periodo):
    ranking = services.obtener_ranking("g", periodo, jugador_id="a", db=_db_ranking(),
                                        ahora=datetime(2026,9,20,15,tzinfo=timezone.utc))
    ids_visibles = [e["alias"] for e in ranking["entries"]]
    assert len(ids_visibles) == len(set(ids_visibles))
    assert "Fuera" not in ids_visibles
    assert all(alias in {"Ana", "Beto"} for alias in ids_visibles)
    assert ranking["player_position"] is not None


def test_desempate_favorece_quien_logro_la_marca_primero():
    ranking = services.obtener_ranking("g", "semana", db=_db_ranking(),
                                        ahora=datetime(2026,9,20,15,tzinfo=timezone.utc))
    assert [e["alias"] for e in ranking["entries"]][:2] == ["Beto", "Ana"]


def test_rate_limit_rechaza_ocho_partidas_en_ultimo_minuto():
    db = FakeDB()
    for i in range(8):
        db.datos["partidas"].append({"id":str(i),"jugador_id":"spam","created_at":db.ahora.isoformat()})
    with pytest.raises(services.JuegoError) as error:
        services.verificar_rate_limit("spam", ahora=db.ahora, db=db)
    assert error.value.status == 429


def test_ruta_circulo_responde_200_y_regresion_jugar():
    jugador = {"id":"j1","alias":None}; juego = {"id":"g1"}
    with patch("juegos.routes.resolver_o_crear_jugador", return_value=(jugador, "token-nuevo")), \
         patch("juegos.routes.obtener_juego_circulo", return_value=juego), \
         patch("juegos.routes.mejor_marca", return_value=None):
        cliente = app.test_client()
        assert cliente.get("/jugar/circulo").status_code == 200
        assert cliente.get("/jugar").status_code == 200


def test_cookie_jugador_es_httponly_lax_y_persistente():
    with patch("juegos.routes.resolver_o_crear_jugador", return_value=({"id":"j1","alias":None}, "a" * 48)):
        cabecera = app.test_client().get("/jugar").headers["Set-Cookie"]
    assert "HttpOnly" in cabecera and "SameSite=Lax" in cabecera and "Max-Age=31536000" in cabecera


def test_post_intento_exige_json_y_valida_origin_referer():
    jugador, juego = {"id": "j-origin"}, {"id": "g-origin"}
    parches = (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=(jugador, None)),
        patch("juegos.routes.obtener_juego_circulo", return_value=juego),
        patch("juegos.routes.iniciar_intento", return_value="n" * 43),
    )
    with parches[0], parches[1], parches[2]:
        cliente = app.test_client()
        assert cliente.post("/jugar/api/circulo/intentos").status_code == 415
        externo = cliente.post(
            "/jugar/api/circulo/intentos", json={},
            headers={"Origin": "https://externo.test"},
        )
        assert externo.status_code == 403
        referer_externo = cliente.post(
            "/jugar/api/circulo/intentos", json={},
            headers={"Referer": "https://externo.test/pagina"},
        )
        assert referer_externo.status_code == 403
        mismo_origen = cliente.post(
            "/jugar/api/circulo/intentos", json={},
            headers={"Origin": "http://localhost"},
        )
        assert mismo_origen.status_code == 200


def test_origin_https_detras_de_proxy_render_es_aceptado():
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=({"id":"j"}, None)),
        patch("juegos.routes.obtener_juego_circulo", return_value={"id":"g"}),
        patch("juegos.routes.iniciar_intento", return_value="n" * 43),
    ):
        respuesta = app.test_client().post(
            "/jugar/api/circulo/intentos",
            json={},
            headers={
                "Host": "clicklocal.example",
                "Origin": "https://clicklocal.example",
                "X-Forwarded-Proto": "https",
            },
        )
    assert respuesta.status_code == 200


@pytest.mark.parametrize("ruta,payload", [
    ("/jugar/api/circulo/partidas", {"nonce": "n" * 43, "points": []}),
    ("/jugar/api/jugador/alias", {"alias": "Nombre"}),
])
def test_origin_externo_se_rechaza_en_los_demás_post(ruta, payload):
    respuesta = app.test_client().post(
        ruta, json=payload, headers={"Origin": "https://externo.test"},
    )
    assert respuesta.status_code == 403


def test_flujo_emite_todos_los_eventos_clickjuegos(analytics_mock):
    jugador = {"id": "j-analytics", "alias": None}
    juego = {"id": "g-analytics"}
    resultado = {
        "score": 98.5,
        "personal_best": 98.5,
        "is_new_record": True,
    }
    ranking = {"entries": [], "player_position": None}
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=(jugador, None)),
        patch("juegos.routes.obtener_juego_circulo", return_value=juego),
        patch("juegos.routes.mejor_marca", return_value=None),
        patch("juegos.routes.iniciar_intento", return_value="n" * 43),
        patch("juegos.routes.consumir_intento"),
        patch("juegos.routes.registrar_partida", return_value=resultado),
        patch("juegos.routes.obtener_ranking", return_value=ranking),
        patch("juegos.routes.guardar_alias", return_value={"alias": "Jugador"}),
    ):
        cliente = app.test_client()
        assert cliente.get("/jugar").status_code == 200
        assert cliente.get("/jugar/circulo").status_code == 200
        assert cliente.post("/jugar/api/circulo/intentos", json={}).status_code == 200
        assert cliente.post(
            "/jugar/api/circulo/partidas",
            json={"nonce": "n" * 43, "points": circulo()},
        ).status_code == 200
        assert cliente.post(
            "/jugar/api/jugador/alias", json={"alias": "Jugador"},
        ).status_code == 200
        assert cliente.get(
            "/jugar/api/circulo/ranking?periodo=semana",
        ).status_code == 200
        assert cliente.get("/jugar/salir/galeria").status_code == 302
        assert cliente.get("/jugar/salir/gastronomia").status_code == 302

    eventos = {llamada.args[0] for llamada in analytics_mock.call_args_list}
    assert eventos == {
        "visita_clickjuegos", "juego_abierto", "partida_iniciada",
        "partida_completada", "nuevo_record", "alias_creado",
        "ranking_visto", "click_galeria", "click_gastronomia",
    }


def test_metadata_analytics_sanea_referrer_y_conserva_utm():
    with app.test_request_context(
        "/jugar?utm_source=Instagram&utm_medium=social&utm_campaign=Lanzamiento",
        headers={"Referer": "https://instagram.com/perfil?dato=privado"},
    ):
        metadata = juegos_routes._metadata_analytics(
            "visita_clickjuegos", jugador_id="jugador-1",
        )
    assert metadata["evento"] == "visita_clickjuegos"
    assert metadata["referrer"] == "https://instagram.com"
    assert metadata["utm_source"] == "Instagram"
    assert metadata["utm_medium"] == "social"
    assert metadata["utm_campaign"] == "Lanzamiento"
    assert metadata["jugador_id"] == "jugador-1"
