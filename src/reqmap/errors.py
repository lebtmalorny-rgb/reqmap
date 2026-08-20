class ReqmapError(Exception):
    """Базовая диагностическая ошибка reqmap с машиночитаемым кодом."""

    def __init__(
        self,
        code: str,
        message_ru: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message_ru)
        self.code = code
        self.message_ru = message_ru
        self.details = {} if details is None else details


class ConfigError(ReqmapError):
    """Ошибка чтения или проверки конфигурации."""
