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


class InputProfileError(ReqmapError):
    """Ошибка безопасного определения или применения входного XLSX-профиля."""


class ValidationError(ReqmapError):
    """Канонический результат нарушает схему или предметный инвариант."""


class ModelError(ReqmapError):
    """Ошибка обращения к локальной OpenAI-compatible модели."""


class ModelOutputError(ModelError):
    """Модель вернула ответ, который нельзя безопасно использовать."""

    def __init__(self, code: str, message_ru: str, raw_response: str) -> None:
        super().__init__(code, message_ru)
        self.raw_response = raw_response
