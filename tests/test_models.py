import hashlib
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from reqmap.ids import (
    atom_id,
    generated_requirement_id,
    mapping_id,
    procedure_graph_id,
    procedure_step_id,
    responsibility_id,
    sha256_bytes,
)
from reqmap.models import (
    AnalysisRequest,
    AnalysisState,
    AtomResult,
    AtomicClaim,
    Candidate,
    DecompositionOutcome,
    Evidence,
    EvidencePolarity,
    EvidenceStrength,
    GroupResult,
    ImplementationSource,
    ImplementationStep,
    Mapping,
    Phase,
    PreflightResult,
    RelationType,
    Requirement,
    RequirementResult,
    RunResult,
    SourceCoordinate,
    SourceField,
    SourceHint,
    SupportStatus,
    to_dict,
)


def _mapping(*, phase: Phase, ordinal: int) -> Mapping:
    return Mapping(
        mapping_id=mapping_id("REQ-0001-A001", ordinal),
        atom_id="REQ-0001-A001",
        component_id="nova",
        role_ru="Исполняет требование",
        relation=RelationType.IMPLEMENTS,
        phase=phase,
        implementation_source=ImplementationSource.UPSTREAM,
        mechanism="Nova API",
        steps=(
            ImplementationStep(
                order=1,
                phase=phase,
                action_ru="Включить службу",
                mechanism="Конфигурация",
            ),
        ),
        evidence_ids=("evidence-1",),
        support_status=SupportStatus.SUPPORTED,
        reason_ru="Прямое свидетельство",
    )


def test_mixed_requirement_uses_two_mapping_records() -> None:
    runtime = _mapping(phase=Phase.RUNTIME, ordinal=1)
    designtime = _mapping(phase=Phase.DESIGNTIME, ordinal=2)

    assert runtime.phase is not designtime.phase
    assert runtime.mapping_id != designtime.mapping_id


def test_generated_ids_are_stable_and_one_based() -> None:
    assert generated_requirement_id(1) == "REQ-0001"
    assert atom_id("REQ-0001", 2) == "REQ-0001-A002"
    assert mapping_id("REQ-0001-A002", 3) == "REQ-0001-A002-M003"
    assert responsibility_id("REQ-0001-A002", 4) == "REQ-0001-A002-R004"
    assert procedure_graph_id("REQ-0001", 5) == "REQ-0001-P005"
    assert procedure_step_id("REQ-0001-P005", 6) == "REQ-0001-P005-S006"
    assert sha256_bytes(b"reqmap") == hashlib.sha256(b"reqmap").hexdigest()


def test_enum_values_are_stable_english_codes() -> None:
    assert {status.value for status in SupportStatus} == {
        "supported",
        "partial",
        "not_supported",
        "insufficient_evidence",
        "not_applicable",
    }
    assert {state.value for state in AnalysisState} == {
        "completed",
        "model_failed",
        "validation_failed",
        "skipped",
    }
    assert {phase.value for phase in Phase} == {"runtime", "designtime"}
    assert {relation.value for relation in RelationType} == {
        "implements",
        "configures",
        "prerequisite",
        "integrates",
        "host_os_change",
    }
    assert {source.value for source in ImplementationSource} == {
        "upstream",
        "kolla_ansible",
        "product_extension",
        "external_component",
    }
    assert {strength.value for strength in EvidenceStrength} == {"direct", "indirect", "none"}
    assert {polarity.value for polarity in EvidencePolarity} == {"positive", "negative"}


def test_to_dict_recursively_serializes_ordered_model_values() -> None:
    requirement = Requirement(
        requirement_id="REQ-0001",
        source_id="duplicate-source-id",
        text="Требование",
        ordinal=1,
        coordinate=SourceCoordinate(source_name="input.xlsx", sheet="Лист 1", row=3),
        group_ids=("group-b", "group-a"),
        source_fields=(SourceField(column="A", value="source-id"),),
        source_hints=(SourceHint(value="hint", column="B"),),
    )
    atom = AtomicClaim(
        atom_id="REQ-0001-A001",
        requirement_id=requirement.requirement_id,
        text="Атом",
        source_quote="Требование",
        mandatory=True,
        ordinal=1,
    )
    evidence = Evidence(
        evidence_id="evidence-1",
        component_id="nova",
        capability_id="compute",
        polarity=EvidencePolarity.POSITIVE,
        strength=EvidenceStrength.DIRECT,
        claim_ru="Подтверждает",
        source_id="manual",
        locator="section-1",
        version_constraint="2025.1",
        source_url=None,
        local_path="knowledge/nova.md",
        source_sha256="abc",
        retrieved_at="2026-08-20T00:00:00Z",
        provenance="local",
    )
    mapping = _mapping(phase=Phase.RUNTIME, ordinal=1)
    atom_result = AtomResult(
        atom=atom,
        analysis_state=AnalysisState.COMPLETED,
        support_status=SupportStatus.SUPPORTED,
        mappings=(mapping,),
    )
    result = RunResult(
        run_id="run-1",
        schema_version="1.0",
        run_status="completed",
        requirements=(
            RequirementResult(
                requirement=requirement,
                analysis_state=AnalysisState.COMPLETED,
                support_status=SupportStatus.SUPPORTED,
                atom_results=(atom_result,),
                mappings=(mapping,),
            ),
        ),
        groups=(
            GroupResult(
                group_id="group-b",
                source_requirement_ids=("REQ-0001",),
                support_status=SupportStatus.SUPPORTED,
                component_ids=("nova",),
                mapping_ids=(mapping.mapping_id,),
                analysis_states=(AnalysisState.COMPLETED,),
            ),
        ),
        evidence=(evidence,),
        metadata={"input": Path("input.xlsx"), "phase": Phase.RUNTIME},
    )

    assert to_dict(result) == {
        "run_id": "run-1",
        "schema_version": "1.0",
        "run_status": "completed",
        "requirements": [
            {
                "requirement": {
                    "requirement_id": "REQ-0001",
                    "source_id": "duplicate-source-id",
                    "text": "Требование",
                    "ordinal": 1,
                    "coordinate": {"source_name": "input.xlsx", "sheet": "Лист 1", "row": 3},
                    "parent_id": None,
                    "group_ids": ["group-b", "group-a"],
                    "source_fields": [{"column": "A", "value": "source-id"}],
                    "source_hints": [{"value": "hint", "column": "B"}],
                },
                "analysis_state": "completed",
                "support_status": "supported",
                "atom_results": [
                    {
                        "atom": {
                            "atom_id": "REQ-0001-A001",
                            "requirement_id": "REQ-0001",
                            "text": "Атом",
                            "source_quote": "Требование",
                            "mandatory": True,
                            "ordinal": 1,
                            "obligation_id": None,
                            "source_spans": [],
                            "source_sha256": None,
                        },
                        "analysis_state": "completed",
                        "support_status": "supported",
                        "mappings": [
                            {
                                "mapping_id": "REQ-0001-A001-M001",
                                "atom_id": "REQ-0001-A001",
                                "component_id": "nova",
                                "role_ru": "Исполняет требование",
                                "relation": "implements",
                                "phase": "runtime",
                                "implementation_source": "upstream",
                                "mechanism": "Nova API",
                                "steps": [
                                    {
                                        "order": 1,
                                        "phase": "runtime",
                                        "action_ru": "Включить службу",
                                        "mechanism": "Конфигурация",
                                        "command": None,
                                        "api_operation": None,
                                    }
                                ],
                                "evidence_ids": ["evidence-1"],
                                "support_status": "supported",
                                "reason_ru": "Прямое свидетельство",
                            }
                        ],
                        "supported_aspects": [],
                        "unconfirmed_aspects": [],
                        "diagnostics": [],
                        "binding_decision": None,
                    }
                ],
                "mappings": [
                    {
                        "mapping_id": "REQ-0001-A001-M001",
                        "atom_id": "REQ-0001-A001",
                        "component_id": "nova",
                        "role_ru": "Исполняет требование",
                        "relation": "implements",
                        "phase": "runtime",
                        "implementation_source": "upstream",
                        "mechanism": "Nova API",
                        "steps": [
                            {
                                "order": 1,
                                "phase": "runtime",
                                "action_ru": "Включить службу",
                                "mechanism": "Конфигурация",
                                "command": None,
                                "api_operation": None,
                            }
                        ],
                        "evidence_ids": ["evidence-1"],
                        "support_status": "supported",
                        "reason_ru": "Прямое свидетельство",
                    }
                ],
                "diagnostics": [],
                "source_binding": None,
            }
        ],
        "groups": [
            {
                "group_id": "group-b",
                "source_requirement_ids": ["REQ-0001"],
                "support_status": "supported",
                "component_ids": ["nova"],
                "mapping_ids": ["REQ-0001-A001-M001"],
                "analysis_states": ["completed"],
            }
        ],
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "component_id": "nova",
                "capability_id": "compute",
                "polarity": "positive",
                "strength": "direct",
                "claim_ru": "Подтверждает",
                "source_id": "manual",
                "locator": "section-1",
                "version_constraint": "2025.1",
                "source_url": None,
                "local_path": "knowledge/nova.md",
                "source_sha256": "abc",
                "retrieved_at": "2026-08-20T00:00:00Z",
                "provenance": "local",
                "claim_scope": "context",
            }
        ],
        "metadata": {"input": "input.xlsx", "phase": "runtime"},
        "diagnostics": [],
    }


def test_canonical_dataclasses_have_exact_field_order_and_are_frozen() -> None:
    expected_fields = {
        SourceCoordinate: ("source_name", "sheet", "row"),
        SourceField: ("column", "value"),
        SourceHint: ("value", "column"),
        Requirement: (
            "requirement_id", "source_id", "text", "ordinal", "coordinate", "parent_id",
            "group_ids", "source_fields", "source_hints",
        ),
        AtomicClaim: ("atom_id", "requirement_id", "text", "source_quote", "mandatory", "ordinal",
                      "obligation_id", "source_spans", "source_sha256"),
        Evidence: (
            "evidence_id", "component_id", "capability_id", "polarity", "strength", "claim_ru",
            "source_id", "locator", "version_constraint", "source_url", "local_path",
            "source_sha256", "retrieved_at", "provenance", "claim_scope",
        ),
        Candidate: ("component_id", "capability_id", "evidence_ids", "score", "reasons"),
        ImplementationStep: ("order", "phase", "action_ru", "mechanism", "command", "api_operation"),
        Mapping: (
            "mapping_id", "atom_id", "component_id", "role_ru", "relation", "phase",
            "implementation_source", "mechanism", "steps", "evidence_ids", "support_status", "reason_ru",
        ),
        DecompositionOutcome: ("atoms", "analysis_state", "diagnostics"),
        AtomResult: (
            "atom", "analysis_state", "support_status", "mappings", "supported_aspects",
            "unconfirmed_aspects", "diagnostics", "binding_decision",
        ),
        RequirementResult: (
            "requirement", "analysis_state", "support_status", "atom_results", "mappings", "diagnostics", "source_binding",
        ),
        GroupResult: (
            "group_id", "source_requirement_ids", "support_status", "component_ids", "mapping_ids",
            "analysis_states",
        ),
        AnalysisRequest: ("requirements", "input_sha256", "input_kind", "source_path", "output_dir", "source_document"),
        PreflightResult: ("ok", "diagnostics", "knowledge_sha256"),
        RunResult: (
            "run_id", "schema_version", "run_status", "requirements", "groups", "evidence", "metadata",
            "diagnostics",
        ),
    }

    assert {model: tuple(field.name for field in fields(model)) for model in expected_fields} == expected_fields
    coordinate = SourceCoordinate(source_name="source", sheet=None, row=None)
    with pytest.raises(FrozenInstanceError):
        coordinate.source_name = "other"
