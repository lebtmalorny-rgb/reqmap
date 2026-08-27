"""Deterministic deep aggregation and runtime graph-closure validation."""

from __future__ import annotations

from dataclasses import replace

from reqmap.deep_mapping import DeepMappingOutcome
from reqmap.deep_models import (
    DeepAtomResult,
    DeepGroupResult,
    DeepRequirementResult,
    DeepRunResult,
    LifecyclePhase,
    ProcedureGraph,
    ProcedureStep,
    ResponsibilityRecord,
    validate_version_scope,
)
from reqmap.ids import (
    atom_id,
    generated_requirement_id,
    procedure_graph_id,
    procedure_step_id,
    responsibility_id,
)
from reqmap.models import AnalysisState, AtomicClaim, Requirement, SupportStatus
from reqmap.procedure import ProcedureBuildResult


PARTIAL_DIAGNOSTIC_PREFIXES = (
    "evidence_conflict",
    "responsibility_ambiguous",
    "procedure_gap",
    "rollback_unverified",
)

_FAILED_STATE_PRECEDENCE = (
    AnalysisState.VALIDATION_FAILED,
    AnalysisState.MODEL_FAILED,
    AnalysisState.SKIPPED,
)
_CONFIRMED_RESPONSIBILITY_STATUSES = frozenset(
    {
        SupportStatus.SUPPORTED,
        SupportStatus.PARTIAL,
        SupportStatus.NOT_SUPPORTED,
    }
)


def aggregate_deep_requirement(
    requirement: Requirement,
    mapping_outcomes: tuple[DeepMappingOutcome, ...],
    procedure_result: ProcedureBuildResult | None = None,
) -> tuple[DeepRequirementResult, tuple[ResponsibilityRecord, ...]]:
    """Aggregate ordered atom mappings and an optional explicit procedure build."""
    outcomes = _validate_mapping_outcomes(requirement, mapping_outcomes)
    mapping_records = tuple(
        record for outcome in outcomes for record in outcome.responsibility_records
    )
    graphs: tuple[ProcedureGraph, ...] = ()
    procedure_diagnostics: tuple[str, ...] = ()
    records = mapping_records
    if procedure_result is not None:
        if type(procedure_result) is not ProcedureBuildResult:
            raise ValueError("procedure_result must be canonical ProcedureBuildResult")
        records = _validate_procedure_projection(
            requirement, mapping_records, procedure_result
        )
        graphs = procedure_result.graphs
        procedure_diagnostics = procedure_result.diagnostics

    atom_results = tuple(outcome.atom_result for outcome in outcomes)
    diagnostics = _ordered_unique(
        message
        for atom_result in atom_results
        for message in atom_result.diagnostics
    )
    diagnostics = _ordered_unique((*diagnostics, *procedure_diagnostics))
    diagnostics = _ordered_unique(
        (
            *diagnostics,
            *(message for graph in graphs for message in graph.diagnostics),
        )
    )
    return (
        DeepRequirementResult(
            requirement,
            _aggregate_analysis_state(
                tuple(item.analysis_state for item in atom_results)
            ),
            _aggregate_atom_status(atom_results),
            atom_results,
            tuple(record.record_id for record in records),
            tuple(graph.graph_id for graph in graphs),
            diagnostics,
        ),
        records,
    )


def aggregate_deep_groups(
    requirements: tuple[DeepRequirementResult, ...],
    responsibility_records: tuple[ResponsibilityRecord, ...],
) -> tuple[DeepGroupResult, ...]:
    """Build source-stable group summaries from normalized responsibility IDs."""
    if type(requirements) is not tuple or any(
        type(item) is not DeepRequirementResult for item in requirements
    ):
        raise ValueError("requirements must be a tuple of DeepRequirementResult")
    records_by_id = _records_by_id(responsibility_records)
    used_ids = tuple(
        record_id
        for result in requirements
        for record_id in result.responsibility_ids
    )
    if len(used_ids) != len(set(used_ids)) or set(used_ids) != set(records_by_id):
        raise ValueError("responsibility records must equal requirement flattening")

    grouped: dict[str, list[DeepRequirementResult]] = {}
    for result in requirements:
        for record_id in result.responsibility_ids:
            record = records_by_id[record_id]
            if record.requirement_id != result.requirement.requirement_id:
                raise ValueError("responsibility belongs to another requirement")
        for group_id in dict.fromkeys(result.requirement.group_ids):
            grouped.setdefault(group_id, []).append(result)
    return tuple(
        _aggregate_group(group_id, tuple(children), records_by_id)
        for group_id, children in grouped.items()
    )


def deep_run_status(
    results: tuple[DeepRequirementResult, ...], preflight_ok: bool
) -> str:
    """Compute operational run status without changing support precedence."""
    if type(preflight_ok) is not bool:
        raise ValueError("preflight_ok must be bool")
    if type(results) is not tuple or any(
        type(item) is not DeepRequirementResult for item in results
    ):
        raise ValueError("results must be a tuple of DeepRequirementResult")
    if not preflight_ok or not results:
        return "FAILED"
    completed = sum(
        item.analysis_state is AnalysisState.COMPLETED for item in results
    )
    if completed != len(results):
        return "PARTIAL" if completed else "FAILED"
    if any(
        diagnostic.startswith(PARTIAL_DIAGNOSTIC_PREFIXES)
        for item in results
        for diagnostic in item.diagnostics
    ):
        return "PARTIAL"
    return "SUCCESS"


def validate_deep_graph(run: DeepRunResult) -> None:
    """Validate closure of only the canonical runtime records carried by ``run``."""
    if type(run) is not DeepRunResult:
        raise ValueError("run must be canonical DeepRunResult")
    if run.run_status not in {"SUCCESS", "PARTIAL", "FAILED"}:
        raise ValueError("run_status must be SUCCESS, PARTIAL or FAILED")
    for ordinal, result in enumerate(run.requirements, 1):
        source = result.requirement
        if (
            source.ordinal != ordinal
            or source.requirement_id != generated_requirement_id(ordinal)
        ):
            raise ValueError(
                "requirement stable ID and ordinal must match source order"
            )

    records_by_id = _records_by_id(run.responsibility_records)
    graphs_by_id = _graphs_by_id(run.procedure_graphs)
    flattened_record_ids: list[str] = []
    flattened_graph_ids: list[str] = []
    atom_by_id: dict[str, DeepAtomResult] = {}
    for result in run.requirements:
        _validate_requirement_result(result, records_by_id, graphs_by_id)
        flattened_record_ids.extend(result.responsibility_ids)
        flattened_graph_ids.extend(result.procedure_graph_ids)
        for atom_result in result.atom_results:
            if atom_result.atom.atom_id in atom_by_id:
                raise ValueError("atomic claim IDs must be unique")
            atom_by_id[atom_result.atom.atom_id] = atom_result

    if tuple(flattened_record_ids) != tuple(records_by_id):
        raise ValueError("responsibility record flattening does not match requirements")
    if tuple(flattened_graph_ids) != tuple(graphs_by_id):
        raise ValueError("procedure graph flattening does not match requirements")
    if run.run_status != deep_run_status(run.requirements, preflight_ok=True):
        raise ValueError("run_status does not match operational recomputation")
    _validate_record_relations(run.responsibility_records, records_by_id, atom_by_id)
    steps_by_id = _validate_graphs(run.procedure_graphs, records_by_id)
    _validate_responsibility_step_links(run.responsibility_records, steps_by_id)
    _validate_evidence_closure(run, steps_by_id)

    expected_groups = aggregate_deep_groups(
        run.requirements, run.responsibility_records
    )
    if run.groups != expected_groups:
        raise ValueError("group records do not match deterministic recomputation")


def _validate_mapping_outcomes(
    requirement: Requirement, outcomes: tuple[DeepMappingOutcome, ...]
) -> tuple[DeepMappingOutcome, ...]:
    if type(requirement) is not Requirement:
        raise ValueError("requirement must be canonical Requirement")
    if type(outcomes) is not tuple or not outcomes or any(
        type(item) is not DeepMappingOutcome for item in outcomes
    ):
        raise ValueError("mapping_outcomes must be a non-empty tuple")
    for ordinal, outcome in enumerate(outcomes, 1):
        _validate_atom_result(requirement, outcome.atom_result, ordinal)
        _validate_outcome_records(outcome)
    return outcomes


def _validate_atom_result(
    requirement: Requirement, result: DeepAtomResult, ordinal: int
) -> None:
    if type(result) is not DeepAtomResult or type(result.atom) is not AtomicClaim:
        raise ValueError("mapping outcome must contain canonical DeepAtomResult")
    claim = result.atom
    if claim.requirement_id != requirement.requirement_id:
        raise ValueError("atomic claim belongs to another requirement")
    if claim.ordinal != ordinal or claim.atom_id != atom_id(
        requirement.requirement_id, ordinal
    ):
        raise ValueError("atomic claim order or stable ID is invalid")
    if not claim.source_quote.strip() or claim.source_quote not in requirement.text:
        raise ValueError("source_quote must be an exact non-empty requirement substring")
    if not claim.text.strip():
        raise ValueError("atomic claim text must be non-empty")
    if result.analysis_state is not AnalysisState.COMPLETED:
        if (
            result.support_status is not None
            or result.responsibility_ids
            or result.supported_aspects
            or result.unconfirmed_aspects
        ):
            raise ValueError("incomplete atom cannot contain subject results")
    elif result.support_status is None:
        raise ValueError("completed atom must contain support_status")


def _validate_outcome_records(outcome: DeepMappingOutcome) -> None:
    result = outcome.atom_result
    records = outcome.responsibility_records
    if type(records) is not tuple or any(
        type(item) is not ResponsibilityRecord for item in records
    ):
        raise ValueError("responsibility_records must be a canonical tuple")
    if tuple(item.record_id for item in records) != result.responsibility_ids:
        raise ValueError("atom responsibility IDs must equal record flattening")
    for ordinal, record in enumerate(records, 1):
        if (
            record.requirement_id != result.atom.requirement_id
            or record.atomic_claim_id != result.atom.atom_id
        ):
            raise ValueError("responsibility traceability does not match atom")
        expected = responsibility_id(result.atom.atom_id, ordinal)
        if record.record_id != expected:
            raise ValueError("responsibility ID is not canonical or source-stable")
        validate_version_scope(record.version_scope, record.lifecycle_phase)
    if result.analysis_state is not AnalysisState.COMPLETED:
        if records or outcome.procedure_template_ids:
            raise ValueError("incomplete mapping outcome cannot carry deep records")
        return
    expected_status = _aggregate_responsibility_status(records)
    if result.support_status is not expected_status:
        raise ValueError("atom support_status disagrees with responsibilities")


def _validate_procedure_projection(
    requirement: Requirement,
    mapping_records: tuple[ResponsibilityRecord, ...],
    result: ProcedureBuildResult,
) -> tuple[ResponsibilityRecord, ...]:
    if type(result.graphs) is not tuple or any(
        type(item) is not ProcedureGraph for item in result.graphs
    ):
        raise ValueError("procedure graphs must be a canonical tuple")
    if type(result.responsibility_records) is not tuple or len(
        result.responsibility_records
    ) != len(mapping_records):
        raise ValueError("procedure responsibility projection is incomplete")
    for original, linked in zip(
        mapping_records, result.responsibility_records, strict=True
    ):
        if type(linked) is not ResponsibilityRecord:
            raise ValueError("procedure responsibility projection is not canonical")
        if replace(linked, procedure_step_ids=original.procedure_step_ids) != original:
            raise ValueError("procedure result changed responsibility content")
    for graph in result.graphs:
        if graph.requirement_id != requirement.requirement_id:
            raise ValueError("procedure graph belongs to another requirement")
    return result.responsibility_records


def _validate_requirement_result(
    result: DeepRequirementResult,
    records_by_id: dict[str, ResponsibilityRecord],
    graphs_by_id: dict[str, ProcedureGraph],
) -> None:
    if type(result) is not DeepRequirementResult:
        raise ValueError("requirements must contain DeepRequirementResult")
    source = result.requirement
    if type(source) is not Requirement:
        raise ValueError("deep result must contain canonical Requirement")
    if not result.atom_results:
        if (
            result.analysis_state is AnalysisState.COMPLETED
            or result.support_status is not None
            or result.responsibility_ids
            or result.procedure_graph_ids
        ):
            raise ValueError("empty requirement result must be incomplete")
        return
    for ordinal, atom_result in enumerate(result.atom_results, 1):
        _validate_atom_result(source, atom_result, ordinal)
        atom_records = tuple(
            records_by_id[record_id]
            for record_id in atom_result.responsibility_ids
            if record_id in records_by_id
        )
        if len(atom_records) == len(atom_result.responsibility_ids):
            expected_atom_status = _aggregate_responsibility_status(atom_records)
            if (
                atom_result.analysis_state is AnalysisState.COMPLETED
                and atom_result.support_status is not expected_atom_status
            ):
                raise ValueError(
                    "atom support_status disagrees with responsibility records"
                )
    expected_record_ids = tuple(
        record_id
        for atom_result in result.atom_results
        for record_id in atom_result.responsibility_ids
    )
    if result.responsibility_ids != expected_record_ids:
        raise ValueError("requirement responsibility flattening is inconsistent")
    if any(record_id not in records_by_id for record_id in expected_record_ids):
        raise ValueError("requirement references unknown responsibility")
    if any(graph_id not in graphs_by_id for graph_id in result.procedure_graph_ids):
        raise ValueError("requirement references unknown procedure graph")
    if any(
        graphs_by_id[graph_id].requirement_id != source.requirement_id
        for graph_id in result.procedure_graph_ids
    ):
        raise ValueError("procedure graph belongs to another requirement")
    if len(result.diagnostics) != len(set(result.diagnostics)):
        raise ValueError("requirement diagnostics contain duplicates")
    required_diagnostics = _ordered_unique(
        message
        for atom_result in result.atom_results
        for message in atom_result.diagnostics
    )
    required_diagnostics = _ordered_unique(
        (
            *required_diagnostics,
            *(
                message
                for graph_id in result.procedure_graph_ids
                for message in graphs_by_id[graph_id].diagnostics
            ),
        )
    )
    if any(message not in result.diagnostics for message in required_diagnostics):
        raise ValueError("requirement diagnostic projection is incomplete")
    positions = tuple(result.diagnostics.index(item) for item in required_diagnostics)
    if positions != tuple(sorted(positions)):
        raise ValueError("requirement diagnostic projection order is not canonical")
    if result.analysis_state is not _aggregate_analysis_state(
        tuple(item.analysis_state for item in result.atom_results)
    ):
        raise ValueError("requirement analysis_state disagrees with atoms")
    if result.support_status is not _aggregate_atom_status(result.atom_results):
        raise ValueError("requirement support_status disagrees with atoms")


def _records_by_id(
    records: tuple[ResponsibilityRecord, ...]
) -> dict[str, ResponsibilityRecord]:
    if type(records) is not tuple or any(
        type(item) is not ResponsibilityRecord for item in records
    ):
        raise ValueError("responsibility_records must be a canonical tuple")
    by_id = {item.record_id: item for item in records}
    if len(by_id) != len(records):
        raise ValueError("responsibility IDs must be unique")
    return by_id


def _graphs_by_id(graphs: tuple[ProcedureGraph, ...]) -> dict[str, ProcedureGraph]:
    if type(graphs) is not tuple or any(type(item) is not ProcedureGraph for item in graphs):
        raise ValueError("procedure_graphs must be a canonical tuple")
    by_id = {item.graph_id: item for item in graphs}
    if len(by_id) != len(graphs):
        raise ValueError("procedure graph IDs must be unique")
    return by_id


def _validate_record_relations(
    records: tuple[ResponsibilityRecord, ...],
    records_by_id: dict[str, ResponsibilityRecord],
    atom_by_id: dict[str, DeepAtomResult],
) -> None:
    ordinal_by_atom: dict[str, int] = {}
    for record in records:
        atom_result = atom_by_id.get(record.atomic_claim_id)
        if atom_result is None or atom_result.atom.requirement_id != record.requirement_id:
            raise ValueError("responsibility traceability references unknown atom")
        ordinal = ordinal_by_atom.get(record.atomic_claim_id, 0) + 1
        ordinal_by_atom[record.atomic_claim_id] = ordinal
        if record.record_id != responsibility_id(record.atomic_claim_id, ordinal):
            raise ValueError("responsibility ID is not canonical or source-stable")
        validate_version_scope(record.version_scope, record.lifecycle_phase)
        if len(record.evidence_ids) != len(set(record.evidence_ids)):
            raise ValueError("responsibility evidence IDs contain duplicates")
        if (record.action_ref is None) != (record.effect_ref is None):
            raise ValueError("action/effect usage is orphaned")
        if record.record_id in record.related_record_ids:
            raise ValueError("related responsibility cannot reference itself")
        if len(record.related_record_ids) != len(set(record.related_record_ids)):
            raise ValueError("related responsibility IDs must be unique")
        for related_id in record.related_record_ids:
            related = records_by_id.get(related_id)
            if related is None or record.record_id not in related.related_record_ids:
                raise ValueError("related responsibility links must be symmetric")
            if (
                related.requirement_id != record.requirement_id
                or related.atomic_claim_id != record.atomic_claim_id
            ):
                raise ValueError(
                    "related responsibilities must belong to the same atomic claim"
                )


def _validate_graphs(
    graphs: tuple[ProcedureGraph, ...],
    records_by_id: dict[str, ResponsibilityRecord],
) -> dict[str, tuple[ProcedureGraph, ProcedureStep]]:
    steps_by_id: dict[str, tuple[ProcedureGraph, ProcedureStep]] = {}
    ordinal_by_requirement: dict[str, int] = {}
    for graph in graphs:
        ordinal = ordinal_by_requirement.get(graph.requirement_id, 0) + 1
        ordinal_by_requirement[graph.requirement_id] = ordinal
        if graph.graph_id != procedure_graph_id(graph.requirement_id, ordinal):
            raise ValueError("procedure graph ID is not canonical or source-stable")
        if not graph.steps:
            raise ValueError("procedure graph usage cannot be orphaned from steps")
        local_ids = {step.step_id for step in graph.steps}
        if len(local_ids) != len(graph.steps):
            raise ValueError("procedure step IDs must be unique")
        ordinals = {
            step.step_id: _validate_stable_step_id(step.step_id, graph.graph_id)
            for step in graph.steps
        }
        steps_by_local_id = {step.step_id: step for step in graph.steps}
        position = {step.step_id: index for index, step in enumerate(graph.steps)}
        adjacency: dict[str, set[str]] = {item: set() for item in local_ids}
        rollback_targets: set[str] = set()
        for step in graph.steps:
            if step.step_id in steps_by_id:
                raise ValueError("procedure step IDs must be globally unique")
            steps_by_id[step.step_id] = (graph, step)
            if not step.evidence_ids:
                raise ValueError("procedure step must cite evidence")
            if len(step.evidence_ids) != len(set(step.evidence_ids)):
                raise ValueError("procedure step evidence IDs contain duplicates")
            if len(step.depends_on) != len(set(step.depends_on)):
                raise ValueError("procedure step dependencies must be unique")
            for dependency in step.depends_on:
                if dependency not in local_ids:
                    raise ValueError("procedure graph contains an unknown step reference")
                if position[dependency] >= position[step.step_id]:
                    raise ValueError("procedure step tuple is not topological")
                adjacency[dependency].add(step.step_id)
            if step.rollback_step_id is not None:
                if step.phase is LifecyclePhase.ROLLBACK:
                    raise ValueError("rollback step cannot have a rollback link")
                if step.rollback_step_id not in local_ids:
                    raise ValueError("procedure graph contains an unknown rollback step")
                if (
                    steps_by_local_id[step.rollback_step_id].phase
                    is not LifecyclePhase.ROLLBACK
                ):
                    raise ValueError("rollback target must have rollback phase")
                if position[step.rollback_step_id] <= position[step.step_id]:
                    raise ValueError("procedure rollback tuple is not topological")
                rollback_targets.add(step.rollback_step_id)
                adjacency[step.step_id].add(step.rollback_step_id)
        if any(
            step.phase is LifecyclePhase.ROLLBACK
            and step.step_id not in rollback_targets
            for step in graph.steps
        ):
            raise ValueError("rollback step is orphaned from a forward link")
        if _has_cycle(adjacency):
            raise ValueError("procedure graph contains a cycle")
        if tuple(step.step_id for step in graph.steps) != _canonical_step_order(
            adjacency, ordinals
        ):
            raise ValueError(
                "procedure topological order violates numeric source ordinal tie break"
            )
        if not any(
            record.requirement_id == graph.requirement_id
            and any(record.action_ref == step.action_ref for step in graph.steps)
            for record in records_by_id.values()
        ):
            raise ValueError("procedure graph has no matching responsibility action")
    return steps_by_id


def _validate_responsibility_step_links(
    records: tuple[ResponsibilityRecord, ...],
    steps_by_id: dict[str, tuple[ProcedureGraph, ProcedureStep]],
) -> None:
    linked_steps: set[str] = set()
    contour_linked_steps: set[str] = set()
    for record in records:
        if len(record.procedure_step_ids) != len(set(record.procedure_step_ids)):
            raise ValueError("responsibility procedure step links must be unique")
        for step_id in record.procedure_step_ids:
            entry = steps_by_id.get(step_id)
            if entry is None:
                raise ValueError("responsibility references unknown procedure step")
            graph, step = entry
            if (
                graph.requirement_id != record.requirement_id
                or step.action_ref != record.action_ref
                or step.executor_ref != record.executor_ref
                or step.target_ref != record.target_ref
            ):
                raise ValueError("responsibility-to-step action linkage is inconsistent")
            linked_steps.add(step_id)
            if record.contour is step.contour:
                contour_linked_steps.add(step_id)
    if linked_steps != set(steps_by_id):
        raise ValueError("procedure step has no matching responsibility link")
    if contour_linked_steps != set(steps_by_id):
        raise ValueError(
            "procedure step contour has no matching responsibility contour"
        )


def _validate_evidence_closure(
    run: DeepRunResult,
    steps_by_id: dict[str, tuple[ProcedureGraph, ProcedureStep]],
) -> None:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    if len(evidence_by_id) != len(run.evidence):
        raise ValueError("evidence IDs must be unique")
    cited = _ordered_unique(
        evidence_id
        for record in run.responsibility_records
        for evidence_id in record.evidence_ids
    )
    cited = _ordered_unique(
        (
            *cited,
            *(
                evidence_id
                for graph in run.procedure_graphs
                for step in graph.steps
                for evidence_id in step.evidence_ids
            ),
        )
    )
    if tuple(evidence_by_id) != cited:
        raise ValueError("evidence records must equal cited evidence IDs")
    for record in run.responsibility_records:
        covered_refs: set[str] = set()
        entity_refs = {record.action_ref, record.effect_ref} - {None}
        for evidence_id in record.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if record.contour not in evidence.applicable_contours:
                raise ValueError("evidence contour does not match responsibility")
            if evidence.version_constraint not in {
                record.version_scope.version_constraint,
                record.version_scope.source_release,
                record.version_scope.target_release,
            }:
                raise ValueError("evidence version does not match responsibility")
            if entity_refs and not entity_refs.intersection(evidence.supports_entity_refs):
                raise ValueError("evidence does not cite responsibility action/effect")
            covered_refs.update(entity_refs.intersection(evidence.supports_entity_refs))
        missing_refs = entity_refs - covered_refs
        if record.action_ref in missing_refs:
            raise ValueError(
                "responsibility action reference is not covered by cited evidence"
            )
        if record.effect_ref in missing_refs:
            raise ValueError(
                "responsibility effect reference is not covered by cited evidence"
            )
    for _graph, step in steps_by_id.values():
        for evidence_id in step.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if step.contour not in evidence.applicable_contours:
                raise ValueError("procedure evidence contour does not match step")
            if step.action_ref not in evidence.supports_entity_refs:
                raise ValueError("procedure evidence does not cite step action")


def _aggregate_group(
    group_id: str,
    children: tuple[DeepRequirementResult, ...],
    records_by_id: dict[str, ResponsibilityRecord],
) -> DeepGroupResult:
    responsibility_ids = _ordered_unique(
        record_id for child in children for record_id in child.responsibility_ids
    )
    component_refs = tuple(
        sorted(
            {
                records_by_id[record_id].component_ref
                for record_id in responsibility_ids
                if records_by_id[record_id].support_status
                in _CONFIRMED_RESPONSIBILITY_STATUSES
            }
        )
    )
    return DeepGroupResult(
        group_id,
        tuple(child.requirement.requirement_id for child in children),
        _aggregate_child_statuses(children),
        component_refs,
        responsibility_ids,
        tuple(dict.fromkeys(child.analysis_state for child in children)),
    )


def _aggregate_atom_status(
    atoms: tuple[DeepAtomResult, ...]
) -> SupportStatus | None:
    mandatory = tuple(item for item in atoms if item.atom.mandatory)
    if any(item.support_status is SupportStatus.NOT_SUPPORTED for item in mandatory):
        return SupportStatus.NOT_SUPPORTED
    if any(item.analysis_state is not AnalysisState.COMPLETED for item in mandatory):
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


def _aggregate_responsibility_status(
    records: tuple[ResponsibilityRecord, ...]
) -> SupportStatus:
    if not records or any(
        item.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
        for item in records
    ):
        return SupportStatus.INSUFFICIENT_EVIDENCE
    statuses = {item.support_status for item in records}
    if len(statuses) == 1:
        return records[0].support_status
    return SupportStatus.PARTIAL


def _aggregate_child_statuses(
    children: tuple[DeepRequirementResult, ...]
) -> SupportStatus | None:
    if any(child.support_status is SupportStatus.NOT_SUPPORTED for child in children):
        return SupportStatus.NOT_SUPPORTED
    if any(child.analysis_state is not AnalysisState.COMPLETED for child in children):
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


def _aggregate_analysis_state(states: tuple[AnalysisState, ...]) -> AnalysisState:
    for state in _FAILED_STATE_PRECEDENCE:
        if state in states:
            return state
    return AnalysisState.COMPLETED


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _validate_stable_step_id(step_id: str, graph_id: str) -> int:
    prefix = f"{graph_id}-S"
    suffix = step_id.removeprefix(prefix)
    if (
        not step_id.startswith(prefix)
        or len(suffix) != 3
        or not suffix.isdigit()
        or int(suffix) <= 0
        or procedure_step_id(graph_id, int(suffix)) != step_id
    ):
        raise ValueError("procedure step ID is not canonical for its graph")
    return int(suffix)


def _canonical_step_order(
    adjacency: dict[str, set[str]], ordinals: dict[str, int]
) -> tuple[str, ...]:
    indegree = {step_id: 0 for step_id in adjacency}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    ready = sorted(
        (step_id for step_id, count in indegree.items() if count == 0),
        key=ordinals.__getitem__,
    )
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(adjacency[current], key=ordinals.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=ordinals.__getitem__)
    return tuple(ordered)


def _has_cycle(adjacency: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(item) for item in adjacency[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in adjacency if node not in visited)
