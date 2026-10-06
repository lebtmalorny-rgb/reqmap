"""Контракт grounded-декомпозиции требований."""

from collections import deque
from collections.abc import Mapping
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
    offset = 0
    selected = []
    for atom in atoms:
        quote = atom["source_quote"]
        selected.append({**atom, "source_span":{"start":offset,"end":offset+len(quote)}})
        offset += len(quote) + 2
    return {"proposal_schema_version":2, "atoms":selected}


def test_decompose_preserves_multiple_obligations_and_order() -> None:
    quotes = ("Nova должна создавать ВМ через API", "Nova должна удалять ВМ через API")
    raw = valid_atoms(*(dict(text=q, source_quote=q, mandatory=True) for q in quotes))
    raw["atoms"].reverse()
    model = FakeModel([raw])
    outcome = decompose(model, requirement_text("; ".join(quotes)), None)
    assert outcome.analysis_state is AnalysisState.COMPLETED
    assert [(a.atom_id, a.ordinal, a.text) for a in outcome.atoms] == [
        ("REQ-0001-A001", 1, quotes[0]), ("REQ-0001-A002", 2, quotes[1])]
    assert model.calls[0][0:2] == ("decomposition", DECOMPOSITION_PROMPT)


def test_decompose_uses_exact_quote_not_free_model_text_as_canonical_content() -> None:
    """Подмена model text не должна попасть в downstream canonical atom."""
    model = FakeModel(
        [
            valid_atoms(
                {
                    "text": "Удалять все резервные копии после создания ВМ",
                    "source_quote": "Nova должна создавать ВМ через API",
                    "mandatory": True,
                }
            )
        ]
    )

    outcome = decompose(model, requirement_text("Nova должна создавать ВМ через API"), None)

    assert outcome.analysis_state is AnalysisState.COMPLETED
    assert outcome.atoms[0].text == "Nova должна создавать ВМ через API"
    assert outcome.atoms[0].source_quote == "Nova должна создавать ВМ через API"
    assert "Удалять" not in outcome.atoms[0].text


def test_decompose_rejects_case_changed_or_invented_quote_after_one_correction() -> None:
    """Case-sensitive grounding запрещает подменять исходную формулировку."""
    invalid = valid_atoms(
        {"text": "Nova должна создавать ВМ через API", "source_quote": "nova должна создавать ВМ через API", "mandatory": True}
    )
    model = FakeModel([invalid, invalid])

    outcome = decompose(model, requirement_text("Nova должна создавать ВМ через API"), None)

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.atoms == ()
    assert "SOURCE_SPAN_INVALID" in outcome.diagnostics[0]
    assert len(model.calls) == 2
    assert model.calls[1][2]["invalid_response"] == invalid


def test_decompose_never_uses_parent_text_as_authoritative_quote() -> None:
    """Цитата родителя не должна создать атом дочернего требования."""
    invalid = valid_atoms(
        {"text": "Шифрование", "source_quote": "Шифрование данных", "mandatory": True}
    )
    model = FakeModel([invalid, invalid])

    outcome = decompose(model, requirement_text("Nova должна создавать ВМ через API"), "Шифрование данных")

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert "SOURCE_SPAN_INVALID" in outcome.diagnostics[0]
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
    quote = "Nova должна создавать ВМ через API"
    response = valid_atoms(dict(text=quote, source_quote=quote, mandatory=True))
    response["other"] = False
    assert decomposition_violations(response, quote)
    response.pop("other")
    response["atoms"].append(dict(response["atoms"][0]))
    assert any("SOURCE_SPAN_INVALID" in v for v in decomposition_violations(response, quote))


class DivergentMapping(Mapping[str, object]):
    """Содержит разные значения для get/item и имитирует небезопасный model object."""

    def __iter__(self) -> Any:
        return iter(("atoms",))

    def __len__(self) -> int:
        return 1

    def __getitem__(self, key: str) -> object:
        assert key == "atoms"
        return [{"text": "invented", "source_quote": "invented", "mandatory": True}]

    def get(self, key: str, default: object = None) -> object:
        assert key == "atoms"
        return [{"text": "safe", "source_quote": "safe", "mandatory": True}]


def test_decompose_rejects_non_builtin_mapping_before_build_can_reread_it() -> None:
    """Нестабильный Mapping нельзя валидировать одним способом и читать другим."""
    model = FakeModel([DivergentMapping(), DivergentMapping()])

    outcome = decompose(model, requirement_text("Nova должна создавать ВМ через API"), None)

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.atoms == ()
    assert "PROPOSAL_SCHEMA" in outcome.diagnostics[0]


def test_decompose_correction_can_return_valid_response() -> None:
    """Одна конкретная коррекция нужна для исправления формата малой локальной моделью."""
    invalid = {"atoms": [{"text": "ВМ", "source_quote": "нет", "mandatory": True}]}
    valid = valid_atoms({"text": "Nova должна создавать ВМ через API", "source_quote": "Nova должна создавать ВМ через API", "mandatory": True})
    model = FakeModel([invalid, valid])

    outcome = decompose(model, requirement_text("Nova должна создавать ВМ через API"), None)

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

    outcome = decompose(model, requirement_text("Nova должна создавать ВМ через API"), None)

    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.atoms == ()
    assert outcome.diagnostics == ("Ответ модели некорректен.",)
    assert model.calls[1][2]["invalid_response"] == {"raw_response": raw_response}


def test_decompose_propagates_transport_model_error() -> None:
    """Транспортный сбой классифицирует pipeline, а не скрывается как недостаток evidence."""
    model = FakeModel([ModelError("MODEL_TRANSPORT_ERROR", "Нет связи")])

    with pytest.raises(ModelError, match="Нет связи"):
        decompose(model, requirement_text("Nova должна создавать ВМ через API"), None)

    assert len(model.calls) == 1


def test_decomposition_prompt_is_versioned_and_declares_parent_untrusted() -> None:
    """Версия и запрет authority родителя являются контрактом переносимых запусков."""
    assert PROMPT_DECOMPOSITION_VERSION == "2.0"
    assert "proposal_schema_version=2" in DECOMPOSITION_PROMPT
    assert "неавторитет" in DECOMPOSITION_PROMPT.lower()
