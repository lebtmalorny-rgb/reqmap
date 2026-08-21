"""Fail-closed contracts for semantic many-to-many mapping."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping as MappingABC
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from reqmap.errors import ModelError, ModelOutputError, ValidationError
from reqmap.knowledge import ComponentRecord, KnowledgeBase, SourceRecord, load_knowledge
from reqmap.mapping import map_atom, validate_atom_result
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
)
from reqmap.prompts import MAPPING_PROMPT, PROMPT_MAPPING_VERSION


class FakeModel:
    def __init__(self, responses: list[object]) -> None:
        self._responses: deque[object] = deque(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def complete_json(
        self, stage: str, system_prompt: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((stage, system_prompt, payload))
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]


def atom(text: str = "Создать виртуальную машину через API") -> AtomicClaim:
    return AtomicClaim(
        atom_id="REQ-0001-A001",
        requirement_id="REQ-0001",
        text=text,
        source_quote=text,
        mandatory=True,
        ordinal=1,
    )


def evidence(
    evidence_id: str,
    component_id: str,
    source_id: str,
    *,
    polarity: EvidencePolarity = EvidencePolarity.POSITIVE,
    strength: EvidenceStrength = EvidenceStrength.DIRECT,
    provenance: str = "official",
    version: str = "2025.1",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        component_id=component_id,
        capability_id=f"CAP-{component_id.upper()}",
        polarity=polarity,
        strength=strength,
        claim_ru=f"Синтетическое доказательство {evidence_id}",
        source_id=source_id,
        locator="synthetic section",
        version_constraint=version,
        source_url=None if provenance == "project_policy" else "https://example.invalid/doc",
        local_path=f"sources/{source_id}.md",
        source_sha256="a" * 64,
        retrieved_at="2026-08-20",
        provenance=provenance,
    )


@pytest.fixture
def kb() -> KnowledgeBase:
    components = {
        "nova": ComponentRecord("nova", "Nova", "openstack_service", "2025.1"),
        "neutron": ComponentRecord("neutron", "Neutron", "openstack_service", "2025.1"),
        "kolla_ansible": ComponentRecord(
            "kolla_ansible", "Kolla-Ansible", "deployment_tool", "2025.1"
        ),
        "host_os_kernel_sysctl": ComponentRecord(
            "host_os_kernel_sysctl", "Host OS sysctl", "host_os_subsystem", "2025.1"
        ),
        "host_os_chrony": ComponentRecord(
            "host_os_chrony", "Host OS Chrony", "host_os_subsystem", "2025.1"
        ),
        "host_os_nftables": ComponentRecord(
            "host_os_nftables", "Host OS nftables", "host_os_subsystem", "2025.1"
        ),
    }
    sources = {
        component_id: SourceRecord(
            source_id=f"SRC-{component_id}",
            component_ids=(component_id,),
            source_url="https://example.invalid/doc",
            retrieved_at="2026-08-20",
            version="2025.1",
            sha256="a" * 64,
            local_path=f"sources/{component_id}.md",
            provenance="official",
        )
        for component_id in components
    }
    sources["policy"] = SourceRecord(
        source_id="SRC-policy",
        component_ids=("nova",),
        source_url=None,
        retrieved_at="2026-08-20",
        version="2025.1",
        sha256="a" * 64,
        local_path="sources/policy.md",
        provenance="project_policy",
    )
    items = {
        "E-NOVA": evidence("E-NOVA", "nova", "SRC-nova"),
        "E-NEUTRON": evidence(
            "E-NEUTRON", "neutron", "SRC-neutron", strength=EvidenceStrength.INDIRECT
        ),
        "E-KOLLA": evidence("E-KOLLA", "kolla_ansible", "SRC-kolla_ansible"),
        "E-SYSCTL": evidence(
            "E-SYSCTL", "host_os_kernel_sysctl", "SRC-host_os_kernel_sysctl"
        ),
        "E-CHRONY-NEG": evidence(
            "E-CHRONY-NEG",
            "host_os_chrony",
            "SRC-host_os_chrony",
            polarity=EvidencePolarity.NEGATIVE,
        ),
        "E-NFTABLES-NEG": evidence(
            "E-NFTABLES-NEG",
            "host_os_nftables",
            "SRC-host_os_nftables",
            polarity=EvidencePolarity.NEGATIVE,
        ),
        "E-POLICY": evidence(
            "E-POLICY", "nova", "SRC-policy", provenance="project_policy"
        ),
    }
    return KnowledgeBase(
        root=Path("synthetic"),
        release="2025.1",
        snapshot_sha256="b" * 64,
        components=MappingProxyType(components),
        capabilities=MappingProxyType({}),
        evidence=MappingProxyType(items),
        sources=MappingProxyType(sources),
        synonyms=MappingProxyType({}),
    )


def candidate(component_id: str, *evidence_ids: str, reasons: tuple[str, ...] = ()) -> Candidate:
    return Candidate(component_id, None, tuple(evidence_ids), 10.0, reasons)


def step(
    phase: str,
    *,
    command: str | None = None,
    api_operation: str | None = None,
) -> dict[str, object]:
    return {
        "action_ru": "Выполнить синтетическое действие",
        "mechanism": "openstack_api" if phase == "runtime" else "kolla_ansible",
        "command": command,
        "api_operation": api_operation,
    }


def raw_mapping(
    component_id: str = "nova",
    evidence_ids: tuple[str, ...] = ("E-NOVA",),
    *,
    phase: str = "runtime",
    relation: str = "implements",
    implementation_source: str = "upstream",
    mechanism: str | None = None,
    support_status: str = "supported",
    steps: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if steps is None:
        steps = [
            step(
                phase,
                command="kolla-ansible reconfigure" if phase == "designtime" else None,
                api_operation="POST /servers" if phase == "runtime" else None,
            )
        ]
    return {
        "component_id": component_id,
        "role_ru": "Реализует проверяемое обязательство",
        "relation": relation,
        "phase": phase,
        "implementation_source": implementation_source,
        "mechanism": mechanism or ("openstack_api" if phase == "runtime" else "kolla_ansible"),
        "steps": steps,
        "evidence_ids": list(evidence_ids),
        "support_status": support_status,
        "reason_ru": "Вывод подтверждён переданным evidence.",
    }


def response(
    *mappings: dict[str, object],
    status: str = "supported",
    supported_aspects: tuple[str, ...] = (),
    unconfirmed_aspects: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "support_status": status,
        "supported_aspects": list(supported_aspects),
        "unconfirmed_aspects": list(unconfirmed_aspects),
        "mappings": list(mappings),
    }


def canonical_mapping(
    *,
    component_id: str = "nova",
    evidence_ids: tuple[str, ...] = ("E-NOVA",),
    support_status: SupportStatus = SupportStatus.SUPPORTED,
    phase: Phase = Phase.RUNTIME,
    relation: RelationType = RelationType.IMPLEMENTS,
    source: ImplementationSource = ImplementationSource.UPSTREAM,
    mechanism: str | None = None,
    command: str | None = None,
    api_operation: str | None = "POST /servers",
) -> Mapping:
    return Mapping(
        mapping_id="REQ-0001-A001-M001",
        atom_id="REQ-0001-A001",
        component_id=component_id,
        role_ru="Роль компонента",
        relation=relation,
        phase=phase,
        implementation_source=source,
        mechanism=mechanism or ("openstack_api" if phase is Phase.RUNTIME else "kolla_ansible"),
        steps=(
            ImplementationStep(
                order=1,
                phase=phase,
                action_ru="Выполнить действие",
                mechanism="openstack_api" if phase is Phase.RUNTIME else "kolla_ansible",
                command=command if phase is Phase.DESIGNTIME else None,
                api_operation=api_operation if phase is Phase.RUNTIME else None,
            ),
        ),
        evidence_ids=evidence_ids,
        support_status=support_status,
        reason_ru="Причина на русском.",
    )


def result(mapping: Mapping, *, status: SupportStatus | None = None) -> AtomResult:
    return AtomResult(
        atom=atom(),
        analysis_state=AnalysisState.COMPLETED,
        support_status=status or mapping.support_status,
        mappings=(mapping,),
    )


def test_one_atom_maps_to_multiple_components_with_stable_ids_and_step_order(kb) -> None:
    model = FakeModel(
        [response(raw_mapping(), raw_mapping("neutron", ("E-NEUTRON",)))]
    )

    mapped = map_atom(
        model,
        atom(),
        (candidate("nova", "E-NOVA"), candidate("neutron", "E-NEUTRON")),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert [item.mapping_id for item in mapped.mappings] == [
        "REQ-0001-A001-M001",
        "REQ-0001-A001-M002",
    ]
    assert [item.component_id for item in mapped.mappings] == ["nova", "neutron"]
    assert [item.steps[0].order for item in mapped.mappings] == [1, 1]


def test_mixed_response_keeps_separate_runtime_and_designtime_records(kb) -> None:
    model = FakeModel(
        [response(raw_mapping(), raw_mapping(phase="designtime", implementation_source="kolla_ansible"))]
    )

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert [item.phase for item in mapped.mappings] == [Phase.RUNTIME, Phase.DESIGNTIME]


def test_mapping_prompt_contains_only_selected_full_evidence_and_marks_hint_untrusted(kb) -> None:
    model = FakeModel([response(raw_mapping())])
    candidates = (
        candidate("nova", "E-NOVA", reasons=("source_hint", "exact_identifier")),
    )

    map_atom(model, atom(), candidates, kb)

    payload = model.calls[0][2]
    assert payload["atom"] == {
        "atom_id": "REQ-0001-A001",
        "requirement_id": "REQ-0001",
        "text": "Создать виртуальную машину через API",
        "source_quote": "Создать виртуальную машину через API",
        "mandatory": True,
        "ordinal": 1,
    }
    assert [item["evidence_id"] for item in payload["evidence"]] == ["E-NOVA"]  # type: ignore[index]
    assert "source_hint" in str(payload["candidates"])
    assert "неавторитет" in str(payload["candidate_reason_policy"]).lower()
    assert model.calls[0][0:2] == ("mapping", MAPPING_PROMPT)


@pytest.mark.parametrize(
    "bad_response",
    [
        {},
        {"support_status": "supported", "supported_aspects": [], "unconfirmed_aspects": [], "mappings": "x"},
        response({**raw_mapping(), "extra": True}),
        response({**raw_mapping(), "relation": "unknown"}),
        response({**raw_mapping(), "role_ru": " "}),
        response({**raw_mapping(), "evidence_ids": ["E-NOVA", 1]}),
        response({**raw_mapping(), "steps": []}),
        response({**raw_mapping(), "steps": [{**step("runtime", api_operation="POST /servers"), "extra": 1}]}),
        response({**raw_mapping(), "steps": [{**step("runtime", api_operation="POST /servers"), "command": 1}]}),
    ],
)
def test_strict_schema_gets_exactly_one_correction_then_model_failed(kb, bad_response) -> None:
    model = FakeModel([bad_response, bad_response])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert mapped.support_status is None
    assert mapped.mappings == ()
    assert mapped.diagnostics and len(model.calls) == 2
    assert model.calls[1][2]["invalid_response"] == bad_response
    assert model.calls[1][2]["violations_ru"]


class DivergentMapping(MappingABC[str, object]):
    def __iter__(self) -> Any:
        return iter(("support_status", "supported_aspects", "unconfirmed_aspects", "mappings"))

    def __len__(self) -> int:
        return 4

    def __getitem__(self, key: str) -> object:
        return response(raw_mapping())[key]

    def get(self, key: str, default: object = None) -> object:
        return response(raw_mapping())[key]


def test_non_builtin_mapping_never_reaches_canonical_builder(kb) -> None:
    model = FakeModel([DivergentMapping(), DivergentMapping()])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert mapped.mappings == ()
    assert "встроенным dict" in mapped.diagnostics[0]


def test_correction_may_return_valid_response(kb) -> None:
    invalid = response({**raw_mapping(), "component_id": "swift"})
    valid = response(raw_mapping())
    model = FakeModel([invalid, valid])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert [item.component_id for item in mapped.mappings] == ["nova"]
    assert len(model.calls) == 2
    assert "кандидат" in str(model.calls[1][2]["violations_ru"]).lower()


def test_model_output_error_is_corrected_once_and_second_failure_is_model_failed(kb) -> None:
    error = ModelOutputError("MODEL_OUTPUT_INVALID", "Ответ модели некорректен.", "{bad")
    model = FakeModel([error, error])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert mapped.support_status is None
    assert mapped.mappings == ()
    assert mapped.diagnostics == ("Ответ модели некорректен.",)
    assert model.calls[1][2]["invalid_response"] == {"raw_response": "{bad"}


def test_transport_error_propagates_without_semantic_correction(kb) -> None:
    model = FakeModel([ModelError("MODEL_TRANSPORT_ERROR", "Нет связи")])

    with pytest.raises(ModelError, match="Нет связи"):
        map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert len(model.calls) == 1


@pytest.mark.parametrize(
    ("response_mapping", "candidates", "diagnostic"),
    [
        (raw_mapping("swift"), (candidate("nova", "E-NOVA"),), "кандидат"),
        (raw_mapping(evidence_ids=("E-NEUTRON",)), (candidate("nova", "E-NOVA"),), "evidence"),
        (raw_mapping(evidence_ids=("E-NEUTRON",)), (candidate("nova", "E-NOVA", "E-NEUTRON"),), "релевант"),
    ],
)
def test_off_candidate_or_cross_component_references_require_correction(
    kb, response_mapping, candidates, diagnostic
) -> None:
    model = FakeModel([response(response_mapping), response(response_mapping)])

    mapped = map_atom(model, atom(), candidates, kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert diagnostic in mapped.diagnostics[0].lower()


def test_runtime_requires_exact_mechanism_nonempty_api_operation_and_null_commands(kb) -> None:
    invalid_responses = [
        raw_mapping(mechanism="cli"),
        raw_mapping(steps=[step("runtime")]),
        raw_mapping(steps=[step("runtime", command="openstack server create", api_operation="POST /servers")]),
    ]
    for invalid in invalid_responses:
        model = FakeModel([response(invalid), response(invalid)])
        mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)
        assert mapped.analysis_state is AnalysisState.MODEL_FAILED


def test_designtime_requires_exact_reconfigure_command(kb) -> None:
    invalid = raw_mapping(
        phase="designtime",
        implementation_source="kolla_ansible",
        steps=[step("designtime", command="kolla-ansible reconfigure -i inventory")],
    )
    model = FakeModel([response(invalid), response(invalid)])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert "kolla-ansible reconfigure" in mapped.diagnostics[0]


def test_step_phase_is_canonical_mapping_phase_not_model_controlled(kb) -> None:
    mapping = canonical_mapping()
    wrong_step = replace(mapping.steps[0], phase=Phase.DESIGNTIME)

    with pytest.raises(ValidationError) as error:
        validate_atom_result(result(replace(mapping, steps=(wrong_step,))), kb)

    assert error.value.code == "STEP_PHASE_MISMATCH"


def test_host_change_requires_separate_kolla_and_host_subsystem_mappings(kb) -> None:
    host = raw_mapping(
        "host_os_kernel_sysctl",
        ("E-SYSCTL",),
        phase="designtime",
        relation="host_os_change",
        implementation_source="kolla_ansible",
    )
    for mappings in [(host,)]:
        candidates = tuple(
            candidate(item["component_id"], *item["evidence_ids"])  # type: ignore[arg-type]
            for item in mappings
        )
        model = FakeModel([response(*mappings), response(*mappings)])
        mapped = map_atom(model, atom("Изменить sysctl"), candidates, kb)
        assert mapped.analysis_state is AnalysisState.MODEL_FAILED
        assert "kolla" in mapped.diagnostics[0].lower() or "подсистем" in mapped.diagnostics[0].lower()


def test_standalone_kolla_mapping_is_valid_for_non_host_designtime_change(kb) -> None:
    kolla = raw_mapping(
        "kolla_ansible",
        ("E-KOLLA",),
        phase="designtime",
        implementation_source="kolla_ansible",
    )
    model = FakeModel([response(kolla)])

    mapped = map_atom(model, atom("Применить конфигурацию сервиса"), (candidate("kolla_ansible", "E-KOLLA"),), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.SUPPORTED


@pytest.mark.parametrize(
    ("phase", "relation", "source"),
    [
        ("runtime", "host_os_change", "kolla_ansible"),
        ("designtime", "configures", "kolla_ansible"),
        ("designtime", "host_os_change", "upstream"),
    ],
)
def test_host_subsystem_has_fixed_phase_relation_and_source(kb, phase, relation, source) -> None:
    host = raw_mapping(
        "host_os_kernel_sysctl",
        ("E-SYSCTL",),
        phase=phase,
        relation=relation,
        implementation_source=source,
    )
    kolla = raw_mapping(
        "kolla_ansible", ("E-KOLLA",), phase="designtime", implementation_source="kolla_ansible"
    )
    candidates = (
        candidate("host_os_kernel_sysctl", "E-SYSCTL"),
        candidate("kolla_ansible", "E-KOLLA"),
    )
    model = FakeModel([response(host, kolla), response(host, kolla)])

    mapped = map_atom(model, atom("Изменить sysctl"), candidates, kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED


def test_host_os_change_cannot_be_not_applicable(kb) -> None:
    host = raw_mapping(
        "host_os_kernel_sysctl",
        ("E-SYSCTL",),
        phase="designtime",
        relation="host_os_change",
        implementation_source="kolla_ansible",
        support_status="not_applicable",
    )
    kolla = raw_mapping(
        "kolla_ansible", ("E-KOLLA",), phase="designtime", implementation_source="kolla_ansible"
    )
    model = FakeModel([response(host, kolla), response(host, kolla)])

    mapped = map_atom(
        model,
        atom("Изменить sysctl"),
        (candidate("host_os_kernel_sysctl", "E-SYSCTL"), candidate("kolla_ansible", "E-KOLLA")),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED


def test_host_os_candidate_cannot_be_dismissed_as_not_applicable_without_mapping(kb) -> None:
    invalid = response(status="not_applicable")
    model = FakeModel([invalid, invalid])

    mapped = map_atom(
        model,
        atom("Изменить sysctl хостовой ОС"),
        (candidate("host_os_kernel_sysctl", "E-SYSCTL"),),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert "host os" in mapped.diagnostics[0].lower()


def test_valid_host_mapping_has_kolla_and_subsystem_with_reconfigure(kb) -> None:
    host = raw_mapping(
        "host_os_kernel_sysctl",
        ("E-SYSCTL",),
        phase="designtime",
        relation="host_os_change",
        implementation_source="kolla_ansible",
    )
    kolla = raw_mapping(
        "kolla_ansible", ("E-KOLLA",), phase="designtime", implementation_source="kolla_ansible"
    )
    model = FakeModel([response(host, kolla)])

    mapped = map_atom(
        model,
        atom("Изменить sysctl"),
        (candidate("host_os_kernel_sysctl", "E-SYSCTL"), candidate("kolla_ansible", "E-KOLLA")),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert [item.component_id for item in mapped.mappings] == [
        "host_os_kernel_sysctl",
        "kolla_ansible",
    ]
    assert all(item.phase is Phase.DESIGNTIME for item in mapped.mappings)


def test_project_policy_alone_downgrades_supported_without_correction(kb) -> None:
    model = FakeModel([response(raw_mapping(evidence_ids=("E-POLICY",)))])

    mapped = map_atom(model, atom(), (candidate("nova", "E-POLICY"),), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "официаль" in mapped.mappings[0].reason_ru.lower()
    assert len(model.calls) == 1


def test_positive_supported_without_evidence_downgrades_deterministically(kb) -> None:
    model = FakeModel([response(raw_mapping(evidence_ids=()))])

    mapped = map_atom(model, atom(), (candidate("nova"),), kb)

    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].evidence_ids == ()
    assert mapped.diagnostics == ("Статус понижен: отсутствует достаточное официальное evidence.",)


def test_unproved_confirmed_aspect_is_moved_to_unconfirmed_without_model_correction(kb) -> None:
    mapping = canonical_mapping(
        evidence_ids=(), support_status=SupportStatus.INSUFFICIENT_EVIDENCE
    )
    proposed = AtomResult(
        atom(),
        AnalysisState.COMPLETED,
        SupportStatus.INSUFFICIENT_EVIDENCE,
        (mapping,),
        supported_aspects=("Неподтверждённая возможность",),
    )

    validated = validate_atom_result(proposed, kb)

    assert validated.supported_aspects == ()
    assert validated.unconfirmed_aspects == ("Неподтверждённая возможность",)
    assert "официальное evidence" in validated.diagnostics[0]


def test_not_supported_requires_official_negative_direct_evidence(kb) -> None:
    unsupported = raw_mapping(support_status="not_supported")
    model = FakeModel([response(unsupported, status="not_supported")])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    ("component_id", "evidence_id"),
    [("host_os_chrony", "E-CHRONY-NEG"), ("host_os_nftables", "E-NFTABLES-NEG")],
)
def test_negative_chrony_and_nftables_evidence_preserves_not_supported(
    kb, component_id, evidence_id
) -> None:
    mapping = canonical_mapping(
        component_id=component_id,
        evidence_ids=(evidence_id,),
        support_status=SupportStatus.NOT_SUPPORTED,
        phase=Phase.DESIGNTIME,
        relation=RelationType.HOST_OS_CHANGE,
        source=ImplementationSource.KOLLA_ANSIBLE,
        command="kolla-ansible reconfigure",
        api_operation=None,
    )
    kolla = replace(
        canonical_mapping(
            component_id="kolla_ansible",
            evidence_ids=("E-KOLLA",),
            phase=Phase.DESIGNTIME,
            source=ImplementationSource.KOLLA_ANSIBLE,
            command="kolla-ansible reconfigure",
            api_operation=None,
        ),
        mapping_id="REQ-0001-A001-M002",
    )
    validated = validate_atom_result(
        AtomResult(atom(), AnalysisState.COMPLETED, SupportStatus.NOT_SUPPORTED, (mapping, kolla)),
        kb,
    )

    assert validated.support_status is SupportStatus.NOT_SUPPORTED
    assert validated.mappings[0].support_status is SupportStatus.NOT_SUPPORTED


def test_version_conflict_is_derived_from_evidence_not_model_boolean(kb) -> None:
    conflicting = replace(kb.evidence["E-NOVA"], version_constraint="2024.2")
    conflict_kb = replace(
        kb,
        evidence=MappingProxyType({**kb.evidence, "E-NOVA": conflicting}),
    )
    mapping = canonical_mapping(support_status=SupportStatus.NOT_SUPPORTED)

    validated = validate_atom_result(result(mapping), conflict_kb)

    assert validated.support_status is SupportStatus.NOT_SUPPORTED


def test_partial_requires_both_aspect_lists(kb) -> None:
    invalid = response(raw_mapping(support_status="partial"), status="partial")
    model = FakeModel([invalid, invalid])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert "partial" in mapped.diagnostics[0]


def test_partial_with_positive_official_evidence_preserves_aspects(kb) -> None:
    model = FakeModel(
        [
            response(
                raw_mapping(support_status="partial"),
                status="partial",
                supported_aspects=("Создание VM",),
                unconfirmed_aspects=("Расширенное планирование",),
            )
        ]
    )

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.support_status is SupportStatus.PARTIAL
    assert mapped.supported_aspects == ("Создание VM",)
    assert mapped.unconfirmed_aspects == ("Расширенное планирование",)


@pytest.mark.parametrize("status", ["supported", "partial", "not_supported"])
def test_no_mappings_cannot_claim_support_or_non_support(kb, status) -> None:
    aspects = ("x",) if status == "partial" else ()
    invalid = response(
        status=status,
        supported_aspects=aspects,
        unconfirmed_aspects=aspects,
    )
    model = FakeModel([invalid, invalid])

    mapped = map_atom(model, atom(), (), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED


def test_not_applicable_has_no_mappings_or_aspects(kb) -> None:
    model = FakeModel([response(status="not_applicable")])

    mapped = map_atom(model, atom("Внешнее организационное требование"), (), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.NOT_APPLICABLE
    assert mapped.mappings == ()


def test_top_level_status_must_match_canonical_mapping_status(kb) -> None:
    invalid = response(raw_mapping(), status="not_applicable")
    model = FakeModel([invalid, invalid])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert "итоговый" in mapped.diagnostics[0].lower() or "not_applicable" in mapped.diagnostics[0]


def test_public_validator_rejects_unknown_component_evidence_and_wrong_atom_reference(kb) -> None:
    base = canonical_mapping()
    cases = [
        replace(base, component_id="swift"),
        replace(base, evidence_ids=("E-UNKNOWN",)),
        replace(base, atom_id="REQ-9999-A999"),
    ]
    for invalid in cases:
        with pytest.raises(ValidationError):
            validate_atom_result(result(invalid), kb)


def test_public_validator_rejects_noncompleted_result_with_support(kb) -> None:
    invalid = replace(result(canonical_mapping()), analysis_state=AnalysisState.MODEL_FAILED)

    with pytest.raises(ValidationError) as error:
        validate_atom_result(invalid, kb)

    assert error.value.code == "ANALYSIS_STATE_STATUS"


def test_public_validator_rejects_forged_analysis_state_even_without_results(kb) -> None:
    invalid = AtomResult(atom(), "completed", None, ())  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as error:
        validate_atom_result(invalid, kb)

    assert error.value.code == "ANALYSIS_STATE"


def test_public_validator_converts_malformed_canonical_field_types_to_validation_error(kb) -> None:
    base = canonical_mapping()
    malformed = [
        replace(base, component_id=["nova"]),  # type: ignore[arg-type]
        replace(base, relation="implements"),  # type: ignore[arg-type]
        replace(base, evidence_ids=(["E-NOVA"],)),  # type: ignore[list-item]
        replace(base, steps=(replace(base.steps[0], phase="runtime"),)),  # type: ignore[arg-type]
    ]

    for item in malformed:
        with pytest.raises(ValidationError):
            validate_atom_result(result(item), kb)


def test_noncompleted_result_cannot_retain_confirmed_aspects(kb) -> None:
    invalid = AtomResult(
        atom(),
        AnalysisState.MODEL_FAILED,
        None,
        (),
        supported_aspects=("Неавторитетный остаток",),
    )

    with pytest.raises(ValidationError) as error:
        validate_atom_result(invalid, kb)

    assert error.value.code == "ANALYSIS_STATE_STATUS"


def test_real_epoxy_snapshot_supports_broad_nova_neutron_many_to_many_scope() -> None:
    real_kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    model = FakeModel(
        [
            response(
                raw_mapping("nova", ("E-NOVA-SCOPE-001",)),
                raw_mapping(
                    "neutron",
                    ("E-NEUTRON-SCOPE-001",),
                    steps=[step("runtime", api_operation="POST /v2.0/ports")],
                ),
            )
        ]
    )

    mapped = map_atom(
        model,
        atom("Создать VM и сетевой порт через API"),
        (
            candidate("nova", "E-NOVA-SCOPE-001"),
            candidate("neutron", "E-NEUTRON-SCOPE-001"),
        ),
        real_kb,
    )

    assert [item.component_id for item in mapped.mappings] == ["nova", "neutron"]
    assert mapped.support_status is SupportStatus.SUPPORTED


@pytest.mark.parametrize(
    ("component_id", "evidence_id"),
    [
        ("host_os_chrony", "E-HOST-CHRONY-OFFICIAL-001"),
        ("host_os_nftables", "E-HOST-NFTABLES-OFFICIAL-001"),
    ],
)
def test_real_epoxy_negative_host_boundaries_remain_not_supported(
    component_id: str, evidence_id: str
) -> None:
    real_kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    host = raw_mapping(
        component_id,
        (evidence_id,),
        phase="designtime",
        relation="host_os_change",
        implementation_source="kolla_ansible",
        support_status="not_supported",
    )
    kolla = raw_mapping(
        "kolla_ansible",
        ("E-KOLLA-RECONFIGURE-001",),
        phase="designtime",
        implementation_source="kolla_ansible",
    )
    model = FakeModel([response(host, kolla, status="not_supported")])

    mapped = map_atom(
        model,
        atom("Изменить подсистему host OS"),
        (
            candidate(component_id, evidence_id),
            candidate("kolla_ansible", "E-KOLLA-RECONFIGURE-001"),
        ),
        real_kb,
    )

    assert mapped.support_status is SupportStatus.NOT_SUPPORTED
    assert mapped.mappings[0].support_status is SupportStatus.NOT_SUPPORTED


def test_mapping_prompt_is_versioned_and_strictly_russian() -> None:
    assert PROMPT_MAPPING_VERSION == "1.0"
    assert "Верни только JSON" in MAPPING_PROMPT
    assert "source_hint" in MAPPING_PROMPT
    assert "не является evidence" in MAPPING_PROMPT
