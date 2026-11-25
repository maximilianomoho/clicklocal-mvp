"""Reglas puras para configurar, emitir y utilizar Beneficios."""

from dataclasses import fields
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ValidacionBeneficioError
from .types import (
    Beneficio,
    CodigoBeneficio,
    EstadoCodigo,
    MetodoUtilizacion,
    ModoInicioVigencia,
    TemaEtiqueta,
    TipoDescuento,
)

CENTAVO = Decimal("0.01")
CIEN = Decimal("100")
DOS = Decimal("2")
HORAS_VIGENCIA_CODIGO = 24
DIAS_RAPIDOS = frozenset({1, 3, 7, 15, 30, 45, 60})
TIPOS_SIN_VALOR = frozenset({
    TipoDescuento.DOS_POR_UNO,
    TipoDescuento.TRES_POR_DOS,
    TipoDescuento.SEGUNDA_UNIDAD_50,
})


def _exigir_texto(valor: str, campo: str) -> None:
    if not isinstance(valor, str) or not valor.strip():
        raise ValidacionBeneficioError(f"{campo} es obligatorio")


def _exigir_fecha_con_zona(valor: datetime, campo: str) -> None:
    if not isinstance(valor, datetime) or valor.tzinfo is None or valor.utcoffset() is None:
        raise ValidacionBeneficioError(f"{campo} debe incluir zona horaria")


def _exigir_decimal_no_negativo(valor: Decimal, campo: str) -> None:
    if not isinstance(valor, Decimal) or not valor.is_finite() or valor < 0:
        raise ValidacionBeneficioError(
            f"{campo} debe ser Decimal finito y no negativo"
        )


def _zona_horaria(nombre: str) -> ZoneInfo:
    _exigir_texto(nombre, "zona_horaria")
    try:
        return ZoneInfo(nombre)
    except ZoneInfoNotFoundError as error:
        raise ValidacionBeneficioError("zona_horaria no admitida") from error


def validar_beneficio(beneficio: Beneficio) -> None:
    """Valida invariantes sin consultar estado externo."""

    _exigir_texto(beneficio.id, "id")
    _exigir_texto(beneficio.comercio_id, "comercio_id")
    _exigir_texto(beneficio.titulo, "titulo")
    if beneficio.detalle_promocion is not None and (
        not isinstance(beneficio.detalle_promocion, str)
        or not beneficio.detalle_promocion
        or beneficio.detalle_promocion != beneficio.detalle_promocion.strip()
        or len(beneficio.detalle_promocion) > 200
    ):
        raise ValidacionBeneficioError(
            "detalle_promocion debe ser texto opcional de hasta 200 caracteres"
        )
    _exigir_texto(beneficio.objetivo.id, "objetivo.id")
    _exigir_fecha_con_zona(beneficio.vigente_desde, "vigente_desde")
    _exigir_fecha_con_zona(beneficio.vigente_hasta, "vigente_hasta")
    if beneficio.vigente_hasta <= beneficio.vigente_desde:
        raise ValidacionBeneficioError(
            "vigente_hasta debe ser posterior a vigente_desde"
        )

    valor = beneficio.valor_descuento
    if beneficio.tipo_descuento is TipoDescuento.PORCENTAJE:
        if not isinstance(valor, Decimal) or not valor.is_finite():
            raise ValidacionBeneficioError("valor_descuento debe ser Decimal finito")
        if valor <= 0 or valor > CIEN:
            raise ValidacionBeneficioError("el porcentaje debe estar entre 0 y 100")
    elif beneficio.tipo_descuento in {
        TipoDescuento.MONTO_FIJO,
        TipoDescuento.PRECIO_ESPECIAL,
    }:
        if not isinstance(valor, Decimal) or not valor.is_finite():
            raise ValidacionBeneficioError("valor_descuento debe ser Decimal finito")
        if valor < 0:
            raise ValidacionBeneficioError("el importe no puede ser negativo")
    elif beneficio.tipo_descuento in TIPOS_SIN_VALOR:
        if valor is not None:
            raise ValidacionBeneficioError(
                "este tipo de beneficio no admite valor_descuento"
            )
    else:
        raise ValidacionBeneficioError("tipo_descuento no admitido")

    if (
        not isinstance(beneficio.dias_semana, frozenset)
        or not beneficio.dias_semana
        or not beneficio.dias_semana.issubset(range(7))
    ):
        raise ValidacionBeneficioError(
            "dias_semana debe contener días entre lunes=0 y domingo=6"
        )
    if (beneficio.hora_desde is None) != (beneficio.hora_hasta is None):
        raise ValidacionBeneficioError(
            "hora_desde y hora_hasta deben informarse juntas"
        )
    if beneficio.hora_desde is not None:
        if not isinstance(beneficio.hora_desde, time) or not isinstance(
            beneficio.hora_hasta, time
        ):
            raise ValidacionBeneficioError("la franja horaria no es válida")
        if beneficio.hora_desde == beneficio.hora_hasta:
            raise ValidacionBeneficioError(
                "la franja horaria no puede abarcar cero horas"
            )
    _zona_horaria(beneficio.zona_horaria)
    if beneficio.cupo_maximo is not None and (
        isinstance(beneficio.cupo_maximo, bool)
        or not isinstance(beneficio.cupo_maximo, int)
        or beneficio.cupo_maximo < 1
    ):
        raise ValidacionBeneficioError("cupo_maximo debe ser positivo o None")
    if (
        isinstance(beneficio.maximo_unidades_codigo, bool)
        or not isinstance(beneficio.maximo_unidades_codigo, int)
        or beneficio.maximo_unidades_codigo < 1
    ):
        raise ValidacionBeneficioError("maximo_unidades_codigo debe ser positivo")
    if not isinstance(beneficio.tema_etiqueta, TemaEtiqueta):
        raise ValidacionBeneficioError("tema_etiqueta no admitido")
    if (
        not isinstance(beneficio.metodos, frozenset)
        or not beneficio.metodos
        or any(not isinstance(item, MetodoUtilizacion) for item in beneficio.metodos)
    ):
        raise ValidacionBeneficioError("debe habilitarse al menos un método")
    if (
        isinstance(beneficio.numero_edicion, bool)
        or not isinstance(beneficio.numero_edicion, int)
        or beneficio.numero_edicion < 1
    ):
        raise ValidacionBeneficioError("numero_edicion debe ser positivo")


def esta_vigente(beneficio: Beneficio, instante: datetime) -> bool:
    """Evalúa una vigencia semiabierta: inicio inclusivo y fin exclusivo."""

    validar_beneficio(beneficio)
    _exigir_fecha_con_zona(instante, "instante")
    if not beneficio.activo or instante < beneficio.vigente_desde:
        return False
    return instante < beneficio.vigente_hasta


def calcular_fin_vigencia(
    vigente_desde: datetime,
    *,
    duracion_dias: int | None = None,
    vigente_hasta: datetime | None = None,
) -> datetime:
    """Resuelve una duración rápida o una finalización personalizada."""

    _exigir_fecha_con_zona(vigente_desde, "vigente_desde")
    if (duracion_dias is None) == (vigente_hasta is None):
        raise ValidacionBeneficioError(
            "indique una duración o una fecha de finalización"
        )
    if duracion_dias is not None:
        if duracion_dias not in DIAS_RAPIDOS:
            raise ValidacionBeneficioError("duración rápida no admitida")
        return vigente_desde + timedelta(days=duracion_dias)
    _exigir_fecha_con_zona(vigente_hasta, "vigente_hasta")
    if vigente_hasta <= vigente_desde:
        raise ValidacionBeneficioError(
            "vigente_hasta debe ser posterior a vigente_desde"
        )
    return vigente_hasta


def calcular_inicio_vigencia(
    modo: ModoInicioVigencia,
    ahora: datetime,
    *,
    inicio_programado: datetime | None = None,
) -> datetime:
    """Resuelve el inicio usando exclusivamente el reloj provisto."""

    _exigir_fecha_con_zona(ahora, "ahora")
    if modo is ModoInicioVigencia.INMEDIATO:
        if inicio_programado is not None:
            raise ValidacionBeneficioError(
                "el inicio inmediato no admite fecha programada"
            )
        return ahora
    if modo is ModoInicioVigencia.PROGRAMADO:
        _exigir_fecha_con_zona(inicio_programado, "inicio_programado")
        if inicio_programado <= ahora:
            raise ValidacionBeneficioError(
                "el inicio programado debe ser posterior al instante actual"
            )
        return inicio_programado
    raise ValidacionBeneficioError("modo de inicio no admitido")


def esta_dentro_de_condiciones(beneficio: Beneficio, instante: datetime) -> bool:
    """Evalúa vigencia, día y horario sin considerar una pausa posterior."""

    validar_beneficio(beneficio)
    _exigir_fecha_con_zona(instante, "instante")
    if instante < beneficio.vigente_desde:
        return False
    if instante >= beneficio.vigente_hasta:
        return False

    local = instante.astimezone(_zona_horaria(beneficio.zona_horaria))
    hora_local = local.timetz().replace(tzinfo=None)
    fecha_inicio_franja = local.date()
    desde = beneficio.hora_desde
    hasta = beneficio.hora_hasta

    if desde is not None and hasta is not None:
        if desde < hasta:
            if not desde <= hora_local <= hasta:
                return False
        else:
            if hora_local >= desde:
                pass
            elif hora_local <= hasta:
                fecha_inicio_franja -= timedelta(days=1)
            else:
                return False

    return fecha_inicio_franja.weekday() in beneficio.dias_semana


def puede_emitir_codigo(beneficio: Beneficio, instante: datetime) -> bool:
    """La pausa impide emitir; días y horarios se controlan al utilizar."""

    return esta_vigente(beneficio, instante)


def puede_utilizar_codigo(
    beneficio: Beneficio,
    codigo: CodigoBeneficio,
    instante: datetime,
    metodo: MetodoUtilizacion,
) -> bool:
    """Un código emitido conserva condiciones aunque la edición se pause."""

    validar_beneficio(beneficio)
    _exigir_fecha_con_zona(instante, "instante")
    if metodo not in beneficio.metodos:
        return False
    if codigo.beneficio_id != beneficio.id:
        return False
    if estado_codigo(codigo, instante) is not EstadoCodigo.PENDIENTE:
        return False
    return esta_dentro_de_condiciones(beneficio, instante)


def calcular_precio_con_beneficio(
    precio_original: Decimal,
    beneficio: Beneficio,
) -> Decimal:
    """Calcula un precio informativo; no altera carritos, pedidos, POS ni caja."""

    return calcular_total_con_beneficio(precio_original, 1, beneficio)


def calcular_total_con_beneficio(
    precio_unitario: Decimal,
    cantidad: int,
    beneficio: Beneficio,
) -> Decimal:
    """Calcula el total puro, respetando el máximo de unidades del código."""

    validar_beneficio(beneficio)
    _exigir_decimal_no_negativo(precio_unitario, "precio_unitario")
    if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad < 1:
        raise ValidacionBeneficioError("cantidad debe ser un entero positivo")

    elegibles = min(cantidad, beneficio.maximo_unidades_codigo)
    no_elegibles = cantidad - elegibles
    precio = precio_unitario
    tipo = beneficio.tipo_descuento
    valor = beneficio.valor_descuento

    if tipo is TipoDescuento.PORCENTAJE:
        total_elegible = precio * elegibles * (CIEN - valor) / CIEN
    elif tipo is TipoDescuento.MONTO_FIJO:
        total_elegible = max(precio - valor, Decimal("0")) * elegibles
    elif tipo is TipoDescuento.PRECIO_ESPECIAL:
        if valor > precio:
            raise ValidacionBeneficioError(
                "el precio especial no puede superar el precio original"
            )
        total_elegible = valor * elegibles
    elif tipo is TipoDescuento.DOS_POR_UNO:
        unidades_pagadas = elegibles - (elegibles // 2)
        total_elegible = precio * unidades_pagadas
    elif tipo is TipoDescuento.TRES_POR_DOS:
        unidades_pagadas = elegibles - (elegibles // 3)
        total_elegible = precio * unidades_pagadas
    elif tipo is TipoDescuento.SEGUNDA_UNIDAD_50:
        pares = elegibles // 2
        total_elegible = precio * elegibles - (precio / DOS * pares)
    else:  # pragma: no cover - validar_beneficio ya rechaza este caso.
        raise ValidacionBeneficioError("tipo_descuento no admitido")

    return (total_elegible + precio * no_elegibles).quantize(
        CENTAVO, rounding=ROUND_HALF_UP
    )


def calcular_vencimiento_codigo(
    beneficio: Beneficio,
    emitido_en: datetime,
) -> datetime:
    validar_beneficio(beneficio)
    _exigir_fecha_con_zona(emitido_en, "emitido_en")
    if not puede_emitir_codigo(beneficio, emitido_en):
        raise ValidacionBeneficioError("no se puede emitir fuera de condiciones")
    limite_24_horas = emitido_en + timedelta(hours=HORAS_VIGENCIA_CODIGO)
    return min(limite_24_horas, beneficio.vigente_hasta)


def estado_codigo(codigo: CodigoBeneficio, instante: datetime) -> EstadoCodigo:
    _exigir_fecha_con_zona(codigo.emitido_en, "codigo.emitido_en")
    _exigir_fecha_con_zona(instante, "instante")
    if codigo.utilizado_en is not None:
        _exigir_fecha_con_zona(codigo.utilizado_en, "codigo.utilizado_en")
        return EstadoCodigo.CONSUMIDO
    if codigo.vence_en is not None:
        _exigir_fecha_con_zona(codigo.vence_en, "codigo.vence_en")
        if instante >= codigo.vence_en:
            return EstadoCodigo.VENCIDO
    return EstadoCodigo.PENDIENTE


def calcular_cupo_ocupado(
    codigos: tuple[CodigoBeneficio, ...],
    instante: datetime,
) -> int:
    """Consumidos y pendientes vigentes ocupan; vencidos libres no."""

    return sum(
        estado_codigo(codigo, instante)
        in {EstadoCodigo.PENDIENTE, EstadoCodigo.CONSUMIDO}
        for codigo in codigos
    )


def calcular_codigos_restantes(
    beneficio: Beneficio,
    codigos: tuple[CodigoBeneficio, ...],
    instante: datetime,
) -> int | None:
    validar_beneficio(beneficio)
    if not beneficio.mostrar_codigos_restantes or beneficio.cupo_maximo is None:
        return None
    return max(
        beneficio.cupo_maximo - calcular_cupo_ocupado(codigos, instante),
        0,
    )


def requiere_nueva_edicion_para_reactivar(
    beneficio: Beneficio,
    instante: datetime,
) -> bool:
    validar_beneficio(beneficio)
    _exigir_fecha_con_zona(instante, "instante")
    return instante >= beneficio.vigente_hasta


CONDICIONES_HISTORICAS = frozenset({
    "objetivo",
    "tipo_descuento",
    "valor_descuento",
    "vigente_desde",
    "vigente_hasta",
    "dias_semana",
    "hora_desde",
    "hora_hasta",
    "zona_horaria",
    "cupo_maximo",
    "maximo_unidades_codigo",
    "metodos",
})


def cambio_requiere_nueva_edicion(
    anterior: Beneficio,
    propuesta: Beneficio,
    *,
    tiene_codigos_emitidos: bool,
) -> bool:
    """Protege las condiciones originales de códigos ya emitidos."""

    validar_beneficio(anterior)
    validar_beneficio(propuesta)
    if not tiene_codigos_emitidos:
        return False
    nombres = {campo.name for campo in fields(Beneficio)} & CONDICIONES_HISTORICAS
    return any(getattr(anterior, nombre) != getattr(propuesta, nombre) for nombre in nombres)
