"""Контракт grounded-декомпозиции требований."""

from collections import deque
from typing import Any

import pytest

from reqmap.decomposition import decompose, decomposition_violations
from reqmap.errors import ModelError, ModelOutputError
from reqmap.models import AnalysisState, Requirement, SourceCoordinate
from reqmap.prompts import DECOMPOSITION_PROMPT, PROMPT_DECOMPOSITION_VERSION


class FakeModel:
    def __init__(self, responses: list[object]) -> None:
        self._responses: deque[object] = deque(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def complete_json(
        self, stage: str, system_prompt: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((stage, system_prompt, payload))
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]


def requirement_text(text: str, *, requirement_id: str = "REQ-0001") -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        source_id="source-1",
        text=text,
        ordinal=1,
        coordinate=SourceCoordinate("synthetic", None, 1),
    )


def valid_atoms(*atoms: dict[str, object]) -> dict[str, object]:
    return {"atoms": list(atoms)}


def test_decompose_preserves_multiple_obligations_and_order() -> None:
    """Слияние или перестановка атомов меняет проверяемый результат анализа."""
    model = FakeModel(
        [
            valid_atoms(
                {
                    "text": "Создание ВМ через API",
                    "source_quote": "создание ВМ через API",
                    "mandatory": True,
                },
                {
                    "text": "Настройка sysctl",
                    "source_quote": "настройку sysctl",
                    "mandatory": True,
                },
            )
        ]
    )

    outcome = decompose(
        model,
        requirement_text("Поддержать создание ВМ через API и настройку sysctl"),
        None,
    )

    assert outcome.analysis_state is AnalysisState.COMPLETED
    assert [(atom.atom_id, atom.ordinal, atom.text) for atom in outcome.atoms] == [
        ("REQ-0001-A001", 1, "Создание ВМ через API"),
        ("REQ-0001-A002", 2, "Настройка sysctl"),
    ]
    assert [atom.source_quote for atom in outcome.atoms] == [
        "создание ВМ через API",
        "настройку sysctl",
    ]
    assert model.calls[0][0:2] == ("decomposition", DECOMPOSITION_PROMPT)


def test_decompose_rejects_case_changed_or_invented_quote_after_one_correction() -> None:
    """Case-sensitive grounding запрещает подменять исходную формулировку."""
    invalid = valid_atoms(
        {"text": "Создание ВМ", "source_quote": "создание ВМ", "mandatory": True}
    )
    model = FakeModel([invalid, invalid])

    outcome = decompose(model, requirement_text("Создание ВМ"), None)

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.atoms == ()
    assert "не найден в исходной формулировке" in outcome.diagnostics[0]
    assert len(model.calls) == 2
    assert model.calls[1][2]["invalid_response"] == invalid


def test_decompose_never_uses_parent_text_as_authoritative_quote() -> None:
    """Цитата родителя не должна создать атом дочернего требования."""
    invalid = valid_atoms(
        {"text": "Шифрование", "source_quote": "Шифрование данных", "mandatory": True}
    )
    model = FakeModel([invalid, invalid])

    outcome = decompose(model, requirement_text("Настроить резервное копирование"), "Шифрование данных")

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert "не найден в исходной формулировке" in outcome.diagnostics[0]
    assert model.calls[0][2]["parent_text"] == "Шифрование данных"
    assert "неавторитет" in str(model.calls[0][2]["parent_text_policy"]).lower()


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"atoms": []},
        {"atoms": "not-a-list"},
        {"atoms": [{"text": "x", "source_quote": "x", "mandatory": 1}]},
        {"atoms": [{"text": "x", "source_quote": "x", "mandatory": True, "extra": 1}]},
        {"atoms": [{"text": " ", "source_quote": "x", "mandatory": True}]},
        {"atoms": [{"text": "x", "source_quote": "\t", "mandatory": True}]},
    ],
)
def test_decomposition_violations_rejects_strict_schema_type_and_empty_values(
    response: dict[str, object],
) -> None:
    """Ослабление схемы позволило бы построить недостоверный canonical atom."""
    assert decomposition_violations(response, "x")


def test_decomposition_violations_rejects_unknown_top_level_and_duplicate_obligations() -> None:
    """Контракт ровно atoms и не допускает повторных одинаковых обязательств/цитат."""
    response = {
        "atoms": [
            {"text": "Проверить API", "source_quote": "Проверить API", "mandatory": True},
            {"text": "Проверить API", "source_quote": "Проверить API", "mandatory": True},
        ],
        "other": False,
    }

    violations = decomposition_violations(response, "Проверить API")

    assert any("верхнего уровня" in violation for violation in violations)
    assert any("повторяет" in violation for violation in violations)


def test_decompose_correction_can_return_valid_response() -> None:
    """Одна конкретная коррекция нужна для исправления формата малой локальной моделью."""
    invalid = {"atoms": [{"text": "ВМ", "source_quote": "нет", "mandatory": True}]}
    valid = valid_atoms({"text": "Создание ВМ", "source_quote": "Создание ВМ", "mandatory": True})
    model = FakeModel([invalid, valid])

    outcome = decompose(model, requirement_text("Создание ВМ"), None)

    assert outcome.analysis_state is AnalysisState.COMPLETED
    assert [atom.atom_id for atom in outcome.atoms] == ["REQ-0001-A001"]
    correction = model.calls[1][2]
    assert correction["invalid_response"] == invalid
    assert correction["violations_ru"]
    assert "original_payload" in correction


@pytest.mark.parametrize("raw_response", ["{invalid", ""])
def test_decompose_handles_model_output_error_once_and_fails_closed(raw_response: str) -> None:
    """Некорректный JSON модели допускает ровно одну исправляющую попытку без raw-text assumptions."""
    error = ModelOutputError("MODEL_OUTPUT_INVALID", "Ответ модели некорректен.", raw_response)
    model = FakeModel([error, error])

    outcome = decompose(model, requirement_text("Создание ВМ"), None)

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.atoms == ()
    assert outcome.diagnostics == ("Ответ модели некорректен.",)
    assert model.calls[1][2]["invalid_response"] == {"raw_response": raw_response}


def test_decompose_propagates_transport_model_error() -> None:
    """Транспортный сбой классифицирует pipeline, а не скрывается как недостаток evidence."""
    model = FakeModel([ModelError("MODEL_TRANSPORT_ERROR", "Нет связи")])

    with pytest.raises(ModelError, match="Нет связи"):
        decompose(model, requirement_text("Создание ВМ"), None)

    assert len(model.calls) == 1


def test_decomposition_prompt_is_versioned_and_declares_parent_untrusted() -> None:
    """Версия и запрет authority родителя являются контрактом переносимых запусков."""
    assert PROMPT_DECOMPOSITION_VERSION == "1.0"
    assert "Не определяй компоненты OpenStack" in DECOMPOSITION_PROMPT
    assert "неавторитет" in DECOMPOSITION_PROMPT.lower()
