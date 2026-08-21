"""Детерминированная агрегация атомов, требований и групп."""

from dataclasses import replace

import pytest

from reqmap.aggregation import (
    aggregate_groups,
    aggregate_requirement,
    aggregate_status,
)
from reqmap.models import (
    AnalysisState,
    AtomResult,
    RequirementResult,
    SupportStatus,
)
from tests.factories import atom, mapping, requirement


def atom_result(
    ordinal: int,
    state: AnalysisState,
    status: SupportStatus | None,
    *,
    mandatory: bool = True,
    component_id: str = "nova",
) -> AtomResult:
    claim = replace(atom(ordinal=ordinal), mandatory=mandatory)
    mappings = ()
    if status is not None and status is not SupportStatus.NOT_APPLICABLE:
        item = replace(
            mapping(ordinal=1, atom_id=claim.atom_id),
            component_id=component_id,
            support_status=status,
        )
        mappings = (item,)
    return AtomResult(
        atom=claim,
        analysis_state=state,
        support_status=status,
        mappings=mappings,
        diagnostics=(() if state is AnalysisState.COMPLETED else (state.value,)),
    )


@pytest.mark.parametrize(
    ("states", "statuses", "expected"),
    [
        (
            (AnalysisState.COMPLETED, AnalysisState.COMPLETED),
            (SupportStatus.SUPPORTED, SupportStatus.NOT_SUPPORTED),
            SupportStatus.NOT_SUPPORTED,
        ),
        (
            (AnalysisState.MODEL_FAILED, AnalysisState.COMPLETED),
            (None, SupportStatus.SUPPORTED),
            None,
        ),
        (
            (AnalysisState.COMPLETED, AnalysisState.COMPLETED),
            (SupportStatus.SUPPORTED, SupportStatus.INSUFFICIENT_EVIDENCE),
            SupportStatus.INSUFFICIENT_EVIDENCE,
        ),
        (
            (AnalysisState.COMPLETED, AnalysisState.COMPLETED),
            (SupportStatus.SUPPORTED, SupportStatus.PARTIAL),
            SupportStatus.PARTIAL,
        ),
        (
            (AnalysisState.COMPLETED, AnalysisState.COMPLETED),
            (SupportStatus.NOT_APPLICABLE, SupportStatus.NOT_APPLICABLE),
            SupportStatus.NOT_APPLICABLE,
        ),
        (
            (AnalysisState.COMPLETED, AnalysisState.COMPLETED),
            (SupportStatus.SUPPORTED, SupportStatus.NOT_APPLICABLE),
            SupportStatus.SUPPORTED,
        ),
    ],
)
def test_requirement_status_precedence(
    states: tuple[AnalysisState, ...],
    statuses: tuple[SupportStatus | None, ...],
    expected: SupportStatus | None,
) -> None:
    results = tuple(
        atom_result(index, state, status)
        for index, (state, status) in enumerate(zip(states, statuses), start=1)
    )

    assert aggregate_status(results) is expected


def test_optional_atom_is_preserved_but_does_not_lower_support() -> None:
    required = atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED)
    optional = atom_result(
        2,
        AnalysisState.COMPLETED,
        SupportStatus.NOT_SUPPORTED,
        mandatory=False,
        component_id="neutron",
    )

    aggregated = aggregate_requirement(requirement(), (required, optional))

    assert aggregated.support_status is SupportStatus.SUPPORTED
    assert aggregated.atom_results == (required, optional)
    assert [item.component_id for item in aggregated.mappings] == ["nova", "neutron"]


def test_requirement_keeps_failure_state_diagnostics_and_mapping_order() -> None:
    completed = atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED)
    failed = atom_result(2, AnalysisState.MODEL_FAILED, None)

    aggregated = aggregate_requirement(requirement(), (completed, failed))

    assert aggregated.analysis_state is AnalysisState.MODEL_FAILED
    assert aggregated.support_status is None
    assert aggregated.atom_results == (completed, failed)
    assert aggregated.mappings == completed.mappings
    assert aggregated.diagnostics == (AnalysisState.MODEL_FAILED.value,)


def test_not_supported_precedes_failure_at_requirement_level() -> None:
    unsupported = atom_result(
        1,
        AnalysisState.COMPLETED,
        SupportStatus.NOT_SUPPORTED,
    )
    failed = atom_result(2, AnalysisState.VALIDATION_FAILED, None)

    aggregated = aggregate_requirement(requirement(), (unsupported, failed))

    assert aggregated.analysis_state is AnalysisState.VALIDATION_FAILED
    assert aggregated.support_status is SupportStatus.NOT_SUPPORTED


def test_aggregate_requirement_rejects_foreign_or_reordered_atoms() -> None:
    source = requirement()
    foreign = replace(atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED).atom, requirement_id="REQ-9999")
    reordered = (
        atom_result(2, AnalysisState.COMPLETED, SupportStatus.SUPPORTED),
        atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED),
    )

    with pytest.raises(ValueError, match="требован"):
        aggregate_requirement(source, (replace(atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED), atom=foreign),))
    with pytest.raises(ValueError, match="поряд"):
        aggregate_requirement(source, reordered)


def test_aggregate_status_rejects_failed_atom_with_subject_status() -> None:
    invalid = atom_result(
        1,
        AnalysisState.MODEL_FAILED,
        SupportStatus.NOT_SUPPORTED,
    )

    with pytest.raises(ValueError, match="Незавершённый"):
        aggregate_status((invalid,))


def test_aggregate_requirement_rejects_mapping_owned_by_another_atom() -> None:
    valid = atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED)
    invalid = replace(
        valid,
        mappings=(replace(valid.mappings[0], atom_id="REQ-9999-A001"),),
    )

    with pytest.raises(ValueError, match="текущий atom"):
        aggregate_requirement(requirement(), (invalid,))


def test_aggregate_requirement_rejects_unstable_mapping_id() -> None:
    valid = atom_result(1, AnalysisState.COMPLETED, SupportStatus.SUPPORTED)
    invalid = replace(
        valid,
        mappings=(replace(valid.mappings[0], mapping_id="forged"),),
    )

    with pytest.raises(ValueError, match="mapping_id"):
        aggregate_requirement(requirement(), (invalid,))


def requirement_result(
    ordinal: int,
    group_ids: tuple[str, ...],
    state: AnalysisState,
    status: SupportStatus | None,
    *,
    component_id: str,
) -> RequirementResult:
    source = replace(
        requirement(ordinal=ordinal, requirement_id=f"REQ-{ordinal:04d}"),
        group_ids=group_ids,
    )
    result = atom_result(
        1,
        state,
        status,
        component_id=component_id,
    )
    result = replace(
        result,
        atom=replace(
            result.atom,
            atom_id=f"{source.requirement_id}-A001",
            requirement_id=source.requirement_id,
        ),
        mappings=tuple(
            replace(
                item,
                mapping_id=f"{source.requirement_id}-A001-M001",
                atom_id=f"{source.requirement_id}-A001",
            )
            for item in result.mappings
        ),
    )
    return RequirementResult(
        requirement=source,
        analysis_state=state,
        support_status=status,
        atom_results=(result,),
        mappings=result.mappings,
        diagnostics=result.diagnostics,
    )


def test_groups_preserve_source_order_and_only_confirmed_component_union() -> None:
    first = requirement_result(
        1,
        ("GRP-A", "GRP-B"),
        AnalysisState.COMPLETED,
        SupportStatus.SUPPORTED,
        component_id="nova",
    )
    second = requirement_result(
        2,
        ("GRP-A",),
        AnalysisState.COMPLETED,
        SupportStatus.INSUFFICIENT_EVIDENCE,
        component_id="neutron",
    )
    third = requirement_result(
        3,
        ("GRP-A",),
        AnalysisState.COMPLETED,
        SupportStatus.NOT_SUPPORTED,
        component_id="cinder",
    )

    groups = aggregate_groups((first, second, third))

    assert [item.group_id for item in groups] == ["GRP-A", "GRP-B"]
    assert groups[0].source_requirement_ids == ("REQ-0001", "REQ-0002", "REQ-0003")
    assert groups[0].support_status is SupportStatus.NOT_SUPPORTED
    assert groups[0].component_ids == ("cinder", "nova")
    assert groups[0].mapping_ids == (
        "REQ-0001-A001-M001",
        "REQ-0002-A001-M001",
        "REQ-0003-A001-M001",
    )
    assert groups[1].source_requirement_ids == ("REQ-0001",)


def test_group_failure_clears_status_without_confirmed_not_supported() -> None:
    completed = requirement_result(
        1,
        ("GRP-A",),
        AnalysisState.COMPLETED,
        SupportStatus.SUPPORTED,
        component_id="nova",
    )
    failed = requirement_result(
        2,
        ("GRP-A",),
        AnalysisState.MODEL_FAILED,
        None,
        component_id="neutron",
    )

    group = aggregate_groups((completed, failed))[0]

    assert group.support_status is None
    assert group.analysis_states == (
        AnalysisState.COMPLETED,
        AnalysisState.MODEL_FAILED,
    )


def test_aggregate_groups_rejects_forged_flattened_mappings() -> None:
    valid = requirement_result(
        1,
        ("GRP-A",),
        AnalysisState.COMPLETED,
        SupportStatus.SUPPORTED,
        component_id="nova",
    )
    forged = replace(
        valid,
        mappings=(replace(valid.mappings[0], component_id="neutron"),),
    )

    with pytest.raises(ValueError, match="ordered flattening"):
        aggregate_groups((forged,))


def test_ungrouped_requirements_do_not_create_group_records() -> None:
    source = requirement_result(
        1,
        (),
        AnalysisState.COMPLETED,
        SupportStatus.SUPPORTED,
        component_id="nova",
    )

    assert aggregate_groups((source,)) == ()
