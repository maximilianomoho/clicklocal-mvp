from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from beneficios.errors import ValidacionBeneficioError
from beneficios.rules import (
    calcular_codigos_restantes,
    calcular_cupo_ocupado,
    calcular_fin_vigencia,
    calcular_inicio_vigencia,
    calcular_total_con_beneficio,
    calcular_vencimiento_codigo,
    cambio_requiere_nueva_edicion,
    estado_codigo,
    esta_dentro_de_condiciones,
    puede_emitir_codigo,
    puede_utilizar_codigo,
    requiere_nueva_edicion_para_reactivar,
    validar_beneficio,
)
from beneficios.types import (
    Beneficio,
    CodigoBeneficio,
    EstadoCodigo,
    MetodoUtilizacion,
    ModoInicioVigencia,
    ObjetivoBeneficio,
    TemaEtiqueta,
    TipoDescuento,
    TipoObjetivo,
)


ZONA = ZoneInfo("America/Argentina/Cordoba")
INICIO = datetime(2026, 11, 17, 9, 0, tzinfo=ZONA)
FIN = INICIO + timedelta(days=7)


def beneficio(tipo=TipoDescuento.PORCENTAJE, valor=Decimal("20"), **cambios):
    datos = dict(
        id="beneficio-1",
        comercio_id="comercio-1",
        titulo="Promoción",
        objetivo=ObjetivoBeneficio(TipoObjetivo.PUBLICACION, "publicacion-1"),
        tipo_descuento=tipo,
        valor_descuento=valor,
        vigente_desde=INICIO,
        vigente_hasta=FIN,
        maximo_unidades_codigo=20,
    )
    datos.update(cambios)
    return Beneficio(**datos)


@pytest.mark.parametrize(
    ("tipo", "valor", "cantidad", "esperado"),
    [
        (TipoDescuento.PORCENTAJE, Decimal("20"), 3, Decimal("240.00")),
        (TipoDescuento.MONTO_FIJO, Decimal("15"), 3, Decimal("255.00")),
        (TipoDescuento.PRECIO_ESPECIAL, Decimal("70"), 3, Decimal("210.00")),
        (TipoDescuento.DOS_POR_UNO, None, 3, Decimal("200.00")),
        (TipoDescuento.TRES_POR_DOS, None, 4, Decimal("300.00")),
        (TipoDescuento.SEGUNDA_UNIDAD_50, None, 3, Decimal("250.00")),
    ],
)
def test_calcula_los_seis_tipos(tipo, valor, cantidad, esperado):
    assert calcular_total_con_beneficio(
        Decimal("100"), cantidad, beneficio(tipo, valor)
    ) == esperado


def test_promociones_por_cantidad_respetan_impares_y_maximo_del_codigo():
    dos_por_uno = beneficio(
        TipoDescuento.DOS_POR_UNO,
        None,
        maximo_unidades_codigo=2,
    )
    assert calcular_total_con_beneficio(Decimal("100"), 3, dos_por_uno) == Decimal("200.00")
    segunda = beneficio(TipoDescuento.SEGUNDA_UNIDAD_50, None)
    assert calcular_total_con_beneficio(Decimal("99.99"), 3, segunda) == Decimal("249.98")


def test_monto_fijo_no_produce_total_negativo_y_redondea_centavos():
    promo = beneficio(TipoDescuento.MONTO_FIJO, Decimal("20"))
    assert calcular_total_con_beneficio(Decimal("10.005"), 2, promo) == Decimal("0.00")
    porcentaje = beneficio(TipoDescuento.PORCENTAJE, Decimal("33.333"))
    assert calcular_total_con_beneficio(Decimal("10"), 1, porcentaje) == Decimal("6.67")


@pytest.mark.parametrize("valor", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_rechaza_importes_invalidos(valor):
    with pytest.raises(ValidacionBeneficioError):
        calcular_total_con_beneficio(valor, 1, beneficio())


def test_duraciones_rapidas_y_fecha_personalizada():
    assert calcular_fin_vigencia(INICIO, duracion_dias=7) == FIN
    personalizada = INICIO + timedelta(days=11, hours=3)
    assert calcular_fin_vigencia(INICIO, vigente_hasta=personalizada) == personalizada
    with pytest.raises(ValidacionBeneficioError):
        calcular_fin_vigencia(INICIO, duracion_dias=2)


def test_inicio_inmediato_o_programado_depende_del_reloj_provisto():
    programado = INICIO + timedelta(days=1)
    assert calcular_inicio_vigencia(ModoInicioVigencia.INMEDIATO, INICIO) == INICIO
    assert calcular_inicio_vigencia(
        ModoInicioVigencia.PROGRAMADO,
        INICIO,
        inicio_programado=programado,
    ) == programado
    with pytest.raises(ValidacionBeneficioError):
        calcular_inicio_vigencia(
            ModoInicioVigencia.PROGRAMADO,
            INICIO,
            inicio_programado=INICIO,
        )


def test_dias_y_happy_hour_en_zona_local():
    jueves = datetime(2026, 11, 19, 19, 0, tzinfo=ZONA)
    promo = beneficio(
        dias_semana=frozenset({3, 4, 5, 6}),
        hora_desde=time(18),
        hora_hasta=time(21),
    )
    assert esta_dentro_de_condiciones(promo, jueves) is True
    assert esta_dentro_de_condiciones(promo, jueves.replace(hour=17)) is False
    assert esta_dentro_de_condiciones(promo, jueves - timedelta(days=1)) is False


def test_dias_y_horarios_restringen_uso_pero_no_emision():
    jueves_antes = datetime(2026, 11, 19, 17, 0, tzinfo=ZONA)
    promo = beneficio(
        dias_semana=frozenset({3}),
        hora_desde=time(18),
        hora_hasta=time(21),
    )
    pendiente = CodigoBeneficio(
        promo.id,
        "CODIGO",
        jueves_antes,
        jueves_antes + timedelta(hours=24),
    )
    assert puede_emitir_codigo(promo, jueves_antes) is True
    assert puede_utilizar_codigo(
        promo, pendiente, jueves_antes, MetodoUtilizacion.WHATSAPP
    ) is False


def test_cruce_de_medianoche_pertenece_al_dia_de_inicio():
    promo = beneficio(
        dias_semana=frozenset({3}),  # jueves
        hora_desde=time(22),
        hora_hasta=time(2),
    )
    jueves_noche = datetime(2026, 11, 19, 23, 30, tzinfo=ZONA)
    viernes_madrugada = datetime(2026, 11, 20, 1, 30, tzinfo=ZONA)
    viernes_noche = datetime(2026, 11, 20, 23, 30, tzinfo=ZONA)
    assert esta_dentro_de_condiciones(promo, jueves_noche) is True
    assert esta_dentro_de_condiciones(promo, viernes_madrugada) is True
    assert esta_dentro_de_condiciones(promo, viernes_noche) is False


def test_instante_utc_se_evalua_en_cordoba():
    promo = beneficio(dias_semana=frozenset({3}), hora_desde=time(22), hora_hasta=time(2))
    viernes_utc = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)
    assert viernes_utc.astimezone(ZONA).hour == 1
    assert esta_dentro_de_condiciones(promo, viernes_utc) is True


def test_codigo_vence_en_24_horas_o_al_finalizar():
    emitido = INICIO + timedelta(hours=1)
    assert calcular_vencimiento_codigo(beneficio(), emitido) == emitido + timedelta(hours=24)
    promo_corta = beneficio(vigente_hasta=emitido + timedelta(hours=3))
    assert calcular_vencimiento_codigo(promo_corta, emitido) == promo_corta.vigente_hasta


def test_no_emite_exactamente_en_el_fin_exclusivo():
    promo = beneficio()
    assert puede_emitir_codigo(promo, promo.vigente_hasta) is False
    with pytest.raises(ValidacionBeneficioError):
        calcular_vencimiento_codigo(promo, promo.vigente_hasta)


def codigo(*, vence_en, utilizado_en=None):
    return CodigoBeneficio(
        "beneficio-1",
        "CODIGO",
        INICIO,
        vence_en,
        utilizado_en=utilizado_en,
    )


def test_estados_y_cupo_liberan_solo_vencidos_sin_consumir():
    ahora = INICIO + timedelta(days=2)
    pendiente = codigo(vence_en=ahora + timedelta(hours=1))
    vencido = codigo(vence_en=ahora - timedelta(hours=1))
    consumido = codigo(vence_en=ahora - timedelta(hours=1), utilizado_en=INICIO + timedelta(hours=2))
    assert estado_codigo(pendiente, ahora) is EstadoCodigo.PENDIENTE
    assert estado_codigo(vencido, ahora) is EstadoCodigo.VENCIDO
    assert estado_codigo(consumido, ahora) is EstadoCodigo.CONSUMIDO
    assert calcular_cupo_ocupado((pendiente, vencido, consumido), ahora) == 2


def test_codigo_vence_y_libera_cupo_exactamente_en_vence_en():
    vence = INICIO + timedelta(hours=2)
    exacto = codigo(vence_en=vence)
    assert estado_codigo(exacto, vence) is EstadoCodigo.VENCIDO
    assert calcular_cupo_ocupado((exacto,), vence) == 0
    assert puede_utilizar_codigo(
        beneficio(), exacto, vence, MetodoUtilizacion.WHATSAPP
    ) is False


def test_contador_publico_respeta_configuracion():
    ahora = INICIO + timedelta(hours=2)
    codigos = (codigo(vence_en=ahora + timedelta(hours=1)),)
    oculto = beneficio(cupo_maximo=5, mostrar_codigos_restantes=False)
    visible = replace(oculto, mostrar_codigos_restantes=True)
    assert calcular_codigos_restantes(oculto, codigos, ahora) is None
    assert calcular_codigos_restantes(visible, codigos, ahora) == 4


def test_pausa_impide_emitir_pero_no_invalida_codigo_emitido():
    ahora = INICIO + timedelta(hours=2)
    pausado = beneficio(activo=False)
    pendiente = codigo(vence_en=ahora + timedelta(hours=1))
    assert puede_emitir_codigo(pausado, ahora) is False
    assert puede_utilizar_codigo(
        pausado, pendiente, ahora, MetodoUtilizacion.WHATSAPP
    ) is True


def test_metodos_tema_y_al_menos_un_metodo():
    todos = frozenset(MetodoUtilizacion)
    validar_beneficio(beneficio(metodos=todos, tema_etiqueta=TemaEtiqueta.OSCURA))
    with pytest.raises(ValidacionBeneficioError):
        validar_beneficio(beneficio(metodos=frozenset()))
    assert puede_utilizar_codigo(
        beneficio(metodos=frozenset({MetodoUtilizacion.QR})),
        codigo(vence_en=INICIO + timedelta(hours=4)),
        INICIO + timedelta(hours=2),
        MetodoUtilizacion.WHATSAPP,
    ) is False


def test_reactivar_finalizada_exige_nueva_edicion():
    assert requiere_nueva_edicion_para_reactivar(beneficio(), FIN) is True
    assert requiere_nueva_edicion_para_reactivar(beneficio(), FIN + timedelta(seconds=1)) is True
    assert requiere_nueva_edicion_para_reactivar(beneficio(), FIN - timedelta(seconds=1)) is False


def test_cambios_historicos_con_codigos_exigen_nueva_edicion():
    original = beneficio()
    cambio_condicion = replace(original, cupo_maximo=100)
    solo_presentacion = replace(original, tema_etiqueta=TemaEtiqueta.OSCURA)
    assert cambio_requiere_nueva_edicion(
        original, cambio_condicion, tiene_codigos_emitidos=True
    ) is True
    assert cambio_requiere_nueva_edicion(
        original, solo_presentacion, tiene_codigos_emitidos=True
    ) is False
    assert cambio_requiere_nueva_edicion(
        original, cambio_condicion, tiene_codigos_emitidos=False
    ) is False
