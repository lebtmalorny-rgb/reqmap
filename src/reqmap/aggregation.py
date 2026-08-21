"""Детерминированная агрегация атомов, требований и групп."""

from reqmap.ids import atom_id, mapping_id
from reqmap.models import (
    AnalysisState,
    AtomResult,
    AtomicClaim,
    GroupResult,
    Mapping,
    Requirement,
    RequirementResult,
    SupportStatus,
)


_FAILED_STATE_PRECEDENCE = (
    AnalysisState.VALIDATION_FAILED,
    AnalysisState.MODEL_FAILED,
    AnalysisState.SKIPPED,
)
_CONFIRMED_MAPPING_STATUSES = frozenset(
    {
        SupportStatus.SUPPORTED,
        SupportStatus.PARTIAL,
        SupportStatus.NOT_SUPPORTED,
    }
)


def aggregate_status(atoms: tuple[AtomResult, ...]) -> SupportStatus | None:
    """Вычислить support status по обязательным атомам в порядке спецификации."""
    _validate_atom_results(atoms)
    return _aggregate_status(atoms)


def _aggregate_status(atoms: tuple[AtomResult, ...]) -> SupportStatus | None:
    mandatory = tuple(item for item in atoms if item.atom.mandatory)
    if any(
        item.support_status is SupportStatus.NOT_SUPPORTED for item in mandatory
    ):
        return SupportStatus.NOT_SUPPORTED
    if any(
        item.analysis_state is not AnalysisState.COMPLETED for item in mandatory
    ):
        return None

    statuses = {item.support_status for item in mandatory}
    if None in statuses:
        return None
    if SupportStatus.INSUFFICIENT_EVIDENCE in statuses:
        return SupportStatus.INSUFFICIENT_EVIDENCE
    if SupportStatus.PARTIAL in statuses:
        return SupportStatus.PARTIAL
    if statuses == {SupportStatus.NOT_APPLICABLE}:
        return SupportStatus.NOT_APPLICABLE
    return SupportStatus.SUPPORTED


def aggregate_requirement(
    requirement: Requirement,
    atoms: tuple[AtomResult, ...],
) -> RequirementResult:
    """Сохранить атомы построчно и добавить воспроизводимую сводку требования."""
    _validate_requirement_atoms(requirement, atoms)
    mappings = tuple(item for result in atoms for item in result.mappings)
    diagnostics = tuple(
        dict.fromkeys(message for result in atoms for message in result.diagnostics)
    )
    return RequirementResult(
        requirement=requirement,
        analysis_state=_aggregate_analysis_state(
            tuple(item.analysis_state for item in atoms)
        ),
        support_status=_aggregate_status(atoms),
        atom_results=atoms,
        mappings=mappings,
        diagnostics=diagnostics,
    )


def aggregate_groups(
    requirements: tuple[RequirementResult, ...],
) -> tuple[GroupResult, ...]:
    """Построить дополнительные group records, не заменяя исходные строки."""
    grouped: dict[str, list[RequirementResult]] = {}
    for result in requirements:
        _validate_requirement_result(result)
        for group_id in dict.fromkeys(result.requirement.group_ids):
            grouped.setdefault(group_id, []).append(result)
    return tuple(
        _aggregate_group(group_id, tuple(children))
        for group_id, children in grouped.items()
    )


def _validate_requirement_atoms(
    requirement: Requirement,
    atoms: tuple[AtomResult, ...],
) -> None:
    if type(requirement) is not Requirement:
        raise ValueError(
            "aggregate_requirement принимает только canonical Requirement."
        )
    if type(atoms) is not tuple or not atoms:
        raise ValueError("Для агрегации требования нужен непустой tuple атомов.")
    if any(item.atom.requirement_id != requirement.requirement_id for item in atoms):
        raise ValueError("Атом ссылается на другое требование.")
    _validate_atom_results(atoms)
    expected_ordinals = tuple(range(1, len(atoms) + 1))
    if tuple(item.atom.ordinal for item in atoms) != expected_ordinals:
        raise ValueError("Нарушен исходный порядок атомов требования.")


def _validate_atom_results(atoms: tuple[AtomResult, ...]) -> None:
    if type(atoms) is not tuple or not atoms:
        raise ValueError("Для агрегации нужен непустой tuple атомов.")
    for result in atoms:
        _validate_atom_result(result)


def _validate_atom_result(result: AtomResult) -> None:
    if type(result) is not AtomResult or type(result.atom) is not AtomicClaim:
        raise ValueError("Агрегация принимает только canonical AtomResult.")
    claim = result.atom
    if (
        type(claim.atom_id) is not str
        or type(claim.requirement_id) is not str
        or type(claim.ordinal) is not int
        or claim.ordinal < 1
        or type(claim.mandatory) is not bool
    ):
        raise ValueError("AtomicClaim содержит неканонические идентификаторы.")
    if claim.atom_id != atom_id(claim.requirement_id, claim.ordinal):
        raise ValueError("Нестабильный atom_id.")
    if type(result.analysis_state) is not AnalysisState:
        raise ValueError("analysis_state атома не входит в канонический enum.")
    if result.support_status is not None and type(result.support_status) is not SupportStatus:
        raise ValueError("support_status атома не входит в канонический enum.")
    if type(result.mappings) is not tuple:
        raise ValueError("mappings атома должен быть tuple.")
    for values in (
        result.supported_aspects,
        result.unconfirmed_aspects,
        result.diagnostics,
    ):
        if type(values) is not tuple or any(
            type(value) is not str or not value.strip() for value in values
        ):
            raise ValueError("Текстовые поля AtomResult должны быть tuple строк.")

    if result.analysis_state is not AnalysisState.COMPLETED:
        if (
            result.support_status is not None
            or result.mappings
            or result.supported_aspects
            or result.unconfirmed_aspects
        ):
            raise ValueError(
                "Незавершённый атом не может содержать status, mappings или aspects."
            )
        return
    if result.support_status is None:
        raise ValueError("Завершённый атом обязан содержать support_status.")

    for ordinal, item in enumerate(result.mappings, start=1):
        if type(item) is not Mapping:
            raise ValueError("mappings должен содержать только canonical Mapping.")
        if item.atom_id != claim.atom_id:
            raise ValueError("Mapping ссылается не на текущий atom.")
        expected_id = mapping_id(claim.atom_id, ordinal)
        if item.mapping_id != expected_id:
            raise ValueError(f"Нестабильный mapping_id: ожидался {expected_id}.")
        if type(item.support_status) is not SupportStatus:
            raise ValueError("support_status mapping не входит в канонический enum.")
        if item.support_status is SupportStatus.NOT_APPLICABLE:
            raise ValueError("not_applicable не может содержать mapping.")

    mapping_status = _aggregate_mapping_status(result.mappings)
    if result.mappings and result.support_status is not mapping_status:
        raise ValueError("support_status атома не согласован со статусами mappings.")
    if not result.mappings and result.support_status not in {
        SupportStatus.INSUFFICIENT_EVIDENCE,
        SupportStatus.NOT_APPLICABLE,
    }:
        raise ValueError(
            "Без mappings допустимы только insufficient_evidence или not_applicable."
        )


def _aggregate_mapping_status(mappings: tuple[Mapping, ...]) -> SupportStatus | None:
    statuses = {item.support_status for item in mappings}
    if not statuses:
        return None
    if SupportStatus.NOT_SUPPORTED in statuses:
        return SupportStatus.NOT_SUPPORTED
    if SupportStatus.INSUFFICIENT_EVIDENCE in statuses:
        return SupportStatus.INSUFFICIENT_EVIDENCE
    if SupportStatus.PARTIAL in statuses:
        return SupportStatus.PARTIAL
    return SupportStatus.SUPPORTED


def _validate_requirement_result(result: RequirementResult) -> None:
    if type(result) is not RequirementResult:
        raise ValueError(
            "aggregate_groups принимает только canonical RequirementResult."
        )
    if type(result.requirement) is not Requirement:
        raise ValueError("RequirementResult должен содержать canonical Requirement.")
    if type(result.analysis_state) is not AnalysisState:
        raise ValueError("analysis_state требования не входит в канонический enum.")
    if result.support_status is not None and type(result.support_status) is not SupportStatus:
        raise ValueError("support_status требования не входит в канонический enum.")
    if type(result.atom_results) is not tuple or type(result.mappings) is not tuple:
        raise ValueError("atom_results и mappings требования должны быть tuple.")

    if not result.atom_results:
        if (
            result.analysis_state is AnalysisState.COMPLETED
            or result.support_status is not None
            or result.mappings
        ):
            raise ValueError("Пустой RequirementResult должен быть незавершённым.")
        return

    _validate_requirement_atoms(result.requirement, result.atom_results)
    expected_mappings = tuple(
        item for atom_result in result.atom_results for item in atom_result.mappings
    )
    if result.mappings != expected_mappings:
        raise ValueError(
            "RequirementResult.mappings должен быть ordered flattening атомов."
        )
    expected_state = _aggregate_analysis_state(
        tuple(item.analysis_state for item in result.atom_results)
    )
    if result.analysis_state is not expected_state:
        raise ValueError("analysis_state требования не согласован с атомами.")
    if result.support_status is not _aggregate_status(result.atom_results):
        raise ValueError("support_status требования не согласован с атомами.")


def _aggregate_analysis_state(
    states: tuple[AnalysisState, ...],
) -> AnalysisState:
    for state in _FAILED_STATE_PRECEDENCE:
        if state in states:
            return state
    return AnalysisState.COMPLETED


def _aggregate_group(
    group_id: str,
    children: tuple[RequirementResult, ...],
) -> GroupResult:
    mappings = tuple(item for child in children for item in child.mappings)
    mapping_ids = tuple(dict.fromkeys(item.mapping_id for item in mappings))
    component_ids = tuple(
        sorted(
            {
                item.component_id
                for item in mappings
                if item.support_status in _CONFIRMED_MAPPING_STATUSES
            }
        )
    )
    return GroupResult(
        group_id=group_id,
        source_requirement_ids=tuple(
            child.requirement.requirement_id for child in children
        ),
        support_status=_aggregate_child_statuses(children),
        component_ids=component_ids,
        mapping_ids=mapping_ids,
        analysis_states=tuple(
            dict.fromkeys(child.analysis_state for child in children)
        ),
    )


def _aggregate_child_statuses(
    children: tuple[RequirementResult, ...],
) -> SupportStatus | None:
    if any(
        child.support_status is SupportStatus.NOT_SUPPORTED for child in children
    ):
        return SupportStatus.NOT_SUPPORTED
    if any(
        child.analysis_state is not AnalysisState.COMPLETED for child in children
    ):
        return None

    statuses = {child.support_status for child in children}
    if None in statuses:
        return None
    if SupportStatus.INSUFFICIENT_EVIDENCE in statuses:
        return SupportStatus.INSUFFICIENT_EVIDENCE
    if SupportStatus.PARTIAL in statuses:
        return SupportStatus.PARTIAL
    if statuses == {SupportStatus.NOT_APPLICABLE}:
        return SupportStatus.NOT_APPLICABLE
    return SupportStatus.SUPPORTED
