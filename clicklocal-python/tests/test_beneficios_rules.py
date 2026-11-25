from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from beneficios.errors import ValidacionBeneficioError
from beneficios.rules import calcular_precio_con_beneficio, esta_vigente
from beneficios.types import Beneficio, ObjetivoBeneficio, TipoDescuento, TipoObjetivo


AHORA = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def crear_beneficio(**cambios):
    datos = {
        "id": "beneficio-1",
        "comercio_id": "comercio-1",
        "titulo": "Promoción",
        "objetivo": ObjetivoBeneficio(TipoObjetivo.PUBLICACION, "publicacion-1"),
        "tipo_descuento": TipoDescuento.PORCENTAJE,
        "valor_descuento": Decimal("25"),
        "vigente_desde": AHORA - timedelta(days=1),
        "vigente_hasta": AHORA + timedelta(days=1),
        "activo": True,
    }
    datos.update(cambios)
    return Beneficio(**datos)


def test_porcentaje_redondea_a_centavos():
    resultado = calcular_precio_con_beneficio(Decimal("99.99"), crear_beneficio())
    assert resultado == Decimal("74.99")


def test_precio_especial_devuelve_valor_promocional():
    beneficio = crear_beneficio(
        tipo_descuento=TipoDescuento.PRECIO_ESPECIAL,
        valor_descuento=Decimal("40"),
    )
    assert calcular_precio_con_beneficio(Decimal("50"), beneficio) == Decimal("40.00")


def test_precio_especial_no_puede_superar_original():
    beneficio = crear_beneficio(
        tipo_descuento=TipoDescuento.PRECIO_ESPECIAL,
        valor_descuento=Decimal("60"),
    )
    with pytest.raises(ValidacionBeneficioError):
        calcular_precio_con_beneficio(Decimal("50"), beneficio)


def test_vigencia_tiene_inicio_inclusivo_y_fin_exclusivo():
    inicio = AHORA
    fin = AHORA + timedelta(hours=1)
    beneficio = crear_beneficio(vigente_desde=inicio, vigente_hasta=fin)
    assert esta_vigente(beneficio, inicio) is True
    assert esta_vigente(beneficio, fin) is False
    assert esta_vigente(crear_beneficio(activo=False), AHORA) is False


def test_fechas_sin_zona_horaria_son_invalidas():
    beneficio = crear_beneficio(vigente_desde=datetime(2026, 11, 16, 12, 0))
    with pytest.raises(ValidacionBeneficioError):
        esta_vigente(beneficio, AHORA)


def test_fecha_de_finalizacion_es_obligatoria():
    beneficio = crear_beneficio(vigente_hasta=None)
    with pytest.raises(ValidacionBeneficioError):
        esta_vigente(beneficio, AHORA)


def test_detalle_promocion_opcional_y_limitado_a_200_caracteres():
    assert esta_vigente(crear_beneficio(detalle_promocion=None), AHORA) is True
    assert esta_vigente(crear_beneficio(detalle_promocion="x" * 200), AHORA) is True
    for invalido in ("", " detalle ", "x" * 201):
        with pytest.raises(ValidacionBeneficioError):
            esta_vigente(crear_beneficio(detalle_promocion=invalido), AHORA)
