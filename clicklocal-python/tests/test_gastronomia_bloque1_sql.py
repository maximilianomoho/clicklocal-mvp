from pathlib import Path


MIGRACION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "gastronomia_bloque1_modelo_estados_pago.sql"
)


def test_migracion_contempla_columnas_y_updated_at():
    sql = MIGRACION.read_text(encoding="utf-8").lower()

    assert "add column if not exists estado_pago text" in sql
    assert "add column if not exists pagado_at timestamptz" in sql
    assert "add column if not exists cerrado_at timestamptz" in sql
    assert "new.updated_at = now()" in sql
    assert "before update" in sql


def test_migracion_retira_constraint_legacy_antes_del_backfill():
    sql = MIGRACION.read_text(encoding="utf-8").lower()

    quitar_constraint = sql.index(
        "drop constraint if exists gastronomia_pedidos_estado_check"
    )
    convertir_recibidos = sql.index("set estado = 'pendiente'")
    agregar_constraint = sql.index(
        "add constraint gastronomia_pedidos_estado_check"
    )

    assert quitar_constraint < convertir_recibidos < agregar_constraint


def test_migracion_conserva_numeracion_y_seguridad():
    sql = MIGRACION.read_text(encoding="utf-8").lower()

    assert "trg_asignar_numero_pedido_gastronomia" not in sql
    assert "gastronomia_pedidos_numero_por_comercio_unique" not in sql
    assert "row level security" not in sql
    assert "policy" not in sql
    assert " grant " not in sql
    assert " revoke " not in sql
