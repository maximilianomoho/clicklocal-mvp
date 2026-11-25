import pytest

from beneficios.config import (
    BASE_AISLADA,
    PUERTO_AISLADO,
    SOCKET_AISLADO,
    USUARIO_AISLADO,
    ConfiguracionBeneficios,
)


SECRETO = "secreto-ficticio-de-pruebas-con-32-bytes-minimo"


def test_configuracion_aislada_exige_activacion_explicita():
    configuracion = ConfiguracionBeneficios.desde_entorno({
        "BENEFICIOS_MODO_AISLADO": "1",
        "BENEFICIOS_CODIGOS_SECRET": SECRETO,
    })

    assert configuracion.parametros_conexion() == {
        "dbname": BASE_AISLADA,
        "user": USUARIO_AISLADO,
        "host": SOCKET_AISLADO,
        "port": PUERTO_AISLADO,
    }
    assert configuracion.secreto_codigos == SECRETO.encode("utf-8")
    assert configuracion.modo_aislado is True


def test_configuracion_normal_usa_url_completa():
    url = "postgresql://usuario:clave-ficticia@db.example.invalid:5432/clicklocal"
    configuracion = ConfiguracionBeneficios.desde_entorno({
        "BENEFICIOS_DATABASE_URL": url,
        "BENEFICIOS_CODIGOS_SECRET": SECRETO,
    })

    assert configuracion.parametros_conexion() == {"conninfo": url}
    assert configuracion.modo_aislado is False


def test_configuracion_no_usa_base_aislada_por_defecto():
    with pytest.raises(
        ValueError,
        match="BENEFICIOS_DATABASE_URL es obligatoria",
    ):
        ConfiguracionBeneficios.desde_entorno({
            "BENEFICIOS_CODIGOS_SECRET": SECRETO,
        })


@pytest.mark.parametrize("url", (
    "",
    "https://usuario:clave@db.example.invalid:5432/clicklocal",
    "postgresql://db.example.invalid:5432/clicklocal",
    "postgresql://usuario:clave@db.example.invalid/clicklocal",
    "postgresql://usuario:clave@db.example.invalid:5432/",
    "postgresql://usuario:clave@db.example.invalid:puerto/clicklocal",
))
def test_configuracion_rechaza_url_incompleta_o_invalida_sin_exponerla(url):
    entorno = {
        "BENEFICIOS_DATABASE_URL": url,
        "BENEFICIOS_CODIGOS_SECRET": SECRETO,
    }

    with pytest.raises(ValueError) as error:
        ConfiguracionBeneficios.desde_entorno(entorno)

    assert url not in str(error.value) or not url


def test_configuracion_rechaza_modos_simultaneos():
    with pytest.raises(
        ValueError,
        match="son excluyentes",
    ):
        ConfiguracionBeneficios.desde_entorno({
            "BENEFICIOS_MODO_AISLADO": "1",
            "BENEFICIOS_DATABASE_URL": (
                "postgresql://usuario:clave-ficticia@"
                "db.example.invalid:5432/clicklocal"
            ),
            "BENEFICIOS_CODIGOS_SECRET": SECRETO,
        })


@pytest.mark.parametrize("secreto", ("", "demasiado-corto"))
def test_configuracion_exige_secreto_robusto(secreto):
    with pytest.raises(
        ValueError,
        match="debe tener al menos 32 bytes",
    ):
        ConfiguracionBeneficios.desde_entorno({
            "BENEFICIOS_MODO_AISLADO": "1",
            "BENEFICIOS_CODIGOS_SECRET": secreto,
        })


def test_repr_no_expone_url_ni_secreto():
    url = "postgresql://usuario:clave-ficticia@db.example.invalid:5432/clicklocal"
    configuracion = ConfiguracionBeneficios.desde_entorno({
        "BENEFICIOS_DATABASE_URL": url,
        "BENEFICIOS_CODIGOS_SECRET": SECRETO,
    })

    representacion = repr(configuracion)
    assert url not in representacion
    assert SECRETO not in representacion
