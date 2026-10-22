from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
MIGRACION = RAIZ / "sql" / "delivery_direcciones_cache_20261004.sql"
ROLLBACK = RAIZ / "sql" / "delivery_direcciones_cache_20261004_ROLLBACK.sql"


def sql_normalizado(ruta):
    return " ".join(ruta.read_text(encoding="utf-8").lower().split())


def test_migracion_es_transaccional_y_define_esquema_minimo():
    sql = sql_normalizado(MIGRACION)

    assert "begin;" in sql and sql.endswith("commit;")
    assert "create table if not exists public.delivery_direcciones_cache" in sql
    assert "references public.comercios(id) on delete cascade" in sql
    assert "primary key ( comercio_id, direccion_normalizada, origen_clave )" in sql
    assert "check (btrim(direccion_normalizada) <> '')" in sql
    assert "check (btrim(origen_clave) <> '')" in sql
    assert "check (distancia_m >= 0)" in sql
    for columna_prohibida in (
        "whatsapp text", "costo_envio numeric", "nombre text",
        "apellido text", "referencia text", "token text",
    ):
        assert columna_prohibida not in sql


def test_migracion_aisla_cache_y_service_role_tiene_privilegios_acotados():
    sql = sql_normalizado(MIGRACION)

    assert "enable row level security" in sql
    assert (
        "revoke all privileges on table public.delivery_direcciones_cache "
        "from public, anon, authenticated, service_role"
    ) in sql
    assert (
        "grant select, insert, update on table public.delivery_direcciones_cache "
        "to service_role"
    ) in sql
    assert "grant delete" not in sql
    assert "grant select" not in sql.replace(
        "grant select, insert, update on table public.delivery_direcciones_cache "
        "to service_role", ""
    )


def test_rollback_es_transaccional_acotado_y_sin_cascade():
    sql = sql_normalizado(ROLLBACK)

    assert "begin;" in sql and sql.endswith("commit;")
    assert "if to_regclass('public.delivery_direcciones_cache') is not null" in sql
    assert (
        "revoke all privileges on table public.delivery_direcciones_cache "
        "from public, anon, authenticated, service_role"
    ) in sql
    assert "drop table if exists public.delivery_direcciones_cache;" in sql
    assert "cascade" not in sql
    for objeto_ajeno in (
        "pedidos", "publicaciones", "gastronomia", "comercios",
        "usuarios", "pedidos_configuracion",
    ):
        assert f"drop table if exists public.{objeto_ajeno}" not in sql
