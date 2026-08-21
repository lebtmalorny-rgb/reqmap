"""Grounded many-to-many mapping with fail-closed implementation validation."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from reqmap.errors import ModelOutputError, ValidationError
from reqmap.ids import mapping_id
from reqmap.knowledge import KnowledgeBase
from reqmap.llm import JsonModel
from reqmap.models import (
    AnalysisState,
    AtomResult,
    AtomicClaim,
    Candidate,
    Evidence,
    EvidencePolarity,
    EvidenceStrength,
    ImplementationSource,
    ImplementationStep,
    Mapping,
    Phase,
    RelationType,
    SupportStatus,
    to_dict,
)
from reqmap.prompts import MAPPING_PROMPT


_TOP_LEVEL_KEYS = frozenset(
    {"support_status", "supported_aspects", "unconfirmed_aspects", "mappings"}
)
_MAPPING_KEYS = frozenset(
    {
        "component_id",
        "role_ru",
        "relation",
        "phase",
        "implementation_source",
        "mechanism",
        "steps",
        "evidence_ids",
        "support_status",
        "reason_ru",
    }
)
_STEP_KEYS = frozenset({"action_ru", "mechanism", "command", "api_operation"})
_POSITIVE_STRENGTHS = frozenset({EvidenceStrength.DIRECT, EvidenceStrength.INDIRECT})
_DOWNGRADE_REASON = "Статус понижен: отсутствует достаточное официальное evidence."
_CANDIDATE_REASON_POLICY = (
    "reasons кандидата, включая source_hint, неавторитетны: они помогают ранжированию, "
    "но никогда не являются evidence."
)


def map_atom(
    model: JsonModel,
    atom: AtomicClaim,
    candidates: tuple[Candidate, ...],
    kb: KnowledgeBase,
) -> AtomResult:
    """Map one exact atom with one semantic correction and local validation."""
    _validate_candidates(candidates, kb)
    base_payload = _mapping_payload(atom, candidates, kb)
    payload = base_payload
    violations: tuple[str, ...] = ()

    for _attempt in range(2):
        try:
            response = model.complete_json("mapping", MAPPING_PROMPT, payload)
            if type(response) is not dict:
                invalid_response: object = response
                violations = (
                    "Ответ модели должен быть встроенным dict JSON object верхнего уровня.",
                )
            else:
                raw = cast(dict[str, object], response)
                invalid_response = response
                violations = _response_violations(raw)
                if not violations:
                    candidate_violations = _candidate_reference_violations(raw, candidates, kb)
                    if candidate_violations:
                        violations = candidate_violations
                    else:
                        proposed = _build_result(atom, raw)
                        try:
                            return validate_atom_result(proposed, kb)
                        except ValidationError as exc:
                            violations = (exc.message_ru,)
        except ModelOutputError as exc:
            invalid_response = {"raw_response": exc.raw_response}
            violations = (exc.message_ru,)

        payload = {
            "original_payload": base_payload,
            "invalid_response": invalid_response,
            "violations_ru": list(violations),
        }

    return AtomResult(
        atom=atom,
        analysis_state=AnalysisState.MODEL_FAILED,
        support_status=None,
        mappings=(),
        diagnostics=("; ".join(violations),),
    )


def validate_atom_result(result: AtomResult, kb: KnowledgeBase) -> AtomResult:
    """Validate references/invariants and normalize unsupported evidence claims.

    Structural or referential failures raise :class:`ValidationError`. Evidence
    insufficiency is not a model-format error and is deterministically downgraded.
    """
    _validate_result_shape(result)
    if not isinstance(result.analysis_state, AnalysisState):
        _fail("ANALYSIS_STATE", "analysis_state не входит в канонический enum.")
    if result.analysis_state is not AnalysisState.COMPLETED:
        if (
            result.support_status is not None
            or result.mappings
            or result.supported_aspects
            or result.unconfirmed_aspects
        ):
            _fail(
                "ANALYSIS_STATE_STATUS",
                "Незавершённый анализ не может содержать support status или mappings.",
            )
        return result
    if result.support_status is None:
        _fail("COMPLETED_WITHOUT_STATUS", "Завершённый анализ обязан содержать support status.")

    _validate_aspects(result)
    normalized: list[Mapping] = []
    downgraded = False
    for ordinal, item in enumerate(result.mappings, start=1):
        if type(item) is not Mapping:
            _fail("MAPPING_TYPE", "mappings должен содержать только canonical Mapping.")
        _validate_mapping_record(item, result.atom, ordinal, kb)
        replacement = _normalize_mapping_support(item, kb)
        if replacement.support_status is not item.support_status:
            downgraded = True
        normalized.append(replacement)
    _validate_host_bundle(result.mappings, kb)

    original_status = _canonical_status(result.mappings)
    _validate_top_level_semantics(result, original_status)
    normalized_status = _canonical_status(tuple(normalized))
    if not normalized and result.support_status in {
        SupportStatus.INSUFFICIENT_EVIDENCE,
        SupportStatus.NOT_APPLICABLE,
    }:
        normalized_status = result.support_status

    if normalized_status is SupportStatus.PARTIAL:
        if not result.supported_aspects or not result.unconfirmed_aspects:
            _fail(
                "PARTIAL_ASPECTS",
                "Статус partial требует непустые supported_aspects и unconfirmed_aspects.",
            )

    supported_aspects = result.supported_aspects
    unconfirmed_aspects = result.unconfirmed_aspects
    diagnostics = result.diagnostics
    unproved_confirmed_aspects = bool(result.supported_aspects) and not any(
        _has_positive_official(item, kb) for item in normalized
    )
    if downgraded or unproved_confirmed_aspects:
        unconfirmed_aspects = tuple(
            dict.fromkeys((*result.unconfirmed_aspects, *result.supported_aspects))
        )
        supported_aspects = ()
        diagnostics = tuple(dict.fromkeys((*result.diagnostics, _DOWNGRADE_REASON)))

    normalized_result = replace(
        result,
        support_status=normalized_status,
        mappings=tuple(normalized),
        supported_aspects=supported_aspects,
        unconfirmed_aspects=unconfirmed_aspects,
        diagnostics=diagnostics,
    )
    _validate_confirmed_aspects(normalized_result, kb)
    return normalized_result


def _mapping_payload(
    atom: AtomicClaim, candidates: tuple[Candidate, ...], kb: KnowledgeBase
) -> dict[str, object]:
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for candidate in candidates
            for evidence_id in candidate.evidence_ids
        )
    )
    return {
        "atom": to_dict(atom),
        "candidates": [to_dict(candidate) for candidate in candidates],
        "candidate_reason_policy": _CANDIDATE_REASON_POLICY,
        "evidence": [to_dict(kb.evidence[evidence_id]) for evidence_id in evidence_ids],
        "response_schema": {
            "support_status": [item.value for item in SupportStatus],
            "supported_aspects": ["non-empty string; required for partial"],
            "unconfirmed_aspects": ["non-empty string; required for partial"],
            "mappings": [
                {
                    "component_id": "candidate component_id",
                    "role_ru": "non-empty string",
                    "relation": [item.value for item in RelationType],
                    "phase": [item.value for item in Phase],
                    "implementation_source": [item.value for item in ImplementationSource],
                    "mechanism": "non-empty string",
                    "steps": [
                        {
                            "action_ru": "non-empty string",
                            "mechanism": "non-empty string",
                            "command": "string or null",
                            "api_operation": "string or null",
                        }
                    ],
                    "evidence_ids": ["selected evidence_id"],
                    "support_status": [item.value for item in SupportStatus],
                    "reason_ru": "non-empty string",
                }
            ],
        },
    }


def _validate_candidates(candidates: tuple[Candidate, ...], kb: KnowledgeBase) -> None:
    for item in candidates:
        if item.component_id not in kb.components:
            _fail(
                "CANDIDATE_COMPONENT_UNKNOWN",
                f"Кандидат ссылается на неизвестный компонент: {item.component_id}",
            )
        if item.capability_id is not None and item.capability_id not in kb.capabilities:
            _fail(
                "CANDIDATE_CAPABILITY_UNKNOWN",
                f"Кандидат ссылается на неизвестную capability: {item.capability_id}",
            )
        for evidence_id in item.evidence_ids:
            if evidence_id not in kb.evidence:
                _fail(
                    "CANDIDATE_EVIDENCE_UNKNOWN",
                    f"Кандидат ссылается на неизвестный evidence: {evidence_id}",
                )


def _response_violations(response: dict[str, object]) -> tuple[str, ...]:
    violations: list[str] = []
    if set(response) != _TOP_LEVEL_KEYS:
        violations.append(
            "Ответ должен содержать только support_status, supported_aspects, "
            "unconfirmed_aspects и mappings."
        )
    _enum_violation(response.get("support_status"), SupportStatus, "support_status", violations)
    _string_list_violations(response.get("supported_aspects"), "supported_aspects", violations)
    _string_list_violations(response.get("unconfirmed_aspects"), "unconfirmed_aspects", violations)

    mappings = response.get("mappings")
    if type(mappings) is not list:
        violations.append("Поле mappings должно быть встроенным JSON array.")
        return tuple(violations)

    for index, raw_mapping in enumerate(mappings, start=1):
        prefix = f"Mapping {index}"
        if type(raw_mapping) is not dict:
            violations.append(f"{prefix} должен быть встроенным dict object.")
            continue
        item = cast(dict[str, object], raw_mapping)
        if set(item) != _MAPPING_KEYS:
            violations.append(f"{prefix} содержит неполный или неизвестный набор полей.")
        for key in ("component_id", "role_ru", "mechanism", "reason_ru"):
            _nonempty_string_violation(item.get(key), f"{prefix}.{key}", violations)
        _enum_violation(item.get("relation"), RelationType, f"{prefix}.relation", violations)
        _enum_violation(item.get("phase"), Phase, f"{prefix}.phase", violations)
        _enum_violation(
            item.get("implementation_source"),
            ImplementationSource,
            f"{prefix}.implementation_source",
            violations,
        )
        _enum_violation(
            item.get("support_status"), SupportStatus, f"{prefix}.support_status", violations
        )
        _string_list_violations(item.get("evidence_ids"), f"{prefix}.evidence_ids", violations)
        steps = item.get("steps")
        if type(steps) is not list or not steps:
            violations.append(f"{prefix}.steps должен быть непустым встроенным JSON array.")
            continue
        for step_index, raw_step in enumerate(steps, start=1):
            step_prefix = f"{prefix}.steps[{step_index}]"
            if type(raw_step) is not dict:
                violations.append(f"{step_prefix} должен быть встроенным dict object.")
                continue
            step = cast(dict[str, object], raw_step)
            if set(step) != _STEP_KEYS:
                violations.append(f"{step_prefix} содержит неполный или неизвестный набор полей.")
            for key in ("action_ru", "mechanism"):
                _nonempty_string_violation(step.get(key), f"{step_prefix}.{key}", violations)
            for key in ("command", "api_operation"):
                value = step.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    violations.append(f"{step_prefix}.{key} должен быть непустой строкой или null.")

    status = response.get("support_status")
    supported = response.get("supported_aspects")
    unconfirmed = response.get("unconfirmed_aspects")
    if status == SupportStatus.PARTIAL.value and (
        type(supported) is not list
        or not supported
        or type(unconfirmed) is not list
        or not unconfirmed
    ):
        violations.append(
            "Статус partial требует непустые supported_aspects и unconfirmed_aspects."
        )
    return tuple(violations)


def _candidate_reference_violations(
    response: dict[str, object], candidates: tuple[Candidate, ...], kb: KnowledgeBase
) -> tuple[str, ...]:
    candidate_components = {item.component_id for item in candidates}
    allowed_evidence = {
        evidence_id for item in candidates for evidence_id in item.evidence_ids
    }
    violations: list[str] = []
    if response["support_status"] == SupportStatus.NOT_APPLICABLE.value and any(
        kb.components[item.component_id].kind == "host_os_subsystem"
        for item in candidates
    ):
        violations.append(
            "Требование с кандидатом host OS subsystem нельзя считать not_applicable."
        )
    raw_mappings = cast(list[dict[str, object]], response["mappings"])
    for index, item in enumerate(raw_mappings, start=1):
        component_id = cast(str, item["component_id"])
        if component_id not in candidate_components:
            violations.append(
                f"Mapping {index}: компонент {component_id} отсутствует в списке кандидатов."
            )
        for evidence_id in cast(list[str], item["evidence_ids"]):
            if evidence_id not in allowed_evidence:
                violations.append(
                    f"Mapping {index}: evidence {evidence_id} отсутствует в evidence кандидатов."
                )
            elif not _evidence_relevant(kb.evidence[evidence_id], component_id, kb):
                violations.append(
                    f"Mapping {index}: evidence {evidence_id} нерелевантно компоненту {component_id}."
                )
    return tuple(violations)


def _build_result(atom: AtomicClaim, response: dict[str, object]) -> AtomResult:
    mappings: list[Mapping] = []
    for ordinal, raw in enumerate(cast(list[dict[str, object]], response["mappings"]), start=1):
        phase = Phase(cast(str, raw["phase"]))
        steps = tuple(
            ImplementationStep(
                order=step_ordinal,
                phase=phase,
                action_ru=cast(str, step["action_ru"]),
                mechanism=cast(str, step["mechanism"]),
                command=cast(str | None, step["command"]),
                api_operation=cast(str | None, step["api_operation"]),
            )
            for step_ordinal, step in enumerate(
                cast(list[dict[str, object]], raw["steps"]), start=1
            )
        )
        mappings.append(
            Mapping(
                mapping_id=mapping_id(atom.atom_id, ordinal),
                atom_id=atom.atom_id,
                component_id=cast(str, raw["component_id"]),
                role_ru=cast(str, raw["role_ru"]),
                relation=RelationType(cast(str, raw["relation"])),
                phase=phase,
                implementation_source=ImplementationSource(
                    cast(str, raw["implementation_source"])
                ),
                mechanism=cast(str, raw["mechanism"]),
                steps=steps,
                evidence_ids=tuple(cast(list[str], raw["evidence_ids"])),
                support_status=SupportStatus(cast(str, raw["support_status"])),
                reason_ru=cast(str, raw["reason_ru"]),
            )
        )
    return AtomResult(
        atom=atom,
        analysis_state=AnalysisState.COMPLETED,
        support_status=SupportStatus(cast(str, response["support_status"])),
        mappings=tuple(mappings),
        supported_aspects=tuple(cast(list[str], response["supported_aspects"])),
        unconfirmed_aspects=tuple(cast(list[str], response["unconfirmed_aspects"])),
    )


def _validate_mapping_record(
    item: Mapping, atom: AtomicClaim, ordinal: int, kb: KnowledgeBase
) -> None:
    expected_id = mapping_id(atom.atom_id, ordinal)
    if not isinstance(item.mapping_id, str) or not item.mapping_id:
        _fail("MAPPING_ID", "mapping_id должен быть непустой строкой.")
    if item.mapping_id != expected_id:
        _fail("MAPPING_ID", f"Нестабильный mapping_id: ожидался {expected_id}.")
    if not isinstance(item.atom_id, str) or not item.atom_id:
        _fail("MAPPING_ATOM_UNKNOWN", "atom_id mapping должен быть непустой строкой.")
    if item.atom_id != atom.atom_id:
        _fail("MAPPING_ATOM_UNKNOWN", "Mapping ссылается не на текущий atom.")
    if not isinstance(item.component_id, str) or not item.component_id:
        _fail("UNKNOWN_COMPONENT", "component_id mapping должен быть непустой строкой.")
    component = kb.components.get(item.component_id)
    if component is None:
        _fail("UNKNOWN_COMPONENT", f"Неизвестный компонент: {item.component_id}")
    if not isinstance(item.role_ru, str) or not item.role_ru.strip():
        _fail("MAPPING_ROLE", "role_ru mapping должен быть непустой строкой.")
    if not isinstance(item.mechanism, str) or not item.mechanism.strip():
        _fail("MAPPING_MECHANISM", "mechanism mapping должен быть непустой строкой.")
    if not isinstance(item.reason_ru, str) or not item.reason_ru.strip():
        _fail("MAPPING_REASON", "reason_ru mapping должен быть непустой строкой.")
    if not isinstance(item.relation, RelationType):
        _fail("MAPPING_RELATION", "relation mapping не входит в канонический enum.")
    if not isinstance(item.phase, Phase):
        _fail("MAPPING_PHASE", "phase mapping не входит в канонический enum.")
    if not isinstance(item.implementation_source, ImplementationSource):
        _fail("MAPPING_SOURCE", "implementation_source не входит в канонический enum.")
    if not isinstance(item.support_status, SupportStatus):
        _fail("MAPPING_STATUS", "support_status mapping не входит в канонический enum.")
    if item.support_status is SupportStatus.NOT_APPLICABLE:
        _fail("MAPPING_NOT_APPLICABLE", "not_applicable не может содержать mapping.")
    if type(item.steps) is not tuple or not item.steps:
        _fail("MAPPING_STEPS", "Mapping обязан содержать хотя бы один implementation step.")
    for step_ordinal, step in enumerate(item.steps, start=1):
        if type(step) is not ImplementationStep:
            _fail("STEP_TYPE", "steps должен содержать только canonical ImplementationStep.")
        _validate_step(step, item, step_ordinal)
    if type(item.evidence_ids) is not tuple:
        _fail("EVIDENCE_IDS", "evidence_ids mapping должен быть tuple строк.")
    for evidence_id in item.evidence_ids:
        if not isinstance(evidence_id, str) or not evidence_id:
            _fail("UNKNOWN_EVIDENCE", "evidence_id должен быть непустой строкой.")
    if len(item.evidence_ids) != len(set(item.evidence_ids)):
        _fail("EVIDENCE_DUPLICATE", "evidence_ids mapping не должен содержать дубли.")
    for evidence_id in item.evidence_ids:
        cited = kb.evidence.get(evidence_id)
        if cited is None:
            _fail("UNKNOWN_EVIDENCE", f"Неизвестный evidence: {evidence_id}")
        if not _evidence_relevant(cited, item.component_id, kb):
            _fail(
                "EVIDENCE_COMPONENT_MISMATCH",
                f"Evidence {evidence_id} нерелевантно компоненту {item.component_id}.",
            )
    _validate_implementation(item, component.kind)


def _validate_step(step: ImplementationStep, item: Mapping, ordinal: int) -> None:
    if step.order != ordinal:
        _fail("STEP_ORDER", "Порядок implementation steps должен быть последовательным с единицы.")
    if step.phase is not item.phase:
        _fail("STEP_PHASE_MISMATCH", "Фаза шага не совпадает с фазой mapping.")
    if not isinstance(step.action_ru, str) or not step.action_ru.strip():
        _fail("STEP_ACTION", "action_ru implementation step должен быть непустой строкой.")
    if not isinstance(step.mechanism, str) or not step.mechanism.strip():
        _fail("STEP_MECHANISM", "mechanism implementation step должен быть непустой строкой.")
    for code, value in (("STEP_COMMAND", step.command), ("STEP_API_OPERATION", step.api_operation)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            _fail(code, "command и api_operation должны быть непустой строкой или null.")


def _validate_implementation(item: Mapping, component_kind: str) -> None:
    if item.phase is Phase.DESIGNTIME and not any(
        step.command == "kolla-ansible reconfigure" for step in item.steps
    ):
        _fail(
            "DESIGNTIME_WITHOUT_RECONFIGURE",
            "Design-time mapping обязан содержать отдельный step с точной командой kolla-ansible reconfigure.",
        )
    if item.phase is Phase.RUNTIME:
        if item.mechanism != "openstack_api":
            _fail("RUNTIME_MECHANISM", "Runtime mapping обязан иметь mechanism openstack_api.")
        if any(step.command is not None for step in item.steps):
            _fail("RUNTIME_COMMAND", "Runtime implementation steps обязаны иметь command=null.")
        if not any(
            isinstance(step.api_operation, str) and bool(step.api_operation.strip())
            for step in item.steps
        ):
            _fail(
                "RUNTIME_API_OPERATION",
                "Runtime mapping обязан содержать непустой api_operation.",
            )

    is_host = component_kind == "host_os_subsystem"
    if is_host:
        if item.relation is not RelationType.HOST_OS_CHANGE:
            _fail("HOST_OS_RELATION", "Host OS subsystem mapping обязан иметь relation host_os_change.")
        if item.phase is not Phase.DESIGNTIME:
            _fail("HOST_OS_PHASE", "Host OS subsystem mapping обязан иметь phase designtime.")
        if item.implementation_source is not ImplementationSource.KOLLA_ANSIBLE:
            _fail(
                "HOST_OS_SOURCE",
                "Host OS subsystem mapping обязан иметь implementation_source kolla_ansible.",
            )
    elif item.relation is RelationType.HOST_OS_CHANGE:
        _fail(
            "HOST_OS_COMPONENT_KIND",
            "relation host_os_change допустим только для component kind host_os_subsystem.",
        )

    if item.component_id == "kolla_ansible":
        if item.phase is not Phase.DESIGNTIME:
            _fail("KOLLA_PHASE", "Kolla-Ansible mapping обязан иметь phase designtime.")
        if item.implementation_source is not ImplementationSource.KOLLA_ANSIBLE:
            _fail(
                "KOLLA_SOURCE",
                "Kolla-Ansible mapping обязан иметь implementation_source kolla_ansible.",
            )


def _validate_host_bundle(mappings: tuple[Mapping, ...], kb: KnowledgeBase) -> None:
    host_mappings = tuple(
        item
        for item in mappings
        if kb.components[item.component_id].kind == "host_os_subsystem"
    )
    kolla_mappings = tuple(item for item in mappings if item.component_id == "kolla_ansible")
    if host_mappings and not kolla_mappings:
        _fail(
            "HOST_OS_WITHOUT_KOLLA",
            "Изменение хостовой ОС требует отдельные mappings для Kolla-Ansible и конкретной подсистемы ОС.",
        )


def _validate_top_level_semantics(
    result: AtomResult, original_status: SupportStatus | None
) -> None:
    if not result.mappings:
        if result.support_status not in {
            SupportStatus.INSUFFICIENT_EVIDENCE,
            SupportStatus.NOT_APPLICABLE,
        }:
            _fail(
                "STATUS_WITHOUT_MAPPINGS",
                "Без mappings допустимы только insufficient_evidence или not_applicable.",
            )
        if result.support_status is SupportStatus.NOT_APPLICABLE and (
            result.supported_aspects or result.unconfirmed_aspects
        ):
            _fail(
                "NOT_APPLICABLE_ASPECTS",
                "not_applicable не может содержать mappings или подтверждённые аспекты.",
            )
        return
    if result.support_status is SupportStatus.NOT_APPLICABLE:
        _fail("NOT_APPLICABLE_MAPPINGS", "not_applicable не может содержать mappings.")
    if result.support_status is not original_status:
        _fail(
            "ATOM_STATUS_MISMATCH",
            "Итоговый support_status атома не согласован со статусами mappings.",
        )


def _validate_aspects(result: AtomResult) -> None:
    for label, values in (
        ("supported_aspects", result.supported_aspects),
        ("unconfirmed_aspects", result.unconfirmed_aspects),
    ):
        if any(not isinstance(value, str) or not value.strip() for value in values):
            _fail("ASPECT_VALUE", f"{label} должен содержать только непустые строки.")
        if len(values) != len(set(values)):
            _fail("ASPECT_DUPLICATE", f"{label} не должен содержать дубли.")
    if result.support_status is SupportStatus.PARTIAL and (
        not result.supported_aspects or not result.unconfirmed_aspects
    ):
        _fail(
            "PARTIAL_ASPECTS",
            "Статус partial требует непустые supported_aspects и unconfirmed_aspects.",
        )


def _validate_result_shape(result: AtomResult) -> None:
    if type(result) is not AtomResult:
        _fail("ATOM_RESULT_TYPE", "Ожидался canonical AtomResult.")
    if type(result.atom) is not AtomicClaim:
        _fail("ATOM_TYPE", "AtomResult должен содержать canonical AtomicClaim.")
    for label, value in (
        ("atom_id", result.atom.atom_id),
        ("requirement_id", result.atom.requirement_id),
        ("text", result.atom.text),
        ("source_quote", result.atom.source_quote),
    ):
        if not isinstance(value, str) or not value.strip():
            _fail("ATOM_FIELD", f"{label} атома должен быть непустой строкой.")
    if type(result.atom.mandatory) is not bool or type(result.atom.ordinal) is not int or result.atom.ordinal < 1:
        _fail("ATOM_FIELD", "mandatory и ordinal атома имеют недопустимый тип или значение.")
    if type(result.mappings) is not tuple:
        _fail("MAPPINGS_TYPE", "mappings AtomResult должен быть tuple.")
    for label, values in (
        ("supported_aspects", result.supported_aspects),
        ("unconfirmed_aspects", result.unconfirmed_aspects),
        ("diagnostics", result.diagnostics),
    ):
        if type(values) is not tuple:
            _fail("ATOM_RESULT_TYPE", f"{label} AtomResult должен быть tuple.")
    if result.support_status is not None and not isinstance(result.support_status, SupportStatus):
        _fail("ATOM_STATUS", "support_status атома не входит в канонический enum.")


def _validate_confirmed_aspects(result: AtomResult, kb: KnowledgeBase) -> None:
    if not result.supported_aspects:
        return
    if not any(_has_positive_official(item, kb) for item in result.mappings):
        _fail(
            "ASPECT_WITHOUT_EVIDENCE",
            "Подтверждённый аспект требует положительное официальное evidence.",
        )


def _normalize_mapping_support(item: Mapping, kb: KnowledgeBase) -> Mapping:
    if item.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}:
        if not _has_positive_official(item, kb):
            return replace(
                item,
                support_status=SupportStatus.INSUFFICIENT_EVIDENCE,
                reason_ru=_DOWNGRADE_REASON,
            )
    elif item.support_status is SupportStatus.NOT_SUPPORTED:
        if not (_has_negative_official_direct(item, kb) or _has_version_conflict(item, kb)):
            return replace(
                item,
                support_status=SupportStatus.INSUFFICIENT_EVIDENCE,
                reason_ru=_DOWNGRADE_REASON,
            )
    return item


def _has_positive_official(item: Mapping, kb: KnowledgeBase) -> bool:
    return any(
        cited.provenance == "official"
        and cited.polarity is EvidencePolarity.POSITIVE
        and cited.strength in _POSITIVE_STRENGTHS
        and _evidence_relevant(cited, item.component_id, kb)
        for cited in _cited(item, kb)
    )


def _has_negative_official_direct(item: Mapping, kb: KnowledgeBase) -> bool:
    return any(
        cited.provenance == "official"
        and cited.polarity is EvidencePolarity.NEGATIVE
        and cited.strength is EvidenceStrength.DIRECT
        and _evidence_relevant(cited, item.component_id, kb)
        for cited in _cited(item, kb)
    )


def _has_version_conflict(item: Mapping, kb: KnowledgeBase) -> bool:
    return any(
        cited.provenance == "official"
        and cited.version_constraint != kb.release
        and _evidence_relevant(cited, item.component_id, kb)
        for cited in _cited(item, kb)
    )


def _cited(item: Mapping, kb: KnowledgeBase) -> tuple[Evidence, ...]:
    return tuple(kb.evidence[evidence_id] for evidence_id in item.evidence_ids)


def _evidence_relevant(evidence: Evidence, component_id: str, kb: KnowledgeBase) -> bool:
    if evidence.component_id == component_id:
        return True
    source = kb.sources.get(evidence.source_id)
    return source is not None and component_id in source.component_ids


def _canonical_status(mappings: tuple[Mapping, ...]) -> SupportStatus | None:
    statuses = {item.support_status for item in mappings}
    if not statuses:
        return None
    if SupportStatus.NOT_SUPPORTED in statuses:
        return SupportStatus.NOT_SUPPORTED
    if SupportStatus.INSUFFICIENT_EVIDENCE in statuses:
        return SupportStatus.INSUFFICIENT_EVIDENCE
    if SupportStatus.PARTIAL in statuses:
        return SupportStatus.PARTIAL
    if statuses == {SupportStatus.NOT_APPLICABLE}:
        return SupportStatus.NOT_APPLICABLE
    return SupportStatus.SUPPORTED


def _nonempty_string_violation(value: object, label: str, violations: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        violations.append(f"{label} должен быть непустой строкой.")


def _string_list_violations(value: object, label: str, violations: list[str]) -> None:
    if type(value) is not list:
        violations.append(f"{label} должен быть встроенным JSON array строк.")
        return
    values = cast(list[object], value)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        violations.append(f"{label} должен содержать только непустые строки.")
    if len(values) != len(set(item for item in values if isinstance(item, str))):
        violations.append(f"{label} не должен содержать дубли.")


def _enum_violation(
    value: object, enum_type: type, label: str, violations: list[str]
) -> None:
    allowed = {item.value for item in enum_type}
    if not isinstance(value, str) or value not in allowed:
        violations.append(f"{label} должен быть одним из {sorted(allowed)}.")


def _fail(code: str, message_ru: str) -> None:
    raise ValidationError(code, message_ru)
