"""Строгая grounded-декомпозиция одного требования на атомарные утверждения."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from reqmap.errors import ModelOutputError
from reqmap.ids import atom_id
from reqmap.llm import JsonModel
from reqmap.models import AnalysisState, AtomicClaim, DecompositionOutcome, Requirement
from reqmap.prompts import DECOMPOSITION_PROMPT
from reqmap.proposals import ProposalError


_TOP_LEVEL_KEYS = frozenset({"atoms"})
_ATOM_KEYS = frozenset({"text", "source_quote", "mandatory"})
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
    violations: list[str] = []
    if type(response) is not dict:
        return ("Ответ модели должен быть встроенным dict JSON object верхнего уровня.",)
    if set(response) != _TOP_LEVEL_KEYS:
        violations.append("Ответ должен содержать только ключ верхнего уровня atoms.")

    atoms = response.get("atoms")
    if type(atoms) is not list or not atoms:
        violations.append("Поле atoms должно быть непустым массивом.")
        return tuple(violations)

    seen_texts: set[str] = set()
    seen_quotes: set[str] = set()
    for index, atom in enumerate(atoms, start=1):
        prefix = f"Атом {index}"
        if type(atom) is not dict:
            violations.append(f"{prefix} должен быть встроенным dict object.")
            continue
        if set(atom) != _ATOM_KEYS:
            violations.append(
                f"{prefix} должен содержать только поля text, source_quote, mandatory."
            )
        text = atom.get("text")
        source_quote = atom.get("source_quote")
        mandatory = atom.get("mandatory")
        if not isinstance(text, str) or not text.strip():
            violations.append(f"{prefix}.text должен быть непустой строкой.")
        if not isinstance(source_quote, str) or not source_quote.strip():
            violations.append(f"{prefix}.source_quote должен быть непустой строкой.")
        elif source_quote not in source_text:
            violations.append(
                f"{prefix}.source_quote не найден в исходной формулировке requirement_text."
            )
        if type(mandatory) is not bool:
            violations.append(f"{prefix}.mandatory должен быть boolean.")

        if isinstance(text, str) and text.strip():
            if text in seen_texts:
                violations.append(f"{prefix} повторяет обязательство предыдущего атома.")
            seen_texts.add(text)
        if isinstance(source_quote, str) and source_quote.strip():
            if source_quote in seen_quotes:
                violations.append(f"{prefix} повторяет source_quote предыдущего атома.")
            seen_quotes.add(source_quote)
    return tuple(violations)


def build_atoms(requirement: Requirement, response: Mapping[str, object]) -> tuple[AtomicClaim, ...]:
    """Строит canonical atoms из concrete JSON snapshot, сохраняя порядок модели."""
    if type(response) is not dict or type(response.get("atoms")) is not list:
        raise ValueError("build_atoms принимает только проверенный встроенный JSON object.")
    raw_atoms = cast(list[dict[str, object]], response["atoms"])
    if any(type(raw_atom) is not dict for raw_atom in raw_atoms):
        raise ValueError("build_atoms принимает только проверенные встроенные JSON atoms.")
    return tuple(
        AtomicClaim(
            atom_id=atom_id(requirement.requirement_id, ordinal),
            requirement_id=requirement.requirement_id,
            # Свободная label text модели не может стать canonical/downstream content.
            text=cast(str, raw_atom["source_quote"]),
            source_quote=cast(str, raw_atom["source_quote"]),
            mandatory=cast(bool, raw_atom["mandatory"]),
            ordinal=ordinal,
        )
        for ordinal, raw_atom in enumerate(raw_atoms, start=1)
    )


def prepare_decomposition(requirement: Requirement, parent_text: str | None) -> dict[str, object]:
    """Return the exact source and response contract without invoking a model."""
    return {
        "requirement_id": requirement.requirement_id,
        "requirement_text": requirement.text,
        "parent_text": parent_text,
        "parent_text_policy": _PARENT_TEXT_POLICY,
        "response_schema": {
            "atoms": [
                {
                    "text": "non-empty non-authoritative compatibility label",
                    "source_quote": "non-empty exact substring of requirement_text",
                    "mandatory": "boolean",
                }
            ]
        },
    }


def accept_decomposition(requirement: Requirement, proposal: object) -> tuple[AtomicClaim, ...]:
    """Validate one proposal and assign canonical atoms locally."""
    if type(proposal) is dict and "proposal_schema_version" in proposal:
        from reqmap.binding_source import bind_source, validate_atom_selection
        return validate_atom_selection(bind_source(requirement), proposal)
    violations = decomposition_violations(proposal, requirement.text)
    if violations:
        raise ProposalError("shape", violations)
    return build_atoms(requirement, proposal)
