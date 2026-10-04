"""Grounded many-to-many mapping with fail-closed implementation validation."""

from __future__ import annotations

from dataclasses import replace
import math
import re
import unicodedata
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
from reqmap.proposals import ProposalError
from reqmap.retrieval import retrieve


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
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_GENERIC_RUNTIME_OPERATIONS = frozenset(
    {
        "api",
        "rest api",
        "server",
        "servers",
        "instance",
        "instances",
    }
)
_SEMANTIC_BOILERPLATE = frozenset(
    {
        "включить",
        "выполнить",
        "вызвать",
        "действие",
        "изменение",
        "изменить",
        "компонент",
        "компонента",
        "конфигурацию",
        "конфигурация",
        "настроить",
        "настройка",
        "обязательство",
        "параметр",
        "параметры",
        "поддержать",
        "поддерживает",
        "применить",
        "проверяемое",
        "проверяемый",
        "реализовать",
        "реализует",
        "роль",
        "синтетическое",
        "установить",
    }
)


def map_atom(
    model: JsonModel,
    atom: AtomicClaim,
    candidates: tuple[Candidate, ...],
    kb: KnowledgeBase,
) -> AtomResult:
    """Map one exact atom with one semantic correction and local validation."""
    _validate_candidates(candidates, kb)
    base_payload = prepare_mapping(atom, candidates, kb)
    payload = base_payload
    violations: tuple[str, ...] = ()

    for _attempt in range(2):
        try:
            response = model.complete_json("mapping", MAPPING_PROMPT, payload)
            invalid_response: object = response
            try:
                return accept_mapping(atom, candidates, kb, response)
            except ProposalError as exc:
                violations = exc.violations
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
    if type(result.analysis_state) is not AnalysisState:
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
    atom_candidates = retrieve(
        kb,
        result.atom.text,
        (),
        max(1, len(kb.capabilities)),
    )
    normalized: list[Mapping] = []
    downgraded = False
    for ordinal, item in enumerate(result.mappings, start=1):
        if type(item) is not Mapping:
            _fail("MAPPING_TYPE", "mappings должен содержать только canonical Mapping.")
        _validate_mapping_record(item, result.atom, ordinal, kb)
        replacement = _normalize_mapping_support(item, kb, atom_candidates)
        if replacement.support_status is not item.support_status:
            downgraded = True
        normalized.append(replacement)
    _validate_host_bundle(result.mappings, kb)

    original_status = _canonical_status(result.mappings)
    _validate_top_level_semantics(result, original_status)
    grounded_roles = {
        item.role_ru
        for item in normalized
        if item.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}
        and _has_positive_official(item, kb)
        and _mapping_grounded(item, kb, atom_candidates)
    }
    supported_aspects = tuple(
        aspect for aspect in result.supported_aspects if aspect in grounded_roles
    )
    unproved_aspects = tuple(
        aspect for aspect in result.supported_aspects if aspect not in grounded_roles
    )
    unconfirmed_aspects = tuple(
        dict.fromkeys((*result.unconfirmed_aspects, *unproved_aspects))
    )
    if (
        result.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}
        and result.supported_aspects
        and not supported_aspects
    ):
        normalized = [
            _downgrade_mapping(item)
            if item.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}
            else item
            for item in normalized
        ]
        downgraded = True

    normalized_status = _canonical_status(tuple(normalized))
    if not normalized and result.support_status in {
        SupportStatus.INSUFFICIENT_EVIDENCE,
        SupportStatus.NOT_APPLICABLE,
    }:
        normalized_status = result.support_status
    if normalized_status is SupportStatus.PARTIAL and (
        not supported_aspects or not unconfirmed_aspects
    ):
        _fail(
            "PARTIAL_ASPECTS",
            "Статус partial требует связанные supported_aspects и непустые unconfirmed_aspects.",
        )

    diagnostics = result.diagnostics
    if downgraded or unproved_aspects:
        diagnostics = tuple(dict.fromkeys((*result.diagnostics, _DOWNGRADE_REASON)))

    normalized_result = replace(
        result,
        support_status=normalized_status,
        mappings=tuple(normalized),
        supported_aspects=supported_aspects,
        unconfirmed_aspects=unconfirmed_aspects,
        diagnostics=diagnostics,
    )
    _validate_confirmed_aspects(normalized_result, kb, atom_candidates)
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
    if type(candidates) is not tuple:
        _fail("CANDIDATES_TYPE", "candidates должен быть встроенным tuple Candidate records.")
    for item in candidates:
        if type(item) is not Candidate:
            _fail("CANDIDATE_TYPE", "candidates должен содержать только canonical Candidate.")
        if type(item.component_id) is not str or not item.component_id:
            _fail("CANDIDATE_COMPONENT_UNKNOWN", "component_id кандидата должен быть built-in str.")
        if item.component_id not in kb.components:
            _fail(
                "CANDIDATE_COMPONENT_UNKNOWN",
                f"Кандидат ссылается на неизвестный компонент: {item.component_id}",
            )
        if item.capability_id is not None and (
            type(item.capability_id) is not str or not item.capability_id
        ):
            _fail("CANDIDATE_CAPABILITY_UNKNOWN", "capability_id кандидата должен быть built-in str или null.")
        if item.capability_id is not None and item.capability_id not in kb.capabilities:
            _fail(
                "CANDIDATE_CAPABILITY_UNKNOWN",
                f"Кандидат ссылается на неизвестную capability: {item.capability_id}",
            )
        if item.capability_id is not None and (
            kb.capabilities[item.capability_id].component_id != item.component_id
        ):
            _fail(
                "CANDIDATE_CAPABILITY_OWNERSHIP",
                "Capability кандидата принадлежит другому компоненту.",
            )
        if type(item.evidence_ids) is not tuple:
            _fail("CANDIDATE_EVIDENCE_TYPE", "evidence_ids кандидата должен быть tuple built-in str.")
        if any(type(evidence_id) is not str or not evidence_id for evidence_id in item.evidence_ids):
            _fail("CANDIDATE_EVIDENCE_TYPE", "evidence_ids кандидата должен содержать built-in str.")
        if len(item.evidence_ids) != len(set(item.evidence_ids)):
            _fail("CANDIDATE_EVIDENCE_DUPLICATE", "evidence_ids кандидата не должен содержать дубли.")
        if type(item.score) not in {int, float} or not math.isfinite(item.score):
            _fail("CANDIDATE_SCORE", "score кандидата должен быть конечным built-in числом.")
        if type(item.reasons) is not tuple or any(
            type(reason) is not str or not reason for reason in item.reasons
        ):
            _fail("CANDIDATE_REASONS", "reasons кандидата должен быть tuple built-in str.")
        for evidence_id in item.evidence_ids:
            if evidence_id not in kb.evidence:
                _fail(
                    "CANDIDATE_EVIDENCE_UNKNOWN",
                    f"Кандидат ссылается на неизвестный evidence: {evidence_id}",
                )
            if not _candidate_evidence_owned(item, kb.evidence[evidence_id], kb):
                _fail(
                    "CANDIDATE_EVIDENCE_OWNERSHIP",
                    f"Evidence {evidence_id} не принадлежит candidate component/capability.",
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
                if value is not None and (type(value) is not str or not value.strip()):
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
    allowed_evidence: dict[str, set[str]] = {}
    for candidate in candidates:
        allowed_evidence.setdefault(candidate.component_id, set()).update(candidate.evidence_ids)
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
            if evidence_id not in allowed_evidence.get(component_id, set()):
                violations.append(
                    f"Mapping {index}: evidence {evidence_id} отсутствует в evidence кандидата {component_id}."
                )
            elif not _mapping_evidence_relevant(kb.evidence[evidence_id], component_id, kb):
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
    if type(item.mapping_id) is not str or not item.mapping_id:
        _fail("MAPPING_ID", "mapping_id должен быть непустой строкой.")
    if item.mapping_id != expected_id:
        _fail("MAPPING_ID", f"Нестабильный mapping_id: ожидался {expected_id}.")
    if type(item.atom_id) is not str or not item.atom_id:
        _fail("MAPPING_ATOM_UNKNOWN", "atom_id mapping должен быть непустой строкой.")
    if item.atom_id != atom.atom_id:
        _fail("MAPPING_ATOM_UNKNOWN", "Mapping ссылается не на текущий atom.")
    if type(item.component_id) is not str or not item.component_id:
        _fail("UNKNOWN_COMPONENT", "component_id mapping должен быть непустой строкой.")
    component = kb.components.get(item.component_id)
    if component is None:
        _fail("UNKNOWN_COMPONENT", f"Неизвестный компонент: {item.component_id}")
    if type(item.role_ru) is not str or not item.role_ru.strip():
        _fail("MAPPING_ROLE", "role_ru mapping должен быть непустой строкой.")
    if type(item.mechanism) is not str or not item.mechanism.strip():
        _fail("MAPPING_MECHANISM", "mechanism mapping должен быть непустой строкой.")
    if type(item.reason_ru) is not str or not item.reason_ru.strip():
        _fail("MAPPING_REASON", "reason_ru mapping должен быть непустой строкой.")
    if type(item.relation) is not RelationType:
        _fail("MAPPING_RELATION", "relation mapping не входит в канонический enum.")
    if type(item.phase) is not Phase:
        _fail("MAPPING_PHASE", "phase mapping не входит в канонический enum.")
    if type(item.implementation_source) is not ImplementationSource:
        _fail("MAPPING_SOURCE", "implementation_source не входит в канонический enum.")
    if type(item.support_status) is not SupportStatus:
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
        if type(evidence_id) is not str or not evidence_id:
            _fail("UNKNOWN_EVIDENCE", "evidence_id должен быть непустой строкой.")
    if len(item.evidence_ids) != len(set(item.evidence_ids)):
        _fail("EVIDENCE_DUPLICATE", "evidence_ids mapping не должен содержать дубли.")
    for evidence_id in item.evidence_ids:
        cited = kb.evidence.get(evidence_id)
        if cited is None:
            _fail("UNKNOWN_EVIDENCE", f"Неизвестный evidence: {evidence_id}")
        if not _mapping_evidence_relevant(cited, item.component_id, kb):
            _fail(
                "EVIDENCE_COMPONENT_MISMATCH",
                f"Evidence {evidence_id} нерелевантно компоненту {item.component_id}.",
            )
    _validate_implementation(item, component.kind)


def _validate_step(step: ImplementationStep, item: Mapping, ordinal: int) -> None:
    if type(step.order) is not int or step.order != ordinal:
        _fail("STEP_ORDER", "Порядок implementation steps должен быть последовательным с единицы.")
    if step.phase is not item.phase:
        _fail("STEP_PHASE_MISMATCH", "Фаза шага не совпадает с фазой mapping.")
    if type(step.action_ru) is not str or not step.action_ru.strip():
        _fail("STEP_ACTION", "action_ru implementation step должен быть непустой строкой.")
    if type(step.mechanism) is not str or not step.mechanism.strip():
        _fail("STEP_MECHANISM", "mechanism implementation step должен быть непустой строкой.")
    for code, value in (("STEP_COMMAND", step.command), ("STEP_API_OPERATION", step.api_operation)):
        if value is not None and (type(value) is not str or not value.strip()):
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
        if any(step.mechanism != "openstack_api" for step in item.steps):
            _fail(
                "RUNTIME_STEP_MECHANISM",
                "Каждый runtime step обязан иметь mechanism openstack_api.",
            )
        if not any(
            type(step.api_operation) is str and bool(step.api_operation.strip())
            for step in item.steps
        ):
            _fail(
                "RUNTIME_API_OPERATION",
                "Runtime mapping обязан содержать непустой api_operation.",
            )
    else:
        if item.implementation_source is not ImplementationSource.KOLLA_ANSIBLE:
            _fail(
                "DESIGNTIME_SOURCE",
                "Design-time mapping обязан иметь implementation_source kolla_ansible.",
            )
        if item.mechanism != "kolla_ansible":
            _fail(
                "DESIGNTIME_MECHANISM",
                "Design-time mapping обязан иметь mechanism kolla_ansible.",
            )
        if any(step.api_operation is not None for step in item.steps):
            _fail("DESIGNTIME_API_OPERATION", "Design-time steps обязаны иметь api_operation=null.")
        if any(
            step.command not in {None, "kolla-ansible reconfigure"}
            for step in item.steps
        ):
            _fail(
                "DESIGNTIME_COMMAND",
                "Design-time command допускает только null или точную kolla-ansible reconfigure.",
            )
        reconfigure_steps = tuple(
            step for step in item.steps if step.command == "kolla-ansible reconfigure"
        )
        if any(step.mechanism != "kolla_ansible" for step in reconfigure_steps):
            _fail(
                "DESIGNTIME_RECONFIGURE_MECHANISM",
                "Step kolla-ansible reconfigure обязан иметь mechanism kolla_ansible.",
            )
        if item.component_id != "kolla_ansible" and not any(
            step.command is None and step.mechanism != "kolla_ansible"
            for step in item.steps
        ):
            _fail(
                "DESIGNTIME_CONFIG_MECHANISM",
                "Design-time mapping компонента требует отдельный non-delivery config mechanism.",
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
        if any(type(value) is not str or not value.strip() for value in values):
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
        if type(value) is not str or not value.strip():
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
    if any(
        type(message) is not str or not message.strip()
        for message in result.diagnostics
    ):
        _fail(
            "ATOM_RESULT_DIAGNOSTICS",
            "diagnostics AtomResult должен содержать только непустые built-in строки.",
        )
    if result.support_status is not None and type(result.support_status) is not SupportStatus:
        _fail("ATOM_STATUS", "support_status атома не входит в канонический enum.")


def _validate_confirmed_aspects(
    result: AtomResult,
    kb: KnowledgeBase,
    atom_candidates: tuple[Candidate, ...],
) -> None:
    grounded_roles = {
        item.role_ru
        for item in result.mappings
        if item.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}
        and _has_positive_official(item, kb)
        and _mapping_grounded(item, kb, atom_candidates)
    }
    if any(aspect not in grounded_roles for aspect in result.supported_aspects):
        _fail(
            "ASPECT_WITHOUT_EVIDENCE",
            "Каждый supported_aspect обязан точно совпадать с role_ru grounded mapping.",
        )


def _normalize_mapping_support(
    item: Mapping,
    kb: KnowledgeBase,
    atom_candidates: tuple[Candidate, ...],
) -> Mapping:
    if item.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}:
        if not (
            _has_positive_official(item, kb)
            and _mapping_grounded(item, kb, atom_candidates)
        ):
            return _downgrade_mapping(item)
    elif item.support_status is SupportStatus.NOT_SUPPORTED:
        if not (
            (_has_negative_official_direct(item, kb) or _has_version_conflict(item, kb))
            and _mapping_grounded(item, kb, atom_candidates)
        ):
            return _downgrade_mapping(item)
    return item


def _downgrade_mapping(item: Mapping) -> Mapping:
    return replace(
        item,
        support_status=SupportStatus.INSUFFICIENT_EVIDENCE,
        reason_ru=_DOWNGRADE_REASON,
    )


def _has_positive_official(item: Mapping, kb: KnowledgeBase) -> bool:
    return any(
        cited.provenance == "official"
        and cited.polarity is EvidencePolarity.POSITIVE
        and cited.strength in _POSITIVE_STRENGTHS
        and cited.version_constraint == kb.release
        and _official_owned(cited, item.component_id, kb)
        for cited in _cited(item, kb)
    )


def _has_negative_official_direct(item: Mapping, kb: KnowledgeBase) -> bool:
    return any(
        cited.provenance == "official"
        and cited.polarity is EvidencePolarity.NEGATIVE
        and cited.strength is EvidenceStrength.DIRECT
        and _official_owned(cited, item.component_id, kb)
        for cited in _cited(item, kb)
    )


def _has_version_conflict(item: Mapping, kb: KnowledgeBase) -> bool:
    return any(
        cited.provenance == "official"
        and cited.version_constraint != kb.release
        and _official_owned(cited, item.component_id, kb)
        for cited in _cited(item, kb)
    )


def _cited(item: Mapping, kb: KnowledgeBase) -> tuple[Evidence, ...]:
    return tuple(kb.evidence[evidence_id] for evidence_id in item.evidence_ids)


def _official_owned(evidence: Evidence, component_id: str, kb: KnowledgeBase) -> bool:
    capability = kb.capabilities.get(evidence.capability_id)
    return (
        evidence.provenance == "official"
        and evidence.component_id == component_id
        and capability is not None
        and capability.component_id == component_id
    )


def _mapping_grounded(
    item: Mapping,
    kb: KnowledgeBase,
    atom_candidates: tuple[Candidate, ...],
) -> bool:
    if not _atom_mapping_grounded(item, kb, atom_candidates):
        return False

    corpus = _official_component_corpus(item, kb)
    if not corpus:
        return False
    if item.phase is Phase.RUNTIME:
        return (
            all(
                type(step.api_operation) is str
                and bool(step.api_operation.strip())
                and _runtime_operation_grounded(step.api_operation, corpus)
                and _semantic_text_grounded(step.action_ru, corpus)
                for step in item.steps
            )
            and _semantic_text_grounded(item.role_ru, corpus)
        )

    if item.component_id == "kolla_ansible":
        return (
            all(
                step.command is None
                or _phrase_in_corpus(step.command, corpus)
                for step in item.steps
            )
            and _semantic_text_grounded(item.role_ru, corpus)
            and all(_semantic_text_grounded(step.action_ru, corpus) for step in item.steps)
        )

    config_steps = tuple(
        step
        for step in item.steps
        if step.command is None and step.mechanism != "kolla_ansible"
    )
    return (
        bool(config_steps)
        and all(_phrase_in_corpus(step.mechanism, corpus) for step in config_steps)
        and _semantic_text_grounded(item.role_ru, corpus)
        and all(_semantic_text_grounded(step.action_ru, corpus) for step in config_steps)
    )


def _official_component_corpus(item: Mapping, kb: KnowledgeBase) -> tuple[str, ...]:
    records: list[str] = []
    seen_capabilities: set[str] = set()
    for cited in _cited(item, kb):
        if not _official_owned(cited, item.component_id, kb):
            continue
        records.extend((cited.claim_ru, cited.locator))
        if cited.capability_id in seen_capabilities:
            continue
        seen_capabilities.add(cited.capability_id)
        capability = kb.capabilities[cited.capability_id]
        records.extend((capability.name_ru, *capability.terms))
    return tuple(records)


def _phrase_in_corpus(phrase: str, corpus: tuple[str, ...]) -> bool:
    normalized_phrase = _normalized_phrase(phrase)
    if not normalized_phrase:
        return False
    needle = f" {normalized_phrase} "
    return any(needle in f" {_normalized_phrase(record)} " for record in corpus)


def _runtime_operation_grounded(operation: str, corpus: tuple[str, ...]) -> bool:
    normalized = _normalized_phrase(operation)
    return (
        normalized not in _GENERIC_RUNTIME_OPERATIONS
        and _phrase_in_corpus(operation, corpus)
    )


def _semantic_text_grounded(value: str, corpus: tuple[str, ...]) -> bool:
    significant_terms = tuple(
        term
        for term in _normalized_phrase(value).split()
        if term not in _SEMANTIC_BOILERPLATE
    )
    return bool(significant_terms) and all(
        _phrase_in_corpus(term, corpus) for term in significant_terms
    )


def _atom_mapping_grounded(
    item: Mapping,
    kb: KnowledgeBase,
    atom_candidates: tuple[Candidate, ...],
) -> bool:
    owned_official_ids = {
        evidence_id
        for evidence_id in item.evidence_ids
        if _official_owned(kb.evidence[evidence_id], item.component_id, kb)
    }
    return bool(owned_official_ids) and any(
        candidate.component_id == item.component_id
        and bool(owned_official_ids.intersection(candidate.evidence_ids))
        for candidate in atom_candidates
    )


def _normalized_phrase(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(_WORD.findall(normalized.replace("_", " ")))


def _candidate_evidence_owned(
    candidate: Candidate, evidence: Evidence, kb: KnowledgeBase
) -> bool:
    capability = kb.capabilities.get(evidence.capability_id)
    if capability is None or capability.component_id != evidence.component_id:
        return False
    if evidence.provenance == "official":
        return (
            evidence.component_id == candidate.component_id
            and evidence.capability_id == candidate.capability_id
        )
    if evidence.provenance != "project_policy":
        return False
    if (
        evidence.component_id == candidate.component_id
        and evidence.capability_id == candidate.capability_id
    ):
        return True
    source = kb.sources.get(evidence.source_id)
    return (
        candidate.component_id == "kolla_ansible"
        and source is not None
        and "kolla_ansible" in source.component_ids
    )


def _mapping_evidence_relevant(
    evidence: Evidence, component_id: str, kb: KnowledgeBase
) -> bool:
    capability = kb.capabilities.get(evidence.capability_id)
    if capability is None or capability.component_id != evidence.component_id:
        return False
    if evidence.component_id == component_id:
        return True
    if evidence.provenance != "project_policy" or component_id != "kolla_ansible":
        return False
    source = kb.sources.get(evidence.source_id)
    return source is not None and "kolla_ansible" in source.component_ids


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
    if type(value) is not str or not value.strip():
        violations.append(f"{label} должен быть непустой строкой.")


def _string_list_violations(value: object, label: str, violations: list[str]) -> None:
    if type(value) is not list:
        violations.append(f"{label} должен быть встроенным JSON array строк.")
        return
    values = cast(list[object], value)
    if any(type(item) is not str or not item.strip() for item in values):
        violations.append(f"{label} должен содержать только непустые строки.")
    exact_strings = [item for item in values if type(item) is str]
    if len(exact_strings) != len(set(exact_strings)):
        violations.append(f"{label} не должен содержать дубли.")


def _enum_violation(
    value: object, enum_type: type, label: str, violations: list[str]
) -> None:
    allowed = {item.value for item in enum_type}
    if type(value) is not str or value not in allowed:
        violations.append(f"{label} должен быть одним из {sorted(allowed)}.")


def _fail(code: str, message_ru: str) -> None:
    raise ValidationError(code, message_ru)


def prepare_mapping(atom: AtomicClaim, candidates: tuple[Candidate, ...], kb: KnowledgeBase) -> dict[str, object]:
    """Prepare a bounded candidate context without a model call."""
    _validate_candidates(candidates, kb)
    return _mapping_payload(atom, candidates, kb)


def accept_mapping(atom: AtomicClaim, candidates: tuple[Candidate, ...], kb: KnowledgeBase, proposal: object) -> AtomResult:
    """Apply the same reference and evidence gate to one proposal."""
    _validate_candidates(candidates, kb)
    if type(proposal) is not dict:
        raise ProposalError("shape", ("Ответ модели должен быть встроенным dict JSON object верхнего уровня.",))
    violations = _response_violations(proposal)
    if violations:
        raise ProposalError("shape", violations)
    violations = _candidate_reference_violations(proposal, candidates, kb)
    if violations:
        raise ProposalError("semantic", violations)
    try:
        return validate_atom_result(_build_result(atom, proposal), kb)
    except ValidationError as exc:
        raise ProposalError("semantic", (exc.message_ru,)) from exc
