from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import modulos


HOY = date(2026, 9, 10)


class ConsultaFalsa:
    def __init__(self, filas=None, error=None):
        self.filas = filas
        self.error = error

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.filas)


class SupabaseFalso:
    def __init__(self, filas=None, error=None):
        self.consulta = ConsultaFalsa(filas, error)

    def table(self, nombre):
        assert nombre == "comercio_modulos"
        return self.consulta


def fila(**cambios):
    datos = {
        "activo": True,
        "fecha_activacion": "2026-08-01",
        "fecha_vencimiento": "2026-09-20",
        "aviso_dias_antes": 5,
        "gracia_dias": 5,
    }
    datos.update(cambios)
    return datos


def evaluar(monkeypatch, datos, fecha_actual=HOY):
    monkeypatch.setattr(
        modulos,
        "supabase_admin",
        SupabaseFalso(datos),
    )
    return modulos.evaluar_vigencia_modulo(
        "comercio-1",
        "turnos",
        fecha_actual=fecha_actual,
    )


def test_fila_inexistente_falla_cerrada(monkeypatch):
    resultado = evaluar(monkeypatch, [])
    assert resultado["existe"] is False
    assert resultado["acceso_operativo"] is False
    assert resultado["motivo_bloqueo"] == "modulo_no_asignado"


def test_activo_false_suspende_inmediatamente(monkeypatch):
    resultado = evaluar(monkeypatch, [fila(activo=False)])
    assert resultado["habilitado_manual"] is False
    assert resultado["acceso_operativo"] is False
    assert resultado["acceso_publico"] is False
    assert resultado["motivo_bloqueo"] == "suspension_manual"


def test_activo_sin_vencimiento_es_indefinido(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila(fecha_activacion=None, fecha_vencimiento=None)],
    )
    assert resultado["estado_vigencia"] == "activo"
    assert resultado["dias_restantes"] is None
    assert resultado["fecha_fin_gracia"] is None
    assert resultado["acceso_operativo"] is True


def test_vigencia_normal(monkeypatch):
    resultado = evaluar(monkeypatch, [fila()])
    assert resultado["estado_vigencia"] == "activo"
    assert resultado["dias_restantes"] == 10
    assert resultado["inicio_aviso"] == date(2026, 9, 15)


def test_primer_dia_de_aviso(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila()],
        date(2026, 9, 15),
    )
    assert resultado["estado_vigencia"] == "por_vencer"
    assert resultado["dias_restantes"] == 5
    assert resultado["acceso_operativo"] is True


def test_dia_de_vencimiento_funciona(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila()],
        date(2026, 9, 20),
    )
    assert resultado["estado_vigencia"] == "por_vencer"
    assert resultado["dias_restantes"] == 0
    assert resultado["acceso_operativo"] is True


def test_primer_dia_de_gracia(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila()],
        date(2026, 9, 21),
    )
    assert resultado["estado_vigencia"] == "en_gracia"
    assert resultado["fecha_fin_gracia"] == date(2026, 9, 25)
    assert resultado["dias_gracia_restantes"] == 4
    assert resultado["acceso_operativo"] is True


def test_ultimo_dia_de_gracia_funciona(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila()],
        date(2026, 9, 25),
    )
    assert resultado["estado_vigencia"] == "en_gracia"
    assert resultado["dias_gracia_restantes"] == 0
    assert resultado["acceso_publico"] is True


def test_primer_dia_vencido_bloquea(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila()],
        date(2026, 9, 26),
    )
    assert resultado["estado_vigencia"] == "vencido"
    assert resultado["acceso_operativo"] is False
    assert resultado["acceso_publico"] is False
    assert resultado["motivo_bloqueo"] == "vigencia_vencida"


def test_aviso_cero_empieza_el_dia_de_vencimiento(monkeypatch):
    antes = evaluar(
        monkeypatch,
        [fila(aviso_dias_antes=0)],
        date(2026, 9, 19),
    )
    vence = evaluar(
        monkeypatch,
        [fila(aviso_dias_antes=0)],
        date(2026, 9, 20),
    )
    assert antes["estado_vigencia"] == "activo"
    assert vence["estado_vigencia"] == "por_vencer"


def test_gracia_cero_bloquea_dia_siguiente(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila(gracia_dias=0)],
        date(2026, 9, 21),
    )
    assert resultado["estado_vigencia"] == "vencido"
    assert resultado["fecha_fin_gracia"] == date(2026, 9, 20)
    assert resultado["acceso_operativo"] is False


def test_fecha_de_activacion_futura_bloquea(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila(fecha_activacion="2026-09-11")],
    )
    assert resultado["estado_vigencia"] == "activo"
    assert resultado["acceso_operativo"] is False
    assert resultado["motivo_bloqueo"] == "vigencia_no_iniciada"


def test_error_de_supabase_falla_cerrada(monkeypatch):
    monkeypatch.setattr(
        modulos,
        "supabase_admin",
        SupabaseFalso(error=RuntimeError("sin conexión")),
    )
    resultado = modulos.evaluar_vigencia_modulo(
        "comercio-1",
        "turnos",
        fecha_actual=HOY,
    )
    assert resultado["existe"] is False
    assert resultado["acceso_operativo"] is False
    assert resultado["motivo_bloqueo"] == "error_consulta"


def test_fecha_actual_respeta_zona_horaria_argentina(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila(fecha_vencimiento="2026-09-09", gracia_dias=0)],
        datetime(2026, 9, 10, 1, 30, tzinfo=timezone.utc),
    )
    assert resultado["estado_vigencia"] == "por_vencer"
    assert resultado["dias_restantes"] == 0
    assert resultado["acceso_operativo"] is True


def test_modulo_activo_delega_y_devuelve_booleano(monkeypatch):
    esperado = {"acceso_operativo": True}
    llamadas = []

    def evaluador(comercio_id, slug):
        llamadas.append((comercio_id, slug))
        return esperado

    monkeypatch.setattr(modulos, "evaluar_vigencia_modulo", evaluador)
    assert modulos.modulo_activo("comercio-1", "turnos") is True
    assert llamadas == [("comercio-1", "turnos")]


def test_alta_inicial_un_mes():
    periodo = modulos.calcular_periodo_renovado(None, 1, HOY)
    assert periodo["fecha_activacion"] == HOY
    assert periodo["fecha_vencimiento"] == date(2026, 10, 10)


def test_alta_inicial_tres_meses():
    periodo = modulos.calcular_periodo_renovado(None, 3, HOY)
    assert periodo["fecha_vencimiento"] == date(2026, 12, 10)


def test_alta_inicial_seis_meses():
    periodo = modulos.calcular_periodo_renovado(None, 6, HOY)
    assert periodo["fecha_vencimiento"] == date(2027, 3, 10)


def test_duracion_invalida_se_rechaza():
    try:
        modulos.calcular_periodo_renovado(None, 2, HOY)
    except ValueError:
        pass
    else:
        raise AssertionError("Una duración inválida debe rechazarse")


def test_fila_sin_vencimiento_inicia_periodo_hoy():
    periodo = modulos.calcular_periodo_renovado(
        fila(fecha_activacion="2026-01-01", fecha_vencimiento=None),
        1,
        HOY,
    )
    assert periodo["fecha_activacion"] == HOY
    assert periodo["fecha_vencimiento"] == date(2026, 10, 10)


def test_renovacion_anticipada_no_pierde_dias():
    periodo = modulos.calcular_periodo_renovado(
        fila(fecha_vencimiento="2026-10-20"),
        1,
        HOY,
    )
    assert periodo["fecha_activacion"] == date(2026, 8, 1)
    assert periodo["fecha_vencimiento"] == date(2026, 11, 20)


def test_renovacion_durante_gracia_usa_vencimiento_contractual():
    periodo = modulos.calcular_periodo_renovado(
        fila(fecha_vencimiento="2026-09-08", gracia_dias=5),
        1,
        HOY,
    )
    assert periodo["fecha_activacion"] == date(2026, 8, 1)
    assert periodo["fecha_vencimiento"] == date(2026, 10, 8)


def test_renovacion_fuera_de_gracia_empieza_hoy():
    periodo = modulos.calcular_periodo_renovado(
        fila(fecha_vencimiento="2026-09-04", gracia_dias=5),
        1,
        HOY,
    )
    assert periodo["fecha_activacion"] == HOY
    assert periodo["fecha_vencimiento"] == date(2026, 10, 10)


def test_calculo_de_renovacion_no_administra_activo():
    periodo = modulos.calcular_periodo_renovado(
        fila(activo=False),
        1,
        HOY,
    )
    assert "activo" not in periodo


def test_meses_calendario_y_fin_de_mes():
    assert modulos.sumar_meses_calendario(
        date(2026, 9, 4), 3
    ) == date(2026, 12, 4)
    assert modulos.sumar_meses_calendario(
        date(2026, 1, 31), 1
    ) == date(2026, 2, 28)
    assert modulos.sumar_meses_calendario(
        date(2028, 1, 31), 1
    ) == date(2028, 2, 29)


class SupabaseEscrituraFalso:
    def __init__(self, filas):
        self.filas = filas
        self.operacion = None
        self.datos_guardados = None

    def table(self, nombre):
        assert nombre == "comercio_modulos"
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def update(self, datos):
        self.operacion = "update"
        self.datos_guardados = datos
        return self

    def insert(self, datos):
        self.operacion = "insert"
        self.datos_guardados = datos
        return self

    def execute(self):
        return SimpleNamespace(data=self.filas)


def test_renovar_exige_instalacion(monkeypatch):
    supabase = SupabaseEscrituraFalso([])
    monkeypatch.setattr(modulos, "supabase_admin", supabase)

    try:
        modulos.activar_renovar_modulo(
            "comercio-1", "turnos", 1, fecha_actual=HOY
        )
    except LookupError:
        pass
    else:
        raise AssertionError("Renovar no debe instalar implícitamente")
    assert supabase.operacion is None


def test_renovacion_preserva_aviso_y_gracia(monkeypatch):
    supabase = SupabaseEscrituraFalso([
        dict(
            fila(aviso_dias_antes=9, gracia_dias=7),
            id="relacion-1",
        )
    ])
    monkeypatch.setattr(modulos, "supabase_admin", supabase)

    modulos.activar_renovar_modulo(
        "comercio-1",
        "turnos",
        3,
        fecha_actual=HOY,
    )

    assert supabase.operacion == "update"
    assert "activo" not in supabase.datos_guardados
    assert "aviso_dias_antes" not in supabase.datos_guardados
    assert "gracia_dias" not in supabase.datos_guardados


def test_suspension_manual_es_independiente_del_vencimiento(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila(activo=False, fecha_vencimiento="2027-09-20")],
    )
    assert resultado["estado_vigencia"] == "activo"
    assert resultado["acceso_operativo"] is False
    assert resultado["motivo_bloqueo"] == "suspension_manual"


def test_modulo_activo_true_sin_vencimiento(monkeypatch):
    monkeypatch.setattr(
        modulos,
        "supabase_admin",
        SupabaseFalso([
            fila(fecha_activacion=None, fecha_vencimiento=None)
        ]),
    )
    assert modulos.modulo_activo("comercio-1", "turnos") is True


def catalogo_con(monkeypatch, resultado):
    monkeypatch.setattr(
        modulos,
        "evaluar_vigencia_modulo",
        lambda comercio_id, slug: resultado,
    )
    return modulos.combinar_catalogo_con_vigencia("comercio-1")[0]


def test_catalogo_relacion_inexistente_no_aparece_activa(monkeypatch):
    modulo = catalogo_con(
        monkeypatch,
        modulos._resultado_vigencia_bloqueado("modulo_no_asignado"),
    )
    assert modulo["existe"] is False
    assert modulo["mostrar_como_activa"] is False
    assert modulo["etiqueta_estado"] == "Inactiva"


def test_catalogo_activo_sin_vencimiento_aparece_activa(monkeypatch):
    resultado = evaluar(
        monkeypatch,
        [fila(fecha_activacion=None, fecha_vencimiento=None)],
    )
    modulo = catalogo_con(monkeypatch, resultado)
    assert modulo["mostrar_como_activa"] is True
    assert modulo["etiqueta_estado"] == "Activo"


def test_catalogo_vigente_aparece_activa(monkeypatch):
    resultado = evaluar(monkeypatch, [fila()])
    modulo = catalogo_con(monkeypatch, resultado)
    assert modulo["mostrar_como_activa"] is True
    assert modulo["estado_vigencia"] == "activo"


def test_catalogo_por_vencer_aparece_activa(monkeypatch):
    resultado = evaluar(monkeypatch, [fila()], date(2026, 9, 15))
    modulo = catalogo_con(monkeypatch, resultado)
    assert modulo["mostrar_como_activa"] is True
    assert modulo["etiqueta_estado"] == "Por vencer"


def test_catalogo_en_gracia_aparece_activa(monkeypatch):
    resultado = evaluar(monkeypatch, [fila()], date(2026, 9, 21))
    modulo = catalogo_con(monkeypatch, resultado)
    assert modulo["mostrar_como_activa"] is True
    assert modulo["etiqueta_estado"] == "En gracia"


def test_catalogo_vencido_no_aparece_activa(monkeypatch):
    resultado = evaluar(monkeypatch, [fila()], date(2026, 9, 26))
    modulo = catalogo_con(monkeypatch, resultado)
    assert modulo["existe"] is True
    assert modulo["mostrar_como_activa"] is False
    assert modulo["etiqueta_estado"] == "Vencido"


def test_catalogo_suspendido_no_aparece_activa(monkeypatch):
    resultado = evaluar(monkeypatch, [fila(activo=False)])
    modulo = catalogo_con(monkeypatch, resultado)
    assert modulo["mostrar_como_activa"] is False
    assert modulo["etiqueta_estado"] == "Suspendido"


def test_catalogo_error_consulta_no_aparece_activa(monkeypatch):
    modulo = catalogo_con(
        monkeypatch,
        modulos._resultado_vigencia_bloqueado("error_consulta"),
    )
    assert modulo["mostrar_como_activa"] is False
    assert modulo["etiqueta_estado"] == "Inactiva"


def test_catalogo_y_modulo_activo_comparten_evaluacion(monkeypatch):
    resultado = evaluar(monkeypatch, [fila()], date(2026, 9, 21))
    monkeypatch.setattr(
        modulos,
        "evaluar_vigencia_modulo",
        lambda comercio_id, slug: resultado,
    )
    modulo = modulos.combinar_catalogo_con_vigencia("comercio-1")[0]
    assert modulo["acceso_operativo"] is True
    assert modulo["mostrar_como_activa"] is True
    assert modulos.modulo_activo("comercio-1", "turnos") is True


class ConsultaModulosMemoria:
    def __init__(self, db, nombre):
        self.db = db
        self.nombre = nombre
        self.filtros = []
        self.operacion = "select"
        self.datos = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, campo, valor):
        self.filtros.append((campo, valor))
        return self

    def limit(self, *args, **kwargs):
        return self

    def insert(self, datos):
        self.operacion = "insert"
        self.datos = dict(datos)
        return self

    def update(self, datos):
        self.operacion = "update"
        self.datos = dict(datos)
        return self

    def delete(self):
        self.operacion = "delete"
        return self

    def _coincide(self, registro):
        return all(registro.get(campo) == valor for campo, valor in self.filtros)

    def execute(self):
        assert self.nombre == "comercio_modulos"
        coincidentes = [
            registro for registro in self.db.relaciones
            if self._coincide(registro)
        ]
        if self.operacion == "insert":
            nuevo = dict(self.datos, id=f"relacion-{len(self.db.relaciones) + 1}")
            self.db.relaciones.append(nuevo)
            return SimpleNamespace(data=[nuevo])
        if self.operacion == "update":
            for registro in coincidentes:
                registro.update(self.datos)
            return SimpleNamespace(data=coincidentes)
        if self.operacion == "delete":
            self.db.relaciones[:] = [
                registro for registro in self.db.relaciones
                if not self._coincide(registro)
            ]
        return SimpleNamespace(data=coincidentes)


class SupabaseModulosMemoria:
    def __init__(self, relaciones=None):
        self.relaciones = [dict(registro) for registro in (relaciones or [])]
        self.tablas_consultadas = []

    def table(self, nombre):
        self.tablas_consultadas.append(nombre)
        return ConsultaModulosMemoria(self, nombre)


def relacion_memoria(**cambios):
    datos = dict(fila(), id="relacion-1", comercio_id="comercio-1", modulo="turnos")
    datos.update(cambios)
    return datos


def test_instalar_un_mes_crea_fechas_correctas(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    assert modulos.instalar_modulo(
        "comercio-1", "turnos", 1, fecha_actual=HOY
    ) is True
    assert db.relaciones[0]["activo"] is True
    assert db.relaciones[0]["fecha_activacion"] == "2026-09-10"
    assert db.relaciones[0]["fecha_vencimiento"] == "2026-10-10"
    assert "aviso_dias_antes" not in db.relaciones[0]
    assert "gracia_dias" not in db.relaciones[0]


def test_instalar_tres_meses_crea_fecha_correcta(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.instalar_modulo("comercio-1", "turnos", 3, HOY)
    assert db.relaciones[0]["fecha_vencimiento"] == "2026-12-10"


def test_instalar_seis_meses_crea_fecha_correcta(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.instalar_modulo("comercio-1", "turnos", 6, HOY)
    assert db.relaciones[0]["fecha_vencimiento"] == "2027-03-10"


def test_instalar_usa_meses_calendario_y_ajusta_fin_de_mes(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.instalar_modulo(
        "comercio-1", "turnos", 1, date(2026, 1, 31)
    )
    assert db.relaciones[0]["fecha_activacion"] == "2026-01-31"
    assert db.relaciones[0]["fecha_vencimiento"] == "2026-02-28"


def test_instalar_usa_meses_calendario_al_cruzar_de_anio(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.instalar_modulo(
        "comercio-1", "turnos", 3, date(2026, 11, 30)
    )
    assert db.relaciones[0]["fecha_vencimiento"] == "2027-02-28"


def test_instalar_ya_existente_es_idempotente(monkeypatch):
    original = relacion_memoria(
        fecha_activacion="2026-06-01",
        fecha_vencimiento="2026-07-01",
    )
    db = SupabaseModulosMemoria([original])
    monkeypatch.setattr(modulos, "supabase_admin", db)
    assert modulos.instalar_modulo("comercio-1", "turnos", 6, HOY) is False
    assert len(db.relaciones) == 1
    assert db.relaciones[0]["fecha_activacion"] == "2026-06-01"
    assert db.relaciones[0]["fecha_vencimiento"] == "2026-07-01"


def test_instalar_rechaza_duracion_invalida(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    for duracion in (0, 2, 12, "1", None):
        try:
            modulos.instalar_modulo("comercio-1", "turnos", duracion, HOY)
        except ValueError:
            pass
        else:
            raise AssertionError("La instalación aceptó una duración inválida")
    assert db.relaciones == []


def test_admin_no_ofrece_instalacion_sin_duracion():
    plantilla = Path("templates/admin_modulos.html").read_text(encoding="utf-8")
    assert "'admin_instalar_modulo'" in plantilla
    assert 'name="duracion_meses"' in plantilla
    assert '<option value="1">1 mes</option>' in plantilla
    assert '<option value="3">3 meses</option>' in plantilla
    assert '<option value="6">6 meses</option>' in plantilla

    backend = Path("app.py").read_text(encoding="utf-8")
    inicio = backend.index("def admin_instalar_modulo")
    fin = backend.index("def admin_desinstalar_modulo", inicio)
    ruta_instalacion = backend[inicio:fin]
    assert 'duracion_raw not in ("1", "3", "6")' in ruta_instalacion
    assert 'modulo_error="duracion_invalida"' in ruta_instalacion


def test_desinstalar_existente_solo_borra_relacion(monkeypatch):
    db = SupabaseModulosMemoria([relacion_memoria()])
    datos_operativos = {"servicios": ["corte"], "reservas": ["reserva-1"]}
    monkeypatch.setattr(modulos, "supabase_admin", db)
    assert modulos.desinstalar_modulo("comercio-1", "turnos") is True
    assert db.relaciones == []
    assert datos_operativos == {"servicios": ["corte"], "reservas": ["reserva-1"]}
    assert set(db.tablas_consultadas) == {"comercio_modulos"}


def test_desinstalar_inexistente(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    assert modulos.desinstalar_modulo("comercio-1", "turnos") is False


def test_reinstalar_recupera_acceso_a_datos_anteriores(monkeypatch):
    db = SupabaseModulosMemoria([relacion_memoria()])
    datos_operativos = {"servicios": ["corte"], "reservas": ["reserva-1"]}
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.desinstalar_modulo("comercio-1", "turnos")
    modulos.instalar_modulo("comercio-1", "turnos", 1, HOY)
    assert datos_operativos["servicios"] == ["corte"]
    assert datos_operativos["reservas"] == ["reserva-1"]
    assert db.relaciones[0]["activo"] is True


def test_desactivar_y_reactivar_exigen_instalacion(monkeypatch):
    db = SupabaseModulosMemoria()
    monkeypatch.setattr(modulos, "supabase_admin", db)
    for activo in (False, True):
        try:
            modulos.cambiar_activo_modulo("comercio-1", "turnos", activo)
        except LookupError:
            pass
        else:
            raise AssertionError("Cambiar activo exige instalación")
    assert db.relaciones == []


def test_desactivar_y_reactivar_solo_cambian_activo(monkeypatch):
    original = relacion_memoria(activo=True)
    db = SupabaseModulosMemoria([original])
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.cambiar_activo_modulo("comercio-1", "turnos", False)
    assert db.relaciones[0]["activo"] is False
    assert db.relaciones[0]["fecha_vencimiento"] == original["fecha_vencimiento"]
    modulos.cambiar_activo_modulo("comercio-1", "turnos", True)
    assert db.relaciones[0]["activo"] is True


def test_renovar_no_reactiva_activo_false(monkeypatch):
    db = SupabaseModulosMemoria([relacion_memoria(activo=False)])
    monkeypatch.setattr(modulos, "supabase_admin", db)
    modulos.activar_renovar_modulo("comercio-1", "turnos", 1, HOY)
    assert db.relaciones[0]["activo"] is False
    assert db.relaciones[0]["fecha_vencimiento"] == "2026-10-20"


def test_panel_superior_es_existe_y_catalogo_inferior_no_existe():
    catalogo = [
        {"slug": "activo", "disponible": True, "existe": True, "acceso_operativo": True},
        {"slug": "suspendido", "disponible": True, "existe": True, "acceso_operativo": False},
        {"slug": "disponible", "disponible": True, "existe": False, "acceso_operativo": False},
    ]
    activos, limitados, disponibles = modulos.clasificar_modulos_panel(catalogo)
    assert [item["slug"] for item in activos + limitados] == ["activo", "suspendido"]
    assert [item["slug"] for item in disponibles] == ["disponible"]
    assert not ({item["slug"] for item in limitados} & {item["slug"] for item in disponibles})
