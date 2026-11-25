from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from beneficios.errors import (
    BeneficioNoEncontradoError,
    BeneficioNoVigenteError,
    CodigoInvalidoError,
    PersistenciaNoConfiguradaError,
)
from beneficios.repository import RepositorioBeneficiosNoConfigurado
from beneficios.services import ServicioBeneficios
from beneficios.types import (
    Beneficio,
    CodigoBeneficio,
    ConsultaBeneficios,
    ObjetivoBeneficio,
    SolicitudCodigo,
    TipoDescuento,
    TipoObjetivo,
    ValidacionCodigo,
)


AHORA = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
OBJETIVO = ObjetivoBeneficio(TipoObjetivo.PUBLICACION, "publicacion-1")


def beneficio_base(*, activo=True):
    return Beneficio(
        id="beneficio-1",
        comercio_id="comercio-1",
        titulo="Promoción",
        objetivo=OBJETIVO,
        tipo_descuento=TipoDescuento.PORCENTAJE,
        valor_descuento=Decimal("10"),
        vigente_desde=AHORA - timedelta(days=1),
        vigente_hasta=AHORA + timedelta(days=1),
        activo=activo,
    )


class RepositorioFalso:
    def __init__(self, beneficios=()):
        self.beneficios = tuple(beneficios)
        self.validacion = None
        self.codigo_validado = None
        self.codigo_consumido = None
        self.historial = []

    def buscar_vigentes(self, consulta):
        return self.beneficios

    def obtener_por_id(self, beneficio_id):
        return next((item for item in self.beneficios if item.id == beneficio_id), None)

    def crear_codigo(self, beneficio, solicitud, emitido_en):
        return CodigoBeneficio(beneficio.id, "CODIGO-1", emitido_en, None)

    def publicacion_pertenece_a_comercio(self, publicacion_id, comercio_id):
        return publicacion_id == "publicacion-1" and comercio_id == "comercio-1"

    def objetivo_pertenece_a_comercio(self, objetivo, comercio_id):
        return (
            comercio_id == "comercio-1"
            and (
                objetivo
                == ObjetivoBeneficio(TipoObjetivo.COMERCIO, "comercio-1")
                or objetivo
                == ObjetivoBeneficio(TipoObjetivo.PUBLICACION, "publicacion-1")
                or objetivo
                == ObjetivoBeneficio(
                    TipoObjetivo.GASTRONOMIA_PRODUCTO,
                    "producto-1",
                )
            )
        )

    def crear_beneficio(self, beneficio):
        self.beneficios = (*self.beneficios, beneficio)
        return beneficio

    def listar_por_comercio(self, comercio_id):
        return tuple(
            item for item in self.beneficios
            if item.comercio_id == comercio_id
        )

    def editar_beneficio(self, beneficio_id, beneficio):
        anterior = self.obtener_por_id(beneficio_id)
        if anterior is None or not anterior.activo:
            return None
        self.historial.append(anterior)
        editado = replace(
            beneficio,
            serie_id=anterior.serie_id or anterior.id,
            numero_edicion=anterior.numero_edicion + 1,
        )
        self.beneficios = tuple(
            editado if item.id == beneficio_id else item
            for item in self.beneficios
        )
        return editado

    def desactivar_beneficio(self, beneficio_id, comercio_id):
        anterior = self.obtener_por_id(beneficio_id)
        if anterior is None or anterior.comercio_id != comercio_id or not anterior.activo:
            return None
        desactivado = replace(anterior, activo=False)
        self.beneficios = tuple(
            desactivado if item.id == beneficio_id else item
            for item in self.beneficios
        )
        return desactivado

    def validar_codigo(self, codigo, comercio_id, instante):
        self.codigo_validado = (
            codigo,
            comercio_id,
            instante,
        )
        return self.validacion

    def consumir_codigo(self, codigo, comercio_id):
        self.codigo_consumido = (
            codigo,
            comercio_id,
        )
        return self.validacion


def test_consulta_filtra_objetivo_y_vigencia_aunque_repositorio_no_lo_haga():
    otro = replace(
        beneficio_base(),
        id="beneficio-otro",
        objetivo=ObjetivoBeneficio(TipoObjetivo.PUBLICACION, "otra"),
    )
    servicio = ServicioBeneficios(RepositorioFalso((beneficio_base(), otro)))
    resultado = servicio.consultar_vigentes(ConsultaBeneficios((OBJETIVO,), AHORA))
    assert resultado == (beneficio_base(),)


def test_detalle_inexistente_es_error_explicito():
    servicio = ServicioBeneficios(RepositorioFalso())
    with pytest.raises(BeneficioNoEncontradoError):
        servicio.obtener_detalle("ausente")


def test_no_genera_codigo_para_beneficio_inactivo():
    servicio = ServicioBeneficios(
        RepositorioFalso((beneficio_base(activo=False),)), reloj=lambda: AHORA
    )
    solicitud = SolicitudCodigo(
        "beneficio-1",
        None,
        "10000000-0000-4000-8000-000000000001",
    )
    with pytest.raises(BeneficioNoVigenteError):
        servicio.generar_codigo(solicitud)


def test_genera_codigo_con_instante_del_reloj_del_servidor():
    servicio = ServicioBeneficios(
        RepositorioFalso((beneficio_base(),)), reloj=lambda: AHORA
    )
    codigo = servicio.generar_codigo(
        SolicitudCodigo(
            "beneficio-1",
            None,
            "10000000-0000-4000-8000-000000000002",
        )
    )
    assert codigo.emitido_en == AHORA


def test_repositorio_no_configurado_no_simula_exito():
    servicio = ServicioBeneficios(RepositorioBeneficiosNoConfigurado())
    with pytest.raises(PersistenciaNoConfiguradaError):
        servicio.obtener_detalle("beneficio-1")


def test_creacion_valida_pertenencia_antes_de_persistir():
    repo = RepositorioFalso()
    creado = ServicioBeneficios(repo).crear_para_publicacion(beneficio_base())
    assert creado == beneficio_base()
    assert repo.beneficios == (beneficio_base(),)


def test_creacion_rechaza_publicacion_de_otro_comercio():
    repo = RepositorioFalso()
    ajeno = replace(beneficio_base(), comercio_id="comercio-ajeno")
    with pytest.raises(BeneficioNoEncontradoError):
        ServicioBeneficios(repo).crear_para_publicacion(ajeno)
    assert repo.beneficios == ()


@pytest.mark.parametrize(
    ("objetivo", "metodo"),
    (
        (
            ObjetivoBeneficio(TipoObjetivo.COMERCIO, "comercio-1"),
            "crear_para_comercio",
        ),
        (
            ObjetivoBeneficio(
                TipoObjetivo.GASTRONOMIA_PRODUCTO,
                "producto-1",
            ),
            "crear_para_producto",
        ),
    ),
)
def test_creacion_admite_objetivos_2a(objetivo, metodo):
    repo = RepositorioFalso()
    beneficio = replace(beneficio_base(), objetivo=objetivo)

    creado = getattr(ServicioBeneficios(repo), metodo)(beneficio)

    assert creado == beneficio
    assert repo.beneficios == (beneficio,)


def test_consulta_conserva_multiples_ofertas_del_mismo_objetivo():
    primera = beneficio_base()
    segunda = replace(primera, id="beneficio-2", titulo="Otra promoción")
    servicio = ServicioBeneficios(RepositorioFalso((primera, segunda)))

    assert servicio.consultar_vigentes(
        ConsultaBeneficios((OBJETIVO,), AHORA)
    ) == (primera, segunda)


def test_lista_solo_ofertas_del_comercio():
    propia = beneficio_base()
    ajena = replace(propia, id="beneficio-ajeno", comercio_id="comercio-ajeno")
    servicio = ServicioBeneficios(RepositorioFalso((propia, ajena)))
    assert servicio.listar_del_comercio("comercio-1") == (propia,)


def test_edicion_crea_revision_y_preserva_anterior_en_historial():
    anterior = beneficio_base()
    repo = RepositorioFalso((anterior,))
    nuevo = replace(anterior, id="beneficio-2", valor_descuento=Decimal("20"))
    editado = ServicioBeneficios(repo).editar_del_comercio(
        anterior.id, anterior.comercio_id, nuevo
    )
    assert editado.id == "beneficio-2"
    assert editado.serie_id == anterior.id
    assert editado.numero_edicion == 2
    assert repo.historial == [anterior]


def test_edicion_y_desactivacion_rechazan_oferta_ajena():
    propia = beneficio_base()
    repo = RepositorioFalso((propia,))
    servicio = ServicioBeneficios(repo)
    with pytest.raises(BeneficioNoEncontradoError):
        servicio.editar_del_comercio(
            propia.id,
            "comercio-ajeno",
            replace(propia, id="beneficio-2", comercio_id="comercio-ajeno"),
        )
    with pytest.raises(BeneficioNoEncontradoError):
        servicio.desactivar_del_comercio(propia.id, "comercio-ajeno")


def test_desactivar_impide_nueva_emision_sin_borrar_oferta():
    repo = RepositorioFalso((beneficio_base(),))
    servicio = ServicioBeneficios(repo, reloj=lambda: AHORA)
    desactivado = servicio.desactivar_del_comercio(
        "beneficio-1", "comercio-1"
    )
    assert desactivado.activo is False
    assert repo.obtener_por_id("beneficio-1") == desactivado
    with pytest.raises(BeneficioNoVigenteError):
        servicio.generar_codigo(SolicitudCodigo(
            "beneficio-1", None,
            "10000000-0000-4000-8000-000000000009",
        ))


def validacion_base(*, utilizado_en=None, vence_en=None):
    return ValidacionCodigo(
        codigo_id="30000000-0000-4000-8000-000000000001",
        beneficio_id="30000000-0000-4000-8000-000000000002",
        comercio_id="30000000-0000-4000-8000-000000000003",
        titulo="Beneficio ficticio",
        emitido_en=AHORA - timedelta(minutes=10),
        vence_en=vence_en or AHORA + timedelta(hours=1),
        utilizado_en=utilizado_en,
    )


def test_validacion_comercial_no_consume_codigo():
    repositorio = RepositorioFalso()
    repositorio.validacion = validacion_base()
    servicio = ServicioBeneficios(
        repositorio,
        reloj=lambda: AHORA,
    )

    resultado = servicio.validar_codigo_comercio(
        "abcd-efgh-2345-6789",
        repositorio.validacion.comercio_id,
    )

    assert resultado == repositorio.validacion
    assert repositorio.codigo_validado == (
        "ABCD-EFGH-2345-6789",
        repositorio.validacion.comercio_id,
        AHORA,
    )
    assert repositorio.codigo_consumido is None


@pytest.mark.parametrize(
    "validacion",
    (
        None,
        validacion_base(utilizado_en=AHORA),
        validacion_base(
            vence_en=AHORA - timedelta(seconds=1),
        ),
    ),
)
def test_validacion_rechaza_codigo_no_disponible(validacion):
    repositorio = RepositorioFalso()
    repositorio.validacion = validacion
    servicio = ServicioBeneficios(
        repositorio,
        reloj=lambda: AHORA,
    )

    with pytest.raises(CodigoInvalidoError):
        servicio.validar_codigo_comercio(
            "ABCD-EFGH-2345-6789",
            "30000000-0000-4000-8000-000000000003",
        )

    assert repositorio.codigo_consumido is None


def test_consumo_ocurre_solo_despues_de_validacion_explicita():
    repositorio = RepositorioFalso()
    repositorio.validacion = validacion_base()
    servicio = ServicioBeneficios(
        repositorio,
        reloj=lambda: AHORA,
    )

    resultado = servicio.consumir_codigo_comercio(
        "abcd-efgh-2345-6789",
        repositorio.validacion.comercio_id,
    )

    assert resultado == repositorio.validacion
    assert repositorio.codigo_validado is not None
    assert repositorio.codigo_consumido == (
        "ABCD-EFGH-2345-6789",
        repositorio.validacion.comercio_id,
    )
