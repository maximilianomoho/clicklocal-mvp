from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import app
from juegos import services


ROOT = Path(__file__).resolve().parents[1]


class Query:
    def __init__(self, db, tabla):
        self.db, self.tabla = db, tabla
        self.filtros, self.ordenes, self.limite = [], [], None
        self.accion, self.payload = "select", None

    def select(self, *_): self.accion = "select"; return self
    def insert(self, payload): self.accion, self.payload = "insert", payload; return self
    def update(self, payload): self.accion, self.payload = "update", payload; return self
    def eq(self, campo, valor): self.filtros.append(("eq", campo, valor)); return self
    def gt(self, campo, valor): self.filtros.append(("gt", campo, valor)); return self
    def gte(self, campo, valor): self.filtros.append(("gte", campo, valor)); return self
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
            for fila in candidatas:
                fila.update(self.payload)
            return SimpleNamespace(data=[dict(fila) for fila in candidatas])
        for campo, desc in reversed(self.ordenes):
            candidatas.sort(key=lambda fila: fila.get(campo), reverse=desc)
        if self.limite is not None:
            candidatas = candidatas[:self.limite]
        return SimpleNamespace(data=[dict(fila) for fila in candidatas])

    def _coincide(self, fila):
        for op, campo, valor in self.filtros:
            actual = fila.get(campo)
            if op == "eq" and actual != valor: return False
            if op == "in" and str(actual) not in {str(v) for v in valor}: return False
            if op == "gt" and str(actual) <= str(valor): return False
            if op == "gte" and str(actual) < str(valor): return False
            if op == "lt" and str(actual) >= str(valor): return False
        return True


class FakeDB:
    def __init__(self):
        self.ahora = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
        self.datos = {"juegos": [], "jugadores": [], "partidas": []}

    def table(self, tabla):
        return Query(self, tabla)


def juego():
    return {
        "id": "reflejos-id", "slug": "reflejos", "ranking_direction": "lower",
        "score_unit": "ms", "score_min": 80, "score_max": 3000,
    }


def test_pagina_reflejos_responde_y_usa_performance_now():
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=({"id": "j1", "alias": None}, None)),
        patch("juegos.routes.obtener_juego_reflejos", return_value=juego()),
        patch("juegos.routes.mejor_marca", return_value=None),
        patch("juegos.routes.contar_partidas_validas", return_value=0),
        patch("juegos.routes._registrar_evento_clickjuegos"),
    ):
        respuesta = app.test_client().get("/jugar/reflejos")
    assert respuesta.status_code == 200
    assert "¿Qué tan rápidos son tus reflejos?" in respuesta.get_data(as_text=True)
    js = (ROOT / "static/juegos/reflejos.js").read_text(encoding="utf-8")
    assert "performance.now()" in js


def test_intento_valido_genera_espera_segura_y_score_entero_del_cliente():
    db = FakeDB()
    db.datos["jugadores"] = [{"id": "j1"}]
    inicio = db.ahora
    intento_nuevo = services.iniciar_intento_reflejos("j1", "reflejos-id", ahora=inicio, db=db)
    assert 1500 <= intento_nuevo["delay_ms"] <= 4500
    final = intento_nuevo["ready_at"] + timedelta(milliseconds=325)
    intento = services.consumir_intento(
        "j1", "reflejos-id", intento_nuevo["nonce"], ahora=final,
        db=db, devolver_detalles=True,
    )
    resultado = services.registrar_partida_reflejos(
        {"id": "j1"}, juego(), 287.49, intento, db=db, ahora=final,
    )
    assert resultado["score"] == 287
    assert db.datos["partidas"][0]["score"] == 287


def test_toque_anticipado_se_rechaza_y_consume_el_intento():
    db = FakeDB(); db.datos["jugadores"] = [{"id": "j1"}]
    nuevo = services.iniciar_intento_reflejos("j1", "reflejos-id", ahora=db.ahora, db=db)
    temprano = db.ahora + timedelta(milliseconds=500)
    intento = services.consumir_intento(
        "j1", "reflejos-id", nuevo["nonce"], ahora=temprano,
        db=db, devolver_detalles=True,
    )
    with pytest.raises(services.JuegoError, match="adelantaste"):
        services.validar_reaccion_reflejos(100, intento, juego(), ahora=temprano)
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j1", "reflejos-id", nuevo["nonce"], ahora=temprano, db=db)


def test_intento_reutilizado_y_vencido_se_rechazan():
    db = FakeDB(); db.datos["jugadores"] = [{"id": "j1"}]
    nuevo = services.iniciar_intento_reflejos("j1", "reflejos-id", ahora=db.ahora, db=db)
    valido = nuevo["ready_at"] + timedelta(milliseconds=200)
    services.consumir_intento("j1", "reflejos-id", nuevo["nonce"], ahora=valido, db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j1", "reflejos-id", nuevo["nonce"], ahora=valido, db=db)

    otro = services.iniciar_intento_reflejos("j1", "reflejos-id", ahora=db.ahora, db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento(
            "j1", "reflejos-id", otro["nonce"],
            ahora=db.ahora + timedelta(seconds=121), db=db,
        )


@pytest.mark.parametrize("score", [79.99, 3000.01, float("nan"), float("inf"), True])
def test_score_fuera_de_rango_o_no_finito_se_rechaza(score):
    intento = {"intento_ready_at": FakeDB().ahora.isoformat()}
    with pytest.raises(services.JuegoError):
        services.validar_reaccion_reflejos(
            score, intento, juego(), ahora=FakeDB().ahora + timedelta(milliseconds=500),
        )


def test_consistencia_temporal_rechaza_incompatibilidades_obvias():
    ahora = FakeDB().ahora
    intento = {"intento_ready_at": ahora.isoformat()}
    with pytest.raises(services.JuegoError, match="no coincide"):
        services.validar_reaccion_reflejos(
            900, intento, juego(), ahora=ahora + timedelta(milliseconds=100),
        )
    with pytest.raises(services.JuegoError, match="no coincide"):
        services.validar_reaccion_reflejos(
            100, intento, juego(), ahora=ahora + timedelta(seconds=8),
        )


def test_ranking_lower_elige_una_mejor_marca_y_posicion_fuera_del_top_20():
    db = FakeDB()
    db.datos["jugadores"] = [
        {"id": f"j{i}", "alias": f"Jugador {i}", "activo": True}
        for i in range(22)
    ]
    for i in range(22):
        db.datos["partidas"].append({
            "id": f"p{i}", "jugador_id": f"j{i}", "juego_id": "g",
            "score": 100 + i, "valida": True,
            "created_at": f"2026-09-{15 + (i % 5):02d}T10:00:00+00:00",
        })
    db.datos["partidas"].append({
        "id": "peor", "jugador_id": "j0", "juego_id": "g", "score": 900,
        "valida": True, "created_at": "2026-09-20T10:00:00+00:00",
    })
    ranking = services.obtener_ranking(
        "g", "historico", jugador_id="j21", db=db, ranking_direction="lower",
    )
    assert len(ranking["entries"]) == 20
    assert ranking["entries"][0]["score"] == 100
    assert ranking["player_position"] == 22


def test_record_personal_lower_conserva_la_marca_mas_baja():
    db = FakeDB(); db.datos["jugadores"] = [{"id": "j1"}]
    db.datos["partidas"] = [{
        "id": "p0", "jugador_id": "j1", "juego_id": "reflejos-id",
        "score": 250, "valida": True, "created_at": "2026-09-20T10:00:00+00:00",
    }]
    intento = {"intento_ready_at": db.ahora.isoformat()}
    resultado = services.registrar_partida_reflejos(
        {"id": "j1"}, juego(), 310, intento, db=db,
        ahora=db.ahora + timedelta(milliseconds=350),
    )
    assert resultado["personal_best"] == 250
    assert resultado["is_new_record"] is False


def test_rutas_reflejos_emiten_analytics_requerido():
    jugador = {"id": "j1", "alias": "Rayo"}
    intento = {"nonce": "n" * 43, "delay_ms": 2000}
    detalle = {"intento_ready_at": datetime.now(timezone.utc).isoformat()}
    resultado = {"score": 287, "personal_best": 287, "is_new_record": True}
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=(jugador, None)),
        patch("juegos.routes.obtener_juego_reflejos", return_value=juego()),
        patch("juegos.routes.mejor_marca", return_value=None),
        patch("juegos.routes.contar_partidas_validas", return_value=0),
        patch("juegos.routes.iniciar_intento_reflejos", return_value=intento),
        patch("juegos.routes.consumir_intento", return_value=detalle),
        patch("juegos.routes.registrar_partida_reflejos", return_value=resultado),
        patch("juegos.routes.obtener_ranking", return_value={"entries": [], "player_position": 1}),
        patch("juegos.routes._registrar_evento_clickjuegos") as analytics,
    ):
        cliente = app.test_client()
        assert cliente.get("/jugar/reflejos").status_code == 200
        assert cliente.post("/jugar/api/reflejos/intentos", json={}).status_code == 200
        assert cliente.post("/jugar/api/reflejos/partidas", json={"nonce": "n" * 43, "reaction_ms": 287}).status_code == 200
        assert cliente.get("/jugar/api/reflejos/ranking?periodo=semana").status_code == 200
    eventos = [llamada.args[0] for llamada in analytics.call_args_list]
    assert eventos == ["juego_abierto", "partida_iniciada", "partida_completada", "nuevo_record", "ranking_visto"]
    assert all(
        llamada.kwargs.get("juego") == "reflejos"
        for llamada in analytics.call_args_list
    )


def test_portada_activa_reflejos_y_mantiene_proximos_restantes():
    template = (ROOT / "templates/juegos/index.html").read_text(encoding="utf-8")
    assert "url_for('juegos.reflejos')" in template
    assert template.count("Próximamente") == 1
    assert "5 segundos" in template and "Memoria" in template


def test_sql_reflejos_es_manual_idempotente_y_no_cambia_esquema_base():
    sql = (ROOT / "sql/juegos_reflejos_v1.sql").read_text(encoding="utf-8").lower()
    assert "add column if not exists intento_ready_at timestamptz" in sql
    assert "on conflict (slug) do update" in sql
    assert "'reflejos'" in sql and "'lower'" in sql and "'ms'" in sql
    assert "80" in sql and "3000" in sql
    assert sql.strip().endswith("commit;")
