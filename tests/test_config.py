import json
from pathlib import Path
import math

import pytest

from reqmap import config as config_module
from reqmap.config import (
    AnalysisProfile,
    AppConfig,
    ConfigError,
    ModelConfig,
    load_config,
)


BASE_CONFIG = {
    "model": {"base_url": "http://llm/v1", "model": "local"},
    "knowledge_path": "knowledge/epoxy-2025.1",
    "top_k": 8,
}


def test_source_context_path_is_operator_config(tmp_path):
    config = load_config(write_config(tmp_path, {**BASE_CONFIG, "source_context_path": "review/map.json"}), {})
    assert config.source_context_path == tmp_path / "review/map.json"


def write_config(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_config_without_profile_remains_legacy(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, BASE_CONFIG), {})

    assert config.analysis_profile is AnalysisProfile.LEGACY
    assert config.knowledge_trust is None


def test_deep_config_resolves_allowed_signers_relative_to_config(tmp_path: Path) -> None:
    config = load_config(
        write_config(
            tmp_path,
            {
                **BASE_CONFIG,
                "analysis_profile": "deep",
                "knowledge_trust": {"allowed_signers_path": "trust/allowed_signers"},
            },
        ),
        {},
    )

    assert config.analysis_profile is AnalysisProfile.DEEP
    assert config.knowledge_trust is not None
    assert config.knowledge_trust.allowed_signers_path == tmp_path / "trust/allowed_signers"
    assert config.knowledge_trust.signer_identity == "reqmap-snapshot"


def test_deep_config_requires_trust(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="knowledge_trust"):
        load_config(write_config(tmp_path, {**BASE_CONFIG, "analysis_profile": "deep"}), {})


@pytest.mark.parametrize(
    ("knowledge_trust", "expected_message"),
    [
        ({"allowed_signers_path": "trust/allowed_signers", "unexpected": "value"}, "unexpected"),
        ({"allowed_signers_path": ""}, "allowed_signers_path"),
        ({"allowed_signers_path": 7}, "allowed_signers_path"),
        ({"allowed_signers_path": "trust/allowed_signers", "signer_identity": ""}, "signer_identity"),
        ({"allowed_signers_path": "trust/allowed_signers", "signer_identity": 7}, "signer_identity"),
        (None, "knowledge_trust"),
    ],
)
def test_deep_config_rejects_invalid_trust_values(
    tmp_path: Path, knowledge_trust: object, expected_message: str
) -> None:
    with pytest.raises(ConfigError, match=expected_message):
        load_config(
            write_config(
                tmp_path,
                {
                    **BASE_CONFIG,
                    "analysis_profile": "deep",
                    "knowledge_trust": knowledge_trust,
                },
            ),
            {},
        )


@pytest.mark.parametrize("analysis_profile", ["", "unknown", 7, None])
def test_config_rejects_invalid_analysis_profile(tmp_path: Path, analysis_profile: object) -> None:
    with pytest.raises(ConfigError, match="analysis_profile"):
        load_config(
            write_config(tmp_path, {**BASE_CONFIG, "analysis_profile": analysis_profile}), {}
        )


def test_app_config_keeps_legacy_positional_constructor() -> None:
    model = ModelConfig("http://llm/v1", "local", None, None)

    config = AppConfig(model, Path("knowledge"), None, 8)

    assert config.analysis_profile is AnalysisProfile.LEGACY
    assert config.knowledge_trust is None


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
