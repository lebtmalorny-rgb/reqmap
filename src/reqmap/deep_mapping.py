"""Strict deep responsibility selection over verified schema-v2 relations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from reqmap.deep_models import (
    DeepAtomResult,
    DeepCandidate,
    DeepRetrievalResult,
    LifecyclePhase,
    ResponsibilityContour,
    ResponsibilityRecord,
    VersionScope,
    validate_version_scope,
)
from reqmap.errors import ModelOutputError, ValidationError
from reqmap.ids import responsibility_id
from reqmap.knowledge_v2 import KnowledgeBaseV2, validate_knowledge_v2
from reqmap.llm import JsonModel
from reqmap.models import (
    AnalysisState,
    AtomicClaim,
    EvidencePolarity,
    EvidenceStrength,
    SupportStatus,
    to_dict,
)
from reqmap.prompts import DEEP_MAPPING_PROMPT


_TOP_KEYS = frozenset(
    {
        "support_status",
        "supported_aspects",
        "unconfirmed_aspects",
        "responsibilities",
        "procedure_template_ids",
    }
)
_RESPONSIBILITY_KEYS = frozenset(
    {
        "contour",
        "component_ref",
        "executor_ref",
        "target_contour",
        "target_ref",
        "action_ref",
        "effect_ref",
        "lifecycle_phase",
        "version_scope",
        "evidence_ids",
        "support_status",
        "related_indexes",
    }
)
_VERSION_KEYS = frozenset(
    {
        "source_release",
        "target_release",
        "kolla_ansible_release",
        "host_profile",
        "version_constraint",
    }
)
_CONTOUR_ORDER = {
    ResponsibilityContour.OPENSTACK_RUNTIME: 0,
    ResponsibilityContour.KOLLA_ANSIBLE: 1,
    ResponsibilityContour.HOST_OS: 2,
}


@dataclass(frozen=True)
class DeepMappingOutcome:
    atom_result: DeepAtomResult
    responsibility_records: tuple[ResponsibilityRecord, ...]
    procedure_template_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ResponsibilitySelection:
    contour: ResponsibilityContour
    component_ref: str
    executor_ref: str
    target_contour: ResponsibilityContour
    target_ref: str
    action_ref: str | None
    effect_ref: str | None
    lifecycle_phase: LifecyclePhase
    version_scope: VersionScope
    evidence_ids: tuple[str, ...]
    claimed_support_status: SupportStatus


class _SelectionError(Exception):
    pass


def map_atom_deep(
    model: JsonModel,
    atom: AtomicClaim,
    retrieval: DeepRetrievalResult,
    kb: KnowledgeBaseV2,
) -> DeepMappingOutcome:
    """Select deep relations with one safe correction, then build records locally."""
    _validate_inputs(atom, retrieval, kb)
    base_payload = _payload(atom, retrieval, kb)
    payload = base_payload
    violations: tuple[str, ...] = ()
    semantic_failure = False
    invalid_response: object = None
    for _attempt in range(2):
        try:
            response = model.complete_json("deep_mapping", DEEP_MAPPING_PROMPT, payload)
            invalid_response = response
            if type(response) is not dict:
                violations = ("Ответ модели должен быть встроенным dict JSON object.",)
                semantic_failure = False
            else:
                raw = cast(dict[str, object], response)
                violations = _shape_violations(raw)
                semantic_failure = False
                if not violations:
                    try:
                        return _build_outcome(atom, raw, retrieval, kb)
                    except _SelectionError as exc:
                        violations = (str(exc),)
                        semantic_failure = True
        except ModelOutputError as exc:
            invalid_response = {"raw_response": exc.raw_response}
            violations = (exc.message_ru,)
            semantic_failure = False
        payload = {
            "original_payload": base_payload,
            "invalid_response": invalid_response,
            "violations_ru": list(violations),
        }
    state = AnalysisState.VALIDATION_FAILED if semantic_failure else AnalysisState.MODEL_FAILED
    return _failed(atom, state, violations)


def validate_responsibility_records(
    records: tuple[ResponsibilityRecord, ...],
    retrieval: DeepRetrievalResult,
    kb: KnowledgeBaseV2,
) -> tuple[ResponsibilityRecord, ...]:
    """Validate canonical records against exact retrieval/KB relations and recompute support."""
    if type(records) is not tuple or any(type(item) is not ResponsibilityRecord for item in records):
        raise ValidationError("RESPONSIBILITY_TYPE", "responsibility records должны быть tuple canonical records.")
    normalized: list[ResponsibilityRecord] = []
    for record in records:
        try:
            candidate = _candidate_for(record, retrieval, kb)
            status, diagnostics = _computed_support(record, candidate, kb)
            normalized.append(
                replace(
                    record,
                    support_status=status,
                    diagnostics=tuple(dict.fromkeys((*record.diagnostics, *diagnostics))),
                )
            )
        except _SelectionError as exc:
            raise ValidationError("DEEP_RESPONSIBILITY_INVALID", str(exc)) from exc
    _validate_record_relations(tuple(normalized))
    try:
        _validate_host_bundles(tuple(normalized), kb)
    except _SelectionError as exc:
        raise ValidationError("DEEP_RESPONSIBILITY_INVALID", str(exc)) from exc
    return tuple(normalized)


def _validate_inputs(atom: AtomicClaim, retrieval: DeepRetrievalResult, kb: KnowledgeBaseV2) -> None:
    if type(atom) is not AtomicClaim:
        raise ValidationError("ATOM_TYPE", "atom должен быть canonical AtomicClaim.")
    if type(kb) is not KnowledgeBaseV2:
        raise ValidationError("KNOWLEDGE_V2_TYPE", "kb должен быть KnowledgeBaseV2.")
    issues = validate_knowledge_v2(kb)
    if issues:
        issue = issues[0]
        raise ValidationError(issue.code, issue.message_ru)
    if type(retrieval) is not DeepRetrievalResult or type(retrieval.normalized_candidates) is not tuple:
        raise ValidationError("DEEP_RETRIEVAL_TYPE", "retrieval должен быть canonical DeepRetrievalResult.")
    for candidate in retrieval.normalized_candidates:
        if type(candidate) is not DeepCandidate:
            raise ValidationError("DEEP_CANDIDATE_TYPE", "normalized candidates должны быть DeepCandidate.")
        try:
            _validate_candidate(candidate, kb)
        except _SelectionError as exc:
            raise ValidationError("DEEP_CANDIDATE_INVALID", str(exc)) from exc


def _validate_candidate(candidate: DeepCandidate, kb: KnowledgeBaseV2) -> None:
    capability = kb.capabilities.get(candidate.capability_ref)
    if capability is None or capability.component_ref != candidate.component_ref:
        raise _SelectionError("CANDIDATE_CAPABILITY_RELATION: capability/component relation отсутствует.")
    action = kb.actions.get(candidate.action_ref or "")
    if action is None or action.component_ref != candidate.component_ref:
        raise _SelectionError("CANDIDATE_ACTION_RELATION: action/component relation отсутствует.")
    if candidate.version_scope != action.version_scope:
        raise _SelectionError("CANDIDATE_VERSION_SCOPE: scope не совпадает с ActionRecord.")
    if candidate.effect_ref not in action.effect_refs:
        raise _SelectionError("CANDIDATE_EFFECT_RELATION: effect отсутствует в ActionRecord.")
    effect = kb.effects.get(candidate.effect_ref or "")
    if effect is None or effect.target_ref != action.target_ref:
        raise _SelectionError("CANDIDATE_EFFECT_TARGET: effect принадлежит другому target.")
    allowed_evidence = set(capability.evidence_ids) | set(action.evidence_ids) | set(effect.evidence_ids)
    if any(item not in allowed_evidence or item not in kb.evidence for item in candidate.evidence_ids):
        raise _SelectionError("CANDIDATE_EVIDENCE_RELATION: evidence не связано с candidate entities.")


def _payload(atom: AtomicClaim, retrieval: DeepRetrievalResult, kb: KnowledgeBaseV2) -> dict[str, object]:
    candidates = retrieval.normalized_candidates
    action_ids = {item.action_ref for item in candidates if item.action_ref is not None}
    actor_ids = {
        actor.actor_id
        for actor in kb.actors.values()
        if actor.component_ref in {item.component_ref for item in candidates}
    }
    target_ids = {kb.actions[action_id].target_ref for action_id in action_ids}
    evidence_ids = {item for candidate in candidates for item in candidate.evidence_ids}
    templates = _allowed_templates(retrieval, kb)
    return {
        "atom": to_dict(atom),
        "candidates": [
            {
                "component_ref": item.component_ref,
                "capability_ref": item.capability_ref,
                "action_ref": item.action_ref,
                "effect_ref": item.effect_ref,
                "evidence_ids": list(item.evidence_ids),
                "version_scope": to_dict(item.version_scope),
            }
            for item in candidates
        ],
        "actors": [
            {"actor_ref": item, "component_ref": kb.actors[item].component_ref}
            for item in sorted(actor_ids)
        ],
        "targets": [
            {
                "target_ref": item,
                "contour": kb.targets[item].contour.value,
                "component_ref": kb.targets[item].component_ref,
                "host_profile": kb.targets[item].host_profile,
            }
            for item in sorted(target_ids)
        ],
        "evidence": [
            {
                "evidence_id": item,
                "claim": kb.evidence[item].claim,
                "polarity": kb.evidence[item].polarity.value,
                "strength": kb.evidence[item].strength.value,
                "version_constraint": kb.evidence[item].version_constraint,
                "applicable_contours": [c.value for c in kb.evidence[item].applicable_contours],
                "supports_entity_refs": list(kb.evidence[item].supports_entity_refs),
                "review_state": kb.evidence[item].review_state,
            }
            for item in sorted(evidence_ids)
        ],
        "procedure_templates": [
            {
                "template_id": item,
                "lifecycle_phase": kb.procedures[item].lifecycle_phase.value,
                "action_refs": list(kb.procedures[item].action_refs),
            }
            for item in sorted(templates)
        ],
        "response_schema": {
            "support_status": [item.value for item in SupportStatus],
            "supported_aspects": ["string"],
            "unconfirmed_aspects": ["string"],
            "responsibilities": [{key: "required" for key in sorted(_RESPONSIBILITY_KEYS)}],
            "procedure_template_ids": ["allowlisted template_id"],
        },
    }


def _shape_violations(raw: dict[str, object]) -> tuple[str, ...]:
    violations: list[str] = []
    if set(raw) != _TOP_KEYS:
        violations.append("Ответ содержит неполный или неизвестный набор полей.")
    _enum_value(raw.get("support_status"), SupportStatus, "support_status", violations)
    for key in ("supported_aspects", "unconfirmed_aspects", "procedure_template_ids"):
        _string_list(raw.get(key), key, violations)
    responsibilities = raw.get("responsibilities")
    if type(responsibilities) is not list:
        violations.append("responsibilities должен быть встроенным JSON array.")
        return tuple(violations)
    for index, value in enumerate(responsibilities, 1):
        prefix = f"responsibilities[{index}]"
        if type(value) is not dict:
            violations.append(f"{prefix} должен быть встроенным dict object.")
            continue
        item = cast(dict[str, object], value)
        if set(item) != _RESPONSIBILITY_KEYS:
            violations.append(f"{prefix} содержит неполный или неизвестный набор полей.")
        _enum_value(item.get("contour"), ResponsibilityContour, f"{prefix}.contour", violations)
        _enum_value(item.get("target_contour"), ResponsibilityContour, f"{prefix}.target_contour", violations)
        _enum_value(item.get("lifecycle_phase"), LifecyclePhase, f"{prefix}.lifecycle_phase", violations)
        _enum_value(item.get("support_status"), SupportStatus, f"{prefix}.support_status", violations)
        for key in ("component_ref", "executor_ref", "target_ref"):
            _text(item.get(key), f"{prefix}.{key}", violations)
        for key in ("action_ref", "effect_ref"):
            if item.get(key) is not None:
                _text(item.get(key), f"{prefix}.{key}", violations)
        _string_list(item.get("evidence_ids"), f"{prefix}.evidence_ids", violations)
        indexes = item.get("related_indexes")
        if type(indexes) is not list or any(type(number) is not int for number in indexes):
            violations.append(f"{prefix}.related_indexes должен быть JSON array built-in integer.")
        scope = item.get("version_scope")
        if type(scope) is not dict or set(scope) != _VERSION_KEYS:
            violations.append(f"{prefix}.version_scope содержит неверный набор полей.")
        else:
            for key in _VERSION_KEYS:
                _text(scope.get(key), f"{prefix}.version_scope.{key}", violations)
    return tuple(violations)


def _build_outcome(
    atom: AtomicClaim,
    raw: dict[str, object],
    retrieval: DeepRetrievalResult,
    kb: KnowledgeBaseV2,
) -> DeepMappingOutcome:
    raw_records = cast(list[dict[str, object]], raw["responsibilities"])
    adjacency = _raw_adjacency(raw_records)
    selections: dict[
        int, tuple[_ResponsibilitySelection, SupportStatus, tuple[str, ...]]
    ] = {}
    semantic_keys: set[tuple[object, ...]] = set()
    for index, item in enumerate(raw_records):
        selection, status, diagnostics = _validated_selection(item, retrieval, kb)
        semantic_key = _semantic_responsibility_key(selection, status)
        if semantic_key in semantic_keys:
            raise _SelectionError(
                "RESPONSIBILITY_SEMANTIC_DUPLICATE: duplicate semantic responsibility запрещена."
            )
        semantic_keys.add(semantic_key)
        selections[index] = (selection, status, diagnostics)
    order = sorted(
        range(len(raw_records)),
        key=lambda index: _semantic_responsibility_key(
            selections[index][0], selections[index][1]
        ),
    )
    ordinal_by_raw = {raw_index: ordinal for ordinal, raw_index in enumerate(order, 1)}
    records: list[ResponsibilityRecord] = []
    for ordinal, raw_index in enumerate(order, 1):
        selection, status, diagnostics = selections[raw_index]
        related = tuple(
            responsibility_id(atom.atom_id, ordinal_by_raw[index])
            for index in sorted(adjacency[raw_index], key=ordinal_by_raw.__getitem__)
        )
        record = ResponsibilityRecord(
            responsibility_id(atom.atom_id, ordinal),
            atom.requirement_id,
            atom.atom_id,
            selection.contour,
            selection.component_ref,
            selection.executor_ref,
            selection.target_contour,
            selection.target_ref,
            selection.action_ref,
            selection.effect_ref,
            selection.lifecycle_phase,
            selection.version_scope,
            selection.evidence_ids,
            status,
            related,
            diagnostics=diagnostics,
        )
        records.append(record)
    try:
        normalized = validate_responsibility_records(tuple(records), retrieval, kb)
    except ValidationError as exc:
        raise _SelectionError(f"{exc.code}: {exc.message_ru}") from exc
    templates = tuple(sorted(cast(list[str], raw["procedure_template_ids"])))
    if len(templates) != len(set(templates)):
        raise _SelectionError("PROCEDURE_TEMPLATE_DUPLICATE: template IDs должны быть unique.")
    allowed_templates = _allowed_templates(retrieval, kb)
    for template in templates:
        if template not in allowed_templates:
            raise _SelectionError(f"PROCEDURE_TEMPLATE_NOT_ALLOWLISTED: {template}.")
    selected_action_refs = {
        item.action_ref for item in normalized if item.action_ref is not None
    }
    for template in templates:
        if not selected_action_refs.intersection(kb.procedures[template].action_refs):
            raise _SelectionError(
                f"PROCEDURE_TEMPLATE_TRIGGER_NOT_SELECTED: {template}."
            )
    statuses = tuple(item.support_status for item in normalized)
    status = _aggregate_status(statuses)
    diagnostics = tuple(dict.fromkeys(item for record in normalized for item in record.diagnostics))
    supported = tuple(cast(list[str], raw["supported_aspects"])) if status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL} else ()
    unconfirmed = tuple(cast(list[str], raw["unconfirmed_aspects"]))
    if not supported:
        unconfirmed = tuple(dict.fromkeys((*unconfirmed, *cast(list[str], raw["supported_aspects"]))))
    atom_result = DeepAtomResult(
        atom,
        AnalysisState.COMPLETED,
        status,
        tuple(item.record_id for item in normalized),
        supported,
        unconfirmed,
        diagnostics,
    )
    return DeepMappingOutcome(atom_result, normalized, templates)


def _validated_selection(
    item: dict[str, object], retrieval: DeepRetrievalResult, kb: KnowledgeBaseV2
) -> tuple[_ResponsibilitySelection, SupportStatus, tuple[str, ...]]:
    scope = cast(dict[str, str], item["version_scope"])
    try:
        selection = _ResponsibilitySelection(
            ResponsibilityContour(cast(str, item["contour"])),
            cast(str, item["component_ref"]),
            cast(str, item["executor_ref"]),
            ResponsibilityContour(cast(str, item["target_contour"])),
            cast(str, item["target_ref"]),
            cast(str | None, item["action_ref"]),
            cast(str | None, item["effect_ref"]),
            LifecyclePhase(cast(str, item["lifecycle_phase"])),
            VersionScope(**scope),
            tuple(cast(list[str], item["evidence_ids"])),
            SupportStatus(cast(str, item["support_status"])),
        )
        validate_version_scope(selection.version_scope, selection.lifecycle_phase)
    except ValueError as exc:
        raise _SelectionError(f"VERSION_OR_RECORD_INVALID: {exc}") from exc
    candidate = _candidate_for(selection, retrieval, kb)
    status, diagnostics = _computed_support(selection, candidate, kb)
    return selection, status, diagnostics


def _candidate_for(
    record: ResponsibilityRecord | _ResponsibilitySelection,
    retrieval: DeepRetrievalResult,
    kb: KnowledgeBaseV2,
) -> DeepCandidate:
    matches = [
        item
        for item in retrieval.normalized_candidates
        if item.action_ref == record.action_ref and item.effect_ref == record.effect_ref
    ]
    if len(matches) != 1:
        raise _SelectionError("ACTION_EFFECT_NOT_ALLOWLISTED: responsibility не соответствует exact candidate relation.")
    candidate = matches[0]
    action = kb.actions[candidate.action_ref or ""]
    target = kb.targets.get(action.target_ref)
    if target is None or record.target_ref != target.target_id or record.target_contour is not target.contour:
        raise _SelectionError("TARGET_NOT_ALLOWLISTED: target/contour не соответствует ActionRecord.")
    actor = kb.actors.get(record.executor_ref)
    if actor is None or actor.component_ref != action.component_ref:
        raise _SelectionError("ACTOR_NOT_ALLOWLISTED: executor не принадлежит component ActionRecord.")
    primary = record.contour is action.contour and record.component_ref == candidate.component_ref
    host_projection = (
        action.contour is ResponsibilityContour.KOLLA_ANSIBLE
        and target.contour is ResponsibilityContour.HOST_OS
        and record.contour is ResponsibilityContour.HOST_OS
        and record.component_ref == target.component_ref
    )
    if not primary and not host_projection:
        raise _SelectionError("COMPONENT_CONTOUR_NOT_ALLOWLISTED: component/contour relation отсутствует.")
    upgrade_phase = record.lifecycle_phase is LifecyclePhase.UPGRADE
    upgrade_target = record.version_scope.target_release == "2026.1"
    if record.version_scope != action.version_scope or upgrade_phase != upgrade_target:
        raise _SelectionError("VERSION_SCOPE_NOT_ALLOWLISTED: scope/phase не соответствует ActionRecord.")
    return candidate


def _computed_support(
    record: ResponsibilityRecord | _ResponsibilitySelection,
    candidate: DeepCandidate,
    kb: KnowledgeBaseV2,
) -> tuple[SupportStatus, tuple[str, ...]]:
    allowed = set(candidate.evidence_ids)
    if len(record.evidence_ids) != len(set(record.evidence_ids)):
        raise _SelectionError("EVIDENCE_DUPLICATE: evidence IDs должны быть unique.")
    relevant_entities = {
        candidate.capability_ref,
        *([candidate.action_ref] if candidate.action_ref else []),
        *([candidate.effect_ref] if candidate.effect_ref else []),
    }
    selected = []
    for evidence_id in record.evidence_ids:
        if evidence_id not in allowed or evidence_id not in kb.evidence:
            raise _SelectionError(f"EVIDENCE_NOT_ALLOWLISTED: {evidence_id}.")
        evidence = kb.evidence[evidence_id]
        source = kb.sources.get(evidence.source_id)
        if not relevant_entities.intersection(evidence.supports_entity_refs):
            raise _SelectionError(f"EVIDENCE_ENTITY_OWNERSHIP: {evidence_id}.")
        if record.contour not in evidence.applicable_contours:
            raise _SelectionError(f"EVIDENCE_CONTOUR_MISMATCH: {evidence_id}.")
        if evidence.version_constraint not in {
            record.version_scope.version_constraint,
            record.version_scope.source_release,
            record.version_scope.target_release,
        }:
            raise _SelectionError(f"EVIDENCE_VERSION_MISMATCH: {evidence_id}.")
        if source is None or source.provenance != "official" or source.source_type == "project_policy":
            raise _SelectionError(f"EVIDENCE_SOURCE_INVALID: {evidence_id}.")
        selected.append(evidence)
    direct = [item for item in selected if item.strength is EvidenceStrength.DIRECT]
    positive = any(item.polarity is EvidencePolarity.POSITIVE for item in direct)
    negative = any(item.polarity is EvidencePolarity.NEGATIVE for item in direct)
    if positive and negative:
        return SupportStatus.INSUFFICIENT_EVIDENCE, ("evidence_conflict",)
    if positive:
        return SupportStatus.SUPPORTED, ()
    if negative:
        return SupportStatus.NOT_SUPPORTED, ()
    claimed_status = (
        record.support_status
        if isinstance(record, ResponsibilityRecord)
        else record.claimed_support_status
    )
    if claimed_status is SupportStatus.SUPPORTED and selected:
        raise _SelectionError("INDIRECT_SUPPORT_CLAIM: indirect/unknown evidence cannot establish supported.")
    if not selected:
        return SupportStatus.INSUFFICIENT_EVIDENCE, ("procedure_gap:responsibility_evidence",)
    return SupportStatus.INSUFFICIENT_EVIDENCE, ()


def _raw_adjacency(records: list[dict[str, object]]) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = {}
    for index, item in enumerate(records):
        indexes = cast(list[int], item["related_indexes"])
        if len(indexes) != len(set(indexes)):
            raise _SelectionError("RELATED_INDEX_DUPLICATE: related_indexes должны быть unique.")
        converted = {number - 1 for number in indexes}
        if index in converted:
            raise _SelectionError("RELATED_INDEX_SELF: self relation запрещена.")
        if any(number < 0 or number >= len(records) for number in converted):
            raise _SelectionError("RELATED_INDEX_RANGE: related index вне диапазона.")
        adjacency[index] = converted
    if any(index not in adjacency[other] for index, values in adjacency.items() for other in values):
        raise _SelectionError("RELATED_INDEX_ASYMMETRIC: relations должны быть symmetric.")
    return adjacency


def _validate_record_relations(records: tuple[ResponsibilityRecord, ...]) -> None:
    by_id = {item.record_id: item for item in records}
    if len(by_id) != len(records):
        raise ValidationError("RESPONSIBILITY_ID_DUPLICATE", "record_id должны быть unique.")
    for item in records:
        if item.record_id in item.related_record_ids:
            raise ValidationError("RELATED_RECORD_SELF", "self relation запрещена.")
        if len(item.related_record_ids) != len(set(item.related_record_ids)):
            raise ValidationError("RELATED_RECORD_DUPLICATE", "related_record_ids должны быть unique.")
        for related in item.related_record_ids:
            if related not in by_id or item.record_id not in by_id[related].related_record_ids:
                raise ValidationError("RELATED_RECORD_ASYMMETRIC", "related_record_ids должны быть symmetric и in-range.")


def _validate_host_bundles(records: tuple[ResponsibilityRecord, ...], kb: KnowledgeBaseV2) -> None:
    groups: dict[tuple[object, ...], list[ResponsibilityRecord]] = {}
    for item in records:
        actor = kb.actors[item.executor_ref]
        target = kb.targets[item.target_ref]
        if actor.component_ref == "kolla_ansible" and target.contour is ResponsibilityContour.HOST_OS:
            groups.setdefault(
                (
                    item.executor_ref,
                    item.target_ref,
                    item.action_ref,
                    item.effect_ref,
                    item.lifecycle_phase,
                    item.version_scope,
                ),
                [],
            ).append(item)
    for bundle in groups.values():
        kolla = [item for item in bundle if item.contour is ResponsibilityContour.KOLLA_ANSIBLE]
        host = [item for item in bundle if item.contour is ResponsibilityContour.HOST_OS]
        if len(bundle) == 1:
            raise _SelectionError(
                "HOST_OS_RECORD_REQUIRED: Kolla host action требует kolla_ansible и host_os records."
            )
        if len(bundle) != 2 or len(kolla) != 1 or len(host) != 1:
            raise _SelectionError(
                "HOST_OS_BUNDLE_CARDINALITY: Kolla host action требует exactly one kolla_ansible and one host_os record."
            )
        if host[0].record_id not in kolla[0].related_record_ids or kolla[0].record_id not in host[0].related_record_ids:
            raise _SelectionError("HOST_OS_LINK_REQUIRED: mixed contour records должны быть linked.")


def _allowed_templates(retrieval: DeepRetrievalResult, kb: KnowledgeBaseV2) -> set[str]:
    action_ids = {item.action_ref for item in retrieval.normalized_candidates if item.action_ref}
    return {
        template.template_id
        for template in kb.procedures.values()
        if set(template.action_refs) and set(template.action_refs).issubset(action_ids)
    }


def _aggregate_status(statuses: tuple[SupportStatus, ...]) -> SupportStatus:
    if not statuses or SupportStatus.INSUFFICIENT_EVIDENCE in statuses:
        return SupportStatus.INSUFFICIENT_EVIDENCE
    unique = set(statuses)
    if len(unique) == 1:
        return statuses[0]
    return SupportStatus.PARTIAL


def _semantic_responsibility_key(
    item: _ResponsibilitySelection, computed_support: SupportStatus
) -> tuple[object, ...]:
    return (
        _CONTOUR_ORDER[item.contour],
        item.component_ref,
        item.executor_ref,
        _CONTOUR_ORDER[item.target_contour],
        item.target_ref,
        item.action_ref or "",
        item.effect_ref or "",
        item.lifecycle_phase.value,
        item.version_scope.source_release,
        item.version_scope.target_release,
        item.version_scope.kolla_ansible_release,
        item.version_scope.host_profile,
        item.version_scope.version_constraint,
        tuple(sorted(item.evidence_ids)),
        computed_support.value,
    )


def _enum_value(value: object, enum_type: type, name: str, violations: list[str]) -> None:
    if type(value) is not str or value not in {item.value for item in enum_type}:
        violations.append(f"{name} содержит недопустимое enum значение.")


def _text(value: object, name: str, violations: list[str]) -> None:
    if type(value) is not str or not value.strip():
        violations.append(f"{name} должен быть непустой built-in string.")


def _string_list(value: object, name: str, violations: list[str]) -> None:
    if type(value) is not list or any(type(item) is not str or not item.strip() for item in value):
        violations.append(f"{name} должен быть JSON array непустых built-in strings.")


def _failed(atom: AtomicClaim, state: AnalysisState, violations: tuple[str, ...]) -> DeepMappingOutcome:
    return DeepMappingOutcome(
        DeepAtomResult(atom, state, None, (), diagnostics=("; ".join(violations),)),
        (),
        (),
    )
