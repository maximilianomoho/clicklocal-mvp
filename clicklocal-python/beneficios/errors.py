"""Errores públicos y seguros del módulo Beneficios."""


class BeneficiosError(Exception):
    """Base para errores controlados del módulo."""

    mensaje_publico = "No pudimos procesar el beneficio."


class ValidacionBeneficioError(BeneficiosError, ValueError):
    """Los datos recibidos violan una regla de negocio."""

    mensaje_publico = "Revisá los datos del beneficio."


class BeneficioNoEncontradoError(BeneficiosError, LookupError):
    """No existe un beneficio con el identificador solicitado."""

    mensaje_publico = "El beneficio solicitado no está disponible."


class BeneficioNoVigenteError(BeneficiosError):
    """El beneficio existe, pero no puede utilizarse ahora."""

    mensaje_publico = "El beneficio no está vigente."


class CodigoNoDisponibleError(BeneficiosError):
    """No fue posible emitir un código válido."""

    mensaje_publico = "No pudimos generar el código del beneficio."


class CodigoInvalidoError(BeneficiosError):
    mensaje_publico = "El código no existe, venció o ya fue utilizado."


class PersistenciaBeneficiosError(BeneficiosError):
    """La capa de persistencia falló o no está configurada."""

    mensaje_publico = "Beneficios no está disponible temporalmente."


class PersistenciaNoConfiguradaError(PersistenciaBeneficiosError):
    """Beneficios 1A todavía no tiene un repositorio real conectado."""
