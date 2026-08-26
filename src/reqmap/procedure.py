"""Deterministic instantiation and fail-closed validation of procedure DAGs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import re

from reqmap.deep_models import (
    LifecyclePhase,
    ProcedureGraph,
    ProcedureStep,
    ResponsibilityRecord,
)
from reqmap.errors import ReqmapError
from reqmap.ids import procedure_graph_id, procedure_step_id
from reqmap.knowledge_v2 import (
    KnowledgeBaseV2,
    ProcedureTemplateRecord,
    ProcedureTemplateStepRecord,
)
from reqmap.models import EvidencePolarity, EvidenceStrength


_EVIDENCE_REQUIRED_PHASES = frozenset(
    {
        LifecyclePhase.PREFLIGHT,
        LifecyclePhase.DEPLOY,
        LifecyclePhase.RUNTIME,
        LifecyclePhase.RECONFIGURE,
        LifecyclePhase.UPGRADE,
        LifecyclePhase.MIGRATE,
        LifecyclePhase.RECOVER,
        LifecyclePhase.VERIFY,
        LifecyclePhase.ROLLBACK,
    }
)
_ROLLBACK_EVIDENCE_CODES = frozenset(
    {
        "PROCEDURE_EVIDENCE_UNKNOWN",
        "PROCEDURE_EVIDENCE_MISMATCH",
        "PROCEDURE_DIRECT_EVIDENCE_REQUIRED",
    }
)


class ProcedureError(ReqmapError):
    """A selected procedure cannot be instantiated without invention."""


@dataclass(frozen=True)
class ProcedureIssue:
    code: str
    object_id: str
    message_ru: str


@dataclass(frozen=True)
class ProcedureBuildResult:
    graphs: tuple[ProcedureGraph, ...]
    responsibility_records: tuple[ResponsibilityRecord, ...]
    diagnostics: tuple[str, ...]


def instantiate_procedure_graphs(
    requirement_id: str,
    template_ids: Sequence[str],
    responsibilities: Sequence[ResponsibilityRecord],
    kb: KnowledgeBaseV2,
) -> ProcedureBuildResult:
    """Instantiate selected trusted templates without creating missing operations."""
    # Invoke the Task-1 helper before consuming any caller-controlled identifier.
    try:
        procedure_graph_id(requirement_id, 1)
    except ValueError as exc:
        _fail("PROCEDURE_REQUIREMENT_INVALID", str(exc))
    selected_ids = tuple(template_ids)
    if len(selected_ids) != len(set(selected_ids)):
        _fail(
            "PROCEDURE_TEMPLATE_DUPLICATE",
            "Выбранные procedure templates содержат duplicate ID.",
        )
    ordered_ids = tuple(sorted(selected_ids))
    templates: list[ProcedureTemplateRecord] = []
    for template_id in ordered_ids:
        template = kb.procedures.get(template_id)
        if template is None:
            _fail(
                "PROCEDURE_TEMPLATE_UNKNOWN",
                f"Procedure template отсутствует в KB: {template_id}.",
            )
        _validate_template_structure(template)
        templates.append(template)

    records = tuple(responsibilities)
    for record in records:
        if record.requirement_id != requirement_id:
            _fail(
                "PROCEDURE_REQUIREMENT_MISMATCH",
                f"Responsibility {record.record_id} относится к другому requirement.",
            )
        if record.action_ref is not None and record.action_ref not in kb.actions:
            _fail(
                "PROCEDURE_ACTION_UNKNOWN",
                f"Responsibility ссылается на неизвестный action: {record.action_ref}.",
            )
    selected_action_refs = {
        record.action_ref for record in records if record.action_ref is not None
    }

    graphs: list[ProcedureGraph] = []
    all_diagnostics: list[str] = []
    links_by_action: dict[str, list[str]] = {}
    covered_actions: set[str] = set()
    for graph_ordinal, template in enumerate(templates, 1):
        unmatched_actions = sorted(
            {
                step.action_ref
                for step in template.steps
                if step.action_ref not in selected_action_refs
            }
        )
        if unmatched_actions:
            _fail(
                "PROCEDURE_TEMPLATE_TRIGGER_NOT_SELECTED",
                "Template содержит step action без matching responsibility: "
                f"{', '.join(unmatched_actions)}.",
            )
        graph, action_links, graph_diagnostics = _instantiate_graph(
            requirement_id, graph_ordinal, template, records, kb
        )
        all_diagnostics.extend(graph_diagnostics)
        covered_actions.update(action_links)
        if graph is None:
            continue
        graphs.append(graph)
        for action_ref, step_ids in action_links.items():
            links_by_action.setdefault(action_ref, []).extend(step_ids)

    gaps = sorted(
        {
            record.action_ref
            for record in records
            if record.action_ref is not None
            and kb.actions[record.action_ref].procedure_required
            and record.action_ref not in covered_actions
        }
    )
    all_diagnostics.extend(f"procedure_gap:{action_ref}" for action_ref in gaps)
    linked_records = tuple(
        replace(
            record,
            procedure_step_ids=tuple(links_by_action.get(record.action_ref or "", ())),
        )
        for record in records
    )
    return ProcedureBuildResult(
        tuple(graphs),
        linked_records,
        tuple(sorted(set(all_diagnostics))),
    )


def validate_procedure_graph(
    graph: ProcedureGraph,
    responsibilities: Sequence[ResponsibilityRecord],
    kb: KnowledgeBaseV2,
) -> tuple[ProcedureIssue, ...]:
    """Validate a public graph and any caller-provided responsibility links."""
    issues: list[ProcedureIssue] = []

    def issue(code: str, object_id: str, message_ru: str) -> None:
        issues.append(ProcedureIssue(code, object_id, message_ru))

    graph_match = re.fullmatch(
        rf"{re.escape(graph.requirement_id)}-P([0-9]{{3}})", graph.graph_id
    )
    graph_id_valid = False
    if graph_match is not None and int(graph_match.group(1)) > 0:
        try:
            graph_id_valid = (
                procedure_graph_id(
                    graph.requirement_id, int(graph_match.group(1))
                )
                == graph.graph_id
            )
        except ValueError:
            pass
    if not graph_id_valid:
        issue(
            "PROCEDURE_GRAPH_ID_INVALID",
            graph.graph_id,
            "Graph ID не соответствует stable Task-1 contract.",
        )
    template = kb.procedures.get(graph.template_id)
    if template is None:
        issue(
            "PROCEDURE_TEMPLATE_UNKNOWN",
            graph.graph_id,
            f"Template отсутствует в KB: {graph.template_id}.",
        )
    elif graph_id_valid:
        _validate_template_projection(graph, template, kb, issue)

    step_ids = [step.step_id for step in graph.steps]
    known_steps = set(step_ids)
    if len(step_ids) != len(known_steps):
        issue(
            "PROCEDURE_STEP_DUPLICATE",
            graph.graph_id,
            "Graph содержит duplicate step ID.",
        )
    adjacency: dict[str, set[str]] = {step_id: set() for step_id in known_steps}
    rollback_targets: set[str] = set()
    for step in graph.steps:
        _validate_stable_step_id(step.step_id, graph.graph_id, issue)
        _validate_step(step, kb, issue, graph.template_id)
        if len(step.depends_on) != len(set(step.depends_on)):
            issue(
                "PROCEDURE_REFERENCE_DUPLICATE",
                step.step_id,
                "depends_on содержит duplicate step ID.",
            )
        for dependency in step.depends_on:
            if not dependency.startswith(f"{graph.graph_id}-S"):
                issue(
                    "PROCEDURE_CROSS_GRAPH_REFERENCE",
                    step.step_id,
                    f"Dependency принадлежит другому graph: {dependency}.",
                )
            elif dependency not in known_steps:
                issue(
                    "PROCEDURE_STEP_UNKNOWN",
                    step.step_id,
                    f"Dependency отсутствует в graph: {dependency}.",
                )
            elif dependency in adjacency:
                adjacency[dependency].add(step.step_id)
        rollback = step.rollback_step_id
        if rollback is not None:
            if not rollback.startswith(f"{graph.graph_id}-S"):
                issue(
                    "PROCEDURE_CROSS_GRAPH_REFERENCE",
                    step.step_id,
                    f"Rollback принадлежит другому graph: {rollback}.",
                )
            elif rollback not in known_steps:
                issue(
                    "PROCEDURE_ROLLBACK_UNKNOWN",
                    step.step_id,
                    f"Rollback отсутствует в graph: {rollback}.",
                )
            else:
                target = next(item for item in graph.steps if item.step_id == rollback)
                if target.phase is not LifecyclePhase.ROLLBACK:
                    issue(
                        "PROCEDURE_ROLLBACK_PHASE_INVALID",
                        step.step_id,
                        "rollback_step_id должен указывать на rollback phase.",
                    )
                if step.phase is LifecyclePhase.ROLLBACK:
                    issue(
                        "PROCEDURE_ROLLBACK_LINK_INVALID",
                        step.step_id,
                        "Rollback step не может иметь собственный rollback link.",
                    )
                rollback_targets.add(rollback)
                adjacency[step.step_id].add(rollback)

    for step in graph.steps:
        if step.phase is LifecyclePhase.ROLLBACK and step.step_id not in rollback_targets:
            issue(
                "PROCEDURE_ROLLBACK_LINK_ASYMMETRIC",
                step.step_id,
                "Rollback step не связан с forward step.",
            )
    if _has_cycle(adjacency):
        issue(
            "PROCEDURE_CYCLE",
            graph.graph_id,
            "Procedure graph содержит цикл dependencies/rollback.",
        )

    steps_by_id = {step.step_id: step for step in graph.steps}
    records = tuple(responsibilities)
    responsibility_actions = {
        record.action_ref
        for record in records
        if record.requirement_id == graph.requirement_id
        and record.action_ref is not None
    }
    for step in graph.steps:
        if step.action_ref not in responsibility_actions:
            issue(
                "PROCEDURE_STEP_RESPONSIBILITY_MISSING",
                step.step_id,
                "Для step отсутствует responsibility того же requirement/action.",
            )
    record_ids = {record.record_id for record in records}
    if len(record_ids) != len(records):
        issue(
            "PROCEDURE_RESPONSIBILITY_DUPLICATE",
            graph.graph_id,
            "Responsibility records содержат duplicate ID.",
        )
    records_by_id = {record.record_id: record for record in records}
    for record in records:
        if record.requirement_id != graph.requirement_id:
            issue(
                "PROCEDURE_REQUIREMENT_MISMATCH",
                record.record_id,
                "Responsibility относится к другому requirement.",
            )
        for related_id in record.related_record_ids:
            related = records_by_id.get(related_id)
            if related is not None and record.record_id not in related.related_record_ids:
                issue(
                    "PROCEDURE_RESPONSIBILITY_LINK_ASYMMETRIC",
                    record.record_id,
                    f"Related responsibility link несимметричен: {related_id}.",
                )
        if len(record.procedure_step_ids) != len(set(record.procedure_step_ids)):
            issue(
                "PROCEDURE_REFERENCE_DUPLICATE",
                record.record_id,
                "Responsibility содержит duplicate procedure step ID.",
            )
        for step_id in record.procedure_step_ids:
            if not step_id.startswith(f"{graph.graph_id}-S"):
                issue(
                    "PROCEDURE_CROSS_GRAPH_REFERENCE",
                    record.record_id,
                    f"Responsibility ссылается на другой graph: {step_id}.",
                )
                continue
            step = steps_by_id.get(step_id)
            if step is None:
                issue(
                    "PROCEDURE_STEP_UNKNOWN",
                    record.record_id,
                    f"Responsibility ссылается на отсутствующий step: {step_id}.",
                )
            elif record.action_ref != step.action_ref:
                issue(
                    "PROCEDURE_RESPONSIBILITY_ACTION_MISMATCH",
                    record.record_id,
                    "Procedure step связан с responsibility другого action.",
                )
    return tuple(
        sorted(
            set(issues),
            key=lambda item: (item.code, item.object_id, item.message_ru),
        )
    )


def _instantiate_graph(
    requirement_id: str,
    graph_ordinal: int,
    template: ProcedureTemplateRecord,
    responsibilities: tuple[ResponsibilityRecord, ...],
    kb: KnowledgeBaseV2,
) -> tuple[
    ProcedureGraph | None,
    dict[str, list[str]],
    tuple[str, ...],
]:
    graph_id = procedure_graph_id(requirement_id, graph_ordinal)
    step_ids = {
        step.local_step_id: procedure_step_id(graph_id, ordinal)
        for ordinal, step in enumerate(template.steps, 1)
    }
    retained: list[ProcedureTemplateStepRecord] = []
    omitted_rollback_ids: set[str] = set()
    diagnostics: set[str] = set()
    for template_step in template.steps:
        provisional = _procedure_step(template_step, step_ids, set())
        local_issues: list[ProcedureIssue] = []
        _validate_step(
            provisional,
            kb,
            lambda code, object_id, message: local_issues.append(
                ProcedureIssue(code, object_id, message)
            ),
            template.template_id,
        )
        if template_step.phase is LifecyclePhase.ROLLBACK:
            evidence_issues = {
                item.code for item in local_issues if item.code in _ROLLBACK_EVIDENCE_CODES
            }
            other_issues = [
                item for item in local_issues if item.code not in _ROLLBACK_EVIDENCE_CODES
            ]
            if other_issues:
                _fail_issue(other_issues[0])
            if evidence_issues:
                omitted_rollback_ids.add(template_step.local_step_id)
                diagnostics.add("rollback_unverified")
                continue
        elif local_issues:
            _fail_issue(local_issues[0])
        retained.append(template_step)

    retained_ids = {step.local_step_id for step in retained}
    steps: list[ProcedureStep] = []
    for template_step in retained:
        if any(item in omitted_rollback_ids for item in template_step.depends_on):
            _fail(
                "PROCEDURE_STEP_UNKNOWN",
                "Non-rollback step зависит от неподтверждённого rollback step.",
            )
        steps.append(_procedure_step(template_step, step_ids, omitted_rollback_ids))

    reversible_actions = {
        step.action_ref
        for step in retained
        if step.phase
        in {
            LifecyclePhase.DEPLOY,
            LifecyclePhase.RUNTIME,
            LifecyclePhase.RECONFIGURE,
            LifecyclePhase.UPGRADE,
            LifecyclePhase.MIGRATE,
            LifecyclePhase.RECOVER,
        }
        and _action_is_reversible(step.action_ref, kb)
    }
    verified_rollback_actions = {
        step.action_ref
        for step in retained
        if step.phase is LifecyclePhase.ROLLBACK
    }
    if reversible_actions - verified_rollback_actions:
        diagnostics.add("rollback_unverified")

    if not steps:
        return None, {}, tuple(sorted(diagnostics))
    order = _topological_local_order(retained, retained_ids)
    by_local = {
        template_step.local_step_id: step
        for template_step, step in zip(retained, steps, strict=True)
    }
    ordered_steps = tuple(by_local[local_id] for local_id in order)
    graph = ProcedureGraph(
        graph_id,
        requirement_id,
        template.template_id,
        ordered_steps,
        tuple(sorted(diagnostics)),
    )
    validation = validate_procedure_graph(graph, responsibilities, kb)
    if validation:
        _fail_issue(validation[0])
    action_links: dict[str, list[str]] = {}
    for step in ordered_steps:
        action_links.setdefault(step.action_ref, []).append(step.step_id)
    return graph, action_links, graph.diagnostics


def _procedure_step(
    template_step: ProcedureTemplateStepRecord,
    step_ids: Mapping[str, str],
    omitted_rollback_ids: set[str],
) -> ProcedureStep:
    rollback = template_step.rollback_step_local_id
    return ProcedureStep(
        step_ids[template_step.local_step_id],
        template_step.phase,
        template_step.contour,
        template_step.executor_ref,
        template_step.target_ref,
        template_step.action_ref,
        template_step.preconditions,
        template_step.success_criteria,
        template_step.evidence_ids,
        tuple(step_ids[item] for item in template_step.depends_on),
        None
        if rollback is None or rollback in omitted_rollback_ids
        else step_ids[rollback],
    )


def _validate_template_projection(
    graph: ProcedureGraph,
    template: ProcedureTemplateRecord,
    kb: KnowledgeBaseV2,
    issue: Callable[[str, str, str], None],
) -> None:
    """Require an exact, non-synthetic projection of the selected template."""
    step_ids = {
        step.local_step_id: procedure_step_id(graph.graph_id, ordinal)
        for ordinal, step in enumerate(template.steps, 1)
    }
    omitted_rollback_ids: set[str] = set()
    diagnostics: set[str] = set()
    for template_step in template.steps:
        if template_step.phase is not LifecyclePhase.ROLLBACK:
            continue
        provisional = _procedure_step(template_step, step_ids, set())
        local_issues: list[ProcedureIssue] = []
        _validate_step(
            provisional,
            kb,
            lambda code, object_id, message: local_issues.append(
                ProcedureIssue(code, object_id, message)
            ),
            template.template_id,
        )
        if any(item.code in _ROLLBACK_EVIDENCE_CODES for item in local_issues):
            omitted_rollback_ids.add(template_step.local_step_id)
            diagnostics.add("rollback_unverified")

    retained = tuple(
        step
        for step in template.steps
        if step.local_step_id not in omitted_rollback_ids
    )
    retained_ids = {step.local_step_id for step in retained}
    expected_by_local = {
        step.local_step_id: _procedure_step(
            step, step_ids, omitted_rollback_ids
        )
        for step in retained
    }
    expected_order = _topological_local_order(retained, retained_ids)
    expected_steps = tuple(expected_by_local[item] for item in expected_order)
    expected_by_id = {step.step_id: step for step in expected_steps}
    actual_by_id = {step.step_id: step for step in graph.steps}
    for step_id in sorted(expected_by_id.keys() - actual_by_id.keys()):
        issue(
            "PROCEDURE_STEP_MISSING",
            graph.graph_id,
            f"Trusted template step отсутствует в graph: {step_id}.",
        )
    for step_id in sorted(actual_by_id.keys() - expected_by_id.keys()):
        issue(
            "PROCEDURE_STEP_NOT_IN_TEMPLATE",
            step_id,
            "Graph содержит step, отсутствующий в trusted template.",
        )
    for step_id in sorted(expected_by_id.keys() & actual_by_id.keys()):
        if actual_by_id[step_id] != expected_by_id[step_id]:
            issue(
                "PROCEDURE_STEP_TEMPLATE_MISMATCH",
                step_id,
                "Graph step не является exact projection trusted template.",
            )
    if tuple(step.step_id for step in graph.steps) != tuple(
        step.step_id for step in expected_steps
    ):
        issue(
            "PROCEDURE_TOPOLOGICAL_ORDER_INVALID",
            graph.graph_id,
            "Graph steps нарушают stable topological order template.",
        )

    reversible_actions = {
        step.action_ref
        for step in retained
        if step.phase
        in {
            LifecyclePhase.DEPLOY,
            LifecyclePhase.RUNTIME,
            LifecyclePhase.RECONFIGURE,
            LifecyclePhase.UPGRADE,
            LifecyclePhase.MIGRATE,
            LifecyclePhase.RECOVER,
        }
        and _action_is_reversible(step.action_ref, kb)
    }
    rollback_actions = {
        step.action_ref
        for step in retained
        if step.phase is LifecyclePhase.ROLLBACK
    }
    if reversible_actions - rollback_actions:
        diagnostics.add("rollback_unverified")
    if graph.diagnostics != tuple(sorted(diagnostics)):
        issue(
            "PROCEDURE_DIAGNOSTIC_MISMATCH",
            graph.graph_id,
            "Graph diagnostics не соответствуют trusted template evidence.",
        )


def _validate_template_structure(template: ProcedureTemplateRecord) -> None:
    local_ids = [step.local_step_id for step in template.steps]
    if len(local_ids) != len(set(local_ids)):
        _fail(
            "PROCEDURE_STEP_DUPLICATE",
            f"Template {template.template_id} содержит duplicate local step ID.",
        )
    known = set(local_ids)
    adjacency: dict[str, set[str]] = {item: set() for item in known}
    rollback_targets: set[str] = set()
    by_id = {step.local_step_id: step for step in template.steps}
    for step in template.steps:
        if not step.preconditions or any(
            type(item) is not str or not item.strip() for item in step.preconditions
        ):
            _fail(
                "PROCEDURE_PRECONDITION_BLANK",
                f"Step требует непустые preconditions: {step.local_step_id}.",
            )
        if not step.success_criteria or any(
            type(item) is not str or not item.strip()
            for item in step.success_criteria
        ):
            _fail(
                "PROCEDURE_SUCCESS_CRITERIA_BLANK",
                f"Step требует непустые success criteria: {step.local_step_id}.",
            )
        if step.action_ref not in template.action_refs:
            _fail(
                "PROCEDURE_ACTION_MISMATCH",
                f"Step action отсутствует в template.action_refs: {step.action_ref}.",
            )
        for dependency in step.depends_on:
            if dependency not in known:
                _fail(
                    "PROCEDURE_STEP_UNKNOWN",
                    f"Dependency не принадлежит template: {dependency}.",
                )
            adjacency[dependency].add(step.local_step_id)
        rollback = step.rollback_step_local_id
        if rollback is not None:
            if rollback not in known:
                _fail(
                    "PROCEDURE_ROLLBACK_UNKNOWN",
                    f"Rollback step не принадлежит template: {rollback}.",
                )
            if by_id[rollback].phase is not LifecyclePhase.ROLLBACK:
                _fail(
                    "PROCEDURE_ROLLBACK_PHASE_INVALID",
                    "rollback_step_local_id должен указывать на rollback phase.",
                )
            rollback_targets.add(rollback)
            adjacency[step.local_step_id].add(rollback)
    for step in template.steps:
        if (
            step.phase is LifecyclePhase.ROLLBACK
            and step.local_step_id not in rollback_targets
        ):
            _fail(
                "PROCEDURE_ROLLBACK_LINK_ASYMMETRIC",
                f"Rollback step не связан с forward step: {step.local_step_id}.",
            )
    if _has_cycle(adjacency):
        _fail(
            "PROCEDURE_CYCLE",
            f"Template {template.template_id} содержит цикл dependencies/rollback.",
        )


def _validate_step(
    step: ProcedureStep,
    kb: KnowledgeBaseV2,
    issue: Callable[[str, str, str], None],
    template_id: str,
) -> None:
    def add(code: str, message: str) -> None:
        issue(code, step.step_id, message)

    action = kb.actions.get(step.action_ref)
    actor = kb.actors.get(step.executor_ref)
    target = kb.targets.get(step.target_ref)
    if action is None:
        add(
            "PROCEDURE_ACTION_UNKNOWN",
            f"Step ссылается на неизвестный action: {step.action_ref}.",
        )
    if actor is None:
        add(
            "PROCEDURE_ACTOR_UNKNOWN",
            f"Step ссылается на неизвестный actor: {step.executor_ref}.",
        )
    elif action is not None and actor.component_ref != action.component_ref:
        add(
            "PROCEDURE_ACTOR_MISMATCH",
            "Actor и action принадлежат разным component.",
        )
    if target is None:
        add(
            "PROCEDURE_TARGET_UNKNOWN",
            f"Step ссылается на неизвестный target: {step.target_ref}.",
        )
    elif action is not None and action.target_ref != step.target_ref:
        add(
            "PROCEDURE_TARGET_MISMATCH",
            "Step target не совпадает с ActionRecord.target_ref.",
        )
    if action is not None and action.contour is not step.contour:
        add(
            "PROCEDURE_CONTOUR_MISMATCH",
            "Step contour не совпадает с ActionRecord.contour.",
        )
    if not step.preconditions or any(not item.strip() for item in step.preconditions):
        add(
            "PROCEDURE_PRECONDITION_BLANK",
            "Step требует непустые preconditions.",
        )
    if not step.success_criteria or any(
        not item.strip() for item in step.success_criteria
    ):
        add(
            "PROCEDURE_SUCCESS_CRITERIA_BLANK",
            "Step требует непустые success criteria.",
        )

    applicable_direct = False
    for evidence_id in step.evidence_ids:
        evidence = kb.evidence.get(evidence_id)
        if evidence is None:
            add(
                "PROCEDURE_EVIDENCE_UNKNOWN",
                f"Step ссылается на неизвестный evidence: {evidence_id}.",
            )
            continue
        source = kb.sources.get(evidence.source_id)
        scoped = (
            action is not None
            and step.contour in evidence.applicable_contours
            and evidence.version_constraint
            in {
                action.version_scope.version_constraint,
                action.version_scope.source_release,
                action.version_scope.target_release,
            }
        )
        action_evidence = (
            scoped
            and step.action_ref in evidence.supports_entity_refs
            and source is not None
            and source.provenance == "official"
            and source.source_type != "project_policy"
        )
        workflow_evidence = (
            scoped
            and template_id in evidence.supports_entity_refs
            and source is not None
            and (
                source.provenance == "project_policy"
                or source.source_type == "project_policy"
            )
        )
        if not action_evidence and not workflow_evidence:
            add(
                "PROCEDURE_EVIDENCE_MISMATCH",
                f"Evidence не подтверждает action/contour/version: {evidence_id}.",
            )
        elif action_evidence and (
            evidence.polarity is EvidencePolarity.POSITIVE
            and evidence.strength is EvidenceStrength.DIRECT
        ):
            applicable_direct = True
    if step.phase in _EVIDENCE_REQUIRED_PHASES and not applicable_direct:
        add(
            "PROCEDURE_DIRECT_EVIDENCE_REQUIRED",
            "Executable/verify/rollback step требует applicable direct evidence.",
        )


def _validate_stable_step_id(
    step_id: str,
    graph_id: str,
    issue: Callable[[str, str, str], None],
) -> None:
    match = re.fullmatch(rf"{re.escape(graph_id)}-S([0-9]{{3}})", step_id)
    valid = False
    if match is not None and int(match.group(1)) > 0:
        try:
            valid = procedure_step_id(graph_id, int(match.group(1))) == step_id
        except ValueError:
            pass
    if not valid:
        issue(
            "PROCEDURE_STEP_ID_INVALID",
            step_id,
            "Step ID не соответствует stable Task-1 contract.",
        )


def _topological_local_order(
    steps: Sequence[ProcedureTemplateStepRecord], known: set[str]
) -> tuple[str, ...]:
    source_index = {step.local_step_id: index for index, step in enumerate(steps)}
    outgoing: dict[str, set[str]] = {item: set() for item in known}
    indegree = {item: 0 for item in known}

    def edge(source: str, target: str) -> None:
        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1

    for step in steps:
        for dependency in step.depends_on:
            edge(dependency, step.local_step_id)
        rollback = step.rollback_step_local_id
        if rollback is not None and rollback in known:
            edge(step.local_step_id, rollback)
    ready = sorted(
        (item for item, count in indegree.items() if count == 0),
        key=source_index.__getitem__,
    )
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for target in sorted(outgoing[current], key=source_index.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=source_index.__getitem__)
    if len(result) != len(known):
        _fail("PROCEDURE_CYCLE", "Procedure template содержит цикл.")
    return tuple(result)


def _action_is_reversible(action_ref: str, kb: KnowledgeBaseV2) -> bool:
    action = kb.actions[action_ref]
    return any(
        effect is not None and effect.reversible
        for effect in (kb.effects.get(item) for item in action.effect_refs)
    )


def _has_cycle(adjacency: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(neighbor) for neighbor in adjacency.get(node, ())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in adjacency if node not in visited)


def _fail_issue(issue: ProcedureIssue) -> None:
    _fail(issue.code, issue.message_ru)


def _fail(code: str, message_ru: str) -> None:
    raise ProcedureError(code, f"{code}: {message_ru}")
