from pathlib import Path
import re


RAIZ = Path(__file__).resolve().parents[1]
MIGRACION = (
    RAIZ / "sql/beneficios_2a_ofertas_multiples.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    RAIZ
    / "sql/beneficios_2a_ofertas_multiples_ROLLBACK.sql"
).read_text(encoding="utf-8")


def test_migracion_es_incremental_y_no_modifica_datos():
    assert "create table" not in MIGRACION.lower()
    assert re.search(r"\bdelete\b", MIGRACION, re.I) is None
    assert re.search(r"\bupdate\b", MIGRACION, re.I) is None
    assert re.search(r"\binsert\b", MIGRACION, re.I) is None


def test_elimina_solo_unicidad_activa_por_objetivo():
    assert (
        "drop index public.beneficios_publicacion_activa_unique"
        in MIGRACION
    )
    assert (
        "drop index public.beneficios_producto_activo_unique"
        in MIGRACION
    )
    assert (
        "drop index public.beneficios_serie_edicion_unique"
        not in MIGRACION
    )
    assert "beneficios_codigos_hash_unique" not in MIGRACION
    assert "beneficios_codigos_idempotencia_unique" not in MIGRACION


def test_oferta_general_no_duplica_objetivos_especificos():
    assert "tipo_objetivo = 'comercio'" in MIGRACION
    assert "publicacion_id is null" in MIGRACION
    assert "gastronomia_producto_id is null" in MIGRACION


def test_rollback_se_niega_a_perder_compatibilidad():
    assert (
        "existen ofertas generales de comercio"
        in ROLLBACK
    )
    assert (
        "varias ofertas activas por publicacion"
        in ROLLBACK
    )
    assert (
        "varias ofertas activas por producto"
        in ROLLBACK
    )
    assert re.search(r"\bdelete\b", ROLLBACK, re.I) is None
    assert re.search(r"\bcascade\b", ROLLBACK, re.I) is None
