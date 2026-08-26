"""Evidence-backed procedure instantiation and public DAG validation."""

from dataclasses import fields, replace
from types import MappingProxyType

import pytest

from reqmap.deep_models import (
    LifecyclePhase,
    ProcedureGraph,
    ProcedureStep,
    ResponsibilityContour,
    ResponsibilityRecord,
)
from reqmap.knowledge_v2 import validate_knowledge_v2
from reqmap.models import EvidenceStrength, SupportStatus
from reqmap.procedure import (
    ProcedureError,
    instantiate_procedure_graphs,
    validate_procedure_graph,
)
from tests.deep_factories import (
    immutable_v2_kb,
    mixed_sysctl_records,
    procedure_kb,
)


@pytest.fixture
def v2_kb(tmp_path):
    return procedure_kb(immutable_v2_kb(tmp_path))


def _nova_record(kb) -> ResponsibilityRecord:
    action = kb.actions["ACTION-NOVA-CREATE"]
    return ResponsibilityRecord(
        "REQ-0001-A002-R001",
        "REQ-0001",
        "REQ-0001-A002",
        ResponsibilityContour.OPENSTACK_RUNTIME,
        "nova",
        "ACTOR-NOVA-API",
        ResponsibilityContour.OPENSTACK_RUNTIME,
        action.target_ref,
        action.action_id,
        "EFFECT-NOVA-SERVER-ACTIVE",
        LifecyclePhase.RUNTIME,
        action.version_scope,
        action.evidence_ids,
        SupportStatus.SUPPORTED,
    )


def _replace_template(kb, template_id: str = "PROC-SYSCTL", **changes):
    template = replace(kb.procedures[template_id], **changes)
    return replace(
        kb,
        procedures=MappingProxyType({**kb.procedures, template_id: template}),
    )


def _replace_step(kb, index: int, **changes):
    template = kb.procedures["PROC-SYSCTL"]
    steps = list(template.steps)
    steps[index] = replace(steps[index], **changes)
    return _replace_template(kb, steps=tuple(steps))


def _instantiate(kb):
    records = mixed_sysctl_records(kb)
    return instantiate_procedure_graphs(
        "REQ-0001", ("PROC-SYSCTL",), records, kb
    )


def _issue_codes(graph, records, kb) -> set[str]:
    return {item.code for item in validate_procedure_graph(graph, records, kb)}


def _forge_step(step: ProcedureStep, **changes) -> ProcedureStep:
    forged = object.__new__(ProcedureStep)
    values = {field.name: getattr(step, field.name) for field in fields(step)}
    values.update(changes)
    for name, value in values.items():
        object.__setattr__(forged, name, value)
    return forged


def test_instantiated_procedure_has_four_phases_stable_ids_and_links(v2_kb) -> None:
    records = mixed_sysctl_records(v2_kb)
    original = tuple(records)

    result = instantiate_procedure_graphs(
        "REQ-0001", ("PROC-SYSCTL",), records, v2_kb
    )

    graph = result.graphs[0]
    assert graph.graph_id == "REQ-0001-P001"
    assert [step.phase for step in graph.steps] == [
        LifecyclePhase.PREFLIGHT,
        LifecyclePhase.RECONFIGURE,
        LifecyclePhase.VERIFY,
        LifecyclePhase.ROLLBACK,
    ]
    assert [step.step_id for step in graph.steps] == [
        "REQ-0001-P001-S001",
        "REQ-0001-P001-S002",
        "REQ-0001-P001-S003",
        "REQ-0001-P001-S004",
    ]
    assert graph.steps[1].depends_on == ("REQ-0001-P001-S001",)
    assert graph.steps[1].rollback_step_id == "REQ-0001-P001-S004"
    assert validate_procedure_graph(graph, records, v2_kb) == ()
    assert validate_procedure_graph(
        graph, result.responsibility_records, v2_kb
    ) == ()
    expected_links = tuple(step.step_id for step in graph.steps)
    assert all(
        record.procedure_step_ids == expected_links
        for record in result.responsibility_records
    )
    assert records == original
    assert all(record.procedure_step_ids == () for record in records)


def test_topological_order_uses_source_order_ties_but_ids_use_source_ordinals(
    v2_kb,
) -> None:
    template = v2_kb.procedures["PROC-SYSCTL"]
    verify, preflight, rollback, action = (
        template.steps[2],
        template.steps[0],
        template.steps[3],
        template.steps[1],
    )
    kb = _replace_template(v2_kb, steps=(verify, preflight, rollback, action))

    graph = _instantiate(kb).graphs[0]

    assert [step.phase for step in graph.steps] == [
        LifecyclePhase.PREFLIGHT,
        LifecyclePhase.RECONFIGURE,
        LifecyclePhase.VERIFY,
        LifecyclePhase.ROLLBACK,
    ]
    assert [step.step_id for step in graph.steps] == [
        "REQ-0001-P001-S002",
        "REQ-0001-P001-S004",
        "REQ-0001-P001-S001",
        "REQ-0001-P001-S003",
    ]


def test_required_procedure_without_template_is_explicit_gap(v2_kb) -> None:
    result = instantiate_procedure_graphs(
        "REQ-0001", (), (_nova_record(v2_kb),), v2_kb
    )

    assert result.graphs == ()
    assert result.diagnostics == ("procedure_gap:ACTION-NOVA-CREATE",)


def test_template_declaration_without_actual_action_step_does_not_cover_gap(
    v2_kb,
) -> None:
    template = v2_kb.procedures["PROC-NOVA-CREATE"]
    kb = _replace_template(v2_kb, template.template_id, steps=())

    result = instantiate_procedure_graphs(
        "REQ-0001", (template.template_id,), (_nova_record(kb),), kb
    )

    assert result.graphs == ()
    assert result.diagnostics == ("procedure_gap:ACTION-NOVA-CREATE",)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"depends_on": ("missing-step",)}, "PROCEDURE_STEP_UNKNOWN"),
        ({"rollback_step_local_id": "missing-step"}, "PROCEDURE_ROLLBACK_UNKNOWN"),
    ],
)
def test_missing_template_local_references_fail_closed(v2_kb, changes, code) -> None:
    kb = _replace_step(v2_kb, 1, **changes)

    with pytest.raises(ProcedureError, match=code):
        _instantiate(kb)


def test_dependency_cycle_is_rejected(v2_kb) -> None:
    kb = _replace_step(v2_kb, 0, depends_on=("apply-sysctl",))

    with pytest.raises(ProcedureError, match="PROCEDURE_CYCLE"):
        _instantiate(kb)


def test_duplicate_template_or_local_step_ids_are_rejected(v2_kb) -> None:
    with pytest.raises(ProcedureError, match="PROCEDURE_TEMPLATE_DUPLICATE"):
        instantiate_procedure_graphs(
            "REQ-0001",
            ("PROC-SYSCTL", "PROC-SYSCTL"),
            mixed_sysctl_records(v2_kb),
            v2_kb,
        )

    template = v2_kb.procedures["PROC-SYSCTL"]
    duplicate = replace(template.steps[1], local_step_id="check-current")
    kb = _replace_template(v2_kb, steps=(template.steps[0], duplicate))
    with pytest.raises(ProcedureError, match="PROCEDURE_STEP_DUPLICATE"):
        _instantiate(kb)


def test_unknown_template_is_rejected_without_fabricating_a_graph(v2_kb) -> None:
    with pytest.raises(ProcedureError, match="PROCEDURE_TEMPLATE_UNKNOWN"):
        instantiate_procedure_graphs(
            "REQ-0001", ("PROC-MISSING",), mixed_sysctl_records(v2_kb), v2_kb
        )


def test_selected_template_without_matching_responsibility_is_rejected(v2_kb) -> None:
    with pytest.raises(
        ProcedureError, match="PROCEDURE_TEMPLATE_TRIGGER_NOT_SELECTED"
    ):
        instantiate_procedure_graphs(
            "REQ-0001", ("PROC-SYSCTL",), (_nova_record(v2_kb),), v2_kb
        )


@pytest.mark.parametrize(
    ("index", "changes", "code"),
    [
        (1, {"executor_ref": "ACTOR-MISSING"}, "PROCEDURE_ACTOR_UNKNOWN"),
        (1, {"target_ref": "TARGET-NOVA-SERVER"}, "PROCEDURE_TARGET_MISMATCH"),
        (1, {"action_ref": "ACTION-NOVA-CREATE"}, "PROCEDURE_ACTION_MISMATCH"),
        (1, {"evidence_ids": ("EV-NOVA-CREATE",)}, "PROCEDURE_EVIDENCE_MISMATCH"),
        (
            1,
            {"contour": ResponsibilityContour.OPENSTACK_RUNTIME},
            "PROCEDURE_CONTOUR_MISMATCH",
        ),
        (1, {"preconditions": (" ",)}, "PROCEDURE_PRECONDITION_BLANK"),
        (1, {"success_criteria": ()}, "PROCEDURE_SUCCESS_CRITERIA_BLANK"),
    ],
)
def test_instantiation_rejects_forged_template_relationships(
    v2_kb, index, changes, code
) -> None:
    kb = _replace_step(v2_kb, index, **changes)

    with pytest.raises(ProcedureError, match=code):
        _instantiate(kb)


@pytest.mark.parametrize("step_index", (0, 2))
def test_non_rollback_step_requires_applicable_direct_action_evidence(
    v2_kb, step_index
) -> None:
    weak = replace(
        v2_kb.evidence["EV-KOLLA-SYSCTL"],
        evidence_id="EV-KOLLA-WEAK",
        strength=EvidenceStrength.INDIRECT,
    )
    kb = replace(
        v2_kb,
        evidence=MappingProxyType({**v2_kb.evidence, weak.evidence_id: weak}),
    )
    kb = _replace_step(kb, step_index, evidence_ids=(weak.evidence_id,))

    with pytest.raises(ProcedureError, match="PROCEDURE_DIRECT_EVIDENCE_REQUIRED"):
        _instantiate(kb)


def test_unverified_rollback_is_reported_and_not_instantiated(v2_kb) -> None:
    weak = replace(
        v2_kb.evidence["EV-KOLLA-SYSCTL"],
        evidence_id="EV-KOLLA-ROLLBACK-WEAK",
        strength=EvidenceStrength.INDIRECT,
    )
    kb = replace(
        v2_kb,
        evidence=MappingProxyType({**v2_kb.evidence, weak.evidence_id: weak}),
    )
    kb = _replace_step(kb, 3, evidence_ids=(weak.evidence_id,))

    result = _instantiate(kb)

    graph = result.graphs[0]
    assert [step.phase for step in graph.steps] == [
        LifecyclePhase.PREFLIGHT,
        LifecyclePhase.RECONFIGURE,
        LifecyclePhase.VERIFY,
    ]
    assert graph.steps[1].rollback_step_id is None
    assert graph.diagnostics == ("rollback_unverified",)
    assert result.diagnostics == ("rollback_unverified",)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda step: replace(step, executor_ref="ACTOR-MISSING"),
            "PROCEDURE_ACTOR_UNKNOWN",
        ),
        (
            lambda step: replace(step, target_ref="TARGET-NOVA-SERVER"),
            "PROCEDURE_TARGET_MISMATCH",
        ),
        (
            lambda step: replace(step, action_ref="ACTION-NOVA-CREATE"),
            "PROCEDURE_CONTOUR_MISMATCH",
        ),
        (
            lambda step: replace(step, evidence_ids=("EV-NOVA-CREATE",)),
            "PROCEDURE_EVIDENCE_MISMATCH",
        ),
        (
            lambda step: replace(
                step, contour=ResponsibilityContour.OPENSTACK_RUNTIME
            ),
            "PROCEDURE_CONTOUR_MISMATCH",
        ),
        (
            lambda step: _forge_step(step, preconditions=(" ",)),
            "PROCEDURE_PRECONDITION_BLANK",
        ),
        (
            lambda step: _forge_step(step, success_criteria=()),
            "PROCEDURE_SUCCESS_CRITERIA_BLANK",
        ),
    ],
)
def test_public_validator_rejects_forged_step_relationships(
    v2_kb, mutator, expected
) -> None:
    result = _instantiate(v2_kb)
    graph = result.graphs[0]
    steps = list(graph.steps)
    steps[1] = mutator(steps[1])
    forged = replace(graph, steps=tuple(steps))

    assert expected in _issue_codes(forged, mixed_sysctl_records(v2_kb), v2_kb)


def test_public_validator_rejects_duplicate_or_cross_graph_step_links(v2_kb) -> None:
    graph = _instantiate(v2_kb).graphs[0]
    duplicate = replace(graph, steps=(*graph.steps, graph.steps[0]))
    assert "PROCEDURE_STEP_DUPLICATE" in _issue_codes(
        duplicate, mixed_sysctl_records(v2_kb), v2_kb
    )

    steps = list(graph.steps)
    steps[1] = replace(steps[1], depends_on=("REQ-0001-P999-S001",))
    cross = replace(graph, steps=tuple(steps))
    assert "PROCEDURE_CROSS_GRAPH_REFERENCE" in _issue_codes(
        cross, mixed_sysctl_records(v2_kb), v2_kb
    )

    steps[1] = replace(steps[1], depends_on=("REQ-0001-P001-S999",))
    orphan = replace(graph, steps=tuple(steps))
    assert "PROCEDURE_STEP_UNKNOWN" in _issue_codes(
        orphan, mixed_sysctl_records(v2_kb), v2_kb
    )


def test_public_validator_rejects_invented_or_missing_template_content(v2_kb) -> None:
    graph = _instantiate(v2_kb).graphs[0]
    steps = list(graph.steps)
    steps[0] = replace(steps[0], preconditions=("invented precondition",))
    invented = replace(graph, steps=tuple(steps))
    assert "PROCEDURE_STEP_TEMPLATE_MISMATCH" in _issue_codes(
        invented, mixed_sysctl_records(v2_kb), v2_kb
    )

    incomplete = replace(
        graph,
        steps=tuple(
            step for step in graph.steps if step.phase is not LifecyclePhase.VERIFY
        ),
    )
    assert "PROCEDURE_STEP_MISSING" in _issue_codes(
        incomplete, mixed_sysctl_records(v2_kb), v2_kb
    )


def test_public_validator_rejects_cycle_and_asymmetric_rollback_link(v2_kb) -> None:
    graph = _instantiate(v2_kb).graphs[0]
    steps = list(graph.steps)
    steps[0] = replace(steps[0], depends_on=(steps[1].step_id,))
    cyclic = replace(graph, steps=tuple(steps))
    assert "PROCEDURE_CYCLE" in _issue_codes(
        cyclic, mixed_sysctl_records(v2_kb), v2_kb
    )

    steps = list(graph.steps)
    steps[1] = replace(steps[1], rollback_step_id=None)
    asymmetric = replace(graph, steps=tuple(steps))
    assert "PROCEDURE_ROLLBACK_LINK_ASYMMETRIC" in _issue_codes(
        asymmetric, mixed_sysctl_records(v2_kb), v2_kb
    )


def test_public_validator_rejects_foreign_responsibility_step_link(v2_kb) -> None:
    graph = _instantiate(v2_kb).graphs[0]
    foreign = replace(
        _nova_record(v2_kb), procedure_step_ids=(graph.steps[0].step_id,)
    )
    cross = replace(
        mixed_sysctl_records(v2_kb)[0],
        procedure_step_ids=("REQ-0001-P999-S001",),
    )

    assert "PROCEDURE_RESPONSIBILITY_ACTION_MISMATCH" in _issue_codes(
        graph, (foreign,), v2_kb
    )
    assert "PROCEDURE_CROSS_GRAPH_REFERENCE" in _issue_codes(
        graph, (cross,), v2_kb
    )


@pytest.mark.parametrize(
    ("index", "changes", "expected"),
    [
        (1, {"executor_ref": "ACTOR-MISSING"}, "PROCEDURE_ACTOR_UNKNOWN"),
        (1, {"target_ref": "TARGET-NOVA-SERVER"}, "PROCEDURE_TARGET_MISMATCH"),
        (
            1,
            {"contour": ResponsibilityContour.OPENSTACK_RUNTIME},
            "PROCEDURE_CONTOUR_MISMATCH",
        ),
        (1, {"preconditions": ()}, "PROCEDURE_PRECONDITION_BLANK"),
        (1, {"success_criteria": (" ",)}, "PROCEDURE_SUCCESS_CRITERIA_BLANK"),
    ],
)
def test_kb_validation_rejects_untrusted_procedure_relations(
    v2_kb, index, changes, expected
) -> None:
    kb = _replace_step(v2_kb, index, **changes)

    assert expected in {issue.code for issue in validate_knowledge_v2(kb)}
