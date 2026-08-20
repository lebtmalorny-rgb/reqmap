from pathlib import Path
import math

import pytest

from reqmap import config as config_module
from reqmap.config import ConfigError, load_config


def test_load_config_reads_api_key_only_from_environment(tmp_path: Path) -> None:
    """Удаление api_key_env или раскрытие секрета в repr нарушит контракт."""
    path = tmp_path / "config.yaml"
    path.write_text(
        '{"model":{"base_url":"http://llm:8000/v1","model":"local","api_key_env":"REQMAP_API_KEY"},'
        '"knowledge_path":"knowledge/epoxy-2025.1","top_k":8}',
        encoding="utf-8",
    )

    config = load_config(path, {"REQMAP_API_KEY": "secret"})

    assert config.model.api_key == "secret"
    assert "secret" not in repr(config)
    assert config.knowledge_path == tmp_path / "knowledge/epoxy-2025.1"


def test_load_config_rejects_unknown_root_key(tmp_path: Path) -> None:
    """Пропуск неизвестного ключа сделал бы конфигурацию нестрогой."""
    path = tmp_path / "config.yaml"
    path.write_text(
        '{"model":{"base_url":"http://llm/v1","model":"local"},'
        '"knowledge_path":"knowledge/epoxy-2025.1","unexpected":true}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Неизвестные параметры: unexpected"):
        load_config(path, {})


def test_load_config_accepts_only_full_line_comments(tmp_path: Path) -> None:
    """Удаление inline-комментария или символа # в строке исказит JSON."""
    path = tmp_path / "config.yaml"
    path.write_text(
        '# Комментарий к локальной модели\n'
        '{"model":{"base_url":"http://llm/v1#fragment","model":"local"},'
        '"knowledge_path":"knowledge/epoxy-2025.1","top_k":1}',
        encoding="utf-8",
    )

    config = load_config(path, {})

    assert config.model.base_url == "http://llm/v1#fragment"


def test_load_config_rejects_inline_comments(tmp_path: Path) -> None:
    """Принятие inline YAML-комментариев вышло бы за JSON-compatible контракт."""
    path = tmp_path / "config.yaml"
    path.write_text(
        '{"model":{"base_url":"http://llm/v1","model":"local"},'
        '"knowledge_path":"knowledge/epoxy-2025.1","top_k":8} # нельзя',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Не удалось прочитать конфигурацию"):
        load_config(path, {})


@pytest.mark.parametrize(
    ("model", "top_k", "expected_message"),
    [
        ('{"base_url":"ftp://llm/v1","model":"local"}', "8", "model.base_url"),
        ('{"base_url":"http://llm/v1","model":""}', "8", "model.model"),
        ('{"base_url":"http://llm/v1","model":"local","timeout_seconds":0}', "8", "model.timeout_seconds"),
        ('{"base_url":"http://llm/v1","model":"local","api_key":"не-допускается"}', "8", "Неизвестные параметры: api_key"),
        ('{"base_url":"http://llm/v1","model":"local"}', "51", "top_k"),
    ],
)
def test_load_config_rejects_invalid_model_or_limits(
    tmp_path: Path, model: str, top_k: str, expected_message: str
) -> None:
    """Ослабление каждой проверки позволило бы небезопасный или некорректный запуск."""
    path = tmp_path / "config.yaml"
    path.write_text(
        '{"model":' + model + ',"knowledge_path":"knowledge/epoxy-2025.1","top_k":' + top_k + '}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=expected_message):
        load_config(path, {})


@pytest.mark.parametrize(
    ("numeric_field", "constant"),
    [
        ('"timeout_seconds":', "NaN"),
        ('"retries":', "Infinity"),
        ('"max_prompt_chars":', "-Infinity"),
    ],
)
def test_load_config_rejects_non_finite_json_constants(
    tmp_path: Path, numeric_field: str, constant: str
) -> None:
    """Принятие NaN или Infinity нарушило бы строгую JSON-совместимость."""
    path = tmp_path / "config.yaml"
    path.write_text(
        '{"model":{"base_url":"http://llm/v1","model":"local",'
        + numeric_field
        + constant
        + '},"knowledge_path":"knowledge/epoxy-2025.1","top_k":8}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as error:
        load_config(path, {})

    assert error.value.code == "CONFIG_INVALID"


def test_load_config_rejects_non_finite_timeout_after_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка timeout не должна зависеть только от строгого JSON-декодера."""
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        config_module.json,
        "loads",
        lambda _, **__: {
            "model": {
                "base_url": "http://llm/v1",
                "model": "local",
                "timeout_seconds": math.nan,
            },
            "knowledge_path": "knowledge/epoxy-2025.1",
            "top_k": 8,
        },
    )

    with pytest.raises(ConfigError) as error:
        load_config(path, {})

    assert error.value.code == "CONFIG_INVALID"
