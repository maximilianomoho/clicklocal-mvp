import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from gastronomia import gastronomia_bp


class ConsultaPedidosFalsa:
    def __init__(self):
        self.columnas = ""
        self.count_solicitado = None
        self.filtros = []
        self.ordenes = []
        self.rango = None

    def select(self, columnas, count=None):
        self.columnas = columnas
        self.count_solicitado = count
        return self

    def eq(self, columna, valor):
        self.filtros.append(("eq", columna, valor))
        return self

    def in_(self, columna, valores):
        self.filtros.append(("in", columna, valores))
        return self

    def order(self, columna, desc=False):
        self.ordenes.append((columna, desc))
        return self

    def range(self, desde, hasta):
        self.rango = (desde, hasta)
        return self

    def execute(self):
        return SimpleNamespace(
            data=[{
                "id": "pedido-1",
                "numero_pedido": 10,
                "created_at": "2026-09-03T20:00:00+00:00",
                "estado": "pendiente",
                "estado_pago": "pendiente",
                "detalle": None,
            }],
            count=26,
        )


class SupabasePedidosFalso:
    def __init__(self):
        self.tabla_consultada = None
        self.consulta = ConsultaPedidosFalsa()

    def table(self, tabla):
        self.tabla_consultada = tabla
        return self.consulta


class PedidosBandejaTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(gastronomia_bp)

        @self.app.route("/login", endpoint="login")
        def login():
            return "login"

    def test_requiere_comercio_autorizado(self):
        with patch(
            "gastronomia.routes._comercio_panel_gastronomia",
            return_value=None,
        ):
            respuesta = self.app.test_client().get(
                "/gastronomia/panel/pedidos"
            )

        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta.location.endswith("/login"))

    def test_consulta_activos_del_comercio_y_pagina_25(self):
        db = SupabasePedidosFalso()

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-propio"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.render_template",
                return_value="bandeja",
            ) as render_mock,
        ):
            respuesta = self.app.test_client().get(
                "/gastronomia/panel/pedidos?comercio_id=otro"
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(db.tabla_consultada, "gastronomia_pedidos")
        self.assertEqual(db.consulta.count_solicitado, "exact")
        self.assertIn(
            ("eq", "comercio_id", "comercio-propio"),
            db.consulta.filtros,
        )
        self.assertNotIn(
            ("eq", "comercio_id", "otro"),
            db.consulta.filtros,
        )
        self.assertIn(
            (
                "in",
                "estado",
                [
                    "pendiente",
                    "marchando",
                    "preparado",
                ],
            ),
            db.consulta.filtros,
        )
        self.assertEqual(
            db.consulta.ordenes,
            [("created_at", True), ("numero_pedido", True)],
        )
        self.assertEqual(db.consulta.rango, (0, 24))
        contexto = render_mock.call_args.kwargs
        self.assertEqual(contexto["pagina"], 1)
        self.assertEqual(contexto["total_paginas"], 2)
        self.assertEqual(contexto["pedidos"][0]["detalle_items"], [])

    def test_valida_filtros_y_pagina(self):
        db = SupabasePedidosFalso()

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-propio"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.render_template",
                return_value="bandeja",
            ) as render_mock,
        ):
            respuesta = self.app.test_client().get(
                "/gastronomia/panel/pedidos"
                "?vista=incorrecta&estado=desconocido"
                "&origen=externo&tipo_entrega=avion"
                "&estado_pago=parcial&pagina=-4"
            )

        self.assertEqual(respuesta.status_code, 200)
        contexto = render_mock.call_args.kwargs
        self.assertEqual(contexto["filtros"], {
            "vista": "activos",
            "estado": "",
            "origen": "",
            "tipo_entrega": "",
            "estado_pago": "",
        })
        self.assertEqual(contexto["pagina"], 1)
        self.assertEqual(db.consulta.rango, (0, 24))

    def test_filtros_validos_y_segunda_pagina(self):
        db = SupabasePedidosFalso()

        with (
            patch(
                "gastronomia.routes._comercio_panel_gastronomia",
                return_value={"id": "comercio-propio"},
            ),
            patch("gastronomia.routes.supabase_admin", db),
            patch(
                "gastronomia.routes.render_template",
                return_value="bandeja",
            ),
        ):
            respuesta = self.app.test_client().get(
                "/gastronomia/panel/pedidos"
                "?vista=todos&estado=cerrado&origen=telefono"
                "&tipo_entrega=mostrador&estado_pago=pagado&pagina=2"
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(
            ("eq", "estado", "cerrado"),
            db.consulta.filtros,
        )
        self.assertIn(
            ("eq", "origen", "telefono"),
            db.consulta.filtros,
        )
        self.assertIn(
            ("eq", "tipo_entrega", "mostrador"),
            db.consulta.filtros,
        )
        self.assertIn(
            ("eq", "estado_pago", "pagado"),
            db.consulta.filtros,
        )
        self.assertEqual(db.consulta.rango, (25, 49))


if __name__ == "__main__":
    unittest.main()
