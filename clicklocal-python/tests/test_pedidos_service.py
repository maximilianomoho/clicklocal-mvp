import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g

from gastronomia import gastronomia_bp
from gastronomia.services.pedidos import (
    ESTADOS_PAGO,
    ESTADOS_PEDIDO,
    PedidoError,
    construir_idempotency_fingerprint,
    crear_pedido,
    pedido_es_venta,
    preparar_actualizacion_estados,
    preparar_transicion_pedido,
    validar_estado_pago,
    validar_estado_pedido,
)


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
            fila = dict(self.datos_insertados)
            fila.setdefault("id", f"pedido-{len(self.db.insertados)}")
            fila.setdefault("numero_pedido", 6 + len(self.db.insertados))
            fila.setdefault("created_at", "2026-09-02T15:00:00+00:00")
            self.db.datos.setdefault(self.tabla, []).append(fila)
            return SimpleNamespace(data=[fila])
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


class ConsultaConflicto(ConsultaFalsa):
    def execute(self):
        if self.datos_insertados is not None:
            ganador = dict(self.datos_insertados)
            ganador.update({
                "id": "pedido-ganador",
                "numero_pedido": 19,
                "created_at": "2026-09-16T17:00:00+00:00",
            })
            self.db.datos.setdefault(self.tabla, []).append(ganador)
            raise RuntimeError("duplicate key value violates unique constraint")
        return super().execute()


class SupabaseConflicto(SupabaseFalso):
    def table(self, nombre):
        return ConsultaConflicto(self, nombre)


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
            "delivery_distancia_activo": False,
            "delivery_franjas": [],
            "delivery_origen_direccion": None,
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
    def test_fingerprint_es_estable_sin_importar_orden(self):
        argumentos = {
            "comercio_id": "comercio-1",
            "nombre": " Ana ",
            "apellido": "Prueba",
            "telefono_normalizado": "3436123456",
            "modalidad": "Retiro",
            "direccion": "",
            "forma_pago": "Efectivo",
            "paga_con": "20000.00",
            "observaciones": "Sin sal",
            "items": [
                {
                    "id": "producto-2",
                    "cantidad": 1,
                    "opciones": [{"id": "b"}, {"id": "a"}],
                },
                {"id": "producto-1", "cantidad": 2, "opciones": []},
            ],
            "cotizacion_delivery": "",
        }
        primero = construir_idempotency_fingerprint(**argumentos)
        argumentos["items"] = list(reversed(argumentos["items"]))
        argumentos["items"][1]["opciones"] = [{"id": "a"}, {"id": "b"}]
        segundo = construir_idempotency_fingerprint(**argumentos)
        self.assertEqual(primero, segundo)

    def test_retry_idempotente_devuelve_original_sin_segundo_insert(self):
        db = SupabaseFalso(datos_base())
        clave = "11111111-1111-4111-8111-111111111111"
        fingerprint = "a" * 64
        primero = crear(
            db,
            idempotency_key=clave,
            idempotency_fingerprint=fingerprint,
        )
        segundo = crear(
            db,
            idempotency_key=clave,
            idempotency_fingerprint=fingerprint,
        )
        self.assertEqual(primero["id"], segundo["id"])
        self.assertEqual(primero["numero_pedido"], segundo["numero_pedido"])
        self.assertTrue(segundo["idempotent_replay"])
        self.assertEqual(len(db.insertados), 1)

    def test_misma_clave_con_otro_fingerprint_da_409(self):
        db = SupabaseFalso(datos_base())
        clave = "11111111-1111-4111-8111-111111111111"
        crear(
            db,
            idempotency_key=clave,
            idempotency_fingerprint="a" * 64,
        )
        with self.assertRaises(PedidoError) as contexto:
            crear(
                db,
                idempotency_key=clave,
                idempotency_fingerprint="b" * 64,
            )
        self.assertEqual(contexto.exception.status_code, 409)
        self.assertEqual(len(db.insertados), 1)

    def test_clave_nueva_permite_mismo_pedido_legitimo(self):
        db = SupabaseFalso(datos_base())
        for clave in (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        ):
            crear(
                db,
                idempotency_key=clave,
                idempotency_fingerprint="a" * 64,
            )
        self.assertEqual(len(db.insertados), 2)

    def test_misma_clave_es_valida_en_otro_comercio(self):
        datos = datos_base()
        datos["comercios"].append({
            "id": "comercio-2",
            "nombre_negocio": "Otro comercio",
            "whatsapp": "5493431111111",
        })
        segunda_config = dict(datos["gastronomia_configuracion"][0])
        segunda_config["comercio_id"] = "comercio-2"
        datos["gastronomia_configuracion"].append(segunda_config)
        segundo_producto = dict(datos["gastronomia_productos"][0])
        segundo_producto.update({"id": "producto-2", "comercio_id": "comercio-2"})
        datos["gastronomia_productos"].append(segundo_producto)
        db = SupabaseFalso(datos)
        clave = "11111111-1111-4111-8111-111111111111"
        crear(db, idempotency_key=clave, idempotency_fingerprint="a" * 64)
        crear(
            db,
            comercio_id="comercio-2",
            items=[{"id": "producto-2", "cantidad": 1, "opciones": []}],
            idempotency_key=clave,
            idempotency_fingerprint="b" * 64,
        )
        self.assertEqual(len(db.insertados), 2)

    def test_conflicto_concurrente_recupera_al_ganador(self):
        db = SupabaseConflicto(datos_base())
        resultado = crear(
            db,
            idempotency_key="11111111-1111-4111-8111-111111111111",
            idempotency_fingerprint="a" * 64,
        )
        self.assertEqual(resultado["id"], "pedido-ganador")
        self.assertEqual(resultado["numero_pedido"], 19)
        self.assertTrue(resultado["idempotent_replay"])

    def test_delivery_por_distancia_guarda_cotizacion_validada(self):
        datos = datos_base()
        datos["gastronomia_configuracion"][0]["delivery_distancia_activo"] = True
        datos["gastronomia_configuracion"][0]["delivery_franjas"] = [
            {"hasta_km": 4, "precio": 2500},
        ]
        db = SupabaseFalso(datos)
        resultado = crear(
            db,
            modalidad="delivery",
            direccion="Perú 50",
            cotizacion_delivery={"distancia_m": 3200, "costo_envio": 2500},
        )
        self.assertEqual(resultado["costo_envio"], 2500)
        self.assertEqual(resultado["total"], 11500)
        self.assertEqual(db.insertados[0][1]["delivery_distancia_m"], 3200)

    def test_delivery_por_distancia_exige_cotizacion(self):
        datos = datos_base()
        datos["gastronomia_configuracion"][0]["delivery_distancia_activo"] = True
        datos["gastronomia_configuracion"][0]["delivery_franjas"] = [
            {"hasta_km": 4, "precio": 2500},
        ]
        with self.assertRaisesRegex(PedidoError, "Calculá el envío"):
            crear(
                SupabaseFalso(datos),
                modalidad="delivery",
                direccion="Perú 50",
            )

    def test_delivery_por_distancia_rechaza_costo_que_no_coincide_con_franja(self):
        datos = datos_base()
        datos["gastronomia_configuracion"][0].update({
            "delivery_distancia_activo": True,
            "delivery_franjas": [{"hasta_km": 4, "precio": 2500}],
        })
        with self.assertRaisesRegex(PedidoError, "cotización cambió"):
            crear(
                SupabaseFalso(datos),
                modalidad="delivery",
                direccion="Perú 50",
                cotizacion_delivery={"distancia_m": 3200, "costo_envio": 1},
            )

    def test_retiro_no_guarda_distancia(self):
        db = SupabaseFalso(datos_base())
        crear(db)
        pedido = db.insertados[0][1]
        self.assertEqual(pedido["costo_envio"], 0)
        self.assertIsNone(pedido["delivery_distancia_m"])

    def test_transiciones_operativas(self):
        self.assertEqual(
            preparar_transicion_pedido("pendiente", "avanzar")["estado"],
            "marchando",
        )
        self.assertEqual(
            preparar_transicion_pedido("marchando", "avanzar")["estado"],
            "preparado",
        )
        self.assertEqual(
            preparar_transicion_pedido("preparado", "avanzar")["estado"],
            "cerrado",
        )
        self.assertEqual(
            preparar_transicion_pedido("cerrado", "retroceder")["estado"],
            "preparado",
        )
        self.assertEqual(
            preparar_transicion_pedido("marchando", "cancelar")["estado"],
            "cancelado",
        )

    def test_transiciones_invalidas(self):
        casos = (
            ("pendiente", "retroceder"),
            ("cerrado", "avanzar"),
            ("cerrado", "cancelar"),
            ("cancelado", "avanzar"),
        )
        for estado, accion in casos:
            with self.subTest(estado=estado, accion=accion):
                with self.assertRaises(PedidoError):
                    preparar_transicion_pedido(estado, accion)

    def test_regla_minima_de_ventas(self):
        self.assertTrue(pedido_es_venta({
            "estado": "cerrado",
            "estado_pago": "pagado",
        }))
        self.assertTrue(pedido_es_venta({
            "estado": "preparado",
            "estado_pago": "pagado",
        }))
        self.assertTrue(pedido_es_venta({
            "estado": "pendiente",
            "estado_pago": "pagado",
        }))
        self.assertFalse(pedido_es_venta({
            "estado": "cancelado",
            "estado_pago": "pagado",
        }))
        self.assertFalse(pedido_es_venta({
            "estado": "cerrado",
            "estado_pago": "pendiente",
        }))

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

    def test_precio_del_cliente_se_ignora_y_se_recalcula(self):
        resultado = crear(
            SupabaseFalso(datos_base()),
            items=[{
                "id": "producto-1",
                "cantidad": 2,
                "precio_unitario": 1,
                "subtotal": 2,
                "opciones": [],
            }],
        )
        self.assertEqual(resultado["subtotal"], 20000)

    def test_varios_productos_cantidades_y_extras_suman_el_total(self):
        datos = datos_base()
        datos["gastronomia_productos"].append({
            "id": "producto-2",
            "comercio_id": "comercio-1",
            "nombre": "Bebida",
            "precio": 2000,
            "precio_promocional": 1500,
            "activo": True,
            "disponible": True,
        })
        datos["gastronomia_opciones"] = [{
            "id": "opcion-1",
            "grupo_id": "grupo-1",
            "nombre": "Extra",
            "precio_extra": 500,
            "activo": True,
            "disponible": True,
        }]
        datos["gastronomia_grupos_opciones"] = [{
            "id": "grupo-1",
            "producto_id": "producto-1",
            "nombre": "Extras",
            "minimo": 0,
            "maximo": 2,
            "activo": True,
        }]
        resultado = crear(
            SupabaseFalso(datos),
            forma_pago="transferencia",
            paga_con=None,
            aplicar_condiciones_comerciales=False,
            items=[
                {
                    "id": "producto-1",
                    "cantidad": 2,
                    "opciones": [{"id": "opcion-1"}],
                },
                {"id": "producto-2", "cantidad": 3, "opciones": []},
            ],
        )
        self.assertEqual(resultado["subtotal"], 25500)
        self.assertEqual(resultado["total"], 25500)
        self.assertEqual(len(resultado["detalle"]), 2)

    def test_minimos_y_maximos_de_extras_se_validan_en_servidor(self):
        datos = datos_base()
        datos["gastronomia_grupos_opciones"] = [{
            "id": "grupo-1",
            "producto_id": "producto-1",
            "nombre": "Salsas",
            "minimo": 1,
            "maximo": 1,
            "activo": True,
        }]
        datos["gastronomia_opciones"] = [
            {
                "id": "opcion-1",
                "grupo_id": "grupo-1",
                "nombre": "Verdeo",
                "precio_extra": 500,
                "activo": True,
                "disponible": True,
            },
            {
                "id": "opcion-2",
                "grupo_id": "grupo-1",
                "nombre": "Queso",
                "precio_extra": 700,
                "activo": True,
                "disponible": True,
            },
        ]
        with self.assertRaisesRegex(PedidoError, "elegir al menos 1"):
            crear(SupabaseFalso(datos))
        with self.assertRaisesRegex(PedidoError, "elegir hasta 1"):
            crear(
                SupabaseFalso(datos),
                items=[{
                    "id": "producto-1",
                    "cantidad": 1,
                    "opciones": [{"id": "opcion-1"}, {"id": "opcion-2"}],
                }],
            )

    def test_pos_no_aplica_minimo_descuentos_ni_envio(self):
        datos = datos_base()
        datos["gastronomia_configuracion"][0]["pedido_minimo"] = 50000
        resultado = crear(
            SupabaseFalso(datos),
            origen="pos",
            modalidad="mostrador",
            forma_pago="transferencia",
            paga_con=None,
            aplicar_condiciones_comerciales=False,
        )
        self.assertEqual(resultado["subtotal"], 10000)
        self.assertEqual(resultado["descuento"], 0)
        self.assertEqual(resultado["costo_envio"], 0)
        self.assertEqual(resultado["total"], 10000)

    def test_pos_guarda_estados_y_timestamps_iniciales(self):
        for forma_pago in ("efectivo", "qr", "debito", "credito"):
            with self.subTest(forma_pago=forma_pago):
                db = SupabaseFalso(datos_base())
                crear(
                    db,
                    origen="pos",
                    modalidad="mostrador",
                    forma_pago=forma_pago,
                    paga_con=None,
                    aplicar_condiciones_comerciales=False,
                    estado_inicial="cerrado",
                    estado_pago_inicial="pagado",
                )
                pedido = db.insertados[0][1]
                self.assertEqual(pedido["estado"], "cerrado")
                self.assertEqual(pedido["estado_pago"], "pagado")
                self.assertTrue(pedido["cerrado_at"])
                self.assertTrue(pedido["pagado_at"])

        db = SupabaseFalso(datos_base())
        crear(
            db,
            origen="pos",
            modalidad="mostrador",
            forma_pago="transferencia",
            paga_con=None,
            aplicar_condiciones_comerciales=False,
            estado_inicial="pendiente",
            estado_pago_inicial="pagado",
        )
        pedido = db.insertados[0][1]
        self.assertEqual(pedido["estado"], "pendiente")
        self.assertEqual(pedido["estado_pago"], "pagado")
        self.assertNotIn("cerrado_at", pedido)
        self.assertTrue(pedido["pagado_at"])

    def test_forma_pago_invalida_se_rechaza_en_servicio(self):
        with self.assertRaisesRegex(PedidoError, "Forma de pago inválida"):
            crear(SupabaseFalso(datos_base()), forma_pago="cheque")

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
                "origen": "clicklocal",
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
                "delivery_distancia_m": None,
                "descuento": 1075.0,
                "total": 21925.0,
                "observaciones": "Sin cubiertos",
                "detalle": detalle,
                "texto_pedido": texto,
                "estado": "pendiente",
                "estado_pago": "pendiente",
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
        for cantidad in (0, 1.5, 100):
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

    def test_contrato_inicial_del_pedido_publico(self):
        db = SupabaseFalso(datos_base())
        crear(db)
        payload = db.insertados[0][1]
        self.assertEqual(payload["estado"], "pendiente")
        self.assertEqual(payload["estado_pago"], "pendiente")
        self.assertEqual(payload["origen"], "clicklocal")
        self.assertEqual(payload["numero_pedido"], 0)

    def test_cliente_opcional_para_origen_interno(self):
        db = SupabaseFalso(datos_base())
        crear(
            db,
            origen="pos",
            nombre=None,
            apellido=None,
            telefono=None,
            telefono_normalizado=None,
        )
        payload = db.insertados[0][1]
        self.assertEqual(payload["origen"], "pos")
        self.assertEqual(payload["nombre_cliente"], "")
        self.assertEqual(payload["telefono_cliente"], "")
        self.assertNotIn("idempotency_key", payload)
        self.assertNotIn("idempotency_fingerprint", payload)

    def test_servicio_mantiene_cliente_obligatorio_para_clicklocal(self):
        for campo in ("nombre", "telefono", "telefono_normalizado"):
            with self.subTest(campo=campo):
                with self.assertRaises(PedidoError):
                    crear(SupabaseFalso(datos_base()), **{campo: ""})

    def test_estados_validos_e_invalidos(self):
        for estado in ESTADOS_PEDIDO:
            self.assertEqual(validar_estado_pedido(estado), estado)
        with self.assertRaisesRegex(PedidoError, "Estado de pedido inválido"):
            validar_estado_pedido("en_camino")

    def test_estados_pago_validos_e_invalidos(self):
        for estado_pago in ESTADOS_PAGO:
            self.assertEqual(validar_estado_pago(estado_pago), estado_pago)
        with self.assertRaisesRegex(PedidoError, "Estado de pago inválido"):
            validar_estado_pago("parcial")

    def test_actualizacion_estado_pago_mantiene_pagado_at(self):
        instante = "2026-09-07T12:00:00+00:00"
        self.assertEqual(
            preparar_actualizacion_estados(
                estado_pago="pagado",
                ahora=instante,
            ),
            {"estado_pago": "pagado", "pagado_at": instante},
        )
        self.assertEqual(
            preparar_actualizacion_estados(estado_pago="pendiente"),
            {"estado_pago": "pendiente", "pagado_at": None},
        )

    def test_actualizacion_estado_mantiene_cerrado_at(self):
        instante = "2026-09-07T12:00:00+00:00"
        self.assertEqual(
            preparar_actualizacion_estados(
                estado="cerrado",
                ahora=instante,
            ),
            {"estado": "cerrado", "cerrado_at": instante},
        )
        self.assertEqual(
            preparar_actualizacion_estados(estado="preparado"),
            {"estado": "preparado", "cerrado_at": None},
        )


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
            "idempotency_key": "11111111-1111-4111-8111-111111111111",
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
    def test_uuid_invalido_devuelve_400(self, crear_mock):
        respuesta = self.app.test_client().post(
            "/gastronomia/comercio/comercio-1/pedido",
            json=self.payload_valido(idempotency_key="no-es-un-uuid"),
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(
            respuesta.get_json()["error"],
            "La identificación de la solicitud es inválida.",
        )
        crear_mock.assert_not_called()

    @patch("gastronomia.routes.buscar_pedido_idempotente")
    @patch("gastronomia.routes.crear_pedido")
    def test_replay_devuelve_el_pedido_original(
        self, crear_mock, buscar_mock
    ):
        buscar_mock.return_value = {
            "id": "pedido-original",
            "numero_pedido": 31,
            "created_at": "2026-09-16T17:00:00+00:00",
            "texto_pedido": "Texto original",
            "whatsapp_comercio": "5493430000000",
            "idempotent_replay": True,
        }
        respuesta = self.app.test_client().post(
            "/gastronomia/comercio/comercio-1/pedido",
            json=self.payload_valido(),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json(), {
            "ok": True,
            "pedido_id": "pedido-original",
            "numero_pedido": 31,
            "created_at": "2026-09-16T17:00:00+00:00",
            "texto_pedido": "Texto original",
            "whatsapp_comercio": "5493430000000",
            "idempotent_replay": True,
        })
        crear_mock.assert_not_called()

    @patch("gastronomia.routes.buscar_pedido_idempotente")
    @patch("gastronomia.routes.crear_pedido")
    def test_clave_reutilizada_con_otros_datos_devuelve_409(
        self, crear_mock, buscar_mock
    ):
        buscar_mock.side_effect = PedidoError(
            "La solicitud de pedido ya fue utilizada con otros datos.",
            409,
        )
        respuesta = self.app.test_client().post(
            "/gastronomia/comercio/comercio-1/pedido",
            json=self.payload_valido(nombre="Otra persona"),
        )
        self.assertEqual(respuesta.status_code, 409)
        crear_mock.assert_not_called()

    @patch("gastronomia.routes.buscar_pedido_idempotente", return_value=None)
    @patch("gastronomia.routes.crear_pedido")
    def test_respuesta_json_publica_conserva_contrato(
        self, crear_mock, buscar_mock
    ):
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
                "idempotency_key": "11111111-1111-4111-8111-111111111111",
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
            "idempotent_replay": False,
        })
        argumentos = crear_mock.call_args.kwargs
        self.assertEqual(argumentos["modalidad"], "retiro")
        self.assertEqual(argumentos["forma_pago"], "efectivo")
        self.assertEqual(argumentos["visitante_id"], "visitante-1")
        self.assertEqual(argumentos["sesion_id"], "sesion-1")
        self.assertEqual(
            argumentos["idempotency_key"],
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(len(argumentos["idempotency_fingerprint"]), 64)
        buscar_mock.assert_called_once()

    @patch("gastronomia.routes.buscar_pedido_idempotente", return_value=None)
    @patch("gastronomia.routes.crear_pedido")
    def test_pedido_error_preserva_mensaje_y_status_http(
        self, crear_mock, _buscar_mock
    ):
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

    @patch("gastronomia.routes.buscar_pedido_idempotente", return_value=None)
    @patch("gastronomia.routes.crear_pedido")
    def test_validaciones_publicas_no_llaman_al_servicio(
        self, crear_mock, _buscar_mock
    ):
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
