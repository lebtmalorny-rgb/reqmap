"""Строгая автономная загрузка JSON-compatible YAML конфигурации."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from reqmap.errors import ConfigError


@dataclass(frozen=True, repr=False)
class ModelConfig:
    base_url: str
    model: str
    api_key_env: str | None
    api_key: str | None
    timeout_seconds: float = 60.0
    retries: int = 2
    supports_response_format: bool = True
    seed: int | None = 1
    max_prompt_chars: int = 50000
    max_response_bytes: int = 1048576

    def __repr__(self) -> str:
        return (
            f"ModelConfig(base_url={self.base_url!r}, model={self.model!r}, "
            "api_key=<redacted>)"
        )


@dataclass(frozen=True)
class InputProfile:
    include_sheets: tuple[str, ...]
    exclude_sheets: tuple[str, ...]
    header_row: int
    id_column: str
    text_column: str
    priority_column: str | None = None
    expected_result_column: str | None = None
    parent_column: str | None = None
    hint_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    knowledge_path: Path
    input_profile: InputProfile | None
    top_k: int


def load_config(path: Path, environ: Mapping[str, str]) -> AppConfig:
    """Читает конфигурацию без YAML-парсера и без раскрытия секретов."""
    try:
        source = path.read_text(encoding="utf-8")
        without_comments = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        raw = json.loads(without_comments)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            "CONFIG_INVALID", f"Не удалось прочитать конфигурацию: {exc}"
        ) from exc

    _require_mapping(raw, "конфигурация")
    _reject_unknown(raw, {"model", "knowledge_path", "input_profile", "top_k"})
    return _build_app_config(raw, environ, path.parent)


def _build_app_config(
    raw: Mapping[str, Any], environ: Mapping[str, str], config_directory: Path
) -> AppConfig:
    model = _build_model(_required(raw, "model", "конфигурация"), environ)
    knowledge_path_value = _required(raw, "knowledge_path", "конфигурация")
    _require_nonempty_string(knowledge_path_value, "knowledge_path")
    knowledge_path = Path(knowledge_path_value)
    if not knowledge_path.is_absolute():
        knowledge_path = config_directory / knowledge_path

    top_k = raw.get("top_k", 8)
    _require_int(top_k, "top_k")
    if not 1 <= top_k <= 50:
        _invalid("top_k должен быть в диапазоне от 1 до 50")

    profile_raw = raw.get("input_profile")
    input_profile = None if profile_raw is None else _build_input_profile(profile_raw)
    return AppConfig(model, knowledge_path, input_profile, top_k)


def _build_model(raw: object, environ: Mapping[str, str]) -> ModelConfig:
    _require_mapping(raw, "model")
    _reject_unknown(
        raw,
        {
            "base_url",
            "model",
            "api_key_env",
            "timeout_seconds",
            "retries",
            "supports_response_format",
            "seed",
            "max_prompt_chars",
            "max_response_bytes",
        },
    )
    base_url = _required(raw, "base_url", "model")
    _require_nonempty_string(base_url, "model.base_url")
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        _invalid("model.base_url должен начинаться с http:// или https://")

    model_name = _required(raw, "model", "model")
    _require_nonempty_string(model_name, "model.model")
    api_key_env = raw.get("api_key_env")
    if api_key_env is not None:
        _require_nonempty_string(api_key_env, "model.api_key_env")
    api_key = environ.get(api_key_env) if api_key_env else None

    timeout_seconds = raw.get("timeout_seconds", 60.0)
    _require_number(timeout_seconds, "model.timeout_seconds")
    if timeout_seconds <= 0:
        _invalid("model.timeout_seconds должен быть больше нуля")
    retries = raw.get("retries", 2)
    _require_nonnegative_int(retries, "model.retries")
    supports_response_format = raw.get("supports_response_format", True)
    if not isinstance(supports_response_format, bool):
        _invalid("model.supports_response_format должен быть логическим значением")
    seed = raw.get("seed", 1)
    if seed is not None:
        _require_int(seed, "model.seed")
    max_prompt_chars = raw.get("max_prompt_chars", 50000)
    _require_positive_int(max_prompt_chars, "model.max_prompt_chars")
    max_response_bytes = raw.get("max_response_bytes", 1048576)
    _require_positive_int(max_response_bytes, "model.max_response_bytes")
    return ModelConfig(
        base_url=base_url,
        model=model_name,
        api_key_env=api_key_env,
        api_key=api_key,
        timeout_seconds=float(timeout_seconds),
        retries=retries,
        supports_response_format=supports_response_format,
        seed=seed,
        max_prompt_chars=max_prompt_chars,
        max_response_bytes=max_response_bytes,
    )


def _build_input_profile(raw: object) -> InputProfile:
    _require_mapping(raw, "input_profile")
    _reject_unknown(
        raw,
        {
            "include_sheets",
            "exclude_sheets",
            "header_row",
            "id_column",
            "text_column",
            "priority_column",
            "expected_result_column",
            "parent_column",
            "hint_columns",
        },
    )
    include_sheets = _string_tuple(
        _required(raw, "include_sheets", "input_profile"), "input_profile.include_sheets"
    )
    exclude_sheets = _string_tuple(raw.get("exclude_sheets", []), "input_profile.exclude_sheets")
    header_row = _required(raw, "header_row", "input_profile")
    _require_positive_int(header_row, "input_profile.header_row")
    id_column = _required(raw, "id_column", "input_profile")
    text_column = _required(raw, "text_column", "input_profile")
    _require_nonempty_string(id_column, "input_profile.id_column")
    _require_nonempty_string(text_column, "input_profile.text_column")
    return InputProfile(
        include_sheets=include_sheets,
        exclude_sheets=exclude_sheets,
        header_row=header_row,
        id_column=id_column,
        text_column=text_column,
        priority_column=_optional_string(raw.get("priority_column"), "input_profile.priority_column"),
        expected_result_column=_optional_string(
            raw.get("expected_result_column"), "input_profile.expected_result_column"
        ),
        parent_column=_optional_string(raw.get("parent_column"), "input_profile.parent_column"),
        hint_columns=_string_tuple(raw.get("hint_columns", []), "input_profile.hint_columns"),
    )


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        _invalid(f"Неизвестные параметры: {', '.join(unknown)}")


def _required(raw: Mapping[str, Any], key: str, location: str) -> object:
    if key not in raw:
        _invalid(f"Не указан обязательный параметр {location}.{key}")
    return raw[key]


def _require_mapping(value: object, location: str) -> None:
    if not isinstance(value, dict):
        _invalid(f"{location} должен быть объектом")


def _require_nonempty_string(value: object, field: str) -> None:
    if not isinstance(value, str) or not value:
        _invalid(f"{field} должен быть непустой строкой")


def _require_number(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(f"{field} должен быть числом")


def _require_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(f"{field} должен быть целым числом")


def _require_nonnegative_int(value: object, field: str) -> None:
    _require_int(value, field)
    if value < 0:
        _invalid(f"{field} не должен быть отрицательным")


def _require_positive_int(value: object, field: str) -> None:
    _require_int(value, field)
    if value <= 0:
        _invalid(f"{field} должен быть больше нуля")


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    _require_nonempty_string(value, field)
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _invalid(f"{field} должен быть списком непустых строк")
    return tuple(value)


def _invalid(message: str) -> None:
    raise ConfigError("CONFIG_INVALID", message)
