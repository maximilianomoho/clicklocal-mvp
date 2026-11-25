"""Persistencia de Beneficios con una conexión PostgreSQL inyectada."""

import base64
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import hmac
from hashlib import sha256
import re
from typing import Protocol
from uuid import UUID

from psycopg import Connection
from psycopg.errors import RaiseException
from psycopg.rows import dict_row

from .errors import (
    CodigoInvalidoError,
    PersistenciaBeneficiosError,
    PersistenciaNoConfiguradaError,
)
from .types import (
    Beneficio, CodigoBeneficio, ConsultaBeneficios, MetodoUtilizacion,
    ObjetivoBeneficio, SolicitudCodigo, TemaEtiqueta, TipoDescuento, TipoObjetivo,
    ValidacionCodigo,
)


class RepositorioBeneficios(Protocol):
    def buscar_vigentes(self, consulta: ConsultaBeneficios) -> tuple[Beneficio, ...]: ...
    def obtener_por_id(self, beneficio_id: str) -> Beneficio | None: ...
    def publicacion_pertenece_a_comercio(self, publicacion_id: str, comercio_id: str) -> bool: ...
    def objetivo_pertenece_a_comercio(self, objetivo: ObjetivoBeneficio, comercio_id: str) -> bool: ...
    def crear_beneficio(self, beneficio: Beneficio) -> Beneficio: ...
    def listar_por_comercio(self, comercio_id: str) -> tuple[Beneficio, ...]: ...
    def editar_beneficio(self, beneficio_id: str, beneficio: Beneficio) -> Beneficio | None: ...
    def desactivar_beneficio(self, beneficio_id: str, comercio_id: str) -> Beneficio | None: ...
    def crear_codigo(self, beneficio: Beneficio, solicitud: SolicitudCodigo, emitido_en: datetime) -> CodigoBeneficio: ...
    def validar_codigo(self, codigo: str, comercio_id: str, instante: datetime) -> ValidacionCodigo | None: ...
    def consumir_codigo(self, codigo: str, comercio_id: str) -> ValidacionCodigo: ...


class RepositorioBeneficiosNoConfigurado:
    """Falla explícitamente; nunca simula persistencia exitosa."""

    def _pendiente(self) -> None:
        raise PersistenciaNoConfiguradaError(
            "La persistencia real de Beneficios no está configurada"
        )

    def buscar_vigentes(self, consulta: ConsultaBeneficios) -> tuple[Beneficio, ...]:
        self._pendiente()

    def obtener_por_id(self, beneficio_id: str) -> Beneficio | None:
        self._pendiente()

    def publicacion_pertenece_a_comercio(self, publicacion_id: str, comercio_id: str) -> bool:
        self._pendiente()

    def objetivo_pertenece_a_comercio(self, objetivo: ObjetivoBeneficio, comercio_id: str) -> bool:
        self._pendiente()

    def crear_beneficio(self, beneficio: Beneficio) -> Beneficio:
        self._pendiente()

    def listar_por_comercio(self, comercio_id: str) -> tuple[Beneficio, ...]:
        self._pendiente()

    def editar_beneficio(self, beneficio_id: str, beneficio: Beneficio) -> Beneficio | None:
        self._pendiente()

    def desactivar_beneficio(self, beneficio_id: str, comercio_id: str) -> Beneficio | None:
        self._pendiente()

    def crear_codigo(self, beneficio: Beneficio, solicitud: SolicitudCodigo, emitido_en: datetime) -> CodigoBeneficio:
        self._pendiente()

    def validar_codigo(self, codigo: str, comercio_id: str, instante: datetime) -> ValidacionCodigo | None:
        self._pendiente()

    def consumir_codigo(self, codigo: str, comercio_id: str) -> ValidacionCodigo:
        self._pendiente()


class RepositorioBeneficiosPostgresql:
    """Repositorio pequeño y síncrono para el esquema ``beneficios_2a``."""

    def __init__(
        self,
        conectar: Callable[[], Connection],
        *,
        secreto_codigos: bytes | None = None,
    ) -> None:
        self._conectar = conectar
        self._secreto_codigos = secreto_codigos

    def buscar_vigentes(self, consulta: ConsultaBeneficios) -> tuple[Beneficio, ...]:
        comercios = [o.id for o in consulta.objetivos if o.tipo is TipoObjetivo.COMERCIO]
        publicaciones = [o.id for o in consulta.objetivos if o.tipo is TipoObjetivo.PUBLICACION]
        productos = [o.id for o in consulta.objetivos if o.tipo is TipoObjetivo.GASTRONOMIA_PRODUCTO]
        if not comercios and not publicaciones and not productos:
            return ()
        try:
            with self._conectar() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    select * from public.beneficios b
                    where b.activo and b.vigente_desde <= %s and %s < b.vigente_hasta
                      and ((b.tipo_objetivo = 'comercio' and b.comercio_id = any(%s::uuid[]))
                        or (b.tipo_objetivo = 'publicacion' and b.publicacion_id = any(%s::uuid[]))
                        or (b.tipo_objetivo = 'gastronomia_producto' and b.gastronomia_producto_id = any(%s::uuid[])))
                    order by b.created_at, b.id
                    """,
                    (
                        consulta.instante, consulta.instante, comercios,
                        publicaciones, productos,
                    ),
                )
                return tuple(_beneficio_desde_fila(fila) for fila in cursor.fetchall())
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def obtener_por_id(self, beneficio_id: str) -> Beneficio | None:
        try:
            with self._conectar() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
                cursor.execute("select * from public.beneficios b where b.id = %s", (beneficio_id,))
                fila = cursor.fetchone()
                return _beneficio_desde_fila(fila) if fila else None
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def publicacion_pertenece_a_comercio(self, publicacion_id: str, comercio_id: str) -> bool:
        try:
            with self._conectar() as conexion, conexion.cursor() as cursor:
                cursor.execute(
                    "select exists(select 1 from public.publicaciones p where p.id = %s and p.comercio_id = %s)",
                    (publicacion_id, comercio_id),
                )
                return bool(cursor.fetchone()[0])
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def objetivo_pertenece_a_comercio(
        self,
        objetivo: ObjetivoBeneficio,
        comercio_id: str,
    ) -> bool:
        if objetivo.tipo is TipoObjetivo.COMERCIO:
            if objetivo.id != comercio_id:
                return False
            tabla = "public.comercios"
        elif objetivo.tipo is TipoObjetivo.PUBLICACION:
            tabla = "public.publicaciones"
        else:
            tabla = "public.gastronomia_productos"
        try:
            with self._conectar() as conexion, conexion.cursor() as cursor:
                if objetivo.tipo is TipoObjetivo.COMERCIO:
                    cursor.execute(
                        "select exists(select 1 from public.comercios "
                        "where id = %s)",
                        (comercio_id,),
                    )
                else:
                    cursor.execute(
                        f"select exists(select 1 from {tabla} "
                        "where id = %s and comercio_id = %s)",
                        (objetivo.id, comercio_id),
                    )
                return bool(cursor.fetchone()[0])
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def crear_beneficio(self, beneficio: Beneficio) -> Beneficio:
        try:
            with self._conectar() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
                return _insertar_beneficio(cursor, beneficio)
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def listar_por_comercio(self, comercio_id: str) -> tuple[Beneficio, ...]:
        try:
            with self._conectar() as conexion, conexion.cursor(
                row_factory=dict_row
            ) as cursor:
                cursor.execute(
                    """
                    select actual.*
                    from (
                      select distinct on (b.serie_id) b.*
                      from public.beneficios b
                      where b.comercio_id = %s
                      order by b.serie_id, b.numero_edicion desc
                    ) actual
                    order by actual.created_at, actual.id
                    """,
                    (comercio_id,),
                )
                return tuple(
                    _beneficio_desde_fila(fila) for fila in cursor.fetchall()
                )
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def editar_beneficio(
        self,
        beneficio_id: str,
        beneficio: Beneficio,
    ) -> Beneficio | None:
        try:
            with self._conectar() as conexion, conexion.cursor(
                row_factory=dict_row
            ) as cursor:
                cursor.execute(
                    """
                    select * from public.beneficios
                    where id = %s and comercio_id = %s and activo
                    for update
                    """,
                    (beneficio_id, beneficio.comercio_id),
                )
                anterior = cursor.fetchone()
                if anterior is None:
                    return None
                nueva = replace(
                    beneficio,
                    serie_id=str(anterior["serie_id"]),
                    numero_edicion=int(anterior["numero_edicion"]) + 1,
                    activo=True,
                )
                cursor.execute(
                    """
                    update public.beneficios
                    set activo = false
                    where id = %s and comercio_id = %s and activo
                    """,
                    (beneficio_id, beneficio.comercio_id),
                )
                return _insertar_beneficio(
                    cursor,
                    nueva,
                    edicion_anterior_id=beneficio_id,
                )
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def desactivar_beneficio(
        self,
        beneficio_id: str,
        comercio_id: str,
    ) -> Beneficio | None:
        try:
            with self._conectar() as conexion, conexion.cursor(
                row_factory=dict_row
            ) as cursor:
                cursor.execute(
                    """
                    update public.beneficios
                    set activo = false
                    where id = %s and comercio_id = %s and activo
                    returning *
                    """,
                    (beneficio_id, comercio_id),
                )
                fila = cursor.fetchone()
                return _beneficio_desde_fila(fila) if fila else None
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def crear_codigo(self, beneficio: Beneficio, solicitud: SolicitudCodigo, emitido_en: datetime) -> CodigoBeneficio:
        del emitido_en  # PostgreSQL es el reloj autoritativo de la emisión.

        if (
            self._secreto_codigos is None
            or len(self._secreto_codigos) < 32
        ):
            raise PersistenciaNoConfiguradaError(
                "Falta el secreto seguro para emitir códigos"
            )

        try:
            idempotency_key = UUID(solicitud.idempotency_key)
            consumidor_id = str(UUID(str(solicitud.consumidor_id)))
        except (AttributeError, TypeError, ValueError) as error:
            raise PersistenciaBeneficiosError(
                "La identidad o idempotencia no es válida"
            ) from error

        material = (
            "beneficios:v1:"
            f"{beneficio.id}:"
            f"{consumidor_id}:"
            f"{idempotency_key}"
        ).encode("utf-8")
        codigo_compacto = base64.b32encode(
            hmac.new(
                self._secreto_codigos,
                material,
                sha256,
            ).digest()
        ).decode("ascii")[:16]
        codigo = "-".join(
            codigo_compacto[indice:indice + 4]
            for indice in range(0, 16, 4)
        )
        codigo_hash = sha256(codigo.encode("utf-8")).digest()

        try:
            with self._conectar() as conexion, conexion.cursor() as cursor:
                cursor.execute(
                    "select codigo_id, emitido_en, vence_en from public.beneficios_emitir_codigo(%s, %s, %s, %s, %s)",
                    (
                        beneficio.id,
                        idempotency_key,
                        codigo_hash,
                        consumidor_id,
                        beneficio.maximo_unidades_codigo,
                    ),
                )
                _, emitido_real, vence_en = cursor.fetchone()
                return CodigoBeneficio(beneficio.id, codigo, emitido_real, vence_en)
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def validar_codigo(
        self,
        codigo: str,
        comercio_id: str,
        instante: datetime,
    ) -> ValidacionCodigo | None:
        codigo_hash = _codigo_hash(codigo)
        if codigo_hash is None:
            return None

        try:
            with self._conectar() as conexion, conexion.cursor(
                row_factory=dict_row
            ) as cursor:
                cursor.execute(
                    """
                    with reloj as (
                      select %s::timestamptz as ahora
                    ),
                    candidato as (
                      select
                        c.id as codigo_id,
                        c.beneficio_id,
                        c.comercio_id,
                        b.titulo,
                        c.emitido_en,
                        c.vence_en,
                        c.utilizado_en,
                        b.dias_semana_mask,
                        b.hora_desde,
                        b.hora_hasta,
                        (r.ahora at time zone b.zona_horaria)
                          as instante_local
                      from public.beneficios_codigos c
                      join public.beneficios b
                        on b.id = c.beneficio_id
                      cross join reloj r
                      where c.codigo_hash = %s
                        and c.comercio_id = %s
                        and c.utilizado_en is null
                        and c.vence_en > r.ahora
                        and b.activo
                        and b.permite_whatsapp
                        and b.vigente_desde <= r.ahora
                        and r.ahora < b.vigente_hasta
                    ),
                    ajustado as (
                      select
                        candidato.*,
                        case
                          when hora_desde > hora_hasta
                           and instante_local::time <= hora_hasta
                          then instante_local - interval '1 day'
                          else instante_local
                        end as instante_habil
                      from candidato
                    )
                    select
                      codigo_id,
                      beneficio_id,
                      comercio_id,
                      titulo,
                      emitido_en,
                      vence_en,
                      utilizado_en
                    from ajustado
                    where (
                      dias_semana_mask
                      & (
                        1 << (
                          extract(isodow from instante_habil)::integer - 1
                        )
                      )
                    ) <> 0
                    and (
                      (
                        hora_desde is null
                        and hora_hasta is null
                      )
                      or (
                        hora_desde < hora_hasta
                        and instante_habil::time
                          between hora_desde and hora_hasta
                      )
                      or (
                        hora_desde > hora_hasta
                        and (
                          instante_habil::time >= hora_desde
                          or instante_habil::time <= hora_hasta
                        )
                      )
                    )
                    """,
                    (instante, codigo_hash, comercio_id),
                )
                fila = cursor.fetchone()
                return _validacion_desde_fila(fila) if fila else None
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error

    def consumir_codigo(
        self,
        codigo: str,
        comercio_id: str,
    ) -> ValidacionCodigo:
        codigo_hash = _codigo_hash(codigo)
        if codigo_hash is None:
            raise CodigoInvalidoError()

        try:
            with self._conectar() as conexion, conexion.cursor(
                row_factory=dict_row
            ) as cursor:
                cursor.execute(
                    """
                    select codigo_id, beneficio_id, utilizado_en
                    from public.beneficios_consumir_codigo(
                      %s, 'whatsapp', %s, null, null, null
                    )
                    """,
                    (codigo_hash, comercio_id),
                )
                consumido = cursor.fetchone()
                cursor.execute(
                    """
                    select
                      c.id as codigo_id,
                      c.beneficio_id,
                      c.comercio_id,
                      b.titulo,
                      c.emitido_en,
                      c.vence_en,
                      c.utilizado_en
                    from public.beneficios_codigos c
                    join public.beneficios b
                      on b.id = c.beneficio_id
                    where c.id = %s
                      and c.comercio_id = %s
                    """,
                    (consumido["codigo_id"], comercio_id),
                )
                return _validacion_desde_fila(cursor.fetchone())
        except RaiseException as error:
            raise CodigoInvalidoError() from error
        except CodigoInvalidoError:
            raise
        except Exception as error:
            raise PersistenciaBeneficiosError(str(error)) from error


def _mascara_dias(dias: frozenset[int]) -> int:
    return sum(1 << dia for dia in dias)


def _dias_desde_mascara(mascara: int) -> frozenset[int]:
    return frozenset(dia for dia in range(7) if mascara & (1 << dia))


def _insertar_beneficio(
    cursor,
    beneficio: Beneficio,
    *,
    edicion_anterior_id: str | None = None,
) -> Beneficio:
    publicacion_id = (
        beneficio.objetivo.id
        if beneficio.objetivo.tipo is TipoObjetivo.PUBLICACION
        else None
    )
    producto_id = (
        beneficio.objetivo.id
        if beneficio.objetivo.tipo is TipoObjetivo.GASTRONOMIA_PRODUCTO
        else None
    )
    cursor.execute(
        """
        insert into public.beneficios (
          id, serie_id, numero_edicion, edicion_anterior_id, comercio_id,
          titulo, detalle_promocion, tipo_objetivo, publicacion_id, gastronomia_producto_id,
          tipo_descuento, valor_descuento, vigente_desde, vigente_hasta,
          dias_semana_mask, hora_desde, hora_hasta, zona_horaria,
          cupo_maximo, maximo_unidades_codigo, mostrar_codigos_restantes,
          tema_etiqueta, permite_whatsapp, permite_qr, permite_carrito, activo
        ) values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) returning *
        """,
        (
            beneficio.id, beneficio.serie_id or beneficio.id,
            beneficio.numero_edicion, edicion_anterior_id,
            beneficio.comercio_id, beneficio.titulo, beneficio.detalle_promocion,
            beneficio.objetivo.tipo.value, publicacion_id, producto_id,
            beneficio.tipo_descuento.value, beneficio.valor_descuento,
            beneficio.vigente_desde, beneficio.vigente_hasta,
            _mascara_dias(beneficio.dias_semana), beneficio.hora_desde,
            beneficio.hora_hasta, beneficio.zona_horaria,
            beneficio.cupo_maximo, beneficio.maximo_unidades_codigo,
            beneficio.mostrar_codigos_restantes,
            beneficio.tema_etiqueta.value,
            MetodoUtilizacion.WHATSAPP in beneficio.metodos,
            MetodoUtilizacion.QR in beneficio.metodos,
            MetodoUtilizacion.CARRITO in beneficio.metodos,
            beneficio.activo,
        ),
    )
    return _beneficio_desde_fila(cursor.fetchone())


def _beneficio_desde_fila(fila) -> Beneficio:
    tipo_objetivo = TipoObjetivo(fila["tipo_objetivo"])
    if tipo_objetivo is TipoObjetivo.COMERCIO:
        objetivo_id = fila["comercio_id"]
    elif tipo_objetivo is TipoObjetivo.PUBLICACION:
        objetivo_id = fila["publicacion_id"]
    else:
        objetivo_id = fila["gastronomia_producto_id"]
    metodos = frozenset(
        metodo for metodo, permitido in (
            (MetodoUtilizacion.WHATSAPP, fila["permite_whatsapp"]),
            (MetodoUtilizacion.QR, fila["permite_qr"]),
            (MetodoUtilizacion.CARRITO, fila["permite_carrito"]),
        ) if permitido
    )
    return Beneficio(
        id=str(fila["id"]), serie_id=str(fila["serie_id"]),
        numero_edicion=fila["numero_edicion"], comercio_id=str(fila["comercio_id"]),
        titulo=fila["titulo"], detalle_promocion=fila.get("detalle_promocion"),
        objetivo=ObjetivoBeneficio(tipo_objetivo, str(objetivo_id)),
        tipo_descuento=TipoDescuento(fila["tipo_descuento"]),
        valor_descuento=fila["valor_descuento"], vigente_desde=fila["vigente_desde"],
        vigente_hasta=fila["vigente_hasta"], activo=fila["activo"],
        dias_semana=_dias_desde_mascara(fila["dias_semana_mask"]),
        hora_desde=fila["hora_desde"], hora_hasta=fila["hora_hasta"],
        zona_horaria=fila["zona_horaria"], cupo_maximo=fila["cupo_maximo"],
        maximo_unidades_codigo=fila["maximo_unidades_codigo"],
        mostrar_codigos_restantes=fila["mostrar_codigos_restantes"],
        tema_etiqueta=TemaEtiqueta(fila["tema_etiqueta"]), metodos=metodos,
    )


def _codigo_hash(codigo: str) -> bytes | None:
    normalizado = str(codigo or "").strip().upper()
    if not re.fullmatch(
        r"[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}",
        normalizado,
    ):
        return None
    return sha256(normalizado.encode("utf-8")).digest()


def _validacion_desde_fila(fila) -> ValidacionCodigo:
    return ValidacionCodigo(
        codigo_id=str(fila["codigo_id"]),
        beneficio_id=str(fila["beneficio_id"]),
        comercio_id=str(fila["comercio_id"]),
        titulo=fila["titulo"],
        emitido_en=fila["emitido_en"],
        vence_en=fila["vence_en"],
        utilizado_en=fila["utilizado_en"],
    )
