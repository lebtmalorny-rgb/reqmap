"""Строгий клиент локального OpenAI-compatible API без внешних зависимостей."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from http.client import HTTPException
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from reqmap.config import ModelConfig
from reqmap.errors import ModelError, ModelOutputError


class JsonModel(Protocol):
    """Минимальная граница модели для декомпозиции и сопоставления."""

    def complete_json(
        self, stage: str, system_prompt: str, payload: dict[str, object]
    ) -> dict[str, object]: ...


def should_retry(status: int | None, attempt: int, retries: int) -> bool:
    """Возвращает, допустима ли ещё одна попытка для HTTP/transport ошибки."""
    return (status is None or status == 429 or 500 <= status <= 599) and attempt < retries


class OpenAICompatibleClient:
    """Клиент двух требуемых OpenAI-compatible endpoint-ов."""

    def __init__(
        self,
        config: ModelConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._opener = opener

    def preflight(self) -> None:
        """Проверяет доступность endpoint-а и точное имя настроенной модели."""
        raw = self._request("GET", "models", None)
        response = self._parse_object_or_preflight_error(raw)
        data = response.get("data")
        if not isinstance(data, list):
            raise ModelError(
                "MODEL_PREFLIGHT_INVALID",
                "Ответ endpoint-а /models имеет недопустимую структуру.",
            )

        model_ids: set[str] = set()
        for item in data:
            model_id = item.get("id") if isinstance(item, Mapping) else None
            if not isinstance(model_id, str):
                raise ModelError(
                    "MODEL_PREFLIGHT_INVALID",
                    "Ответ endpoint-а /models имеет недопустимую структуру.",
                )
            model_ids.add(model_id)
        if self._config.model not in model_ids:
            raise ModelError(
                "MODEL_NOT_AVAILABLE",
                "Настроенная модель недоступна в endpoint-е /models.",
            )

    def complete_json(
        self, stage: str, system_prompt: str, payload: dict[str, object]
    ) -> dict[str, object]:
        """Отправляет детерминированный prompt и принимает только JSON object."""
        del stage  # Этап нужен вызывающему pipeline, но не является полем OpenAI API.
        prompt, request_bytes = self._serialize_prompt(system_prompt, payload)
        if len(prompt) > self._config.max_prompt_chars:
            raise ModelError(
                "MODEL_PROMPT_TOO_LARGE",
                "Сформированный запрос к модели превышает допустимый размер.",
            )

        raw = self._request("POST", "chat/completions", request_bytes)
        envelope = self._parse_output_object(raw)
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._output_error(raw)
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise self._output_error(raw)
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise self._output_error(raw)
        content = message.get("content")
        if not isinstance(content, str):
            raise self._output_error(raw)
        return self._parse_output_object(content)

    def _serialize_prompt(
        self, system_prompt: str, payload: dict[str, object]
    ) -> tuple[str, bytes]:
        """Сериализует prompt строго и без передачи Python ошибок наружу."""
        try:
            request_body: dict[str, object] = {
                "model": self._config.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": _strict_dump(payload),
                    },
                ],
            }
            if self._config.supports_response_format:
                request_body["response_format"] = {"type": "json_object"}
            if self._config.seed is not None:
                request_body["seed"] = self._config.seed
            prompt = _strict_dump(request_body)
            request_bytes = prompt.encode("utf-8")
        except (RecursionError, TypeError, UnicodeError, ValueError, OverflowError):
            serialization_failed = True
        else:
            serialization_failed = False
        if serialization_failed:
            raise ModelError(
                "MODEL_PROMPT_INVALID",
                "Не удалось строго сериализовать запрос к локальной модели.",
            )
        return prompt, request_bytes

    def _request(self, method: str, endpoint: str, body: bytes | None) -> str:
        request = Request(
            self._endpoint_url(endpoint),
            data=body,
            method=method,
            headers=self._headers(body is not None),
        )
        for attempt in range(self._config.retries + 1):
            try:
                response = self._opener(request, timeout=self._config.timeout_seconds)
                return self._read_response(response)
            except HTTPError as error:
                status = error.code
                self._close_http_error(error)
            except (HTTPException, URLError, OSError, TimeoutError):
                status = None
            except ModelError:
                raise

            if should_retry(status, attempt, self._config.retries):
                self._sleep(0.25 * 2**attempt)
                continue
            if status is None:
                raise ModelError(
                    "MODEL_TRANSPORT_ERROR",
                    "Не удалось подключиться к локальному endpoint-у модели.",
                )
            raise ModelError(
                "MODEL_HTTP_ERROR",
                f"Локальный endpoint модели вернул HTTP status {status}.",
                {"http_status": status},
            )
        raise AssertionError("Цикл запросов завершился без результата")

    def _headers(self, is_json_request: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if is_json_request:
            headers["Content-Type"] = "application/json"
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _endpoint_url(self, endpoint: str) -> str:
        """Присоединяет API path, не теряя префикс base_url вроде /v1."""
        parts = urlsplit(self._config.base_url)
        base_path = parts.path.rstrip("/")
        path = f"{base_path}/{endpoint.lstrip('/')}"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    def _read_response(self, response: object) -> str:
        try:
            read = getattr(response, "read")
            body = read(self._config.max_response_bytes + 1)
        finally:
            self._close_response(response)
        if not isinstance(body, bytes):
            raise ModelError(
                "MODEL_TRANSPORT_ERROR",
                "Локальный endpoint модели вернул недопустимое тело ответа.",
            )
        if len(body) > self._config.max_response_bytes:
            raise ModelError(
                "MODEL_RESPONSE_TOO_LARGE",
                "Ответ локальной модели превышает допустимый размер.",
            )
        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError:
            invalid_utf8 = True
        else:
            invalid_utf8 = False
        if invalid_utf8:
            raise ModelOutputError(
                "MODEL_OUTPUT_INVALID",
                "Модель вернула ответ не в UTF-8 кодировке.",
                self._sanitize_raw(body.decode("utf-8", errors="replace")),
            )
        return decoded

    @staticmethod
    def _close_response(response: object) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    def _close_http_error(self, error: HTTPError) -> None:
        try:
            error.read(self._config.max_response_bytes + 1)
        except (HTTPException, OSError):
            pass
        finally:
            try:
                error.close()
            except (HTTPException, OSError):
                pass

    def _parse_object_or_preflight_error(self, raw: str) -> dict[str, object]:
        try:
            parsed = _strict_object(raw)
        except (RecursionError, ValueError):
            invalid_json = True
        else:
            invalid_json = False
        if invalid_json:
            raise ModelError(
                "MODEL_PREFLIGHT_INVALID",
                "Ответ endpoint-а /models не является строгим JSON object.",
            )
        return parsed

    def _parse_output_object(self, raw: str) -> dict[str, object]:
        try:
            parsed = _strict_object(raw)
        except (RecursionError, ValueError):
            invalid_json = True
        else:
            invalid_json = False
        if invalid_json:
            raise self._output_error(raw)
        return parsed

    def _output_error(self, raw: str) -> ModelOutputError:
        return ModelOutputError(
            "MODEL_OUTPUT_INVALID",
            "Модель вернула ответ, не соответствующий строгому JSON-контракту.",
            self._sanitize_raw(raw),
        )

    def _sanitize_raw(self, raw: str) -> str:
        limit = 4096
        redacted = raw
        if self._config.api_key:
            for representation in _secret_representations(
                self._config.api_key, len(raw)
            ):
                redacted = redacted.replace(representation, "<redacted>")
        bounded = redacted[:limit]
        if len(redacted) > limit:
            bounded += "…[truncated]"
        return bounded


def _strict_object(raw: str) -> dict[str, object]:
    decoder = json.JSONDecoder(
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_non_finite_constant,
        parse_float=_finite_float,
    )
    value = decoder.decode(raw)
    if not isinstance(value, dict):
        raise ValueError("Ожидался JSON object")
    return cast(dict[str, object], value)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющийся ключ JSON: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> object:
    raise ValueError(f"Недопустимая JSON-константа: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Недопустимое неограниченное число JSON: {value}")
    return parsed


def _strict_dump(value: object) -> str:
    _require_string_mapping_keys(value, set())
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_string_mapping_keys(value: object, active: set[int]) -> None:
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            return
        active.add(marker)
        try:
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                _require_string_mapping_keys(nested, active)
        finally:
            active.remove(marker)
    elif isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in active:
            return
        active.add(marker)
        try:
            for nested in value:
                _require_string_mapping_keys(nested, active)
        finally:
            active.remove(marker)


def _secret_representations(secret: str, max_length: int) -> tuple[str, ...]:
    """Возвращает plain и все релевантные вложенные JSON-экранирования секрета."""
    known = {secret}
    pending = [secret]
    while pending:
        current = pending.pop()
        for ensure_ascii in (False, True):
            encoded = json.dumps(current, ensure_ascii=ensure_ascii)[1:-1]
            if not encoded or len(encoded) > max_length or encoded in known:
                continue
            known.add(encoded)
            pending.append(encoded)
    return tuple(sorted(known, key=len, reverse=True))
