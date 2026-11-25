"""Estructuras de datos propias de Beneficios, sin dependencias de Flask."""

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from enum import Enum


class TipoObjetivo(str, Enum):
    COMERCIO = "comercio"
    PUBLICACION = "publicacion"
    GASTRONOMIA_PRODUCTO = "gastronomia_producto"


class TipoDescuento(str, Enum):
    PORCENTAJE = "porcentaje"
    MONTO_FIJO = "monto_fijo"
    PRECIO_ESPECIAL = "precio_especial"
    DOS_POR_UNO = "dos_por_uno"
    TRES_POR_DOS = "tres_por_dos"
    SEGUNDA_UNIDAD_50 = "segunda_unidad_50"


class TemaEtiqueta(str, Enum):
    CLARA = "clara"
    OSCURA = "oscura"


class MetodoUtilizacion(str, Enum):
    WHATSAPP = "whatsapp"
    QR = "qr"
    CARRITO = "carrito"


class EstadoCodigo(str, Enum):
    PENDIENTE = "pendiente"
    CONSUMIDO = "consumido"
    VENCIDO = "vencido"


class ModoInicioVigencia(str, Enum):
    INMEDIATO = "inmediato"
    PROGRAMADO = "programado"


TODOS_LOS_DIAS = frozenset(range(7))
TODOS_LOS_METODOS = frozenset(MetodoUtilizacion)


@dataclass(frozen=True, slots=True)
class ObjetivoBeneficio:
    tipo: TipoObjetivo
    id: str


@dataclass(frozen=True, slots=True)
class Beneficio:
    id: str
    comercio_id: str
    titulo: str
    objetivo: ObjetivoBeneficio
    tipo_descuento: TipoDescuento
    valor_descuento: Decimal | None
    vigente_desde: datetime
    vigente_hasta: datetime
    detalle_promocion: str | None = None
    activo: bool = True
    dias_semana: frozenset[int] = TODOS_LOS_DIAS
    hora_desde: time | None = None
    hora_hasta: time | None = None
    zona_horaria: str = "America/Argentina/Cordoba"
    cupo_maximo: int | None = None
    maximo_unidades_codigo: int = 1
    mostrar_codigos_restantes: bool = False
    tema_etiqueta: TemaEtiqueta = TemaEtiqueta.CLARA
    metodos: frozenset[MetodoUtilizacion] = TODOS_LOS_METODOS
    serie_id: str | None = None
    numero_edicion: int = 1


@dataclass(frozen=True, slots=True)
class ConsultaBeneficios:
    objetivos: tuple[ObjetivoBeneficio, ...]
    instante: datetime


@dataclass(frozen=True, slots=True)
class SolicitudCodigo:
    beneficio_id: str
    consumidor_id: str | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CodigoBeneficio:
    beneficio_id: str
    codigo: str
    emitido_en: datetime
    vence_en: datetime | None
    utilizado_en: datetime | None = None
    metodo_utilizacion: MetodoUtilizacion | None = None


@dataclass(frozen=True, slots=True)
class ValidacionCodigo:
    codigo_id: str
    beneficio_id: str
    comercio_id: str
    titulo: str
    emitido_en: datetime
    vence_en: datetime
    utilizado_en: datetime | None

    @property
    def disponible(self) -> bool:
        return self.utilizado_en is None
