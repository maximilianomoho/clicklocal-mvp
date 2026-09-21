from pathlib import Path
from unittest.mock import patch

from app import app


ROOT = Path(__file__).resolve().parents[1]


def test_blueprint_juegos_esta_registrado():
    assert "juegos" in app.blueprints
    assert app.blueprints["juegos"].url_prefix == "/jugar"


def test_portada_juegos_responde_y_renderiza_contenido_base():
    with (
        patch(
            "juegos.routes.resolver_o_crear_jugador",
            return_value=({"id": "jugador-test", "alias": None}, "token-test"),
        ),
        patch("juegos.routes.obtener_resumen_juegos", return_value={}),
        patch("juegos.routes._registrar_evento_clickjuegos"),
    ):
        respuesta = app.test_client().get("/jugar")

    assert respuesta.status_code == 200
    assert "ClickJuegos" in respuesta.get_data(as_text=True)
    assert "Círculo Perfecto" in respuesta.get_data(as_text=True)
    assert "Próximamente" in respuesta.get_data(as_text=True)


def test_rutas_publicas_principales_siguen_registradas():
    reglas = {regla.rule for regla in app.url_map.iter_rules()}

    assert "/" in reglas
    assert "/gastronomia/" in reglas
    assert "/turnos/agenda" in reglas
    assert "/jugar/" in reglas


def test_portada_publica_incluye_acceso_a_juegos():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'href="{{ url_for(\'juegos.inicio\') }}"' in template
    assert "Jugar" in template


def test_sql_juegos_define_tablas_constraints_indices_y_seguridad():
    sql = (ROOT / "sql" / "juegos_v1.sql").read_text(encoding="utf-8").lower()

    assert sql.strip().startswith("--")
    assert "begin;" in sql
    assert sql.strip().endswith("commit;")

    for tabla in ("juegos", "jugadores", "partidas", "desafios"):
        assert f"create table if not exists public.{tabla}" in sql
        assert f"alter table public.{tabla} enable row level security" in sql
        assert f"revoke all on table public.{tabla} from anon, authenticated" in sql

    assert "default gen_random_uuid()" in sql
    assert "references public.jugadores(id)" in sql
    assert "references public.juegos(id)" in sql
    assert "references public.partidas(id)" in sql
    assert "ranking_direction in ('higher', 'lower')" in sql
    assert "estado in ('abierto', 'completado', 'cancelado', 'expirado')" in sql
    assert "on conflict (slug) do update" in sql
    assert "'circulo'" in sql
    assert "'círculo perfecto'" in sql
