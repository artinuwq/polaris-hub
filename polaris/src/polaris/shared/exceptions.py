class PolarisError(Exception):
    """Базовое исключение Polaris."""


class ConfigurationError(PolarisError):
    """Ошибка конфигурации."""


class AuthorizationError(PolarisError):
    """Недостаточно прав."""
