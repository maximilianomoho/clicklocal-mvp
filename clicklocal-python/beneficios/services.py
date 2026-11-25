"""Casos de uso de Beneficios con repositorio inyectado."""

from collections.abc import Callable
from datetime import datetime, timezone

from .errors import (
    BeneficioNoEncontradoError,
    BeneficioNoVigenteError,
    CodigoInvalidoError,
    ValidacionBeneficioError,
)
from .repository import RepositorioBeneficios
from .rules import esta_vigente, validar_beneficio
from .types import (
    Beneficio,
    CodigoBeneficio,
    ConsultaBeneficios,
    SolicitudCodigo,
    TipoObjetivo,
    ValidacionCodigo,
)


class ServicioBeneficios:
    def __init__(
        self,
        repositorio: RepositorioBeneficios,
        *,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._reloj = reloj or (lambda: datetime.now(timezone.utc))

    def consultar_vigentes(
        self,
        consulta: ConsultaBeneficios,
    ) -> tuple[Beneficio, ...]:
        encontrados = self._repositorio.buscar_vigentes(consulta)
        objetivos = set(consulta.objetivos)
        resultado: list[Beneficio] = []
        for beneficio in encontrados:
            validar_beneficio(beneficio)
            if beneficio.objetivo in objetivos and esta_vigente(
                beneficio, consulta.instante
            ):
                resultado.append(beneficio)
        return tuple(resultado)

    def obtener_detalle(self, beneficio_id: str) -> Beneficio:
        if not isinstance(beneficio_id, str) or not beneficio_id.strip():
            raise BeneficioNoEncontradoError("beneficio_id es obligatorio")
        beneficio = self._repositorio.obtener_por_id(beneficio_id)
        if beneficio is None:
            raise BeneficioNoEncontradoError(beneficio_id)
        validar_beneficio(beneficio)
        return beneficio

    def crear_para_publicacion(self, beneficio: Beneficio) -> Beneficio:
        """Valida dominio y pertenencia antes de persistir una promoción."""

        if beneficio.objetivo.tipo is not TipoObjetivo.PUBLICACION:
            raise BeneficioNoEncontradoError(beneficio.objetivo.id)
        return self.crear_beneficio(beneficio)

    def crear_para_producto(self, beneficio: Beneficio) -> Beneficio:
        if beneficio.objetivo.tipo is not TipoObjetivo.GASTRONOMIA_PRODUCTO:
            raise BeneficioNoEncontradoError(beneficio.objetivo.id)
        return self.crear_beneficio(beneficio)

    def crear_para_comercio(self, beneficio: Beneficio) -> Beneficio:
        if beneficio.objetivo.tipo is not TipoObjetivo.COMERCIO:
            raise BeneficioNoEncontradoError(beneficio.objetivo.id)
        return self.crear_beneficio(beneficio)

    def crear_beneficio(self, beneficio: Beneficio) -> Beneficio:
        """Valida dominio y pertenencia para cualquier objetivo 2A."""

        validar_beneficio(beneficio)
        if not self._repositorio.objetivo_pertenece_a_comercio(
            beneficio.objetivo,
            beneficio.comercio_id,
        ):
            raise BeneficioNoEncontradoError(beneficio.objetivo.id)
        return self._repositorio.crear_beneficio(beneficio)

    def listar_del_comercio(self, comercio_id: str) -> tuple[Beneficio, ...]:
        comercio_normalizado = str(comercio_id or "").strip()
        if not comercio_normalizado:
            raise BeneficioNoEncontradoError("comercio_id es obligatorio")
        beneficios = self._repositorio.listar_por_comercio(comercio_normalizado)
        for beneficio in beneficios:
            validar_beneficio(beneficio)
            if beneficio.comercio_id != comercio_normalizado:
                raise BeneficioNoEncontradoError(beneficio.id)
        return beneficios

    def editar_del_comercio(
        self,
        beneficio_id: str,
        comercio_id: str,
        beneficio: Beneficio,
    ) -> Beneficio:
        comercio_normalizado = str(comercio_id or "").strip()
        if (
            not str(beneficio_id or "").strip()
            or not comercio_normalizado
            or beneficio.comercio_id != comercio_normalizado
        ):
            raise BeneficioNoEncontradoError(beneficio_id)
        validar_beneficio(beneficio)
        actual = self._repositorio.obtener_por_id(beneficio_id)
        if (
            actual is None
            or actual.comercio_id != comercio_normalizado
            or actual.objetivo != beneficio.objetivo
            or not actual.activo
        ):
            raise BeneficioNoEncontradoError(beneficio_id)
        if not self._repositorio.objetivo_pertenece_a_comercio(
            beneficio.objetivo,
            comercio_normalizado,
        ):
            raise BeneficioNoEncontradoError(beneficio_id)
        editado = self._repositorio.editar_beneficio(beneficio_id, beneficio)
        if editado is None:
            raise BeneficioNoEncontradoError(beneficio_id)
        return editado

    def desactivar_del_comercio(
        self,
        beneficio_id: str,
        comercio_id: str,
    ) -> Beneficio:
        beneficio_normalizado = str(beneficio_id or "").strip()
        comercio_normalizado = str(comercio_id or "").strip()
        if not beneficio_normalizado or not comercio_normalizado:
            raise BeneficioNoEncontradoError(beneficio_id)
        desactivado = self._repositorio.desactivar_beneficio(
            beneficio_normalizado,
            comercio_normalizado,
        )
        if desactivado is None:
            raise BeneficioNoEncontradoError(beneficio_id)
        return desactivado

    def generar_codigo(self, solicitud: SolicitudCodigo) -> CodigoBeneficio:
        beneficio = self.obtener_detalle(solicitud.beneficio_id)
        emitido_en = self._instante_actual()
        if (
            not isinstance(emitido_en, datetime)
            or emitido_en.tzinfo is None
            or emitido_en.utcoffset() is None
        ):
            raise ValidacionBeneficioError(
                "el reloj del servidor debe devolver una fecha con zona horaria"
            )
        if not esta_vigente(beneficio, emitido_en):
            raise BeneficioNoVigenteError(solicitud.beneficio_id)
        return self._repositorio.crear_codigo(beneficio, solicitud, emitido_en)

    def validar_codigo_comercio(
        self,
        codigo: str,
        comercio_id: str,
    ) -> ValidacionCodigo:
        codigo_normalizado = str(codigo or "").strip().upper()
        comercio_normalizado = str(comercio_id or "").strip()
        if not codigo_normalizado or not comercio_normalizado:
            raise CodigoInvalidoError()

        instante = self._instante_actual()
        resultado = self._repositorio.validar_codigo(
            codigo_normalizado,
            comercio_normalizado,
            instante,
        )
        if (
            resultado is None
            or not resultado.disponible
            or resultado.vence_en <= instante
        ):
            raise CodigoInvalidoError()

        return resultado

    def consumir_codigo_comercio(
        self,
        codigo: str,
        comercio_id: str,
    ) -> ValidacionCodigo:
        codigo_normalizado = str(codigo or "").strip().upper()
        comercio_normalizado = str(comercio_id or "").strip()

        self.validar_codigo_comercio(
            codigo_normalizado,
            comercio_normalizado,
        )

        # La comprobación previa sólo alimenta la confirmación visual.
        # La garantía final de consumo único corresponde a la función
        # PostgreSQL atómica invocada por el repositorio.
        return self._repositorio.consumir_codigo(
            codigo_normalizado,
            comercio_normalizado,
        )

    def _instante_actual(self) -> datetime:
        instante = self._reloj()
        if (
            not isinstance(instante, datetime)
            or instante.tzinfo is None
            or instante.utcoffset() is None
        ):
            raise ValidacionBeneficioError(
                "el reloj del servidor debe devolver una fecha con zona horaria"
            )
        return instante
