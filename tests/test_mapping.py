"""Fail-closed contracts for semantic many-to-many mapping."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping as MappingABC
from dataclasses import replace
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from reqmap.errors import ModelError, ModelOutputError, ValidationError
from reqmap.knowledge import (
    CapabilityRecord,
    ComponentRecord,
    KnowledgeBase,
    SourceRecord,
    load_knowledge,
)
from reqmap.mapping import map_atom, validate_atom_result
from reqmap.models import (
    AnalysisState,
    AtomResult,
    AtomicClaim,
    Candidate,
    Evidence,
    EvidenceClaimScope,
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
        self.proposals = tuple(responses)
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
        claim_scope=EvidenceClaimScope.SPECIFIC,
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
        f"SRC-{component_id}": SourceRecord(
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
    sources["SRC-policy"] = SourceRecord(
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
        "E-NOVA": replace(
            evidence("E-NOVA", "nova", "SRC-nova"),
            claim_ru="Nova API документирует операцию POST /servers.",
            locator="Servers / POST /servers",
        ),
        "E-NEUTRON": evidence(
            "E-NEUTRON", "neutron", "SRC-neutron", strength=EvidenceStrength.INDIRECT
        ),
        "E-KOLLA": replace(
            evidence("E-KOLLA", "kolla_ansible", "SRC-kolla_ansible"),
            claim_ru="Kolla-Ansible применяет конфигурацию командой kolla-ansible reconfigure.",
        ),
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
    items["E-SYSCTL"] = replace(
        items["E-SYSCTL"],
        claim_ru="Kolla-Ansible применяет параметры sysctl хостовой ОС.",
    )
    items["E-CHRONY-NEG"] = replace(
        items["E-CHRONY-NEG"],
        claim_ru="Kolla-Ansible не поддерживает deployment Chrony.",
    )
    items["E-NFTABLES-NEG"] = replace(
        items["E-NFTABLES-NEG"],
        claim_ru="Kolla-Ansible не документирует native nftables management.",
    )
    capabilities = {
        "CAP-NOVA": CapabilityRecord(
            "CAP-NOVA", "nova", "Nova server API", ("POST /servers", "server API")
        ),
        "CAP-NEUTRON": CapabilityRecord(
            "CAP-NEUTRON", "neutron", "Neutron networking API", ("network API",)
        ),
        "CAP-KOLLA_ANSIBLE": CapabilityRecord(
            "CAP-KOLLA_ANSIBLE",
            "kolla_ansible",
            "Kolla-Ansible reconfigure",
            ("kolla-ansible reconfigure",),
        ),
        "CAP-HOST_OS_KERNEL_SYSCTL": CapabilityRecord(
            "CAP-HOST_OS_KERNEL_SYSCTL",
            "host_os_kernel_sysctl",
            "Kernel sysctl",
            ("sysctl", "kernel parameter"),
        ),
        "CAP-HOST_OS_CHRONY": CapabilityRecord(
            "CAP-HOST_OS_CHRONY", "host_os_chrony", "Chrony", ("chrony",)
        ),
        "CAP-HOST_OS_NFTABLES": CapabilityRecord(
            "CAP-HOST_OS_NFTABLES", "host_os_nftables", "nftables", ("nftables",)
        ),
    }
    return KnowledgeBase(
        root=Path("synthetic"),
        release="2025.1",
        snapshot_sha256="b" * 64,
        components=MappingProxyType(components),
        capabilities=MappingProxyType(capabilities),
        evidence=MappingProxyType(items),
        sources=MappingProxyType(sources),
        synonyms=MappingProxyType(
            {"виртуальная машина": ("instance", "server", "nova")}
        ),
    )


_DEFAULT_CAPABILITY = object()


def candidate(
    component_id: str,
    *evidence_ids: str,
    reasons: tuple[str, ...] = (),
    capability_id: str | None | object = _DEFAULT_CAPABILITY,
) -> Candidate:
    if capability_id is _DEFAULT_CAPABILITY:
        capability_id = f"CAP-{component_id.upper()}"
    return Candidate(component_id, capability_id, tuple(evidence_ids), 10.0, reasons)  # type: ignore[arg-type]


def step(
    phase: str,
    *,
    command: str | None = None,
    api_operation: str | None = None,
    mechanism: str | None = None,
    action_ru: str | None = None,
) -> dict[str, object]:
    resolved_mechanism = mechanism or (
        "openstack_api" if phase == "runtime" else "kolla_ansible"
    )
    if action_ru is None:
        if api_operation is not None:
            action_ru = f"Вызвать {api_operation}"
        elif command is not None:
            action_ru = f"Выполнить {command}"
        else:
            action_ru = f"Настроить {resolved_mechanism}"
    return {
        "action_ru": action_ru,
        "mechanism": resolved_mechanism,
        "command": command,
        "api_operation": api_operation,
    }


def design_steps(
    config_mechanism: str,
    *,
    action_ru: str | None = None,
) -> list[dict[str, object]]:
    return [
        step(
            "designtime",
            mechanism=config_mechanism,
            action_ru=action_ru,
        ),
        step(
            "designtime",
            command="kolla-ansible reconfigure",
            mechanism="kolla_ansible",
            action_ru="Применить конфигурацию Kolla-Ansible",
        ),
    ]


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
    role_ru: str | None = None,
) -> dict[str, object]:
    if steps is None:
        if phase == "runtime":
            steps = [step(phase, api_operation="POST /servers")]
        elif component_id == "kolla_ansible":
            steps = [
                step(
                    phase,
                    command="kolla-ansible reconfigure",
                    mechanism="kolla_ansible",
                    action_ru="Применить конфигурацию Kolla-Ansible",
                )
            ]
        else:
            config_mechanism = {
                "host_os_kernel_sysctl": "sysctl",
                "host_os_chrony": "chrony",
                "host_os_nftables": "nftables",
            }.get(component_id, "config_override")
            steps = design_steps(config_mechanism)
    if role_ru is None:
        role_ru = {
            "nova": "Nova server API",
            "neutron": "Neutron networking API",
            "kolla_ansible": "Kolla-Ansible reconfigure",
            "host_os_kernel_sysctl": "Kernel sysctl",
            "host_os_chrony": "Chrony",
            "host_os_nftables": "nftables",
            "host_os_networking": "/etc/hosts",
            "host_os_package_management": "package installation",
            "host_os_storage": "data-root",
            "host_os_identity_access": "sudoers",
        }.get(component_id, component_id.replace("_", " "))
    return {
        "component_id": component_id,
        "role_ru": role_ru,
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
    if phase is Phase.RUNTIME:
        implementation_steps = (
            ImplementationStep(
                order=1,
                phase=phase,
                action_ru=f"Вызвать {api_operation}",
                mechanism="openstack_api",
                command=None,
                api_operation=api_operation,
            ),
        )
    elif component_id == "kolla_ansible":
        implementation_steps = (
            ImplementationStep(
                order=1,
                phase=phase,
                action_ru="Применить конфигурацию Kolla-Ansible",
                mechanism="kolla_ansible",
                command=command,
                api_operation=None,
            ),
        )
    else:
        config_mechanism = {
            "host_os_kernel_sysctl": "sysctl",
            "host_os_chrony": "chrony",
            "host_os_nftables": "nftables",
        }.get(component_id, "config_override")
        implementation_steps = (
            ImplementationStep(
                order=1,
                phase=phase,
                action_ru=f"Настроить {config_mechanism}",
                mechanism=config_mechanism,
            ),
            ImplementationStep(
                order=2,
                phase=phase,
                action_ru="Применить конфигурацию Kolla-Ansible",
                mechanism="kolla_ansible",
                command=command,
            ),
        )
    return Mapping(
        mapping_id="REQ-0001-A001-M001",
        atom_id="REQ-0001-A001",
        component_id=component_id,
        role_ru={
            "nova": "Nova server API",
            "neutron": "Neutron networking API",
            "kolla_ansible": "Kolla-Ansible reconfigure",
            "host_os_kernel_sysctl": "Kernel sysctl",
            "host_os_chrony": "Chrony",
            "host_os_nftables": "nftables",
        }.get(component_id, component_id.replace("_", " ")),
        relation=relation,
        phase=phase,
        implementation_source=source,
        mechanism=mechanism or ("openstack_api" if phase is Phase.RUNTIME else "kolla_ansible"),
        steps=implementation_steps,
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
        "obligation_id": None,
        "source_spans": [],
        "source_sha256": None,
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
    ],
)
def test_off_candidate_or_cross_component_references_require_correction(
    kb, response_mapping, candidates, diagnostic
) -> None:
    model = FakeModel([response(response_mapping), response(response_mapping)])

    mapped = map_atom(model, atom(), candidates, kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert diagnostic in mapped.diagnostics[0].lower()


def test_shared_official_source_scope_never_transfers_evidence_to_sibling_component(kb) -> None:
    shared_source = replace(kb.sources["SRC-nova"], component_ids=("nova", "neutron"))
    shared_kb = replace(
        kb,
        sources=MappingProxyType({**kb.sources, "SRC-nova": shared_source}),
    )
    neutron = replace(canonical_mapping(), component_id="neutron")

    with pytest.raises(ValidationError) as error:
        validate_atom_result(result(neutron), shared_kb)

    assert error.value.code == "EVIDENCE_COMPONENT_MISMATCH"


def test_candidate_cannot_borrow_official_evidence_from_another_component(kb) -> None:
    forged_candidates = (
        candidate("nova"),
        candidate("neutron", "E-NOVA"),
    )
    model = FakeModel([response(raw_mapping())])

    with pytest.raises(ValidationError) as error:
        map_atom(model, atom(), forged_candidates, kb)

    assert error.value.code == "CANDIDATE_EVIDENCE_OWNERSHIP"
    assert model.calls == []


def test_model_evidence_allowlist_is_scoped_to_mapping_component_candidate(kb) -> None:
    invalid = raw_mapping("nova", ("E-NEUTRON",))
    model = FakeModel([response(invalid), response(invalid)])

    mapped = map_atom(
        model,
        atom(),
        (candidate("nova", "E-NOVA"), candidate("neutron", "E-NEUTRON")),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert "кандидата nova" in mapped.diagnostics[0]


def test_kolla_candidate_may_carry_host_policy_only_as_relational_context(kb) -> None:
    policy_source = replace(
        kb.sources["SRC-policy"],
        component_ids=("host_os_kernel_sysctl", "kolla_ansible"),
    )
    policy = replace(
        kb.evidence["E-POLICY"],
        evidence_id="E-HOST-POLICY",
        component_id="host_os_kernel_sysctl",
        capability_id="CAP-HOST_OS_KERNEL_SYSCTL",
    )
    relational_kb = replace(
        kb,
        sources=MappingProxyType({**kb.sources, "SRC-policy": policy_source}),
        evidence=MappingProxyType({**kb.evidence, policy.evidence_id: policy}),
    )
    kolla = raw_mapping(
        "kolla_ansible",
        ("E-KOLLA", "E-HOST-POLICY"),
        phase="designtime",
        implementation_source="kolla_ansible",
    )
    model = FakeModel([response(kolla)])

    mapped = map_atom(
        model,
        atom("Применить kolla-ansible reconfigure"),
        (
            candidate(
                "kolla_ansible",
                "E-KOLLA",
                "E-HOST-POLICY",
            ),
        ),
        relational_kb,
    )

    # The old evidence validator still checks these host/delivery relations.
    # They cannot prove a source obligation without a reviewed binding catalog.
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "BINDING_CATALOG_MISSING" in {d.code for d in mapped.binding_decision.diagnostics}
    from reqmap.mapping import _build_result
    mapped = validate_atom_result(_build_result(mapped.atom, model.proposals[-1]), relational_kb)

    assert mapped.support_status is SupportStatus.SUPPORTED
    assert mapped.mappings[0].evidence_ids == ("E-KOLLA", "E-HOST-POLICY")


class CandidateSubclass(Candidate):
    pass


class StringSubclass(str):
    pass


def test_candidate_boundary_rejects_non_builtin_or_nonfinite_fields_without_typeerror(kb) -> None:
    valid = candidate("nova", "E-NOVA")
    malformed: list[object] = [
        [valid],
        (CandidateSubclass(*valid.__dict__.values()),),
        (replace(valid, component_id=StringSubclass("nova")),),
        (replace(valid, capability_id=["CAP-NOVA"]),),
        (replace(valid, capability_id="CAP-NEUTRON"),),
        (replace(valid, evidence_ids=["E-NOVA"]),),
        (replace(valid, evidence_ids=(["E-NOVA"],)),),
        (replace(valid, score=True),),
        (replace(valid, score=math.nan),),
        (replace(valid, reasons=["source_hint"]),),
    ]

    for candidates_value in malformed:
        model = FakeModel([response(raw_mapping())])
        with pytest.raises(ValidationError):
            map_atom(model, atom(), candidates_value, kb)  # type: ignore[arg-type]
        assert model.calls == []


def test_response_rejects_string_subclass_before_membership_checks(kb) -> None:
    invalid = response({**raw_mapping(), "component_id": StringSubclass("nova")})
    model = FakeModel([invalid, invalid])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED
    assert "непустой строкой" in mapped.diagnostics[0]


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


def test_every_runtime_step_requires_openstack_api_mechanism(kb) -> None:
    invalid = raw_mapping(
        steps=[
            step("runtime", api_operation="POST /servers"),
            step("runtime", mechanism="shell", api_operation="POST /servers"),
        ]
    )
    model = FakeModel([response(invalid), response(invalid)])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED


def test_specific_runtime_operation_requires_complete_phrase_in_owned_official_corpus(kb) -> None:
    invented = raw_mapping(
        steps=[step("runtime", api_operation="POST /quantum-teleportation")]
    )
    model = FakeModel([response(invented)])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 1


def test_broad_real_scope_evidence_does_not_prove_invented_endpoint() -> None:
    real_kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    invented = raw_mapping(
        "nova",
        ("E-NOVA-SCOPE-001",),
        steps=[step("runtime", api_operation="POST /quantum-teleportation")],
    )
    model = FakeModel([response(invented)])

    mapped = map_atom(
        model,
        atom(),
        (
            candidate(
                "nova",
                "E-NOVA-SCOPE-001",
                capability_id="CAP-NOVA-COMPUTE-API",
            ),
        ),
        real_kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 1


@pytest.mark.parametrize("api_operation", ["API", "server", "instance", "REST API"])
def test_real_nova_scope_generic_nouns_do_not_ground_runtime_operation(
    api_operation: str,
) -> None:
    real_kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    proposed = raw_mapping(
        "nova",
        ("E-NOVA-SCOPE-001",),
        steps=[step("runtime", api_operation=api_operation)],
    )
    model = FakeModel([response(proposed)])

    mapped = map_atom(
        model,
        atom(),
        (
            candidate(
                "nova",
                "E-NOVA-SCOPE-001",
                capability_id="CAP-NOVA-COMPUTE-API",
            ),
        ),
        real_kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 1


def test_runtime_role_semantics_must_be_grounded_by_owned_official_evidence(kb) -> None:
    role = "Поддерживает квантовую телепортацию"
    proposed = {**raw_mapping(), "role_ru": role}
    model = FakeModel([response(proposed, supported_aspects=(role,))])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.supported_aspects == ()
    assert mapped.unconfirmed_aspects == (mapped.atom.source_quote,)
    assert len(model.calls) == 1


def test_unrelated_atom_cannot_claim_supported_known_runtime_mapping(kb) -> None:
    model = FakeModel([response(raw_mapping())])

    mapped = map_atom(
        model,
        atom("Выполнить квантовую телепортацию"),
        (candidate("nova", "E-NOVA"),),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE


def test_boilerplate_only_role_and_action_do_not_ground_mapping(kb) -> None:
    proposed = {
        **raw_mapping(
            steps=[
                step(
                    "runtime",
                    api_operation="POST /servers",
                    action_ru="Выполнить действие",
                )
            ]
        ),
        "role_ru": "Роль",
    }
    model = FakeModel([response(proposed)])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE


def test_specific_synthetic_runtime_operation_is_supported_when_phrase_is_owned(kb, tmp_path) -> None:
    from tests.binding_factories import binding_case, loaded_catalog
    bound_atom, context, proposal = binding_case(kb, loaded_catalog(tmp_path, kb), response(raw_mapping()))
    model = FakeModel([proposal])
    mapped = map_atom(model, bound_atom, (candidate("nova", "E-NOVA"),), kb, binding_context=context)
    assert mapped.support_status is SupportStatus.SUPPORTED
    assert mapped.supported_aspects == (bound_atom.source_quote,)


@pytest.mark.parametrize(
    ("mechanism", "action_ru"),
    [
        ("quantum_mode", "Включить quantum_mode"),
        ("config_override", "Настроить RAID"),
        ("config_override", "Настроить OVS"),
        ("sysctl", "Установить kernel.unicorn"),
    ],
)
def test_invented_designtime_identifiers_never_remain_supported(
    kb, mechanism: str, action_ru: str
) -> None:
    if mechanism == "sysctl":
        component_id = "host_os_kernel_sysctl"
        evidence_id = "E-SYSCTL"
        relation = "host_os_change"
        mappings = (
            raw_mapping(
                component_id,
                (evidence_id,),
                phase="designtime",
                relation=relation,
                implementation_source="kolla_ansible",
                steps=design_steps(mechanism, action_ru=action_ru),
            ),
            raw_mapping(
                "kolla_ansible",
                ("E-KOLLA",),
                phase="designtime",
                implementation_source="kolla_ansible",
            ),
        )
        candidates_value = (
            candidate(component_id, evidence_id),
            candidate("kolla_ansible", "E-KOLLA"),
        )
    else:
        mappings = (
            raw_mapping(
                "nova",
                ("E-NOVA",),
                phase="designtime",
                implementation_source="kolla_ansible",
                steps=design_steps(mechanism, action_ru=action_ru),
            ),
        )
        candidates_value = (candidate("nova", "E-NOVA"),)
    model = FakeModel([response(*mappings)])

    mapped = map_atom(model, atom("Изменить конфигурацию"), candidates_value, kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    ("role_ru", "action_ru"),
    [
        ("настроить raid", "Настроить параметр компонента"),
        ("изменить ovs", "Настроить параметр компонента"),
        ("Реализует проверяемое обязательство", "включить unicorn mode"),
    ],
)
def test_lowercase_designtime_semantics_cannot_bypass_grounding(
    kb, role_ru: str, action_ru: str
) -> None:
    host = {
        **raw_mapping(
            "host_os_kernel_sysctl",
            ("E-SYSCTL",),
            phase="designtime",
            relation="host_os_change",
            implementation_source="kolla_ansible",
            steps=design_steps("sysctl", action_ru=action_ru),
        ),
        "role_ru": role_ru,
    }
    kolla = raw_mapping(
        "kolla_ansible",
        ("E-KOLLA",),
        phase="designtime",
        implementation_source="kolla_ansible",
    )
    model = FakeModel([response(host, kolla)])

    mapped = map_atom(
        model,
        atom("Изменить sysctl"),
        (
            candidate("host_os_kernel_sysctl", "E-SYSCTL"),
            candidate("kolla_ansible", "E-KOLLA"),
        ),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 1


def test_supported_evidence_must_match_current_kb_release(kb) -> None:
    stale = replace(kb.evidence["E-NOVA"], version_constraint="2024.2")
    stale_kb = replace(
        kb,
        evidence=MappingProxyType({**kb.evidence, "E-NOVA": stale}),
    )
    model = FakeModel([response(raw_mapping())])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), stale_kb)

    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE


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


def test_designtime_structure_requires_kolla_source_mechanisms_and_null_api(kb) -> None:
    invalid_responses = [
        raw_mapping(phase="designtime", implementation_source="upstream"),
        raw_mapping(
            phase="designtime",
            implementation_source="kolla_ansible",
            mechanism="config_override",
        ),
        raw_mapping(
            phase="designtime",
            implementation_source="kolla_ansible",
            steps=[
                *design_steps("config_override"),
                step("designtime", api_operation="POST /servers"),
            ],
        ),
        raw_mapping(
            phase="designtime",
            implementation_source="kolla_ansible",
            steps=[
                step("designtime", mechanism="config_override"),
                step(
                    "designtime",
                    command="kolla-ansible reconfigure",
                    mechanism="shell",
                ),
            ],
        ),
    ]
    for invalid in invalid_responses:
        model = FakeModel([response(invalid), response(invalid)])
        mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)
        assert mapped.analysis_state is AnalysisState.MODEL_FAILED


def test_non_kolla_designtime_mapping_requires_non_delivery_config_step(kb) -> None:
    invalid = raw_mapping(
        phase="designtime",
        implementation_source="kolla_ansible",
        steps=[
            step(
                "designtime",
                command="kolla-ansible reconfigure",
                mechanism="kolla_ansible",
            )
        ],
    )
    model = FakeModel([response(invalid), response(invalid)])

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.analysis_state is AnalysisState.MODEL_FAILED


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

    mapped = map_atom(
        model,
        atom("Применить kolla-ansible reconfigure"),
        (candidate("kolla_ansible", "E-KOLLA"),),
        kb,
    )

    # The old evidence validator still checks these host/delivery relations.
    # They cannot prove a source obligation without a reviewed binding catalog.
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "BINDING_CATALOG_MISSING" in {d.code for d in mapped.binding_decision.diagnostics}
    from reqmap.mapping import _build_result
    mapped = validate_atom_result(_build_result(mapped.atom, model.proposals[-1]), kb)

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

    # The old evidence validator still checks these host/delivery relations.
    # They cannot prove a source obligation without a reviewed binding catalog.
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "BINDING_CATALOG_MISSING" in {d.code for d in mapped.binding_decision.diagnostics}
    from reqmap.mapping import _build_result
    mapped = validate_atom_result(_build_result(mapped.atom, model.proposals[-1]), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert [item.component_id for item in mapped.mappings] == [
        "host_os_kernel_sysctl",
        "kolla_ansible",
    ]
    assert all(item.phase is Phase.DESIGNTIME for item in mapped.mappings)
    assert mapped.support_status is SupportStatus.SUPPORTED


@pytest.mark.parametrize(
    ("component_id", "official_id", "policy_id", "capability_id", "mechanism"),
    [
        (
            "host_os_networking",
            "E-HOST-NETWORKING-OFFICIAL-001",
            "E-HOST-NETWORKING-POLICY-001",
            "CAP-HOST-NETWORKING-AUTOMATION",
            "/etc/hosts",
        ),
        (
            "host_os_package_management",
            "E-HOST-PACKAGES-OFFICIAL-001",
            "E-HOST-PACKAGES-POLICY-001",
            "CAP-HOST-PACKAGES-AUTOMATION",
            "package installation",
        ),
        (
            "host_os_storage",
            "E-HOST-STORAGE-OFFICIAL-001",
            "E-HOST-STORAGE-POLICY-001",
            "CAP-HOST-STORAGE-AUTOMATION",
            "data-root",
        ),
        (
            "host_os_identity_access",
            "E-HOST-IDENTITY-OFFICIAL-001",
            "E-HOST-IDENTITY-POLICY-001",
            "CAP-HOST-IDENTITY-AUTOMATION",
            "sudoers",
        ),
    ],
)
def test_real_host_subsystem_grounding_is_separate_from_kolla_delivery(
    component_id: str,
    official_id: str,
    policy_id: str,
    capability_id: str,
    mechanism: str,
) -> None:
    real_kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    host = raw_mapping(
        component_id,
        (official_id, policy_id),
        phase="designtime",
        relation="host_os_change",
        implementation_source="kolla_ansible",
        steps=design_steps(mechanism, action_ru=f"Настроить {mechanism}"),
    )
    kolla = raw_mapping(
        "kolla_ansible",
        ("E-KOLLA-RECONFIGURE-001", policy_id),
        phase="designtime",
        implementation_source="kolla_ansible",
    )
    model = FakeModel([response(host, kolla)])

    mapped = map_atom(
        model,
        atom(f"Настроить {mechanism} в подсистеме host OS"),
        (
            candidate(component_id, official_id, policy_id, capability_id=capability_id),
            candidate(
                "kolla_ansible",
                "E-KOLLA-RECONFIGURE-001",
                policy_id,
                capability_id="CAP-KOLLA-RECONFIGURE",
            ),
        ),
        real_kb,
    )

    # The old evidence validator still checks these host/delivery relations.
    # They cannot prove a source obligation without a reviewed binding catalog.
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "BINDING_CATALOG_MISSING" in {d.code for d in mapped.binding_decision.diagnostics}
    from reqmap.mapping import _build_result
    mapped = validate_atom_result(_build_result(mapped.atom, model.proposals[-1]), real_kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.SUPPORTED
    assert [item.support_status for item in mapped.mappings] == [
        SupportStatus.SUPPORTED,
        SupportStatus.SUPPORTED,
    ]
    assert len(model.calls) == 1


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
    assert "Статус понижен: отсутствует достаточное официальное evidence." in mapped.diagnostics


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
        AtomResult(
            atom(f"Настроить {component_id.removeprefix('host_os_')}"),
            AnalysisState.COMPLETED,
            SupportStatus.NOT_SUPPORTED,
            (mapping, kolla),
        ),
        kb,
    )

    assert validated.support_status is SupportStatus.NOT_SUPPORTED
    assert validated.mappings[0].support_status is SupportStatus.NOT_SUPPORTED


def test_unrelated_atom_cannot_claim_not_supported_from_negative_host_evidence(kb) -> None:
    host = raw_mapping(
        "host_os_chrony",
        ("E-CHRONY-NEG",),
        phase="designtime",
        relation="host_os_change",
        implementation_source="kolla_ansible",
        support_status="not_supported",
    )
    kolla = raw_mapping(
        "kolla_ansible",
        ("E-KOLLA",),
        phase="designtime",
        implementation_source="kolla_ansible",
    )
    model = FakeModel([response(host, kolla, status="not_supported")])

    mapped = map_atom(
        model,
        atom("Выполнить квантовую телепортацию"),
        (
            candidate("host_os_chrony", "E-CHRONY-NEG"),
            candidate("kolla_ansible", "E-KOLLA"),
        ),
        kb,
    )

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE


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


def test_partial_with_positive_official_evidence_preserves_aspects(kb, tmp_path) -> None:
    model = FakeModel(
        [
            response(
                raw_mapping(support_status="partial"),
                status="partial",
                supported_aspects=("Nova server API",),
                unconfirmed_aspects=("Расширенное планирование",),
            )
        ]
    )

    from tests.binding_factories import binding_case, loaded_catalog
    bound_atom, context, proposal = binding_case(kb, loaded_catalog(tmp_path, kb), model.proposals[0])
    model = FakeModel([proposal])
    mapped = map_atom(model, bound_atom, (candidate("nova", "E-NOVA"),), kb, binding_context=context)

    assert mapped.support_status is SupportStatus.PARTIAL
    assert mapped.supported_aspects == (bound_atom.source_quote,)
    assert mapped.unconfirmed_aspects == (bound_atom.source_quote,)


def test_partial_unlinked_supported_aspect_downgrades_instead_of_any_mapping_proving_it(kb) -> None:
    model = FakeModel(
        [
            response(
                raw_mapping(support_status="partial"),
                status="partial",
                supported_aspects=("Несвязанное обещание",),
                unconfirmed_aspects=("Другой аспект",),
            )
        ]
    )

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.supported_aspects == ()
    assert mapped.unconfirmed_aspects == (mapped.atom.source_quote,)


def test_partial_moves_only_unlinked_aspects_and_keeps_exact_role_link(kb, tmp_path) -> None:
    role = "Nova server API"
    model = FakeModel(
        [
            response(
                raw_mapping(support_status="partial"),
                status="partial",
                supported_aspects=(role, "Чужой аспект"),
                unconfirmed_aspects=("Не подтверждено",),
            )
        ]
    )

    from tests.binding_factories import binding_case, loaded_catalog
    bound_atom, context, proposal = binding_case(kb, loaded_catalog(tmp_path, kb), model.proposals[0])
    model = FakeModel([proposal])
    mapped = map_atom(model, bound_atom, (candidate("nova", "E-NOVA"),), kb, binding_context=context)

    assert mapped.support_status is SupportStatus.PARTIAL
    assert mapped.supported_aspects == (bound_atom.source_quote,)
    assert mapped.unconfirmed_aspects == (bound_atom.source_quote,)


def test_supported_status_downgrades_when_its_only_promised_aspect_is_unproved(kb) -> None:
    promised = "Квантовая телепортация"
    model = FakeModel(
        [response(raw_mapping(), supported_aspects=(promised,))]
    )

    mapped = map_atom(model, atom(), (candidate("nova", "E-NOVA"),), kb)

    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.mappings[0].support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert mapped.supported_aspects == ()
    assert mapped.unconfirmed_aspects == (mapped.atom.source_quote,)
    assert len(model.calls) == 1


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


def test_not_applicable_without_scope_rule_remains_insufficient(kb) -> None:
    model = FakeModel([response(status="not_applicable")])

    mapped = map_atom(model, atom("Внешнее организационное требование"), (), kb)

    assert mapped.analysis_state is AnalysisState.COMPLETED
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
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


@pytest.mark.parametrize(
    "diagnostics",
    [
        ["сообщение"],
        (StringSubclass("сообщение"),),
        ("",),
        (1,),
    ],
)
def test_public_validator_requires_exact_nonempty_builtin_diagnostic_strings(
    kb, diagnostics: object
) -> None:
    invalid = replace(result(canonical_mapping()), diagnostics=diagnostics)

    with pytest.raises(ValidationError):
        validate_atom_result(invalid, kb)  # type: ignore[arg-type]


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


def test_real_epoxy_snapshot_keeps_broad_nova_neutron_scope_as_context() -> None:
    real_kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    model = FakeModel(
        [
            response(
                raw_mapping(
                    "nova",
                    ("E-NOVA-SCOPE-001",),
                    steps=[
                        step(
                            "runtime",
                            api_operation="Compute instances через REST API",
                        )
                    ],
                ),
                raw_mapping(
                    "neutron",
                    ("E-NEUTRON-SCOPE-001",),
                    steps=[step("runtime", api_operation="OpenStack Networking API")],
                ),
            )
        ]
    )

    mapped = map_atom(
        model,
        atom(
            "Создать виртуальную машину через Nova API и network port через Neutron API"
        ),
        (
            candidate(
                "nova", "E-NOVA-SCOPE-001", capability_id="CAP-NOVA-COMPUTE-API"
            ),
            candidate(
                "neutron",
                "E-NEUTRON-SCOPE-001",
                capability_id="CAP-NEUTRON-NETWORKING-API",
            ),
        ),
        real_kb,
    )

    assert [item.component_id for item in mapped.mappings] == ["nova", "neutron"]
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert all(item.support_status is SupportStatus.INSUFFICIENT_EVIDENCE for item in mapped.mappings)


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
        atom(
            "Настроить chrony в подсистеме host OS"
            if component_id == "host_os_chrony"
            else "Настроить nftables в подсистеме host OS"
        ),
        (
            candidate(
                component_id,
                evidence_id,
                capability_id={
                    "host_os_chrony": "CAP-HOST-CHRONY-AUTOMATION",
                    "host_os_nftables": "CAP-HOST-NFTABLES-AUTOMATION",
                }[component_id],
            ),
            candidate(
                "kolla_ansible",
                "E-KOLLA-RECONFIGURE-001",
                capability_id="CAP-KOLLA-RECONFIGURE",
            ),
        ),
        real_kb,
    )

    # The old evidence validator still checks these host/delivery relations.
    # They cannot prove a source obligation without a reviewed binding catalog.
    assert mapped.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "BINDING_CATALOG_MISSING" in {d.code for d in mapped.binding_decision.diagnostics}
    from reqmap.mapping import _build_result
    mapped = validate_atom_result(_build_result(mapped.atom, model.proposals[-1]), real_kb)

    assert mapped.support_status is SupportStatus.NOT_SUPPORTED
    assert mapped.mappings[0].support_status is SupportStatus.NOT_SUPPORTED


def test_mapping_prompt_is_versioned_and_strictly_russian() -> None:
    assert PROMPT_MAPPING_VERSION == "1.2"
    assert "Верни только JSON" in MAPPING_PROMPT
    assert "source_hint" in MAPPING_PROMPT
    assert "не является evidence" in MAPPING_PROMPT
    assert "каждый runtime step" in MAPPING_PROMPT
    assert "non-delivery" in MAPPING_PROMPT
    assert "supported_aspect" in MAPPING_PROMPT
    assert "role_ru" in MAPPING_PROMPT
