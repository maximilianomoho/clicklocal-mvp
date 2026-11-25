"""Configuración de Beneficios explícita y separada por entorno."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


BASE_AISLADA = "clicklocal_beneficios_ui_test"
USUARIO_AISLADO = "debian"
SOCKET_AISLADO = (
    "/home/debian/PROYECTO-LIMPIO/"
    "clicklocal-postgresql-pruebas/socket"
)
PUERTO_AISLADO = 55432


@dataclass(frozen=True, slots=True)
class ConfiguracionBeneficios:
    database_url: str | None = field(repr=False)
    secreto_codigos: bytes = field(repr=False)
    modo_aislado: bool = False

    @classmethod
    def desde_entorno(
        cls,
        entorno: Mapping[str, str],
    ) -> "ConfiguracionBeneficios":
        modo_aislado = str(
            entorno.get("BENEFICIOS_MODO_AISLADO", "")
        ).strip() == "1"
        database_url = str(
            entorno.get("BENEFICIOS_DATABASE_URL", "")
        ).strip()

        if modo_aislado and database_url:
            raise ValueError(
                "La configuración aislada y la URL de base son excluyentes"
            )

        if not modo_aislado:
            if not database_url:
                raise ValueError(
                    "BENEFICIOS_DATABASE_URL es obligatoria"
                )
            try:
                partes = urlsplit(database_url)
                puerto = partes.port
            except ValueError as error:
                raise ValueError(
                    "BENEFICIOS_DATABASE_URL no es válida"
                ) from error
            if (
                partes.scheme not in {"postgres", "postgresql"}
                or not partes.hostname
                or not partes.username
                or not partes.password
                or not partes.path.strip("/")
                or puerto is None
            ):
                raise ValueError(
                    "BENEFICIOS_DATABASE_URL no es válida"
                )

        secreto = str(
            entorno.get("BENEFICIOS_CODIGOS_SECRET", "")
        ).encode("utf-8")

        if len(secreto) < 32:
            raise ValueError(
                "BENEFICIOS_CODIGOS_SECRET debe tener al menos 32 bytes"
            )

        return cls(
            database_url=database_url or None,
            secreto_codigos=secreto,
            modo_aislado=modo_aislado,
        )

    def parametros_conexion(self) -> dict[str, object]:
        if not self.modo_aislado:
            return {"conninfo": self.database_url}
        return {
            "dbname": BASE_AISLADA,
            "user": USUARIO_AISLADO,
            "host": SOCKET_AISLADO,
            "port": PUERTO_AISLADO,
        }
