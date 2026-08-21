import json
from collections import deque
from typing import Any
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

import pytest

from reqmap.config import ModelConfig
from reqmap.errors import ModelError, ModelOutputError
from reqmap.llm import OpenAICompatibleClient


class FakeLlmServer:
    """Детерминированная замена urlopen на границе локального endpoint-а."""

    def __init__(self) -> None:
        self.responses: deque[tuple[int, bytes]] = deque()
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []

    @property
    def base_url(self) -> str:
        return "http://local-llm.invalid/v1"

    def enqueue(self, value: object, status: int = 200) -> None:
        self.responses.append((status, json.dumps(value, ensure_ascii=False).encode("utf-8")))

    def enqueue_text(self, value: str, status: int = 200) -> None:
        self.responses.append((status, value.encode("utf-8")))

    def enqueue_bytes(self, value: bytes, status: int = 200) -> None:
        self.responses.append((status, value))

    def open(self, request: Request, timeout: float) -> object:
        del timeout
        headers = {key.lower(): value for key, value in request.header_items()}
        self.requests.append(
            (
                request.get_method(),
                urlsplit(request.full_url).path,
                headers,
                request.data or b"",
            )
        )
        status, response = self.responses.popleft()
        if status >= 400:
            raise HTTPError(request.full_url, status, "server error", None, BytesIO(response))
        return _RecordingResponse(response)


@pytest.fixture
def fake_llm_server() -> FakeLlmServer:
    return FakeLlmServer()


def client_for(
    server: FakeLlmServer,
    *,
    api_key: str | None = None,
    retries: int = 0,
    seed: int | None = 7,
    supports_response_format: bool = True,
    max_prompt_chars: int = 10_000,
    max_response_bytes: int = 10_000,
    sleep: Any = lambda _: None,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        ModelConfig(
            base_url=server.base_url,
            model="local-model",
            api_key_env="REQMAP_API_KEY" if api_key else None,
            api_key=api_key,
            timeout_seconds=2,
            retries=retries,
            supports_response_format=supports_response_format,
            seed=seed,
            max_prompt_chars=max_prompt_chars,
            max_response_bytes=max_response_bytes,
        ),
        sleep=sleep,
        opener=server.open,
    )


def chat_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


def test_complete_json_posts_canonical_payload_and_parses_chat_completion(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Изменение endpoint, параметров или JSON-контракта должно ломать вызов модели."""
    fake_llm_server.enqueue(chat_response('{"atoms":[]}'))

    result = client_for(fake_llm_server).complete_json(
        "decomposition", "системная инструкция", {"z": "я", "a": 1}
    )

    assert result == {"atoms": []}
    assert len(fake_llm_server.requests) == 1
    method, path, headers, body = fake_llm_server.requests[0]
    assert method == "POST"
    assert path == "/v1/chat/completions"
    assert headers["content-type"] == "application/json"
    assert "authorization" not in headers
    assert json.loads(body) == {
        "model": "local-model",
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "системная инструкция"},
            {"role": "user", "content": '{"a":1,"z":"я"}'},
        ],
        "response_format": {"type": "json_object"},
        "seed": 7,
    }
    assert body.decode("utf-8") == json.dumps(
        json.loads(body), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_complete_json_adds_bearer_authorization_only_for_nonempty_key(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Добавление пустого заголовка или пропуск непустого ключа меняет границу auth."""
    fake_llm_server.enqueue(chat_response('{"ok":true}'))
    fake_llm_server.enqueue(chat_response('{"ok":true}'))

    client_for(fake_llm_server, api_key="top-secret").complete_json("mapping", "s", {})
    client_for(fake_llm_server, api_key="").complete_json("mapping", "s", {})

    assert fake_llm_server.requests[0][2]["authorization"] == "Bearer top-secret"
    assert "authorization" not in fake_llm_server.requests[1][2]


def test_complete_json_omits_optional_openai_fields_when_disabled(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Отправка seed/response_format вопреки config ломает совместимость простых серверов."""
    fake_llm_server.enqueue(chat_response('{"ok":true}'))

    client_for(
        fake_llm_server, seed=None, supports_response_format=False
    ).complete_json("mapping", "s", {})

    body = json.loads(fake_llm_server.requests[0][3])
    assert "seed" not in body
    assert "response_format" not in body


def test_preflight_checks_models_endpoint_and_configured_model(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Пропуск GET /models либо проверки имени позволил бы начать анализ без модели."""
    fake_llm_server.enqueue({"data": [{"id": "another"}, {"id": "local-model"}]})

    client_for(fake_llm_server).preflight()

    assert [(request[0], request[1]) for request in fake_llm_server.requests] == [
        ("GET", "/v1/models")
    ]


@pytest.mark.parametrize(
    "response, code",
    [
        ({"data": []}, "MODEL_NOT_AVAILABLE"),
        ({"data": [{"name": "local-model"}]}, "MODEL_PREFLIGHT_INVALID"),
        ([], "MODEL_PREFLIGHT_INVALID"),
    ],
)
def test_preflight_fails_closed_on_unavailable_or_malformed_models(
    fake_llm_server: FakeLlmServer, response: object, code: str
) -> None:
    """Ослабление models schema сделало бы preflight недостоверным."""
    fake_llm_server.enqueue(response)

    with pytest.raises(ModelError) as caught:
        client_for(fake_llm_server).preflight()

    assert caught.value.code == code


def test_complete_json_rejects_prompt_over_limit_without_request(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Обрезка либо отправка слишком длинного prompt нарушает лимит контекста."""
    client = client_for(fake_llm_server, max_prompt_chars=1)

    with pytest.raises(ModelError) as caught:
        client.complete_json("mapping", "system", {"text": "значение"})

    assert caught.value.code == "MODEL_PROMPT_TOO_LARGE"
    assert fake_llm_server.requests == []


def test_complete_json_rejects_oversized_response_before_json_parsing(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Разбор усечённого body скрывал бы превышение заданного лимита ответа."""
    fake_llm_server.enqueue_text('{"choices":[{"message":{"content":"{}"}}]}')
    client = client_for(fake_llm_server, max_response_bytes=1)

    with pytest.raises(ModelError) as caught:
        client.complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_RESPONSE_TOO_LARGE"


@pytest.mark.parametrize(
    "content",
    [
        "```json\n{}\n```",
        '{"answer": 1} trailing',
        '{"answer": 1, "answer": 2}',
        '{"answer": NaN}',
    ],
)
def test_complete_json_marks_non_strict_content_as_model_output_failure(
    fake_llm_server: FakeLlmServer, content: str
) -> None:
    """Fences, хвосты, duplicate keys и NaN не должны стать валидным ответом."""
    fake_llm_server.enqueue(chat_response(content))

    with pytest.raises(ModelOutputError) as caught:
        client_for(fake_llm_server).complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_OUTPUT_INVALID"
    assert caught.value.raw_response == content
    assert len(fake_llm_server.requests) == 1


@pytest.mark.parametrize(
    "envelope",
    [
        "[]",
        '{"choices":[]}',
        '{"choices":[{"message":{"content":{}}}]}',
        '{"choices":[{"message":{"content":"{}"}}],"choices":[]}',
        '{"choices":[{"message":{"content":"{}"}}],"n":Infinity}',
    ],
)
def test_complete_json_rejects_malformed_or_non_strict_envelope(
    fake_llm_server: FakeLlmServer, envelope: str
) -> None:
    """Нестрогий response envelope не должен интерпретироваться как ответ модели."""
    fake_llm_server.enqueue_text(envelope)

    with pytest.raises(ModelOutputError) as caught:
        client_for(fake_llm_server).complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_complete_json_retries_only_retryable_http_statuses_with_exponential_backoff(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Неверный retry-класс или backoff создаст лишние вызовы локальной модели."""
    sleeps: list[float] = []
    fake_llm_server.enqueue_text("temporary", status=500)
    fake_llm_server.enqueue_text("busy", status=429)
    fake_llm_server.enqueue(chat_response('{"ok":true}'))

    result = client_for(fake_llm_server, retries=2, sleep=sleeps.append).complete_json(
        "mapping", "s", {}
    )

    assert result == {"ok": True}
    assert len(fake_llm_server.requests) == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_complete_json_never_retries_non_retryable_http_status(
    fake_llm_server: FakeLlmServer, status: int
) -> None:
    """Повтор 4xx ошибок маскирует неверную конфигурацию и расходует время."""
    fake_llm_server.enqueue_text("bad request", status=status)

    with pytest.raises(ModelError) as caught:
        client_for(fake_llm_server, retries=2).complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_HTTP_ERROR"
    assert len(fake_llm_server.requests) == 1


def test_complete_json_retries_transport_error_without_secret_in_diagnostics() -> None:
    """Транспортная ошибка должна повторяться и не раскрывать ключ в исключении."""
    calls = 0
    sleeps: list[float] = []

    def opener(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise URLError("connection to top-secret failed")

    config = ModelConfig(
        base_url="http://127.0.0.1:1/v1",
        model="local-model",
        api_key_env="REQMAP_API_KEY",
        api_key="top-secret",
        retries=1,
    )
    client = OpenAICompatibleClient(config, sleep=sleeps.append, opener=opener)

    with pytest.raises(ModelError) as caught:
        client.complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_TRANSPORT_ERROR"
    assert calls == 2
    assert sleeps == [0.25]
    assert "top-secret" not in str(caught.value)
    assert "top-secret" not in repr(caught.value)
    assert "top-secret" not in str(caught.value.details)


def test_complete_json_does_not_retry_invalid_utf8_output() -> None:
    """Повтор invalid UTF-8 скрывал бы ошибку контрактного ответа модели."""
    server = FakeLlmServer()
    server.enqueue_bytes(b"\xff")

    with pytest.raises(ModelOutputError) as caught:
        client_for(server, retries=2).complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_OUTPUT_INVALID"
    assert len(server.requests) == 1


def test_http_error_and_invalid_model_output_redact_key_echo(
    fake_llm_server: FakeLlmServer,
) -> None:
    """Сервер не вправе вернуть ключ в exception message, details или raw_response."""
    fake_llm_server.enqueue_text("failure top-secret", status=500)
    fake_llm_server.enqueue(chat_response('{"x":NaN,"echo":"top-secret"}'))
    client = client_for(fake_llm_server, api_key="top-secret", retries=0)

    with pytest.raises(ModelError) as http_error:
        client.complete_json("mapping", "s", {})
    with pytest.raises(ModelOutputError) as output_error:
        client.complete_json("mapping", "s", {})

    for error in (http_error.value, output_error.value):
        assert "top-secret" not in str(error)
        assert "top-secret" not in repr(error)
        assert "top-secret" not in str(error.details)
    assert "top-secret" not in output_error.value.raw_response
    assert "<redacted>" in output_error.value.raw_response


def test_complete_json_reads_response_at_maximum_plus_one_bytes() -> None:
    """Чтение без +1 не отличило бы body ровно на байт больше заданного лимита."""
    response = _RecordingResponse(b'{"choices":[{"message":{"content":"{}"}}]}')
    config = ModelConfig(
        base_url="http://127.0.0.1:1/v1",
        model="local-model",
        api_key_env=None,
        api_key=None,
        max_response_bytes=9,
    )
    client = OpenAICompatibleClient(config, opener=lambda *args, **kwargs: response)

    with pytest.raises(ModelError) as caught:
        client.complete_json("mapping", "s", {})

    assert caught.value.code == "MODEL_RESPONSE_TOO_LARGE"
    assert response.read_sizes == [10]
    assert response.closed is True


class _RecordingResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, amount: int) -> bytes:
        self.read_sizes.append(amount)
        return self.body[:amount]

    def close(self) -> None:
        self.closed = True
