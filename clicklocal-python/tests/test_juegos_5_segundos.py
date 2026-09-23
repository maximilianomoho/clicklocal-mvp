from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app import app
from juegos import services
from test_juegos_reflejos import FakeDB


ROOT = Path(__file__).resolve().parents[1]


def juego():
    return {
        "id": "cinco-id", "slug": "5-segundos",
        "ranking_direction": "lower", "score_unit": "ms",
        "score_min": 0, "score_max": 10000,
    }


def intento_para(inicio):
    return {
        "intento_expires_at": (
            inicio + timedelta(seconds=services.NONCE_TTL_SECONDS)
        ).isoformat(),
    }


def test_pagina_medicion_sonido_layout_mobile_y_compartir():
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=({"id": "j1", "alias": None}, None)),
        patch("juegos.routes.obtener_juego_5_segundos", return_value=juego()),
        patch("juegos.routes.mejor_marca", return_value=None),
        patch("juegos.routes.contar_partidas_validas", return_value=24),
        patch("juegos.routes._registrar_evento_clickjuegos"),
    ):
        respuesta = app.test_client().get("/jugar/5-segundos")
    html = respuesta.get_data(as_text=True)
    js = (ROOT / "static/juegos/5_segundos.js").read_text(encoding="utf-8")
    css = (ROOT / "static/juegos/juegos.css").read_text(encoding="utf-8")
    template = (ROOT / "templates/juegos/5_segundos.html").read_text(encoding="utf-8")
    assert respuesta.status_code == 200
    assert "24 partidas jugadas" in html
    assert template.index('class="cinco-info"') < template.index('class="cinco-juego-columna"')
    assert "padding-inline: 6px" in css and "width: min(100%, 340px)" in css
    assert "min-height: clamp(290px, 38svh, 340px)" in css
    assert js.index("startedAt = performance.now()") < js.index("ClickJuegos.audio.playReady()")
    assert "playSuccess()" in js and "playRecord()" in js
    assert "juego_slug='5-segundos'" in template


@pytest.mark.parametrize(("elapsed", "score", "diferencia"), [
    (5180, 180, 180),
    (4920, 80, -80),
    (5000, 0, 0),
])
def test_calculo_error_absoluto_antes_despues_y_perfecto(elapsed, score, diferencia):
    inicio = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
    resultado = services.validar_tiempo_5_segundos(
        elapsed, intento_para(inicio), juego(),
        ahora=inicio + timedelta(milliseconds=elapsed + 100),
    )
    assert resultado == {"elapsed_ms": elapsed, "difference_ms": diferencia, "score": score}


def test_finalizacion_valida_guarda_metadata_y_record_cero():
    db = FakeDB(); db.datos["jugadores"] = [{"id": "j1"}]
    inicio = db.ahora
    resultado = services.registrar_partida_5_segundos(
        {"id": "j1"}, juego(), 5000, intento_para(inicio), db=db,
        ahora=inicio + timedelta(milliseconds=5100),
    )
    assert resultado["score"] == 0
    assert resultado["personal_best"] == 0
    assert resultado["is_new_record"] is True
    assert db.datos["partidas"][0]["metadata"]["elapsed_ms"] == 5000


def test_rango_invalido_y_consistencia_obvia_se_rechazan():
    inicio = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
    with pytest.raises(services.JuegoError, match="entre 0 y 10000"):
        services.validar_tiempo_5_segundos(
            16000, intento_para(inicio), juego(),
            ahora=inicio + timedelta(milliseconds=16100),
        )
    with pytest.raises(services.JuegoError, match="no coincide"):
        services.validar_tiempo_5_segundos(
            5000, intento_para(inicio), juego(),
            ahora=inicio + timedelta(milliseconds=1000),
        )


def test_intento_es_de_un_solo_uso():
    db = FakeDB(); db.datos["jugadores"] = [{"id": "j1"}]
    nuevo = services.iniciar_intento("j1", "cinco-id", ahora=db.ahora, db=db)
    services.consumir_intento("j1", "cinco-id", nuevo, ahora=db.ahora + timedelta(seconds=5), db=db)
    with pytest.raises(services.JuegoError):
        services.consumir_intento("j1", "cinco-id", nuevo, ahora=db.ahora + timedelta(seconds=5), db=db)


def test_ranking_lower_y_record_personal_conservan_error_menor():
    db = FakeDB(); db.datos["jugadores"] = [
        {"id": "j1", "alias": "Uno", "activo": True},
        {"id": "j2", "alias": "Dos", "activo": True},
    ]
    db.datos["partidas"] = [
        {"id": "p1", "jugador_id": "j1", "juego_id": "cinco-id", "score": 200, "valida": True, "created_at": db.ahora.isoformat()},
        {"id": "p2", "jugador_id": "j2", "juego_id": "cinco-id", "score": 80, "valida": True, "created_at": db.ahora.isoformat()},
    ]
    ranking = services.obtener_ranking("cinco-id", "historico", jugador_id="j1", db=db, ranking_direction="lower")
    assert [fila["score"] for fila in ranking["entries"]] == [80, 200]
    resultado = services.registrar_partida_5_segundos(
        {"id": "j1"}, juego(), 5300, intento_para(db.ahora), db=db,
        ahora=db.ahora + timedelta(milliseconds=5400),
    )
    assert resultado["personal_best"] == 200 and resultado["is_new_record"] is False


def test_rutas_emiten_analytics_y_devuelven_resultado():
    jugador = {"id": "j1", "alias": "Crono"}
    resultado = {"score": 180, "elapsed_ms": 5180, "difference_ms": 180, "personal_best": 180, "is_new_record": True}
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=(jugador, None)),
        patch("juegos.routes.obtener_juego_5_segundos", return_value=juego()),
        patch("juegos.routes.mejor_marca", return_value=None),
        patch("juegos.routes.contar_partidas_validas", return_value=0),
        patch("juegos.routes.iniciar_intento", return_value="n" * 43),
        patch("juegos.routes.consumir_intento", return_value=intento_para(datetime.now(timezone.utc))),
        patch("juegos.routes.registrar_partida_5_segundos", return_value=resultado),
        patch("juegos.routes.obtener_ranking", return_value={"entries": [], "player_position": 1}),
        patch("juegos.routes._registrar_evento_clickjuegos") as analytics,
    ):
        cliente = app.test_client()
        assert cliente.get("/jugar/5-segundos").status_code == 200
        assert cliente.post("/jugar/api/5-segundos/intentos", json={}).status_code == 200
        fin = cliente.post("/jugar/api/5-segundos/partidas", json={"nonce": "n" * 43, "elapsed_ms": 5180})
        assert fin.status_code == 200 and fin.get_json()["score"] == 180
        assert cliente.get("/jugar/api/5-segundos/ranking?periodo=mes").status_code == 200
    assert [c.args[0] for c in analytics.call_args_list] == [
        "juego_abierto", "partida_iniciada", "partida_completada", "nuevo_record", "ranking_visto",
    ]
    assert all(c.kwargs.get("juego") == "5-segundos" for c in analytics.call_args_list)


def test_compartir_whatsapp_registra_analytics():
    with patch("juegos.routes._registrar_evento_clickjuegos") as analytics:
        respuesta = app.test_client().get("/jugar/compartir/5-segundos")
    assert respuesta.status_code == 302
    assert respuesta.location.startswith("https://wa.me/?text=")
    assert "5-segundos" in respuesta.location
    assert analytics.call_args.args[0] == "compartir_juego"
    assert analytics.call_args.kwargs["juego"] == "5-segundos"
    assert analytics.call_args.kwargs["destino"] == "whatsapp"


def test_sql_es_idempotente_y_solo_registra_el_juego():
    sql = (ROOT / "sql/juegos_5_segundos_v1.sql").read_text(encoding="utf-8").lower()
    assert "on conflict (slug) do update" in sql
    assert "'5-segundos'" in sql and "'lower'" in sql and "'ms'" in sql
    assert "alter table" not in sql
    assert sql.strip().endswith("commit;")
