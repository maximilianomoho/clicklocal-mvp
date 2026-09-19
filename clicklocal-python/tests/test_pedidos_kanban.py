import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from gastronomia import gastronomia_bp


@pytest.fixture(autouse=True)
def pos_activo(monkeypatch):
    monkeypatch.setattr(
        "gastronomia.routes._pos_activo_gastronomia",
        lambda _comercio_id: True,
    )
from gastronomia.routes import pedido_visible_tablero


class ConsultaFalsa:
    def __init__(self, db):
        self.db = db
        self.filtros = []
        self.cambios = None

    def select(self, *args, **kwargs): return self
    def eq(self, campo, valor): self.filtros.append((campo, valor)); return self
    def in_(self, campo, valor): self.filtros.append((campo, valor)); return self
    def gte(self, *args): return self
    def lt(self, *args): return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args): return self
    def update(self, cambios): self.cambios = cambios; return self
    def execute(self):
        self.db.consultas.append(self)
        if self.cambios is not None:
            self.db.pedido.update(self.cambios)
        return SimpleNamespace(data=[dict(self.db.pedido)])


class DBFalsa:
    def __init__(self):
        self.pedido = {
            "id": "pedido-1", "estado": "pendiente",
            "estado_pago": "pendiente", "entregado_at": None, "detalle": [],
        }
        self.consultas = []
    def table(self, _): return ConsultaFalsa(self)


class PedidosKanbanTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(gastronomia_bp)
        @self.app.route("/login", endpoint="login")
        def login(): return "login"

    def test_kanban_agrupa_estados_sin_columna_cancelado(self):
        db = DBFalsa()
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db), patch("gastronomia.routes.render_template", return_value="kanban") as render:
            respuesta = self.app.test_client().get("/gastronomia/panel/pedidos")
        self.assertEqual(respuesta.status_code, 200)
        contexto = render.call_args.kwargs
        self.assertEqual(contexto["estados_kanban"], ("pendiente", "marchando", "preparado", "cerrado"))
        self.assertNotIn("cancelado", contexto["columnas"])
        self.assertEqual(len(contexto["columnas"]["pendiente"]), 1)

    def test_visibilidad_exige_pago_y_entrega_juntos(self):
        casos = (
            ("pagado", None, True),
            ("pendiente", "2026-09-14T15:00:00+00:00", True),
            ("pagado", "2026-09-14T15:00:00+00:00", False),
        )
        for estado_pago, entregado_at, visible in casos:
            with self.subTest(estado_pago=estado_pago, entregado_at=entregado_at):
                self.assertEqual(pedido_visible_tablero({
                    "estado_pago": estado_pago,
                    "entregado_at": entregado_at,
                }), visible)

    def test_venta_rapida_pagada_sigue_visible_hasta_entrega(self):
        self.assertTrue(pedido_visible_tablero({
            "estado": "cerrado", "estado_pago": "pagado", "entregado_at": None,
        }))

    def test_cerrado_muestra_volver_y_entregado(self):
        raiz = Path(__file__).resolve().parents[1]
        plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
            encoding="utf-8"
        )
        javascript = (raiz / "static/gastronomia/pedidos.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Reabrir como preparado", plantilla)
        self.assertNotIn("Reabrir como preparado", javascript)
        self.assertIn("data-accion-entrega", plantilla)
        self.assertIn("data-accion-entrega", javascript)
        self.assertIn("v='entrega-3'", plantilla)
        self.assertIn("pedido_detalle.js", plantilla)
        self.assertIn(
            'evento.target.closest("[data-accion-entrega]")',
            javascript,
        )
        self.assertIn(
            'evento.target.closest("button[data-estado-pago]")',
            javascript,
        )
        self.assertNotIn(
            'evento.target.closest("[data-estado-pago]")',
            javascript,
        )
        self.assertIn("if (botonEntrega) {", javascript)
        self.assertIn("} else if (botonPago) {", javascript)
        self.assertIn(
            "`${tablero.dataset.endpointBase}/${tarjeta.dataset.pedidoId}/entrega`",
            javascript,
        )

    def test_comanda_activa_muestra_pedido_completo_y_permanece_separada_de_ticket(self):
        raiz = Path(__file__).resolve().parents[1]
        plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
            encoding="utf-8"
        )
        modal = (raiz / "templates/gastronomia/_comanda_modal.html").read_text(
            encoding="utf-8"
        )
        javascript = (raiz / "static/gastronomia/comanda.js").read_text(
            encoding="utf-8"
        )
        css = (raiz / "static/gastronomia/pedidos.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="js-ver-comanda"', plantilla)
        self.assertIn('data-pedido="{{ pedido|tojson|forceescape }}"', plantilla)
        self.assertNotIn('disabled title="Próximamente">Comanda', plantilla)
        self.assertNotIn('disabled title="Próximamente">Ticket', plantilla)
        self.assertIn('{% include "gastronomia/_comanda_modal.html" %}', plantilla)
        self.assertIn("Pedido completo", modal)
        self.assertIn("Imprimir comanda", modal)
        self.assertIn("JSON.parse(boton.dataset.pedido)", javascript)
        self.assertIn("pedido.detalle_items", javascript)
        self.assertIn("item.cantidad", javascript)
        self.assertIn("item.opciones", javascript)
        self.assertIn("item.nota", javascript)
        self.assertIn("pedido.observaciones", javascript)
        self.assertNotIn("bebida", javascript.lower())
        self.assertIn("window.print()", javascript)
        self.assertIn("@media print", css)
        self.assertIn("body.comanda-imprimiendo", css)

    def test_ticket_activo_muestra_pedido_completo_e_imprime_aislado(self):
        raiz = Path(__file__).resolve().parents[1]
        plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
            encoding="utf-8"
        )
        modal = (raiz / "templates/gastronomia/_ticket_modal.html").read_text(
            encoding="utf-8"
        )
        javascript = (raiz / "static/gastronomia/ticket.js").read_text(
            encoding="utf-8"
        )
        css = (raiz / "static/gastronomia/pedidos.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="js-ver-ticket"', plantilla)
        self.assertIn('data-pedido="{{ pedido|tojson|forceescape }}"', plantilla)
        self.assertIn('{% include "gastronomia/_ticket_modal.html" %}', plantilla)
        self.assertIn("gastronomia/ticket.js", plantilla)
        self.assertIn("comercio.nombre_negocio", modal)
        self.assertIn("Ticket / Recibo no fiscal", modal)
        self.assertIn("Imprimir ticket", modal)
        self.assertIn("JSON.parse(boton.dataset.pedido)", javascript)
        self.assertIn("pedido.detalle_items", javascript)
        self.assertIn("item.cantidad", javascript)
        self.assertIn("item.opciones", javascript)
        for campo in (
            "pedido.subtotal", "pedido.costo_envio", "pedido.descuento",
            "pedido.total", "pedido.forma_pago", "pedido.tipo_entrega",
        ):
            self.assertIn(campo, javascript)
        for campo in (
            "pedido.estado_pago", "pedido.nombre_cliente",
            "pedido.telefono_cliente", "pedido.origen", "item.nota",
        ):
            self.assertNotIn(campo, javascript)
        self.assertIn('mesa: "Salón"', javascript)
        self.assertIn('[["Pago", etiqueta(pedido.forma_pago)]]', javascript)
        self.assertIn("Math.round(subtotal * 100) !== Math.round(total * 100)", javascript)
        self.assertIn("if (envio > 0 || descuento > 0 || subtotalDifiere)", javascript)
        self.assertIn('totales.push(["Subtotal", subtotal])', javascript)
        self.assertIn("window.print()", javascript)
        self.assertIn("body.ticket-imprimiendo *", css)
        self.assertIn("max-width:80mm", css)
        self.assertIn("ticket-acciones{display:none!important}", css)
        self.assertNotIn("comandaModal", javascript)
        self.assertNotIn("comanda-imprimiendo", javascript)

    def test_endpoint_requiere_autorizacion(self):
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value=None):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/estado", json={"estado": "marchando"})
        self.assertEqual(respuesta.status_code, 401)

    def test_avance_valido_y_timestamps(self):
        casos = (
            ("pendiente", "marchando", False),
            ("marchando", "preparado", False),
            ("preparado", "cerrado", True),
        )
        for anterior, nuevo, tiene_cierre in casos:
            with self.subTest(anterior=anterior, nuevo=nuevo):
                db = DBFalsa(); db.pedido["estado"] = anterior
                with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
                    respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/estado", json={"accion": "avanzar"})
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.get_json()["estado"], nuevo)
                valor = db.consultas[-1].cambios["cerrado_at"]
                self.assertEqual(valor is not None, tiene_cierre)

    def test_retroceso_y_cancelacion_explicitos(self):
        for anterior, accion, nuevo in (
            ("cerrado", "retroceder", "preparado"),
            ("preparado", "retroceder", "marchando"),
            ("marchando", "cancelar", "cancelado"),
        ):
            with self.subTest(anterior=anterior, accion=accion):
                db = DBFalsa(); db.pedido["estado"] = anterior
                with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
                    respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/estado", json={"accion": accion, "confirmado": accion == "cancelar"})
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.get_json()["estado"], nuevo)

    def test_volver_desde_cerrado_limpia_entrega_y_conserva_pago(self):
        db = DBFalsa()
        db.pedido.update({
            "estado": "cerrado", "estado_pago": "pagado",
            "pagado_at": "2026-09-14T14:00:00+00:00",
            "entregado_at": "2026-09-14T15:00:00+00:00",
        })
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/estado", json={"accion": "retroceder"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(db.pedido["estado"], "preparado")
        self.assertEqual(db.pedido["estado_pago"], "pagado")
        self.assertEqual(db.pedido["pagado_at"], "2026-09-14T14:00:00+00:00")
        self.assertIsNone(db.pedido["entregado_at"])

    def test_cancelacion_sin_confirmacion_se_rechaza(self):
        db = DBFalsa()
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/estado", json={"accion": "cancelar"})
        self.assertEqual(respuesta.status_code, 400)

    def test_movimiento_no_permitido_rechazado(self):
        db = DBFalsa(); db.pedido["estado"] = "pendiente"
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/estado", json={"accion": "retroceder"})
        self.assertEqual(respuesta.status_code, 400)

    def test_pago_actualiza_estado_y_timestamp(self):
        db = DBFalsa()
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/pago", json={"estado_pago": "pagado"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()["estado_pago"], "pagado")
        self.assertIsNotNone(db.consultas[-1].cambios["pagado_at"])

    def test_pagado_y_luego_entregado_desaparece(self):
        db = DBFalsa()
        db.pedido.update({"estado": "cerrado", "estado_pago": "pagado"})
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/entrega", json={})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.get_json()["ocultar_tablero"])
        self.assertIsNotNone(db.pedido["entregado_at"])

    def test_entregado_y_luego_pagado_desaparece(self):
        db = DBFalsa()
        db.pedido.update({
            "estado": "cerrado", "estado_pago": "pendiente",
            "entregado_at": "2026-09-14T15:00:00+00:00",
        })
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/pago", json={"estado_pago": "pagado"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.get_json()["ocultar_tablero"])

    def test_pedido_cancelado_no_admite_cambio_de_pago(self):
        db = DBFalsa(); db.pedido["estado"] = "cancelado"
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-1"}), patch("gastronomia.routes.supabase_admin", db):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/pedido-1/pago", json={"estado_pago": "pagado"})
        self.assertEqual(respuesta.status_code, 400)

    def test_pedido_ajeno_no_se_actualiza(self):
        db = DBFalsa()
        def vacio(_consulta): return SimpleNamespace(data=[])
        with patch("gastronomia.routes._comercio_panel_gastronomia", return_value={"id": "comercio-propio"}), patch("gastronomia.routes.supabase_admin", db), patch.object(ConsultaFalsa, "execute", vacio):
            respuesta = self.app.test_client().post("/gastronomia/panel/pedidos/ajeno/estado", json={"accion": "avanzar"})
        self.assertEqual(respuesta.status_code, 404)


if __name__ == "__main__": unittest.main()
