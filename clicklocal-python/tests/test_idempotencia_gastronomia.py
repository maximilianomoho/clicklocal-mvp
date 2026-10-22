from pathlib import Path
import shutil
import subprocess

import pytest


RAIZ = Path(__file__).resolve().parents[1]
MIGRACION = RAIZ / "sql" / "gastronomia_pedidos_idempotencia.sql"
MENU = RAIZ / "templates" / "gastronomia" / "menu.html"


def test_migracion_define_columnas_constraint_y_compatibilidad_null():
    sql = MIGRACION.read_text(encoding="utf-8").lower()
    assert "add column if not exists idempotency_key uuid" in sql
    assert "add column if not exists idempotency_fingerprint text" in sql
    assert "unique (comercio_id, idempotency_key)" in sql
    assert "idempotency_key is null and idempotency_fingerprint is null" in sql
    assert "idempotency_key is not null and idempotency_fingerprint is not null" in sql
    assert "alter column idempotency_key set not null" not in sql


def test_frontend_persiste_reutiliza_y_limpia_la_intencion():
    html = MENU.read_text(encoding="utf-8")
    assert "crypto.randomUUID()" in html
    assert "sessionStorage.getItem(claveIntencionPedido)" in html
    assert "sessionStorage.setItem(" in html
    assert "existente.firma === firma" in html
    assert "limpiarIntencionPedido(idempotencyKey);" in html
    assert "sessionStorage.removeItem(claveIntencionPedido)" in html
    assert "pedidoPayload.idempotency_key" in html


def test_frontend_reutiliza_en_retry_refresh_y_renueva_si_corresponde():
    if not shutil.which("node"):
        pytest.skip("Node.js no está disponible para ejecutar los helpers frontend")

    html = MENU.read_text(encoding="utf-8")
    inicio = html.index("const claveIntencionPedido")
    fin = html.index("async function prepararPedido", inicio)
    helpers = html[inicio:fin].replace("{{ comercio.id }}", "comercio-1")
    prueba = r'''
const memoriaStorage = {};
global.sessionStorage = {
  getItem: clave => Object.prototype.hasOwnProperty.call(memoriaStorage, clave)
    ? memoriaStorage[clave] : null,
  setItem: (clave, valor) => { memoriaStorage[clave] = valor; },
  removeItem: clave => { delete memoriaStorage[clave]; }
};
''' + helpers + r'''
const pedido = {
  nombre:"Ana", apellido:"Prueba", whatsapp:"3436123456",
  modalidad:"Retiro", direccion:"", cotizacion_delivery:"",
  forma_pago:"Efectivo", paga_con:20000, observaciones:"",
  detalle:[{id:"producto-1", cantidad:1, nota:"", opciones:[]}]
};
const primera = obtenerIdempotencyKey(pedido);
const retry = obtenerIdempotencyKey(pedido);
if (primera !== retry) throw new Error("El retry cambió la clave");

intencionPedidoMemoria = null;
const refresh = obtenerIdempotencyKey(pedido);
if (primera !== refresh) throw new Error("El refresh cambió la clave");

const modificado = JSON.parse(JSON.stringify(pedido));
modificado.detalle[0].cantidad = 2;
const segunda = obtenerIdempotencyKey(modificado);
if (segunda === primera) throw new Error("El pedido modificado reutilizó la clave");

limpiarIntencionPedido(segunda);
const siguiente = obtenerIdempotencyKey(modificado);
if (siguiente === segunda) throw new Error("El pedido siguiente reutilizó la clave");
'''
    resultado = subprocess.run(
        ["node", "-e", prueba],
        check=False,
        capture_output=True,
        text=True,
    )
    assert resultado.returncode == 0, resultado.stderr
