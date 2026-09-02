import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g

from gastronomia import gastronomia_bp
from gastronomia.services.pedidos import PedidoError, crear_pedido


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.datos_insertados = None
        self.filtros = []

    def select(self, *args):
        return self

    def eq(self, columna, valor):
        self.filtros.append(("eq", columna, valor))
        return self

    def in_(self, columna, valores):
        self.filtros.append(("in", columna, valores))
        return self

    def limit(self, *args):
        return self

    def insert(self, datos):
        self.datos_insertados = datos
        return self

    def execute(self):
        if self.datos_insertados is not None:
            if self.db.error_insercion is not None:
                raise self.db.error_insercion
            self.db.insertados.append((self.tabla, self.datos_insertados))
            if self.db.insercion_vacia:
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{
                "id": "pedido-1",
                "numero_pedido": 7,
                "created_at": "2026-09-02T15:00:00+00:00",
            }])
        datos = list(self.db.datos.get(self.tabla, []))
        for operador, columna, valor in self.filtros:
            if operador == "eq":
                datos = [fila for fila in datos if fila.get(columna) == valor]
            else:
                datos = [fila for fila in datos if fila.get(columna) in valor]
        return SimpleNamespace(data=datos)


class SupabaseFalso:
    def __init__(self, datos, error_insercion=None, insercion_vacia=False):
        self.datos = datos
        self.insertados = []
        self.error_insercion = error_insercion
        self.insercion_vacia = insercion_vacia

    def table(self, nombre):
        return ConsultaFalsa(self, nombre)


def datos_base():
    return {
        "comercios": [{
            "id": "comercio-1",
            "nombre_negocio": "Comercio de prueba",
            "whatsapp": "5493430000000",
        }],
        "gastronomia_configuracion": [{
            "comercio_id": "comercio-1",
            "activo": True,
            "acepta_delivery": True,
            "acepta_retiro": True,
            "pedido_minimo": 0,
            "costo_envio": 1500,
            "descuento_efectivo_pct": 10,
            "descuento_transferencia_pct": 5,
        }],
        "gastronomia_productos": [{
            "id": "producto-1",
            "comercio_id": "comercio-1",
            "nombre": "Producto",
            "precio": 10000,
            "precio_promocional": None,
            "activo": True,
            "disponible": True,
        }],
        "gastronomia_opciones": [],
        "gastronomia_grupos_opciones": [],
    }


def crear(db, **cambios):
    argumentos = {
        "comercio_id": "comercio-1",
        "nombre": "Ana",
        "apellido": "Prueba",
        "telefono": "3436123456",
        "telefono_normalizado": "3436123456",
        "modalidad": "retiro",
        "direccion": "",
        "forma_pago": "efectivo",
        "paga_con": 20000,
        "observaciones": "",
        "items": [{"id": "producto-1", "cantidad": 1, "opciones": []}],
        "visitante_id": "visitante-1",
        "sesion_id": "sesion-1",
        "cliente_supabase": db,
    }
    argumentos.update(cambios)
    return crear_pedido(**argumentos)


class PedidosServiceTest(unittest.TestCase):
    def test_retiro_efectivo_producto_sin_extras(self):
        db = SupabaseFalso(datos_base())
        resultado = crear(db)
        self.assertEqual(resultado["subtotal"], 10000)
        self.assertEqual(resultado["costo_envio"], 0)
        self.assertEqual(resultado["descuento"], 1000)
        self.assertEqual(resultado["total"], 9000)
        self.assertEqual(db.insertados[0][1]["tipo_entrega"], "retiro")

    def test_delivery_transferencia(self):
        db = SupabaseFalso(datos_base())
        resultado = crear(
            db,
            modalidad="delivery",
            direccion="Calle de prueba 123",
            forma_pago="transferencia",
            paga_con=None,
        )
        self.assertEqual(resultado["costo_envio"], 1500)
        self.assertEqual(resultado["descuento"], 500)
        self.assertEqual(resultado["total"], 11000)

    def test_producto_con_extra(self):
        datos = datos_base()
        datos["gastronomia_opciones"] = [{
            "id": "opcion-1",
            "grupo_id": "grupo-1",
            "nombre": "Extra",
            "precio_extra": 750,
            "activo": True,
            "disponible": True,
        }]
        datos["gastronomia_grupos_opciones"] = [{
            "id": "grupo-1",
            "producto_id": "producto-1",
            "activo": True,
        }]
        resultado = crear(
            SupabaseFalso(datos),
            items=[{
                "id": "producto-1",
                "cantidad": 2,
                "opciones": [{"id": "opcion-1"}],
            }],
        )
        self.assertEqual(resultado["detalle"][0]["precio_unitario"], 10750)
        self.assertEqual(resultado["subtotal"], 21500)

    def test_pedido_minimo(self):
        datos = datos_base()
        datos["gastronomia_configuracion"][0]["pedido_minimo"] = 12000
        with self.assertRaisesRegex(PedidoError, "pedido mínimo"):
            crear(SupabaseFalso(datos))

    def test_producto_no_disponible(self):
        datos = datos_base()
        datos["gastronomia_productos"][0]["disponible"] = False
        with self.assertRaisesRegex(PedidoError, "ya no está disponible"):
            crear(SupabaseFalso(datos))

    def test_opcion_invalida(self):
        with self.assertRaisesRegex(PedidoError, "opción.*no está disponible"):
            crear(
                SupabaseFalso(datos_base()),
                items=[{
                    "id": "producto-1",
                    "cantidad": 1,
                    "opciones": [{"id": "inexistente"}],
                }],
            )

    def test_payload_insertado_completo_y_texto_delivery_transferencia(self):
        datos = datos_base()
        datos["gastronomia_opciones"] = [{
            "id": "opcion-1",
            "grupo_id": "grupo-1",
            "nombre": "Extra",
            "precio_extra": 750,
            "activo": True,
            "disponible": True,
        }]
        datos["gastronomia_grupos_opciones"] = [{
            "id": "grupo-1",
            "producto_id": "producto-1",
            "activo": True,
        }]
        db = SupabaseFalso(datos)
        resultado = crear(
            db,
            modalidad="delivery",
            direccion="Calle de prueba 123",
            forma_pago="transferencia",
            paga_con=None,
            observaciones="Sin cubiertos",
            items=[{
                "id": "producto-1",
                "cantidad": 2,
                "opciones": [{"id": "opcion-1"}],
                "nota": "Bien cocido",
            }],
        )
        detalle = [{
            "id": "producto-1",
            "nombre": "Producto",
            "cantidad": 2,
            "precio_unitario": 10750.0,
            "subtotal": 21500.0,
            "opciones": [{
                "id": "opcion-1",
                "nombre": "Extra",
                "precio": 750.0,
            }],
            "nota": "Bien cocido",
        }]
        texto = (
            "Pedido para Comercio de prueba\n\n"
            "Cliente: Ana Prueba\n"
            "WhatsApp: 3436123456\n\n"
            "2x Producto - $21.500\n"
            "  + Extra ($750)\n"
            "  Aclaración: Bien cocido\n\n"
            "Productos: $21.500\n"
            "Descuento transferencia (5%): -$1.075\n"
            "Envío: $1.500\n"
            "Total: $21.925\n\n"
            "Modalidad: Delivery\n"
            "Dirección: Calle de prueba 123\n"
            "Forma de pago: Transferencia\n\n"
            "Aclaración general: Sin cubiertos"
        )
        self.assertEqual(resultado["texto_pedido"], texto)
        self.assertEqual(db.insertados, [(
            "gastronomia_pedidos",
            {
                "numero_pedido": 0,
                "comercio_id": "comercio-1",
                "visitante_id": "visitante-1",
                "sesion_id": "sesion-1",
                "nombre_cliente": "Ana",
                "apellido_cliente": "Prueba",
                "telefono_cliente": "3436123456",
                "telefono_normalizado": "3436123456",
                "tipo_entrega": "delivery",
                "direccion_entrega": "Calle de prueba 123",
                "forma_pago": "transferencia",
                "paga_con": None,
                "subtotal": 21500.0,
                "costo_envio": 1500.0,
                "descuento": 1075.0,
                "total": 21925.0,
                "observaciones": "Sin cubiertos",
                "detalle": detalle,
                "texto_pedido": texto,
                "estado": "recibido",
            },
        )])

    def test_texto_completo_retiro_efectivo(self):
        resultado = crear(
            SupabaseFalso(datos_base()),
            observaciones="Tocar timbre",
        )
        self.assertEqual(
            resultado["texto_pedido"],
            "Pedido para Comercio de prueba\n\n"
            "Cliente: Ana Prueba\n"
            "WhatsApp: 3436123456\n\n"
            "1x Producto - $10.000\n\n"
            "Productos: $10.000\n"
            "Descuento efectivo (10%): -$1.000\n"
            "Total: $9.000\n\n"
            "Modalidad: Retiro\n"
            "Forma de pago: Efectivo\n"
            "Paga con: $20.000\n"
            "Cambio aproximado: $11.000\n\n"
            "Aclaración general: Tocar timbre",
        )

    def test_fallo_de_insercion_oculta_error_interno(self):
        db = SupabaseFalso(
            datos_base(),
            error_insercion=RuntimeError("secreto interno de base"),
        )
        with self.assertRaises(PedidoError) as contexto:
            crear(db)
        self.assertEqual(contexto.exception.status_code, 500)
        self.assertEqual(
            contexto.exception.mensaje,
            "No se pudo registrar el pedido.",
        )
        self.assertNotIn("secreto interno", contexto.exception.mensaje)

    def test_insercion_con_respuesta_vacia(self):
        db = SupabaseFalso(datos_base(), insercion_vacia=True)
        with self.assertRaises(PedidoError) as contexto:
            crear(db)
        self.assertEqual(contexto.exception.status_code, 500)
        self.assertEqual(
            contexto.exception.mensaje,
            "No se pudo confirmar el pedido.",
        )

    def test_cantidades_invalidas(self):
        for cantidad in (0, 100):
            with self.subTest(cantidad=cantidad):
                with self.assertRaisesRegex(PedidoError, "Cantidad de producto inválida"):
                    crear(
                        SupabaseFalso(datos_base()),
                        items=[{"id": "producto-1", "cantidad": cantidad}],
                    )

    def test_producto_inexistente_inactivo_y_no_disponible(self):
        casos = (
            ("inexistente", None, "Uno de los productos"),
            ("inactivo", "activo", "Producto ya no está disponible"),
            ("no disponible", "disponible", "Producto ya no está disponible"),
        )
        for nombre, campo, mensaje in casos:
            with self.subTest(caso=nombre):
                datos = datos_base()
                producto_id = "producto-inexistente"
                if campo:
                    datos["gastronomia_productos"][0][campo] = False
                    producto_id = "producto-1"
                with self.assertRaisesRegex(PedidoError, mensaje):
                    crear(
                        SupabaseFalso(datos),
                        items=[{"id": producto_id, "cantidad": 1}],
                    )

    def test_opciones_invalidas(self):
        casos = (
            ("inexistente", None, None, "opción.*no está disponible"),
            ("inactiva", "activo", None, "opción.*no está disponible"),
            ("no disponible", "disponible", None, "opción.*no está disponible"),
            ("grupo inactivo", None, "activo", "opción no corresponde"),
            ("otro producto", None, "producto_id", "opción no corresponde"),
        )
        for nombre, campo_opcion, campo_grupo, mensaje in casos:
            with self.subTest(caso=nombre):
                datos = datos_base()
                opcion_id = "opcion-inexistente"
                if nombre != "inexistente":
                    opcion_id = "opcion-1"
                    datos["gastronomia_opciones"] = [{
                        "id": opcion_id,
                        "grupo_id": "grupo-1",
                        "nombre": "Extra",
                        "precio_extra": 100,
                        "activo": True,
                        "disponible": True,
                    }]
                    datos["gastronomia_grupos_opciones"] = [{
                        "id": "grupo-1",
                        "producto_id": "producto-1",
                        "activo": True,
                    }]
                    if campo_opcion:
                        datos["gastronomia_opciones"][0][campo_opcion] = False
                    if campo_grupo == "activo":
                        datos["gastronomia_grupos_opciones"][0][campo_grupo] = False
                    elif campo_grupo == "producto_id":
                        datos["gastronomia_grupos_opciones"][0][campo_grupo] = "otro"
                with self.assertRaisesRegex(PedidoError, mensaje):
                    crear(
                        SupabaseFalso(datos),
                        items=[{
                            "id": "producto-1",
                            "cantidad": 1,
                            "opciones": [{"id": opcion_id}],
                        }],
                    )

    def test_modalidades_deshabilitadas(self):
        casos = (
            ("delivery", "acepta_delivery", "Delivery"),
            ("retiro", "acepta_retiro", "Retiro"),
        )
        for modalidad, campo, mensaje in casos:
            with self.subTest(modalidad=modalidad):
                datos = datos_base()
                datos["gastronomia_configuracion"][0][campo] = False
                with self.assertRaisesRegex(PedidoError, mensaje):
                    crear(
                        SupabaseFalso(datos),
                        modalidad=modalidad,
                        direccion="Dirección" if modalidad == "delivery" else "",
                    )

    def test_fake_filtra_por_comercio_e_ids(self):
        datos = datos_base()
        datos["comercios"][0]["id"] = "otro-comercio"
        with self.assertRaises(PedidoError) as contexto:
            crear(SupabaseFalso(datos))
        self.assertEqual(contexto.exception.status_code, 404)


class PedidoPublicoTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(gastronomia_bp)

        @self.app.before_request
        def identidad_falsa():
            g.analytics_visitante_id = "visitante-1"
            g.analytics_sesion_id = "sesion-1"

    def payload_valido(self, **cambios):
        payload = {
            "nombre": "Ana",
            "apellido": "Prueba",
            "whatsapp": "3436123456",
            "modalidad": "retiro",
            "direccion": "",
            "forma_pago": "efectivo",
            "paga_con": 20000,
            "detalle": [{"id": "producto-1", "cantidad": 1}],
        }
        payload.update(cambios)
        return payload

    @patch("gastronomia.routes.crear_pedido")
    def test_respuesta_json_publica_conserva_contrato(self, crear_mock):
        crear_mock.return_value = {
            "id": "pedido-1",
            "numero_pedido": 7,
            "created_at": "2026-09-02T15:00:00+00:00",
            "texto_pedido": "Pedido de prueba",
            "whatsapp_comercio": "5493430000000",
        }
        respuesta = self.app.test_client().post(
            "/gastronomia/comercio/comercio-1/pedido",
            json={
                "nombre": "Ana",
                "apellido": "Prueba",
                "whatsapp": "3436123456",
                "modalidad": "Retiro",
                "forma_pago": "Efectivo",
                "paga_con": 20000,
                "detalle": [{"id": "producto-1", "cantidad": 1}],
            },
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json(), {
            "ok": True,
            "pedido_id": "pedido-1",
            "numero_pedido": 7,
            "created_at": "2026-09-02T15:00:00+00:00",
            "texto_pedido": "Pedido de prueba",
            "whatsapp_comercio": "5493430000000",
        })
        argumentos = crear_mock.call_args.kwargs
        self.assertEqual(argumentos["modalidad"], "retiro")
        self.assertEqual(argumentos["forma_pago"], "efectivo")
        self.assertEqual(argumentos["visitante_id"], "visitante-1")
        self.assertEqual(argumentos["sesion_id"], "sesion-1")

    @patch("gastronomia.routes.crear_pedido")
    def test_pedido_error_preserva_mensaje_y_status_http(self, crear_mock):
        casos = (
            (400, "Error de validación."),
            (404, "Comercio no encontrado."),
            (500, "No se pudo registrar el pedido."),
        )
        for status, mensaje in casos:
            with self.subTest(status=status):
                crear_mock.side_effect = PedidoError(mensaje, status)
                respuesta = self.app.test_client().post(
                    "/gastronomia/comercio/comercio-1/pedido",
                    json=self.payload_valido(),
                )
                self.assertEqual(respuesta.status_code, status)
                self.assertEqual(respuesta.get_json(), {
                    "ok": False,
                    "error": mensaje,
                })

    @patch("gastronomia.routes.crear_pedido")
    def test_validaciones_publicas_no_llaman_al_servicio(self, crear_mock):
        casos = (
            ("nombre vacío", {"nombre": ""}, "Ingresá tu nombre."),
            ("apellido vacío", {"apellido": ""}, "Ingresá tu apellido."),
            ("WhatsApp vacío", {"whatsapp": ""}, "Ingresá tu WhatsApp."),
            (
                "teléfono inválido",
                {"whatsapp": "123"},
                "Ingresá un WhatsApp válido con característica. Ejemplo: 343 6123456.",
            ),
            (
                "delivery sin dirección",
                {"modalidad": "delivery", "direccion": ""},
                "Ingresá la dirección de entrega.",
            ),
            (
                "forma de pago inválida",
                {"forma_pago": "tarjeta"},
                "Elegí una forma de pago.",
            ),
            ("carrito vacío", {"detalle": []}, "El pedido está vacío."),
        )
        for nombre, cambios, mensaje in casos:
            with self.subTest(caso=nombre):
                crear_mock.reset_mock()
                respuesta = self.app.test_client().post(
                    "/gastronomia/comercio/comercio-1/pedido",
                    json=self.payload_valido(**cambios),
                )
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.get_json(), {
                    "ok": False,
                    "error": mensaje,
                })
                crear_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
