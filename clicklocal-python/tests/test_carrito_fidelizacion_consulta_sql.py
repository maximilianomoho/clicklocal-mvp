from pathlib import Path
import re


BASE = Path(__file__).resolve().parents[1]
MIGRACION = BASE / "sql" / "carrito_fidelizacion_consulta_20261010.sql"
ROLLBACK = BASE / "sql" / "carrito_fidelizacion_consulta_20261010_ROLLBACK.sql"


def sql_migracion():
    return MIGRACION.read_text(encoding="utf-8").lower()


def cuerpo_funcion():
    sql = sql_migracion()
    return sql.split("as $$", 1)[1].split("$$;", 1)[0]


def test_funcion_firma_retorno_y_propiedades():
    sql = sql_migracion()
    assert "function public.consultar_fidelizacion_clicklocal(" in sql
    assert "p_comercio_id uuid" in sql
    assert "p_cliente_claves text[]" in sql
    assert "returns jsonb" in sql
    assert "stable" in sql
    assert "security definer" in sql
    assert "set search_path = pg_catalog" in sql


def test_validaciones_de_entrada_y_limite_real():
    cuerpo = cuerpo_funcion()
    assert "if p_comercio_id is null" in cuerpo
    assert "if p_cliente_claves is null" in cuerpo
    assert "cardinality(p_cliente_claves) > 50" in cuerpo
    assert "array_position(p_cliente_claves, null) is not null" in cuerpo
    assert "btrim(clave.cliente_clave) = ''" in cuerpo
    assert "count(distinct pg_catalog.btrim(clave.cliente_clave))" in cuerpo
    assert "array[]::text[]" in cuerpo


def test_configuracion_y_array_vacio_no_descubren_clientes():
    cuerpo = cuerpo_funcion()
    assert "from public.pedidos_configuracion" in cuerpo
    assert "el comercio no tiene configuracion de pedidos." in cuerpo
    assert "from pg_catalog.unnest(v_cliente_claves)" in cuerpo
    assert "'clientes', v_clientes" in cuerpo
    assert "'[]'::jsonb" in cuerpo
    assert "select distinct" not in cuerpo


def test_formula_de_sellos_respeta_comercio_estado_identidad_y_periodos():
    cuerpo = cuerpo_funcion()
    assert "pedido.comercio_id = p_comercio_id" in cuerpo
    assert "pedido.estado = 'entregado'" in cuerpo
    assert "public.clave_cliente_pedido_clicklocal(" in cuerpo
    assert "= clave.cliente_clave" in cuerpo
    assert "from public.pedidos_fidelizacion_periodos periodo" in cuerpo
    assert "periodo.comercio_id = pedido.comercio_id" in cuerpo
    assert "periodo.inicio <= pedido.created_at" in cuerpo
    assert "pedido.created_at < periodo.fin" in cuerpo
    assert re.search(r"and exists\s*\(", cuerpo)


def test_consumos_saldo_premio_e_historial():
    cuerpo = cuerpo_funcion()
    assert "from public.pedidos_fidelizacion_canjes canje" in cuerpo
    assert "canje.comercio_id = p_comercio_id" in cuerpo
    assert "canje.cliente_clave = clave.cliente_clave" in cuerpo
    assert "pg_catalog.sum(canje.sellos_consumidos)" in cuerpo
    assert "'saldo'" in cuerpo
    assert "'premio_disponible'" in cuerpo
    for campo in (
        "'id'",
        "'canjeado_at'",
        "'sellos_consumidos'",
        "'meta_snapshot'",
        "'premio_snapshot'",
        "'saldo_restante_snapshot'",
    ):
        assert campo in cuerpo
    assert "order by canje.canjeado_at desc, canje.id desc" in cuerpo


def test_funcion_es_estrictamente_de_lectura():
    cuerpo = cuerpo_funcion()
    for operacion in ("insert", "update", "delete", "upsert", "alter", "create"):
        assert re.search(rf"\b{operacion}\b", cuerpo) is None
    for rpc in (
        "configurar_fidelizacion_clicklocal",
        "canjear_premio_fidelizacion_clicklocal",
        "crear_pedido_clicklocal",
    ):
        assert rpc not in cuerpo


def test_acl_solo_otorga_execute_a_service_role():
    sql = sql_migracion()
    firma = "public.consultar_fidelizacion_clicklocal(uuid, text[])"
    assert f"revoke all on function {firma}" in sql
    assert "from public, anon, authenticated, service_role" in sql
    assert f"grant execute on function {firma}" in sql
    assert "to service_role" in sql
    assert "grant select" not in sql
    grants = re.findall(r"grant\s+execute\s+on\s+function[\s\S]*?\s+to\s+(\w+)\s*;", sql)
    assert grants == ["service_role"]


def test_rollback_solo_elimina_la_rpc_nueva():
    sql = ROLLBACK.read_text(encoding="utf-8").lower()
    assert "drop function if exists" in sql
    assert "public.consultar_fidelizacion_clicklocal(uuid, text[])" in sql
    for objeto in (
        "pedidos_fidelizacion_periodos",
        "pedidos_fidelizacion_canjes",
        "configurar_fidelizacion_clicklocal",
        "canjear_premio_fidelizacion_clicklocal",
    ):
        assert objeto not in sql
    for operacion in ("drop table", "drop column", "truncate", "insert", "update", "delete"):
        assert operacion not in sql
