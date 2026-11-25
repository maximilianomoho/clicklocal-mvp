from uuid import UUID

import pytest

from beneficios.identity import (
    CLAVE_CONSUMIDOR,
    CLAVE_IDEMPOTENCIAS,
    ContextoComercianteAutenticado,
    resolver_idempotencia_beneficio,
    resolver_identidad_comerciante,
    resolver_identidad_consumidor,
)


USUARIO = "20000000-0000-4000-8000-000000000001"
COMERCIO = "20000000-0000-4000-8000-000000000002"
CONSUMIDOR = "20000000-0000-4000-8000-000000000003"
BENEFICIO = "20000000-0000-4000-8000-000000000004"


def test_crea_identidad_anonima_en_sesion_controlada():
    sesion = {}

    identidad = resolver_identidad_consumidor(sesion)

    assert identidad.consumidor_id == sesion[CLAVE_CONSUMIDOR]
    assert str(UUID(identidad.consumidor_id)) == identidad.consumidor_id


def test_reutiliza_identidad_anonima_de_la_sesion():
    sesion = {
        CLAVE_CONSUMIDOR: CONSUMIDOR,
    }

    primera = resolver_identidad_consumidor(sesion)
    segunda = resolver_identidad_consumidor(sesion)

    assert primera.consumidor_id == CONSUMIDOR
    assert segunda.consumidor_id == CONSUMIDOR


def test_reemplaza_identidad_invalida_sin_aceptarla():
    sesion = {
        CLAVE_CONSUMIDOR: "valor-alterado",
    }

    identidad = resolver_identidad_consumidor(sesion)

    assert identidad.consumidor_id != "valor-alterado"
    assert sesion[CLAVE_CONSUMIDOR] == identidad.consumidor_id
    assert str(UUID(identidad.consumidor_id)) == identidad.consumidor_id


def test_idempotencia_es_estable_por_beneficio_y_nace_en_servidor():
    sesion = {}

    primera = resolver_idempotencia_beneficio(sesion, BENEFICIO)
    segunda = resolver_idempotencia_beneficio(sesion, BENEFICIO)

    assert primera == segunda
    assert str(UUID(primera)) == primera
    assert sesion[CLAVE_IDEMPOTENCIAS][BENEFICIO] == primera


def test_idempotencia_alterada_se_reemplaza():
    sesion = {CLAVE_IDEMPOTENCIAS: {BENEFICIO: "alterada"}}

    resultado = resolver_idempotencia_beneficio(sesion, BENEFICIO)

    assert resultado != "alterada"
    assert str(UUID(resultado)) == resultado


def test_identidad_comercial_procede_de_contexto_autenticado():
    llamadas = []

    def obtener_contexto():
        llamadas.append(True)
        return ContextoComercianteAutenticado(
            usuario_id=USUARIO,
            comercio_id=COMERCIO,
        )

    identidad = resolver_identidad_comerciante(
        obtener_contexto
    )

    assert llamadas == [True]
    assert identidad.usuario_id == USUARIO
    assert identidad.comercio_id == COMERCIO


def test_uuid_en_sesion_no_autentica_comerciante():
    sesion = {
        "user_id": USUARIO,
        "comercio": {"id": COMERCIO},
    }

    def sin_contexto_autenticado():
        return None

    with pytest.raises(
        PermissionError,
        match="sesión autenticada",
    ):
        resolver_identidad_comerciante(
            sin_contexto_autenticado
        )

    assert sesion["user_id"] == USUARIO
    assert sesion["comercio"]["id"] == COMERCIO


@pytest.mark.parametrize(
    "contexto",
    (
        ContextoComercianteAutenticado(
            usuario_id="usuario-invalido",
            comercio_id=COMERCIO,
        ),
        ContextoComercianteAutenticado(
            usuario_id=USUARIO,
            comercio_id="comercio-invalido",
        ),
    ),
)
def test_rechaza_contexto_autenticado_invalido(contexto):
    with pytest.raises(
        PermissionError,
        match="contexto autenticado no es válido",
    ):
        resolver_identidad_comerciante(
            lambda: contexto
        )


def test_identidad_no_decide_pertenencia_de_publicacion():
    identidad = resolver_identidad_comerciante(
        lambda: ContextoComercianteAutenticado(
            usuario_id=USUARIO,
            comercio_id=COMERCIO,
        )
    )

    assert not hasattr(identidad, "publicacion_id")
    assert not hasattr(identidad, "publicacion_autorizada")
