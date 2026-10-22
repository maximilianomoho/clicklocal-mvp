import ast
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


# Impide que la importación del servicio cargue .env o cree clientes remotos.
configuracion_falsa = ModuleType("config.supabase_config")
configuracion_falsa.supabase_admin = object()
sys.modules["config.supabase_config"] = configuracion_falsa

gastronomia_falsa = ModuleType("gastronomia")
gastronomia_falsa.__path__ = []
servicios_falsos = ModuleType("gastronomia.services")
servicios_falsos.__path__ = []
delivery_falso = ModuleType("gastronomia.services.delivery")


class DeliveryError(Exception):
    pass


delivery_falso.DeliveryError = DeliveryError
delivery_falso.seleccionar_franja = lambda franjas, distancia: None
sys.modules["gastronomia"] = gastronomia_falsa
sys.modules["gastronomia.services"] = servicios_falsos
sys.modules["gastronomia.services.delivery"] = delivery_falso

ruta_servicio = (
    Path(__file__).resolve().parents[1]
    / "gastronomia/services/pedidos.py"
)
especificacion = importlib.util.spec_from_file_location(
    "gastronomia.services.pedidos",
    ruta_servicio,
)
pedidos = importlib.util.module_from_spec(especificacion)
sys.modules[especificacion.name] = pedidos
especificacion.loader.exec_module(pedidos)

MODALIDADES_GESTION_BASE = pedidos.MODALIDADES_GESTION_BASE
PedidoError = pedidos.PedidoError
crear_pedido = pedidos.crear_pedido
confirmar_cobro_pedido_base = pedidos.confirmar_cobro_pedido_base
consultar_pedidos_solo_whatsapp = pedidos.consultar_pedidos_solo_whatsapp
consultar_pedidos_recibidos_base = pedidos.consultar_pedidos_recibidos_base
actualizar_estado_pedido_base = pedidos.actualizar_estado_pedido_base
validar_modalidad_gestion_base = pedidos.validar_modalidad_gestion_base


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla
        self.filtros = []
        self.insertado = None
        self.cambios = None
        self.rango = None
        self.ordenes = []

    def select(self, *args, **kwargs):
        return self

    def update(self, cambios):
        self.cambios = dict(cambios)
        return self

    def eq(self, columna, valor):
        self.filtros.append(("eq", columna, valor))
        return self

    def in_(self, columna, valores):
        self.filtros.append(("in", columna, valores))
        return self

    def neq(self, columna, valor):
        self.filtros.append(("neq", columna, valor))
        return self

    def gte(self, columna, valor):
        self.filtros.append(("gte", columna, valor))
        return self

    def lt(self, columna, valor):
        self.filtros.append(("lt", columna, valor))
        return self

    def is_(self, columna, valor):
        self.filtros.append(("is", columna, valor))
        return self

    def order(self, columna, desc=False):
        self.ordenes.append((columna, desc))
        return self

    def range(self, desde, hasta):
        self.rango = (desde, hasta)
        return self

    def limit(self, *args):
        return self

    def insert(self, datos):
        self.insertado = dict(datos)
        return self

    def execute(self):
        if self.insertado is not None:
            self.db.insertados.append((self.tabla, self.insertado))
            fila = {
                **self.insertado,
                "id": "pedido-1",
                "numero_pedido": 1,
                "created_at": "2026-10-16T12:00:00+00:00",
            }
            return SimpleNamespace(data=[fila])

        if self.cambios is not None and self.db.antes_de_actualizar:
            accion = self.db.antes_de_actualizar
            self.db.antes_de_actualizar = None
            accion(self.db)

        filas = list(self.db.datos.get(self.tabla, []))
        for operador, columna, valor in self.filtros:
            if operador == "eq":
                filas = [fila for fila in filas if fila.get(columna) == valor]
            elif operador == "neq":
                filas = [fila for fila in filas if fila.get(columna) != valor]
            elif operador == "gte":
                filas = [fila for fila in filas if fila.get(columna) >= valor]
            elif operador == "lt":
                filas = [fila for fila in filas if fila.get(columna) < valor]
            elif operador == "is":
                filas = [
                    fila for fila in filas
                    if valor == "null" and fila.get(columna) is None
                ]
            else:
                filas = [fila for fila in filas if fila.get(columna) in valor]
        if self.cambios is not None:
            for fila in filas:
                fila.update(self.cambios)
            return SimpleNamespace(data=filas, count=len(filas))
        total = len(filas)
        for columna, descendente in reversed(self.ordenes):
            filas.sort(key=lambda fila: str(fila.get(columna) or ""), reverse=descendente)
        if self.rango:
            filas = filas[self.rango[0]:self.rango[1] + 1]
        return SimpleNamespace(data=filas, count=total)


class BaseFalsa:
    def __init__(self):
        self.insertados = []
        self.antes_de_actualizar = None
        self.datos = {
            "comercios": [{
                "id": "comercio-1",
                "nombre_negocio": "Prueba",
                "whatsapp": "5493430000000",
            }],
            "gastronomia_configuracion": [{
                "comercio_id": "comercio-1",
                "activo": True,
                "acepta_delivery": True,
                "acepta_retiro": True,
                "pedido_minimo": 0,
                "costo_envio": 0,
                "delivery_distancia_activo": False,
                "delivery_franjas": [],
                "delivery_origen_direccion": None,
                "descuento_efectivo_pct": 0,
                "descuento_transferencia_pct": 0,
                "modalidad_pedidos": "solo_whatsapp",
            }],
            "gastronomia_productos": [{
                "id": "producto-1",
                "comercio_id": "comercio-1",
                "nombre": "Producto",
                "precio": 1000,
                "precio_promocional": None,
                "activo": True,
                "disponible": True,
            }],
            "gastronomia_opciones": [],
            "gastronomia_grupos_opciones": [],
            "gastronomia_pedidos": [],
        }

    def table(self, nombre):
        return ConsultaFalsa(self, nombre)


class FormularioFalso(dict):
    def getlist(self, clave):
        valor = self.get(clave, [])
        return valor if isinstance(valor, list) else [valor]


def cargar_funcion_rutas(nombre, extras):
    ruta = Path(__file__).resolve().parents[1] / "gastronomia/routes.py"
    modulo = ast.parse(ruta.read_text(encoding="utf-8"))
    funcion = next(
        nodo for nodo in modulo.body
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
        and nodo.name == nombre
    )
    funcion.decorator_list = []
    aislado = ast.Module(body=[funcion], type_ignores=[])
    ast.fix_missing_locations(aislado)
    namespace = dict(extras)
    exec(compile(aislado, str(ruta), "exec"), namespace)
    return namespace[nombre]


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
        "paga_con": 1000,
        "observaciones": "",
        "items": [{"id": "producto-1", "cantidad": 1, "opciones": []}],
        "cliente_supabase": db,
    }
    argumentos.update(cambios)
    return crear_pedido(**argumentos)


def pedido_base(
    pedido_id="pedido-1",
    comercio_id="comercio-1",
    modalidad_gestion="solo_whatsapp",
    estado="pendiente",
    estado_pago="pendiente",
    pagado_at=None,
    created_at="2026-10-17T12:00:00+00:00",
):
    return {
        "id": pedido_id,
        "numero_pedido": pedido_id,
        "comercio_id": comercio_id,
        "origen": "clicklocal",
        "modalidad_gestion": modalidad_gestion,
        "estado": estado,
        "estado_pago": estado_pago,
        "pagado_at": pagado_at,
        "total": 1000,
        "created_at": created_at,
    }


class ModalidadesGastronomiaAisladoTest(unittest.TestCase):
    def test_interfaz_base_es_lista_unica_paginada_y_sin_acciones(self):
        raiz = Path(__file__).resolve().parents[1]
        plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
            encoding="utf-8"
        )
        javascript = (raiz / "static/gastronomia/pedidos.js").read_text(
            encoding="utf-8"
        )

        base = plantilla.split('class="pedidos-recibidos"', 1)[1]
        self.assertIn("{% for pedido in pedidos_recibidos %}", base)
        self.assertIn("paginacion_recibidos.pagina", base)
        self.assertIn("pedido.detalle_items", base)
        self.assertNotIn("data-confirmar-cobro", base)
        self.assertNotIn("data-accion-estado", base)
        self.assertIn("pedidos-recibidos-cuerpo", javascript)

    def test_pos_conserva_cuatro_columnas_y_base_no_exige_modalidad(self):
        raiz = Path(__file__).resolve().parents[1]
        plantilla = (raiz / "templates/gastronomia/pedidos.html").read_text(
            encoding="utf-8"
        )
        rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
        tablero = rutas.split("def pedidos_gastronomia():", 1)[1].split(
            "def _consultar_ventas_clicklocal", 1
        )[0]

        self.assertIn("{% for estado in estados_kanban %}", plantilla)
        self.assertIn("grid-template-columns:repeat(4", (
            raiz / "static/gastronomia/pedidos.css"
        ).read_text(encoding="utf-8"))
        self.assertIn("if not pos_activo:", tablero)
        self.assertIn("consultar_pedidos_recibidos_base", tablero)
        bloque_base = plantilla.split('class="pedidos-recibidos"', 1)[1]
        self.assertNotIn("modalidad_pedidos", bloque_base)
        self.assertNotIn("Modalidad configurada:", plantilla)

    def test_historial_base_oculta_filtros_avanzados_y_conserva_consultas(self):
        plantilla = (
            Path(__file__).resolve().parents[1]
            / "templates/gastronomia/pedidos_historial.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn('<select name="modalidad">', plantilla)
        self.assertIn('<select name="vista">', plantilla)
        self.assertIn('<select name="tipo_entrega">', plantilla)
        self.assertIn("{% if pos_activo %}\n    <label>\n      Estado", plantilla)
        self.assertIn("{% if pos_activo %}\n    <label>\n      Pago", plantilla)
        self.assertNotIn("Confirmación:", plantilla)
        rutas = (Path(__file__).resolve().parents[1] / "gastronomia/routes.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('.is_("modalidad_gestion", "null")', rutas)
        self.assertIn('modalidad_pedidos == "sin_snapshot"', rutas)

    def test_historial_base_usa_grilla_modal_y_acciones_aisladas(self):
        raiz = Path(__file__).resolve().parents[1]
        plantilla = (
            raiz / "templates/gastronomia/pedidos_historial.html"
        ).read_text(encoding="utf-8")
        modal = (
            raiz / "templates/gastronomia/_pedido_modal_base.html"
        ).read_text(encoding="utf-8")
        javascript = (
            raiz / "static/gastronomia/pedido_acciones_base.js"
        ).read_text(encoding="utf-8")
        css = (
            raiz / "static/gastronomia/pedidos.css"
        ).read_text(encoding="utf-8")

        self.assertIn("pedidos-historial-base-grid", plantilla)
        self.assertIn("data-ver-pedido", plantilla)
        self.assertIn("{% if not pos_activo %}", plantilla)
        self.assertIn("_pedido_modal_base.html", plantilla)
        self.assertIn("pedido_acciones_base.js", plantilla)
        self.assertIn("data-imprimir-modal", modal)
        self.assertIn("data-compartir-modal", modal)
        self.assertNotIn("Copiar", modal)
        self.assertIn("https://wa.me/?text=", javascript)
        self.assertIn('window.open(url, "_blank", "noopener,noreferrer")', javascript)
        self.assertIn('window.print()', javascript)
        self.assertNotIn("navigator.clipboard", javascript)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", css)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", css)
        self.assertIn("body.pedido-base-imprimiendo *", css)
        self.assertIn("max-width:80mm", css)

    def test_lectura_modalidad_se_aísla_por_comercio(self):
        db = BaseFalsa()
        db.datos["gastronomia_configuracion"].append({
            "comercio_id": "comercio-2",
            "modalidad_pedidos": "bandeja",
        })
        leer = cargar_funcion_rutas("_modalidad_pedidos_comercio", {
            "supabase_admin": db,
            "validar_modalidad_gestion_base": validar_modalidad_gestion_base,
            "PedidoError": PedidoError,
        })

        self.assertEqual(leer("comercio-1"), "solo_whatsapp")
        self.assertEqual(leer("comercio-2"), "bandeja")

    def test_lectura_configuracion_null_usa_solo_whatsapp_sin_escribir(self):
        db = BaseFalsa()
        db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"] = None
        leer = cargar_funcion_rutas("_modalidad_pedidos_comercio", {
            "supabase_admin": db,
            "validar_modalidad_gestion_base": validar_modalidad_gestion_base,
            "PedidoError": PedidoError,
        })

        self.assertEqual(leer("comercio-1"), "solo_whatsapp")
        self.assertIsNone(
            db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"]
        )

    def test_guardado_modalidad_base_actualiza_solo_su_comercio(self):
        db = BaseFalsa()
        db.datos["gastronomia_configuracion"].append({
            "comercio_id": "comercio-2",
            "modalidad_pedidos": "solo_whatsapp",
        })
        formulario = FormularioFalso({
            "modalidad_pedidos": "bandeja",
            "acepta_retiro": "on",
            "pedido_minimo": "0",
            "costo_envio": "0",
            "tiempo_estimado_min": "30",
            "descuento_efectivo_pct": "0",
            "descuento_transferencia_pct": "0",
            "delivery_modalidad": "fijo",
        })
        guardar = cargar_funcion_rutas("guardar_configuracion_negocio", {
            "request": SimpleNamespace(form=formulario),
            "_comercio_panel_gastronomia": lambda: {"id": "comercio-1"},
            "_pos_activo_gastronomia": lambda _comercio_id: False,
            "validar_modalidad_gestion_base": validar_modalidad_gestion_base,
            "PedidoError": PedidoError,
            "supabase_admin": db,
            "_parsear_importe_config": lambda valor: float(valor or 0),
            "url_for": lambda endpoint, **parametros: f"/{endpoint}",
            "redirect": lambda destino: destino,
        })

        guardar()

        self.assertEqual(
            db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"],
            "bandeja",
        )
        self.assertEqual(
            db.datos["gastronomia_configuracion"][1]["modalidad_pedidos"],
            "solo_whatsapp",
        )

    def test_guardado_pos_no_escribe_modalidad_base(self):
        db = BaseFalsa()
        formulario = FormularioFalso({
            "modalidad_pedidos": "bandeja",
            "acepta_retiro": "on",
            "pedido_minimo": "0",
            "costo_envio": "0",
            "tiempo_estimado_min": "30",
            "descuento_efectivo_pct": "0",
            "descuento_transferencia_pct": "0",
            "delivery_modalidad": "fijo",
        })
        guardar = cargar_funcion_rutas("guardar_configuracion_negocio", {
            "request": SimpleNamespace(form=formulario),
            "_comercio_panel_gastronomia": lambda: {"id": "comercio-1"},
            "_pos_activo_gastronomia": lambda _comercio_id: True,
            "validar_modalidad_gestion_base": validar_modalidad_gestion_base,
            "PedidoError": PedidoError,
            "supabase_admin": db,
            "_parsear_importe_config": lambda valor: float(valor or 0),
            "url_for": lambda endpoint, **parametros: f"/{endpoint}",
            "redirect": lambda destino: destino,
        })

        guardar()

        self.assertEqual(
            db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"],
            "solo_whatsapp",
        )

    def test_migracion_es_estructural_nullable_y_reversible(self):
        raiz = Path(__file__).resolve().parents[1]
        migracion = (
            raiz / "sql/gastronomia_modalidades_pedidos_20261016.sql"
        ).read_text(encoding="utf-8").lower()
        rollback = (
            raiz
            / "sql/gastronomia_modalidades_pedidos_20261016_ROLLBACK.sql"
        ).read_text(encoding="utf-8").lower()

        self.assertIn("add column if not exists modalidad_pedidos text", migracion)
        self.assertIn("add column if not exists modalidad_gestion text", migracion)
        self.assertIn("update public.gastronomia_configuracion", migracion)
        self.assertIn("where modalidad_pedidos is null", migracion)
        self.assertNotIn("set not null", migracion)
        self.assertIn("set default 'solo_whatsapp'", migracion)
        self.assertNotIn("update public.gastronomia_pedidos", migracion)
        self.assertIn("drop column if exists modalidad_gestion", rollback)
        self.assertIn("drop column if exists modalidad_pedidos", rollback)

    def test_valida_unicamente_modalidades_base_aprobadas(self):
        self.assertEqual(
            MODALIDADES_GESTION_BASE,
            ("solo_whatsapp", "bandeja"),
        )
        self.assertEqual(
            validar_modalidad_gestion_base(" SOLO_WHATSAPP "),
            "solo_whatsapp",
        )
        self.assertEqual(
            validar_modalidad_gestion_base("bandeja"),
            "bandeja",
        )
        self.assertIsNone(
            validar_modalidad_gestion_base(None, permite_none=True)
        )
        with self.assertRaises(PedidoError):
            validar_modalidad_gestion_base("pos")

    def test_pedido_clicklocal_conserva_snapshot_modalidad(self):
        db = BaseFalsa()

        crear(db)

        tabla, pedido = db.insertados[-1]
        self.assertEqual(tabla, "gastronomia_pedidos")
        self.assertEqual(pedido["modalidad_gestion"], "solo_whatsapp")

    def test_modalidad_null_actual_crea_snapshot_solo_whatsapp(self):
        db = BaseFalsa()
        db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"] = None

        crear(db)

        self.assertEqual(
            db.insertados[-1][1]["modalidad_gestion"],
            "solo_whatsapp",
        )

    def test_snapshot_solicitado_debe_coincidir_con_configuracion(self):
        db = BaseFalsa()

        with self.assertRaises(PedidoError) as contexto:
            crear(db, modalidad_gestion="bandeja")

        self.assertEqual(contexto.exception.status_code, 409)
        self.assertEqual(db.insertados, [])

    def test_bandeja_se_conserva_como_snapshot_explicito(self):
        db = BaseFalsa()
        db.datos["gastronomia_configuracion"][0][
            "modalidad_pedidos"
        ] = "bandeja"

        crear(db)

        self.assertEqual(db.insertados[-1][1]["modalidad_gestion"], "bandeja")

    def test_codigo_anterior_se_conserva_pero_no_se_expone_en_base(self):
        raiz = Path(__file__).resolve().parents[1]
        rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
        servicio = (
            raiz / "gastronomia/services/pedidos.py"
        ).read_text(encoding="utf-8")
        plantilla = (
            raiz / "templates/gastronomia/pedidos.html"
        ).read_text(encoding="utf-8")
        javascript = (
            raiz / "static/gastronomia/pedidos.js"
        ).read_text(encoding="utf-8")

        self.assertIn('.eq("modalidad_gestion", "solo_whatsapp")', servicio)
        self.assertIn('("pendientes", "Pendientes de verificar")', rutas)
        self.assertIn('("cobrados", "Cobros confirmados")', rutas)
        self.assertIn('("cancelados", "Cancelados")', rutas)
        self.assertIn('"resultados": resultados', rutas)
        self.assertNotIn("data-confirmar-cobros", plantilla)
        self.assertNotIn("data-seleccionar-cobro", plantilla)
        self.assertIn("¿Confirmar el cobro de ${detalle}?", javascript)
        self.assertIn('.neq("estado_pago", "pagado")', servicio)

    def test_cancelacion_concurrente_impide_el_cobro(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [pedido_base()]
        db.antes_de_actualizar = lambda base: base.datos[
            "gastronomia_pedidos"
        ][0].update({"estado": "cancelado"})

        resultado = confirmar_cobro_pedido_base(
            db, "comercio-1", "pedido-1", "solo_whatsapp"
        )

        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["estado"], "cancelado")
        self.assertEqual(
            db.datos["gastronomia_pedidos"][0]["estado_pago"],
            "pendiente",
        )

    def test_cobro_concurrente_impide_la_cancelacion(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(modalidad_gestion="bandeja")
        ]
        db.antes_de_actualizar = lambda base: base.datos[
            "gastronomia_pedidos"
        ][0].update({"estado_pago": "pagado", "pagado_at": "primero"})

        filas = actualizar_estado_pedido_base(
            db,
            "comercio-1",
            "pedido-1",
            "pendiente",
            {"estado": "cancelado"},
            es_cancelacion=True,
        )

        self.assertEqual(filas, [])
        self.assertEqual(db.datos["gastronomia_pedidos"][0]["estado"], "pendiente")
        self.assertEqual(db.datos["gastronomia_pedidos"][0]["pagado_at"], "primero")

    def test_cobro_individual_y_doble_confirmacion_preservan_primera_fecha(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [pedido_base()]

        primero = confirmar_cobro_pedido_base(
            db, "comercio-1", "pedido-1", "solo_whatsapp", ahora="primero"
        )
        segundo = confirmar_cobro_pedido_base(
            db, "comercio-1", "pedido-1", "solo_whatsapp", ahora="segundo"
        )

        self.assertEqual(primero["estado"], "confirmado")
        self.assertEqual(segundo["estado"], "ya_confirmado")
        self.assertEqual(segundo["pagado_at"], "primero")

    def test_cobro_respeta_snapshot_y_aislamiento_por_comercio(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(modalidad_gestion="bandeja"),
            pedido_base(pedido_id="ajeno", comercio_id="comercio-2"),
        ]

        anterior = confirmar_cobro_pedido_base(
            db, "comercio-1", "pedido-1", "bandeja"
        )
        ajeno = confirmar_cobro_pedido_base(
            db, "comercio-1", "ajeno", "solo_whatsapp"
        )

        self.assertTrue(anterior["ok"])
        self.assertEqual(ajeno["estado"], "no_encontrado")
        self.assertEqual(db.datos["gastronomia_pedidos"][1]["estado_pago"], "pendiente")

    def test_cambio_modalidad_en_ambos_sentidos_no_invalida_snapshot(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(pedido_id="whatsapp"),
            pedido_base(pedido_id="bandeja", modalidad_gestion="bandeja"),
        ]

        db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"] = "bandeja"
        whatsapp = confirmar_cobro_pedido_base(
            db, "comercio-1", "whatsapp", "solo_whatsapp"
        )
        db.datos["gastronomia_configuracion"][0]["modalidad_pedidos"] = "solo_whatsapp"
        bandeja = confirmar_cobro_pedido_base(
            db, "comercio-1", "bandeja", "bandeja"
        )

        self.assertTrue(whatsapp["ok"])
        self.assertTrue(bandeja["ok"])

    def test_lote_ficticio_informa_exito_y_cancelado_por_separado(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(pedido_id="cobrable"),
            pedido_base(pedido_id="cancelado", estado="cancelado"),
        ]

        resultados = [
            confirmar_cobro_pedido_base(
                db, "comercio-1", pedido_id, "solo_whatsapp"
            )
            for pedido_id in ("cobrable", "cancelado")
        ]

        self.assertEqual(resultados[0]["estado"], "confirmado")
        self.assertEqual(resultados[1]["estado"], "cancelado")

    def test_paginacion_encuentra_pedidos_antiguos_sin_mezclar_filtros(self):
        ahora = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(
                pedido_id=f"pendiente-{indice:03d}",
                created_at=(ahora - timedelta(hours=indice)).isoformat(),
            )
            for indice in range(60)
        ] + [
            pedido_base(pedido_id="pagado", estado_pago="pagado"),
            pedido_base(pedido_id="cancelado", estado="cancelado"),
            pedido_base(pedido_id="otra-modalidad", modalidad_gestion="bandeja"),
            pedido_base(pedido_id="otro-comercio", comercio_id="comercio-2"),
        ]

        tercera, pagina = consultar_pedidos_solo_whatsapp(
            db,
            "comercio-1",
            "pendientes",
            3,
            por_pagina=25,
            ahora=ahora,
        )

        self.assertEqual(len(tercera), 10)
        self.assertEqual(pagina["total"], 60)
        self.assertEqual(pagina["total_paginas"], 3)
        self.assertTrue(all(p["estado_pago"] == "pendiente" for p in tercera))

    def test_pendientes_operativos_incluyen_168_horas_y_excluyen_mas_antiguos(self):
        ahora = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(
                pedido_id="reciente",
                created_at=(ahora - timedelta(hours=167, minutes=59)).isoformat(),
            ),
            pedido_base(
                pedido_id="limite",
                created_at=(ahora - timedelta(hours=168)).isoformat(),
            ),
            pedido_base(
                pedido_id="antiguo",
                created_at=(
                    ahora - timedelta(hours=168, seconds=1)
                ).isoformat(),
            ),
        ]

        filas, pagina = consultar_pedidos_solo_whatsapp(
            db,
            "comercio-1",
            "pendientes",
            1,
            ahora=ahora,
        )

        self.assertEqual({fila["id"] for fila in filas}, {"reciente", "limite"})
        self.assertEqual(pagina["total"], 2)

    def test_pedidos_recibidos_pagina_todos_los_snapshots_y_aísla_origen(self):
        db = BaseFalsa()
        db.datos["gastronomia_pedidos"] = [
            pedido_base(
                pedido_id=f"pedido-{indice:03d}",
                created_at=f"2026-10-{(indice % 28) + 1:02d}T12:00:00+00:00",
                modalidad_gestion=(None if indice == 0 else "solo_whatsapp"),
            )
            for indice in range(30)
        ] + [
            pedido_base(pedido_id="ajeno", comercio_id="comercio-2"),
            {**pedido_base(pedido_id="pos"), "origen": "pos"},
        ]

        segunda, pagina = consultar_pedidos_recibidos_base(
            db, "comercio-1", 2, por_pagina=25
        )

        self.assertEqual(len(segunda), 5)
        self.assertEqual(pagina["total"], 30)
        self.assertEqual(pagina["total_paginas"], 2)
        self.assertTrue(any(fila["modalidad_gestion"] is None for fila in segunda))
        self.assertTrue(all(fila["comercio_id"] == "comercio-1" for fila in segunda))
        self.assertTrue(all(fila["origen"] == "clicklocal" for fila in segunda))

    def test_altas_escriben_solo_whatsapp_explicitamente(self):
        raiz = Path(__file__).resolve().parents[1]
        rutas = (raiz / "gastronomia/routes.py").read_text(encoding="utf-8")
        aplicacion = (raiz / "app.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            rutas.count('"modalidad_pedidos": "solo_whatsapp"'),
            1,
        )
        self.assertIn('"modalidad_pedidos": "solo_whatsapp"', aplicacion)

    def test_pos_no_admite_modalidad_de_gestion_base(self):
        db = BaseFalsa()

        with self.assertRaises(PedidoError):
            crear(
                db,
                origen="pos",
                modalidad="mostrador",
                modalidad_gestion="bandeja",
                nombre="",
                apellido="",
                telefono="",
                telefono_normalizado="",
            )

        self.assertEqual(db.insertados, [])


if __name__ == "__main__":
    unittest.main()
