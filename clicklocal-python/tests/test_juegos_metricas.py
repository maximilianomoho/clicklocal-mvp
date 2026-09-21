from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import app
from juegos import services


class CountQuery:
    def __init__(self, filas):
        self.filas = filas
        self.filtros = []

    def select(self, *_, **__): return self
    def eq(self, campo, valor): self.filtros.append((campo, valor)); return self

    def execute(self):
        filas = [
            fila for fila in self.filas
            if all(fila.get(campo) == valor for campo, valor in self.filtros)
        ]
        return SimpleNamespace(data=[], count=len(filas))


class CountDB:
    def __init__(self, partidas):
        self.partidas = partidas

    def table(self, tabla):
        assert tabla == "partidas"
        return CountQuery(self.partidas)


def test_contador_considera_solo_partidas_validas_del_juego():
    db = CountDB([
        {"juego_id": "circulo", "valida": True},
        {"juego_id": "circulo", "valida": True},
        {"juego_id": "circulo", "valida": False},
        {"juego_id": "reflejos", "valida": True},
    ])
    assert services.contar_partidas_validas("circulo", db=db) == 2
    assert services.contar_partidas_validas("reflejos", db=db) == 1


@pytest.mark.parametrize(("cantidad", "esperado"), [
    (0, "0 partidas jugadas"),
    (1, "1 partida jugada"),
    (24, "24 partidas jugadas"),
    (1284, "1.284 partidas jugadas"),
])
def test_formato_publico_singular_plural_y_miles(cantidad, esperado):
    assert services.formatear_partidas_jugadas(cantidad) == esperado


def test_portada_muestra_tres_juegos_equilibrados_con_contadores():
    resumen = {
        "circulo": {"partidas_texto": "1 partida jugada"},
        "reflejos": {"partidas_texto": "1.284 partidas jugadas"},
        "5-segundos": {"partidas_texto": "24 partidas jugadas"},
    }
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=({"id": "j1"}, None)),
        patch("juegos.routes.obtener_resumen_juegos", return_value=resumen),
        patch("juegos.routes._registrar_evento_clickjuegos"),
    ):
        html = app.test_client().get("/jugar").get_data(as_text=True)
    assert html.count("juego-card juego-card-disponible") == 3
    assert "1 partida jugada" in html
    assert "1.284 partidas jugadas" in html
    assert "24 partidas jugadas" in html
    assert html.count("Próximamente") == 1


@pytest.mark.parametrize(("ruta", "juego_getter"), [
    ("/jugar/circulo", "juegos.routes.obtener_juego_circulo"),
    ("/jugar/reflejos", "juegos.routes.obtener_juego_reflejos"),
    ("/jugar/5-segundos", "juegos.routes.obtener_juego_5_segundos"),
])
def test_cada_juego_muestra_su_contador(ruta, juego_getter):
    with (
        patch("juegos.routes.resolver_o_crear_jugador", return_value=({"id": "j1", "alias": "Jugador"}, None)),
        patch(juego_getter, return_value={"id": "g1"}),
        patch("juegos.routes.mejor_marca", return_value=None),
        patch("juegos.routes.contar_partidas_validas", return_value=24),
        patch("juegos.routes._registrar_evento_clickjuegos"),
    ):
        respuesta = app.test_client().get(ruta)
    assert respuesta.status_code == 200
    assert "24 partidas jugadas" in respuesta.get_data(as_text=True)
