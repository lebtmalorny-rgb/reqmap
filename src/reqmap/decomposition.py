"""Строгая grounded-декомпозиция одного требования на атомарные утверждения."""

from __future__ import annotations

from collections.abc import Mapping

from reqmap.binding_source import bind_source, canonical_atoms, atom_selection_proposal, validate_atom_selection
from reqmap.models import SourceCoordinate, to_dict
from reqmap.errors import ModelOutputError
from reqmap.llm import JsonModel
from reqmap.models import AnalysisState, AtomicClaim, DecompositionOutcome, Requirement
from reqmap.prompts import DECOMPOSITION_PROMPT
from reqmap.proposals import ProposalError


_PARENT_TEXT_POLICY = (
    "parent_text неавторитетный: это только контекст; source_quote и новые атомы "
    "должны опираться исключительно на requirement_text."
)


def decompose(
    model: JsonModel, requirement: Requirement, parent_text: str | None
) -> DecompositionOutcome:
    """Декомпозирует requirement с одной корректирующей попыткой модели.

    Ошибки транспорта и лимитов сознательно не перехватываются: pipeline обязан
    классифицировать их отдельно от некорректного model output.
    """
    binding = bind_source(requirement)
    if any(o.parse_state != "bound" for o in binding.obligations):
        return DecompositionOutcome(canonical_atoms(binding), AnalysisState.COMPLETED)
    payload = prepare_decomposition(requirement, parent_text)
    violations: tuple[str, ...] = ()

    for _attempt in range(2):
        try:
            response = model.complete_json("decomposition", DECOMPOSITION_PROMPT, payload)
            invalid_response: object = response
            try:
                return DecompositionOutcome(
                    atoms=accept_decomposition(requirement, response),
                    analysis_state=AnalysisState.COMPLETED,
                )
            except ProposalError as exc:
                violations = exc.violations
        except ModelOutputError as exc:
            invalid_response = {"raw_response": exc.raw_response}
            violations = (exc.message_ru,)

        payload = {
            "original_payload": payload,
            "invalid_response": invalid_response,
            "violations_ru": list(violations),
        }

    return DecompositionOutcome(
        atoms=(),
        analysis_state=AnalysisState.MODEL_FAILED,
        diagnostics=("; ".join(violations),),
    )


def decomposition_violations(
    response: Mapping[str, object], source_text: str
) -> tuple[str, ...]:
    """Возвращает детерминированный список нарушений строгого response contract."""
    requirement = Requirement("REQ-0001", None, source_text, 1, SourceCoordinate("validation", None, 1))
    try:
        validate_atom_selection(bind_source(requirement), response)
    except ProposalError as exc:
        return exc.violations
    return ()


def build_atoms(requirement: Requirement, response: Mapping[str, object]) -> tuple[AtomicClaim, ...]:
    """Выбирает проверенные atoms в исходном порядке backend."""
    return validate_atom_selection(bind_source(requirement), response)


def prepare_decomposition(requirement: Requirement, parent_text: str | None) -> dict[str, object]:
    """Return the exact source and response contract without invoking a model."""
    return {
        "requirement_id": requirement.requirement_id,
        "requirement_text": requirement.text,
        "parent_text": parent_text,
        "parent_text_policy": _PARENT_TEXT_POLICY,
        "source_binding": to_dict(bind_source(requirement)),
        "canonical_proposal": atom_selection_proposal(bind_source(requirement)),
        "response_schema": {"proposal_schema_version": 2,
            "atoms": [{"text": "non-empty label", "source_quote": "exact canonical obligation quote",
                       "mandatory": True, "source_span": {"start": "Unicode codepoint offset", "end": "exclusive Unicode codepoint offset"}}]},
    }


def accept_decomposition(requirement: Requirement, proposal: object) -> tuple[AtomicClaim, ...]:
    """Validate one proposal and assign canonical atoms locally."""
    return validate_atom_selection(bind_source(requirement), proposal)
