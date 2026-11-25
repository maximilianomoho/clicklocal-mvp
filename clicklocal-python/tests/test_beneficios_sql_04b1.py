from pathlib import Path
import re


RAIZ = Path(__file__).resolve().parents[1]
MIGRACION = (RAIZ / "sql/beneficios_1a.sql").read_text(encoding="utf-8")
ROLLBACK = (RAIZ / "sql/beneficios_1a_ROLLBACK.sql").read_text(encoding="utf-8")


def test_limites_sql_son_exclusivos_en_fin_y_vencimiento():
    assert "ahora>=b.vigente_hasta" in MIGRACION
    assert "c.vence_en<=ahora" in MIGRACION
    assert "x.vence_en>ahora" in MIGRACION
    assert "utilizado_en<vence_en" in MIGRACION
    assert "new.emitido_en>=b.vigente_hasta" in MIGRACION


def test_emision_califica_columnas_que_chocan_con_variables_de_salida():
    assert "where x.beneficio_id=b.id" in MIGRACION
    assert "where beneficio_id=b.id" not in MIGRACION
    assert "where x.codigo_hash=p_codigo_hash" in MIGRACION


def test_conflicto_gastronomico_usa_el_mismo_bloqueo_en_ambas_direcciones():
    clave = "'beneficios:gastronomia_producto:'||new."
    assert MIGRACION.count("pg_advisory_xact_lock") == 2
    assert MIGRACION.count(clave) == 2
    assert MIGRACION.count("interval '1 microsecond'") == 2


def test_reintento_equivalente_de_carrito_no_duplica_evento_ni_consume():
    assert "beneficios_eventos_carrito_unique" in MIGRACION
    assert "on conflict (codigo_id,referencia_id)" in MIGRACION
    bloque = MIGRACION.split(
        "create function public.beneficios_validar_codigo_carrito", 1
    )[1].split("create function public.beneficios_consumir_codigo", 1)[0]
    assert "update public.beneficios_codigos" not in bloque


def test_rollback_exige_marca_y_no_usa_cascade():
    marca = "clicklocal:beneficios_1a:v2"
    assert MIGRACION.count(marca) >= 16
    assert marca in ROLLBACK
    assert "obj_description" in ROLLBACK
    assert re.search(r"\bdrop\b[^;]*\bcascade\b", ROLLBACK, re.IGNORECASE) is None
    assert ROLLBACK.lower().count(" restrict;") == 15
