"""Deep result aggregation and runtime-only graph closure validation."""

from dataclasses import fields, replace

import pytest

from reqmap.deep_aggregation import (
    aggregate_deep_groups,
    aggregate_deep_requirement,
    deep_run_status,
    validate_deep_graph,
)
from reqmap.deep_mapping import DeepMappingOutcome
from reqmap.deep_models import (
    DeepAtomResult,
    DeepEvidence,
    DeepRunResult,
    LifecyclePhase,
    ProcedureGraph,
    ProcedureStep,
    ResponsibilityContour,
    ResponsibilityRecord,
    VersionScope,
)
from reqmap.ids import procedure_graph_id, procedure_step_id, responsibility_id
from reqmap.models import (
    AnalysisState,
    AtomicClaim,
    EvidencePolarity,
    EvidenceStrength,
    Requirement,
    SupportStatus,
)
from reqmap.procedure import ProcedureBuildResult
from tests.factories import atom, requirement


SCOPE = VersionScope("2025.1", "2025.1", "2025.1", "rocky_linux_9", "2025.1")


def _record(
    claim: AtomicClaim,
    ordinal: int,
    *,
    contour: ResponsibilityContour = ResponsibilityContour.OPENSTACK_RUNTIME,
    component_ref: str = "nova",
    executor_ref: str = "ACTOR-NOVA-API",
    target_contour: ResponsibilityContour = ResponsibilityContour.OPENSTACK_RUNTIME,
    target_ref: str = "TARGET-NOVA-SERVER",
    action_ref: str = "ACTION-NOVA-CREATE",
    effect_ref: str = "EFFECT-NOVA-SERVER-ACTIVE",
    support_status: SupportStatus = SupportStatus.SUPPORTED,
    related_record_ids: tuple[str, ...] = (),
    procedure_step_ids: tuple[str, ...] = (),
) -> ResponsibilityRecord:
    return ResponsibilityRecord(
        responsibility_id(claim.atom_id, ordinal),
        claim.requirement_id,
        claim.atom_id,
        contour,
        component_ref,
        executor_ref,
        target_contour,
        target_ref,
        action_ref,
        effect_ref,
        LifecyclePhase.RUNTIME,
        SCOPE,
        ("EV-NOVA-CREATE",),
        support_status,
        related_record_ids,
        procedure_step_ids,
    )


def _outcome(
    claim: AtomicClaim,
    records: tuple[ResponsibilityRecord, ...],
    *,
    state: AnalysisState = AnalysisState.COMPLETED,
    status: SupportStatus | None = SupportStatus.SUPPORTED,
    diagnostics: tuple[str, ...] = (),
) -> DeepMappingOutcome:
    return DeepMappingOutcome(
        DeepAtomResult(
            claim,
            state,
            status,
            tuple(item.record_id for item in records),
            diagnostics=diagnostics,
        ),
        records,
        (),
    )


def _evidence(evidence_id: str = "EV-NOVA-CREATE") -> DeepEvidence:
    return DeepEvidence(
        evidence_id,
        "Nova creates a server.",
        "capability",
        EvidencePolarity.POSITIVE,
        EvidenceStrength.DIRECT,
        "SRC-NOVA",
        "servers#create",
        "2025.1",
        (ResponsibilityContour.OPENSTACK_RUNTIME,),
        ("ACTION-NOVA-CREATE", "EFFECT-NOVA-SERVER-ACTIVE"),
        "A server is created.",
        "reviewed",
    )


def _completed_requirement(
    *, diagnostics: tuple[str, ...] = (), group_ids: tuple[str, ...] = ()
):
    source = replace(requirement(), group_ids=group_ids)
    claim = atom()
    record = _record(claim, 1)
    result, records = aggregate_deep_requirement(
        source, (_outcome(claim, (record,), diagnostics=diagnostics),)
    )
    return result, records


def _run(*, with_procedure: bool = False) -> DeepRunResult:
    source = replace(requirement(), group_ids=("GRP-A",))
    claim = atom()
    record = _record(claim, 1)
    procedure_result = None
    graphs: tuple[ProcedureGraph, ...] = ()
    if with_procedure:
        graph_id = procedure_graph_id(source.requirement_id, 1)
        step_id = procedure_step_id(graph_id, 1)
        step = ProcedureStep(
            step_id,
            LifecyclePhase.RUNTIME,
            ResponsibilityContour.OPENSTACK_RUNTIME,
            record.executor_ref,
            record.target_ref,
            record.action_ref or "",
            ("request is valid",),
            ("server reaches ACTIVE",),
            record.evidence_ids,
        )
        graphs = (ProcedureGraph(graph_id, source.requirement_id, "PROC-NOVA", (step,)),)
        procedure_result = ProcedureBuildResult(
            graphs,
            (replace(record, procedure_step_ids=(step_id,)),),
            (),
        )
    result, records = aggregate_deep_requirement(
        source, (_outcome(claim, (record,)),), procedure_result
    )
    groups = aggregate_deep_groups((result,), records)
    return DeepRunResult(
        "RUN-0001",
        "2.0",
        deep_run_status((result,), preflight_ok=True),
        (result,),
        groups,
        records,
        graphs,
        (_evidence(),),
        {"source_release": "2025.1"},
    )


def _forge(value, **changes):
    forged = object.__new__(type(value))
    values = {field.name: getattr(value, field.name) for field in fields(value)}
    values.update(changes)
    for name, item in values.items():
        object.__setattr__(forged, name, item)
    return forged


def test_deep_run_is_partial_when_completed_requirement_has_procedure_gap() -> None:
    result, _ = _completed_requirement(
        diagnostics=("procedure_gap:ACTION-MIGRATE",)
    )
    assert deep_run_status((result,), preflight_ok=True) == "PARTIAL"


def test_deep_graph_requires_all_responsibility_links() -> None:
    run = _run()
    forged = replace(run, responsibility_records=run.responsibility_records[:-1])
    with pytest.raises(ValueError, match="responsibility"):
        validate_deep_graph(forged)


def test_mixed_requirement_preserves_all_contours() -> None:
    source = requirement()
    claim = atom()
    kolla_id = responsibility_id(claim.atom_id, 1)
    host_id = responsibility_id(claim.atom_id, 2)
    kolla = _record(
        claim,
        1,
        contour=ResponsibilityContour.KOLLA_ANSIBLE,
        component_ref="kolla_ansible",
        executor_ref="actor:kolla_ansible",
        target_contour=ResponsibilityContour.HOST_OS,
        target_ref="rocky_linux_9.kernel_sysctl",
        action_ref="ACTION-KOLLA-SYSCTL",
        effect_ref="EFFECT-HOST-SYSCTL",
        related_record_ids=(host_id,),
    )
    host = _record(
        claim,
        2,
        contour=ResponsibilityContour.HOST_OS,
        component_ref="rocky_linux_9",
        executor_ref="actor:kolla_ansible",
        target_contour=ResponsibilityContour.HOST_OS,
        target_ref="rocky_linux_9.kernel_sysctl",
        action_ref="ACTION-KOLLA-SYSCTL",
        effect_ref="EFFECT-HOST-SYSCTL",
        related_record_ids=(kolla_id,),
    )

    result, records = aggregate_deep_requirement(
        source, (_outcome(claim, (kolla, host)),)
    )

    assert {record.contour for record in records} == {
        ResponsibilityContour.KOLLA_ANSIBLE,
        ResponsibilityContour.HOST_OS,
    }
    assert result.responsibility_ids == tuple(record.record_id for record in records)


@pytest.mark.parametrize(
    ("preflight_ok", "states", "expected"),
    [
        (False, (AnalysisState.COMPLETED,), "FAILED"),
        (True, (), "FAILED"),
        (True, (AnalysisState.MODEL_FAILED,), "FAILED"),
        (True, (AnalysisState.COMPLETED, AnalysisState.MODEL_FAILED), "PARTIAL"),
        (True, (AnalysisState.COMPLETED,), "SUCCESS"),
    ],
)
def test_deep_run_status_keeps_operational_state_separate_from_support(
    preflight_ok: bool, states: tuple[AnalysisState, ...], expected: str
) -> None:
    source, records = _completed_requirement()
    results = tuple(
        source
        if state is AnalysisState.COMPLETED
        else replace(source, analysis_state=state, support_status=None)
        for state in states
    )
    assert deep_run_status(results, preflight_ok=preflight_ok) == expected


@pytest.mark.parametrize(
    "diagnostic",
    (
        "evidence_conflict:EV-1",
        "responsibility_ambiguous:REQ-0001-A001",
        "procedure_gap:ACTION-X",
        "rollback_unverified:PROC-X",
    ),
)
def test_each_operational_gap_prefix_makes_completed_run_partial(
    diagnostic: str,
) -> None:
    result, _ = _completed_requirement(diagnostics=(diagnostic,))
    assert deep_run_status((result,), preflight_ok=True) == "PARTIAL"


def test_requirement_aggregation_preserves_atom_order_and_v1_support_precedence() -> None:
    source = requirement()
    first = atom(ordinal=1)
    second = atom(ordinal=2)
    first_record = _record(first, 1, support_status=SupportStatus.SUPPORTED)
    second_record = _record(second, 1, support_status=SupportStatus.NOT_SUPPORTED)

    result, records = aggregate_deep_requirement(
        source,
        (
            _outcome(first, (first_record,)),
            _outcome(
                second,
                (second_record,),
                status=SupportStatus.NOT_SUPPORTED,
            ),
        ),
    )

    assert result.support_status is SupportStatus.NOT_SUPPORTED
    assert result.atom_results == tuple(
        outcome.atom_result
        for outcome in (
            _outcome(first, (first_record,)),
            _outcome(second, (second_record,), status=SupportStatus.NOT_SUPPORTED),
        )
    )
    assert records == (first_record, second_record)


def test_procedure_result_supplies_graphs_linked_records_and_diagnostics() -> None:
    run = _run(with_procedure=True)
    result = run.requirements[0]

    assert result.procedure_graph_ids == ("REQ-0001-P001",)
    assert run.responsibility_records[0].procedure_step_ids == (
        "REQ-0001-P001-S001",
    )
    assert validate_deep_graph(run) is None


def test_groups_are_recomputed_in_source_order_with_sorted_components() -> None:
    first, first_records = _completed_requirement(group_ids=("GRP-A", "GRP-B"))
    second_requirement = replace(
        requirement(ordinal=2, requirement_id="REQ-0002"), group_ids=("GRP-A",)
    )
    second_claim = atom(requirement_id="REQ-0002")
    second_record = _record(second_claim, 1, component_ref="cinder")
    second, second_records = aggregate_deep_requirement(
        second_requirement, (_outcome(second_claim, (second_record,)),)
    )

    groups = aggregate_deep_groups(
        (first, second), (*first_records, *second_records)
    )

    assert tuple(group.group_id for group in groups) == ("GRP-A", "GRP-B")
    assert groups[0].source_requirement_ids == ("REQ-0001", "REQ-0002")
    assert groups[0].component_refs == ("cinder", "nova")
    assert groups[0].responsibility_ids == (
        "REQ-0001-A001-R001",
        "REQ-0002-A001-R001",
    )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda run: replace(
                run,
                requirements=(
                    replace(
                        run.requirements[0],
                        atom_results=(
                            replace(
                                run.requirements[0].atom_results[0],
                                atom=replace(
                                    run.requirements[0].atom_results[0].atom,
                                    source_quote="invented text",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            "source_quote",
        ),
        (
            lambda run: replace(
                run,
                responsibility_records=(
                    replace(
                        run.responsibility_records[0],
                        related_record_ids=("REQ-0001-A001-R002",),
                    ),
                ),
            ),
            "related",
        ),
        (
            lambda run: replace(run, evidence=()),
            "evidence",
        ),
        (
            lambda run: replace(
                run,
                groups=(replace(run.groups[0], component_refs=("forged",)),),
            ),
            "group",
        ),
    ],
)
def test_deep_graph_rejects_traceability_and_flattening_forgery(
    mutator, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_deep_graph(mutator(_run()))


def test_deep_graph_rejects_noncanonical_ids_and_version_scope() -> None:
    run = _run()
    record = run.responsibility_records[0]
    forged_id = _forge(record, record_id="REQ-0001-A001-R009")
    atom_result = replace(
        run.requirements[0].atom_results[0],
        responsibility_ids=(forged_id.record_id,),
    )
    requirement_result = replace(
        run.requirements[0],
        atom_results=(atom_result,),
        responsibility_ids=(forged_id.record_id,),
    )
    group = replace(run.groups[0], responsibility_ids=(forged_id.record_id,))
    with pytest.raises(ValueError, match="responsibility.*ID"):
        validate_deep_graph(
            replace(
                run,
                requirements=(requirement_result,),
                groups=(group,),
                responsibility_records=(forged_id,),
            )
        )

    bad_scope = VersionScope(
        "2025.1", "2026.1", "2025.1", "rocky_linux_9", "2026.1"
    )
    forged_scope = _forge(record, version_scope=bad_scope)
    with pytest.raises(ValueError, match="upgrade"):
        validate_deep_graph(replace(run, responsibility_records=(forged_scope,)))


def test_deep_graph_rejects_orphan_or_mismatched_procedure_steps() -> None:
    run = _run(with_procedure=True)
    graph = run.procedure_graphs[0]
    record = run.responsibility_records[0]

    unlinked = replace(record, procedure_step_ids=())
    with pytest.raises(ValueError, match="step.*responsibility"):
        validate_deep_graph(replace(run, responsibility_records=(unlinked,)))

    wrong_action_step = replace(graph.steps[0], action_ref="ACTION-OTHER")
    wrong_action_graph = replace(graph, steps=(wrong_action_step,))
    with pytest.raises(ValueError, match="action"):
        validate_deep_graph(replace(run, procedure_graphs=(wrong_action_graph,)))


def test_deep_graph_rejects_extra_uncited_evidence_and_unreferenced_graph() -> None:
    run = _run()
    with pytest.raises(ValueError, match="evidence"):
        validate_deep_graph(replace(run, evidence=(*run.evidence, _evidence("EV-EXTRA"))))

    procedure_run = _run(with_procedure=True)
    requirement_without_graph = replace(
        procedure_run.requirements[0], procedure_graph_ids=()
    )
    with pytest.raises(ValueError, match="procedure graph"):
        validate_deep_graph(
            replace(procedure_run, requirements=(requirement_without_graph,))
        )


def test_deep_graph_recomputes_atom_support_and_operational_run_status() -> None:
    run = _run()
    atom_result = replace(
        run.requirements[0].atom_results[0], support_status=SupportStatus.PARTIAL
    )
    requirement_result = replace(
        run.requirements[0],
        atom_results=(atom_result,),
        support_status=SupportStatus.PARTIAL,
    )
    group = replace(run.groups[0], support_status=SupportStatus.PARTIAL)
    with pytest.raises(ValueError, match="atom support_status"):
        validate_deep_graph(
            replace(run, requirements=(requirement_result,), groups=(group,))
        )

    with pytest.raises(ValueError, match="run_status"):
        validate_deep_graph(replace(run, run_status="PARTIAL"))


def test_deep_graph_rejects_procedure_step_without_cited_evidence() -> None:
    run = _run(with_procedure=True)
    graph = run.procedure_graphs[0]
    empty_evidence = replace(graph.steps[0], evidence_ids=())

    with pytest.raises(ValueError, match="step.*evidence"):
        validate_deep_graph(
            replace(
                run,
                procedure_graphs=(replace(graph, steps=(empty_evidence,)),),
            )
        )


def test_deep_graph_allows_noncontiguous_source_step_ids_but_requires_topological_order() -> None:
    run = _run(with_procedure=True)
    graph = run.procedure_graphs[0]
    first = graph.steps[0]
    verify = ProcedureStep(
        procedure_step_id(graph.graph_id, 3),
        LifecyclePhase.VERIFY,
        first.contour,
        first.executor_ref,
        first.target_ref,
        first.action_ref,
        ("server create was requested",),
        ("server reaches ACTIVE",),
        first.evidence_ids,
        (first.step_id,),
    )
    linked_record = replace(
        run.responsibility_records[0],
        procedure_step_ids=(first.step_id, verify.step_id),
    )
    valid_graph = replace(graph, steps=(first, verify))
    valid = replace(
        run,
        responsibility_records=(linked_record,),
        procedure_graphs=(valid_graph,),
    )

    assert validate_deep_graph(valid) is None
    with pytest.raises(ValueError, match="topological"):
        validate_deep_graph(
            replace(valid, procedure_graphs=(replace(graph, steps=(verify, first)),))
        )


def test_deep_graph_requires_rollback_target_after_forward_step() -> None:
    run = _run(with_procedure=True)
    graph = run.procedure_graphs[0]
    rollback_id = procedure_step_id(graph.graph_id, 3)
    forward = replace(graph.steps[0], rollback_step_id=rollback_id)
    rollback = ProcedureStep(
        rollback_id,
        LifecyclePhase.ROLLBACK,
        forward.contour,
        forward.executor_ref,
        forward.target_ref,
        forward.action_ref,
        ("created server is known",),
        ("server is absent",),
        forward.evidence_ids,
    )
    linked_record = replace(
        run.responsibility_records[0],
        procedure_step_ids=(forward.step_id, rollback.step_id),
    )
    forged = replace(
        run,
        responsibility_records=(linked_record,),
        procedure_graphs=(replace(graph, steps=(rollback, forward)),),
    )

    with pytest.raises(ValueError, match="topological"):
        validate_deep_graph(forged)


def test_related_responsibilities_cannot_cross_atomic_claims() -> None:
    source = replace(requirement(), group_ids=("GRP-A",))
    first_claim = atom(ordinal=1)
    second_claim = atom(ordinal=2)
    first_id = responsibility_id(first_claim.atom_id, 1)
    second_id = responsibility_id(second_claim.atom_id, 1)
    first = _record(first_claim, 1, related_record_ids=(second_id,))
    second = _record(second_claim, 1, related_record_ids=(first_id,))
    result, records = aggregate_deep_requirement(
        source,
        (
            _outcome(first_claim, (first,)),
            _outcome(second_claim, (second,)),
        ),
    )
    run = DeepRunResult(
        "RUN-0001",
        "2.0",
        "SUCCESS",
        (result,),
        aggregate_deep_groups((result,), records),
        records,
        (),
        (_evidence(),),
        {},
    )

    with pytest.raises(ValueError, match="same atomic claim"):
        validate_deep_graph(run)


def test_responsibility_evidence_must_cover_both_action_and_effect() -> None:
    run = _run()
    action_only = replace(
        run.evidence[0], supports_entity_refs=("ACTION-NOVA-CREATE",)
    )

    with pytest.raises(ValueError, match="effect"):
        validate_deep_graph(replace(run, evidence=(action_only,)))


@pytest.mark.parametrize(
    "source",
    (
        replace(requirement(), ordinal=2),
        replace(requirement(), requirement_id="REQ-0002"),
    ),
)
def test_deep_graph_requires_source_ordered_generated_requirement_ids(
    source: Requirement,
) -> None:
    run = _run()
    forged = replace(run.requirements[0], requirement=source)

    with pytest.raises(ValueError, match="requirement.*ID.*order"):
        validate_deep_graph(replace(run, requirements=(forged,)))
