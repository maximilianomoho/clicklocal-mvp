from datetime import datetime, timedelta, timezone
from decimal import Decimal

from beneficios.contracts import (
    consultar_para_comercios,
    consultar_para_gastronomia_productos,
    consultar_para_publicaciones,
    generar_codigo_beneficio,
    obtener_detalle_beneficio,
)
from beneficios.services import ServicioBeneficios
from beneficios.types import (
    Beneficio,
    CodigoBeneficio,
    ObjetivoBeneficio,
    SolicitudCodigo,
    TipoDescuento,
    TipoObjetivo,
)


AHORA = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def crear_beneficio(tipo, objetivo_id, beneficio_id):
    return Beneficio(
        id=beneficio_id,
        comercio_id="comercio-1",
        titulo="Promoción",
        objetivo=ObjetivoBeneficio(tipo, objetivo_id),
        tipo_descuento=TipoDescuento.PORCENTAJE,
        valor_descuento=Decimal("15"),
        vigente_desde=AHORA - timedelta(days=1),
        vigente_hasta=AHORA + timedelta(days=1),
    )


class RepositorioFalso:
    def __init__(self, beneficios):
        self.beneficios = tuple(beneficios)

    def buscar_vigentes(self, consulta):
        return self.beneficios

    def obtener_por_id(self, beneficio_id):
        return next((item for item in self.beneficios if item.id == beneficio_id), None)

    def crear_codigo(self, beneficio, solicitud, emitido_en):
        return CodigoBeneficio(beneficio.id, "PUBLICO-1", emitido_en, None)


def test_contratos_separan_publicaciones_y_productos():
    comercio = crear_beneficio(TipoObjetivo.COMERCIO, "comercio-1", "b-0")
    publicacion = crear_beneficio(TipoObjetivo.PUBLICACION, "pub-1", "b-1")
    producto = crear_beneficio(TipoObjetivo.GASTRONOMIA_PRODUCTO, "prod-1", "b-2")
    servicio = ServicioBeneficios(
        RepositorioFalso((comercio, publicacion, producto))
    )

    assert consultar_para_comercios(
        ("comercio-1",), AHORA, servicio=servicio
    ) == (comercio,)
    assert consultar_para_publicaciones(("pub-1",), AHORA, servicio=servicio) == (
        publicacion,
    )
    assert consultar_para_gastronomia_productos(
        ("prod-1",), AHORA, servicio=servicio
    ) == (producto,)


def test_detalle_y_codigo_delegan_en_servicio_publico():
    beneficio = crear_beneficio(TipoObjetivo.PUBLICACION, "pub-1", "b-1")
    servicio = ServicioBeneficios(
        RepositorioFalso((beneficio,)), reloj=lambda: AHORA
    )
    assert obtener_detalle_beneficio("b-1", servicio=servicio) == beneficio

    codigo = generar_codigo_beneficio(
        SolicitudCodigo(
            "b-1",
            "consumidor-1",
            "10000000-0000-4000-8000-000000000003",
        ),
        servicio=servicio,
    )
    assert codigo.codigo == "PUBLICO-1"
    assert codigo.emitido_en == AHORA
