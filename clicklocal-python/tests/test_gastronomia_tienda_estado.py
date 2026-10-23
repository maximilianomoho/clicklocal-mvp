from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app as app_module
from gastronomia.services.pedidos import PedidoError, buscar_pedido_idempotente


COMERCIO_ID = "11111111-1111-1111-1111-111111111111"
OTRO_COMERCIO_ID = "22222222-2222-2222-2222-222222222222"
USUARIO_ID = "propietaria-1"
CSRF = "csrf-estado-tienda-prueba"


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.payload = dict(payload)
        return self

    def execute(self):
        filas = self.db.datos.get(self.tabla, [])
        coincidentes = [
            fila for fila in filas
            if all(fila.get(campo) == valor for campo, valor in self.filtros)
        ]

        if self.payload is not None:
            for fila in coincidentes:
                fila.update(self.payload)
            self.db.actualizaciones.append((self.tabla, dict(self.payload)))

        return SimpleNamespace(data=[dict(fila) for fila in coincidentes])


class SupabaseFalso:
    def __init__(self, *, activo, autorizada):
        self.actualizaciones = []
        self.datos = {
            "comercios": [{
                "id": COMERCIO_ID,
                "user_id": USUARIO_ID,
                "nombre_negocio": "Granola",
                "categoria": "Gastronomía",
                "activo": True,
            }],
            "gastronomia_configuracion": [{
                "comercio_id": COMERCIO_ID,
                "activo": activo,
                "publicacion_autorizada_at": (
                    datetime.now(timezone.utc).isoformat()
                    if autorizada else None
                ),
            }],
            "gastronomia_pedidos": [{
                "id": "pedido-anterior",
                "comercio_id": COMERCIO_ID,
                "idempotency_key": "pedido-previo",
                "idempotency_fingerprint": "huella-previa",
            }],
        }

    def table(self, tabla):
        return ConsultaFalsa(self, tabla)


def cliente_propietario(
    *,
    user_id=USUARIO_ID,
    comercio_id=COMERCIO_ID,
    csrf_token=CSRF,
):
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["user_id"] = user_id
        sesion["comercio"] = {
            "id": comercio_id,
            "user_id": user_id,
            "categoria": "Gastronomía",
            "activo": True,
        }
        if csrf_token:
            sesion["csrf_estado_tienda_gastronomia"] = csrf_token
    return cliente


def test_borrador_no_puede_encenderse_por_su_cuenta():
    db = SupabaseFalso(activo=False, autorizada=False)

    with patch("gastronomia.routes.supabase_admin", db):
        respuesta = cliente_propietario().post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "encender", "csrf_token": CSRF},
        )

    assert respuesta.status_code == 302
    assert "tienda_error=sin_autorizacion" in respuesta.location
    assert db.actualizaciones == []
    assert db.datos["gastronomia_configuracion"][0]["activo"] is False


def test_comercio_autorizado_puede_apagar_y_volver_a_encender():
    db = SupabaseFalso(activo=True, autorizada=True)
    autorizacion = db.datos["gastronomia_configuracion"][0][
        "publicacion_autorizada_at"
    ]
    pedidos_antes = list(db.datos["gastronomia_pedidos"])
    cliente = cliente_propietario()

    with patch("gastronomia.routes.supabase_admin", db):
        apagada = cliente.post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "apagar", "csrf_token": CSRF},
        )
        encendida = cliente.post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "encender", "csrf_token": CSRF},
        )

    assert apagada.status_code == 302
    assert "tienda_estado=apagada" in apagada.location
    assert encendida.status_code == 302
    assert "tienda_estado=encendida" in encendida.location
    assert db.actualizaciones == [
        ("gastronomia_configuracion", {"activo": False}),
        ("gastronomia_configuracion", {"activo": True}),
    ]
    assert db.datos["gastronomia_configuracion"][0][
        "publicacion_autorizada_at"
    ] == autorizacion
    assert db.datos["gastronomia_pedidos"] == pedidos_antes


def test_admin_no_reemplaza_una_autorizacion_previa_al_reencender():
    db = SupabaseFalso(activo=False, autorizada=True)
    autorizacion = db.datos["gastronomia_configuracion"][0][
        "publicacion_autorizada_at"
    ]
    cliente = app_module.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True

    with patch("app.supabase_admin", db):
        respuesta = cliente.post(f"/admin/gastronomia/{COMERCIO_ID}/toggle")

    assert respuesta.status_code == 302
    actualizacion = db.actualizaciones[-1][1]
    assert actualizacion["activo"] is True
    assert "publicacion_autorizada_at" not in actualizacion
    assert db.datos["gastronomia_configuracion"][0][
        "publicacion_autorizada_at"
    ] == autorizacion


def test_tienda_apagada_rechaza_pedidos_nuevos_aunque_este_autorizada():
    db = SupabaseFalso(activo=False, autorizada=True)

    with pytest.raises(PedidoError) as error:
        buscar_pedido_idempotente(
            COMERCIO_ID,
            "pedido-nuevo",
            "huella-nueva",
            cliente_supabase=db,
        )

    assert error.value.status_code == 403
    assert db.datos["gastronomia_pedidos"][0]["id"] == "pedido-anterior"


def test_cambio_estado_exige_propietario_autenticado_del_comercio():
    db = SupabaseFalso(activo=True, autorizada=True)

    with patch("gastronomia.routes.supabase_admin", db):
        anonima = app_module.app.test_client().post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "apagar", "csrf_token": CSRF},
        )
        otro_usuario = cliente_propietario(
            user_id="otra-persona",
            comercio_id=OTRO_COMERCIO_ID,
        ).post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "apagar", "csrf_token": CSRF},
        )

    assert anonima.status_code == 302
    assert otro_usuario.status_code == 302
    assert anonima.location.endswith(("/login", "/login.html"))
    assert otro_usuario.location.endswith(("/login", "/login.html"))
    assert db.actualizaciones == []


def test_accion_invalida_no_modifica_estado():
    db = SupabaseFalso(activo=True, autorizada=True)

    with patch("gastronomia.routes.supabase_admin", db):
        respuesta = cliente_propietario().post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "alternar", "csrf_token": CSRF},
        )

    assert respuesta.status_code == 302
    assert "tienda_error=accion_invalida" in respuesta.location
    assert db.actualizaciones == []


def test_cambio_estado_rechaza_csrf_ausente_o_incorrecto():
    db = SupabaseFalso(activo=True, autorizada=True)

    with patch("gastronomia.routes.supabase_admin", db):
        sin_token = cliente_propietario(csrf_token=None).post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "apagar"},
        )
        token_incorrecto = cliente_propietario().post(
            "/gastronomia/panel/tienda/estado",
            data={"accion": "apagar", "csrf_token": "incorrecto"},
        )

    assert sin_token.status_code == 400
    assert token_incorrecto.status_code == 400
    assert db.actualizaciones == []


def test_control_aparece_arriba_en_productos_y_no_en_configuracion():
    db = SupabaseFalso(activo=True, autorizada=True)
    cliente = cliente_propietario()

    with patch("gastronomia.routes.supabase_admin", db), patch(
        "gastronomia.routes._pos_activo_gastronomia",
        return_value=False,
    ):
        productos = cliente.get("/gastronomia/panel/productos")
        configuracion = cliente.get("/gastronomia/panel/configuracion")

    assert productos.status_code == 200
    html_productos = productos.get_data(as_text=True)
    assert (
        html_productos.index("Menú y productos")
        < html_productos.index("ESTADO DE LA TIENDA")
        < html_productos.index("Cargar producto")
    )
    assert 'name="csrf_token"' in html_productos
    assert f'value="{CSRF}"' in html_productos

    assert configuracion.status_code == 200
    assert "ESTADO DE LA TIENDA" not in configuracion.get_data(as_text=True)


def test_migracion_agrega_columna_y_solo_autoriza_configuraciones_activas():
    sql = Path("sql/gastronomia_tienda_estado_20261023.sql").read_text(
        encoding="utf-8"
    ).lower()

    assert "add column if not exists publicacion_autorizada_at timestamptz" in sql
    assert "update public.gastronomia_configuracion" in sql
    assert "where activo = true" in sql
    assert "publicacion_autorizada_at is null" in sql
    assert "milanguche" not in sql


def test_rollback_impide_perder_autorizaciones_existentes():
    sql = Path(
        "sql/gastronomia_tienda_estado_20261023_ROLLBACK.sql"
    ).read_text(encoding="utf-8").lower()

    assert "publicacion_autorizada_at is not null" in sql
    assert "raise exception" in sql
    assert "drop column if exists publicacion_autorizada_at" in sql
