"""Fail-closed contracts for deep responsibility selection and evidence gating."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from types import MappingProxyType

import pytest

from reqmap.errors import ModelError, ModelOutputError
from reqmap.models import AnalysisState, EvidencePolarity, EvidenceStrength, SupportStatus
from tests.deep_factories import (
    deep_candidate,
    deep_mapping_response,
    immutable_v2_kb,
    mixed_kolla_host_kb,
    responsibility_selection,
    with_nova_evidence,
)
from tests.factories import atom


class FakeModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def complete_json(self, stage, system_prompt, payload):
        self.calls.append((stage, system_prompt, payload))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def v2_kb(tmp_path):
    return immutable_v2_kb(tmp_path)


def test_direct_positive_selection_builds_canonical_supported_record(tmp_path) -> None:
    from reqmap.deep_mapping import map_atom_deep

    from tests.binding_factories import binding_case, deep_knowledge, write_catalog, sign_catalog
    from reqmap.binding_catalog import load_binding_catalog
    v2_kb, signers, key = deep_knowledge(tmp_path)
    path = write_catalog(tmp_path / "bindings", v2_kb)
    sign_catalog(path, key)
    bound_atom, context, proposal = binding_case(v2_kb, load_binding_catalog(path, v2_kb, signers), deep_mapping_response())
    outcome = map_atom_deep(
        FakeModel([proposal]), bound_atom, deep_candidate(v2_kb), v2_kb, binding_context=context
    )

    assert outcome.atom_result.analysis_state is AnalysisState.COMPLETED
    assert outcome.atom_result.support_status is SupportStatus.SUPPORTED
    assert outcome.atom_result.responsibility_ids == ("REQ-0001-A001-R001",)
    assert outcome.responsibility_records[0].component_ref == "nova"
    assert outcome.procedure_template_ids == ("PROC-NOVA-CREATE",)


def test_exact_schema_retries_once_with_validation_feedback(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    invalid = {**deep_mapping_response(), "stable_id": "MODEL-CONTROLLED"}
    model = FakeModel([invalid, deep_mapping_response()])

    outcome = map_atom_deep(model, atom(), deep_candidate(v2_kb), v2_kb)

    assert outcome.atom_result.analysis_state is AnalysisState.COMPLETED
    assert len(model.calls) == 2
    assert set(model.calls[1][2]) == {
        "original_payload",
        "invalid_response",
        "violations_ru",
    }
    assert "полей" in str(model.calls[1][2]["violations_ru"])


def test_two_malformed_objects_end_as_model_failed(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    invalid = {"support_status": "supported"}
    outcome = map_atom_deep(
        FakeModel([invalid, invalid]), atom(), deep_candidate(v2_kb), v2_kb
    )

    assert outcome.atom_result.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.responsibility_records == ()


def test_model_output_error_retries_once_but_transport_error_propagates(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    output_error = ModelOutputError("MODEL_OUTPUT_INVALID", "bad JSON", "{bad")
    failed = map_atom_deep(
        FakeModel([output_error, output_error]), atom(), deep_candidate(v2_kb), v2_kb
    )
    assert failed.atom_result.analysis_state is AnalysisState.MODEL_FAILED

    model = FakeModel([ModelError("MODEL_TRANSPORT_ERROR", "offline")])
    with pytest.raises(ModelError, match="offline"):
        map_atom_deep(model, atom(), deep_candidate(v2_kb), v2_kb)
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    ("changes", "needle"),
    [
        ({"component_ref": "swift"}, "component"),
        ({"executor_ref": "ACTOR-UNKNOWN"}, "actor"),
        ({"target_ref": "TARGET-UNKNOWN"}, "target"),
        ({"action_ref": "ACTION-UNKNOWN"}, "action"),
        ({"effect_ref": "EFFECT-UNKNOWN"}, "effect"),
        ({"evidence_ids": ["EV-UNKNOWN"]}, "evidence"),
    ],
)
def test_reference_escape_is_validation_failed(v2_kb, changes, needle) -> None:
    from reqmap.deep_mapping import map_atom_deep

    invalid = deep_mapping_response(responsibility_selection(**changes))
    outcome = map_atom_deep(
        FakeModel([invalid, invalid]), atom(), deep_candidate(v2_kb), v2_kb
    )

    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert needle.lower() in outcome.atom_result.diagnostics[0].lower()


def test_unknown_procedure_template_cannot_expand_allowlist(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    invalid = deep_mapping_response(procedure_template_ids=("PROC-UNKNOWN",))
    outcome = map_atom_deep(
        FakeModel([invalid, invalid]), atom(), deep_candidate(v2_kb), v2_kb
    )
    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert "template" in outcome.atom_result.diagnostics[0].lower()


def test_direct_negative_conflict_none_and_indirect_are_computed(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    negative_kb = with_nova_evidence(
        v2_kb, "EV-NOVA-NEGATIVE", polarity=EvidencePolarity.NEGATIVE
    )
    negative = responsibility_selection(
        evidence_ids=["EV-NOVA-NEGATIVE"], support_status="supported"
    )
    negative_outcome = map_atom_deep(
        FakeModel([deep_mapping_response(negative)]),
        atom(),
        deep_candidate(negative_kb, "EV-NOVA-NEGATIVE"),
        negative_kb,
    )
    assert negative_outcome.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    from reqmap.deep_mapping import validate_responsibility_records
    assert validate_responsibility_records(
        negative_outcome.responsibility_records,
        deep_candidate(negative_kb, "EV-NOVA-NEGATIVE"), negative_kb,
    )[0].support_status is SupportStatus.NOT_SUPPORTED

    conflict = responsibility_selection(
        evidence_ids=["EV-NOVA-CREATE", "EV-NOVA-NEGATIVE"],
        support_status="supported",
    )
    conflict_outcome = map_atom_deep(
        FakeModel([deep_mapping_response(conflict)]),
        atom(),
        deep_candidate(negative_kb, "EV-NOVA-CREATE", "EV-NOVA-NEGATIVE"),
        negative_kb,
    )
    assert conflict_outcome.atom_result.analysis_state is AnalysisState.COMPLETED
    assert conflict_outcome.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "evidence_conflict" in conflict_outcome.atom_result.diagnostics

    none = responsibility_selection(evidence_ids=[], support_status="supported")
    none_outcome = map_atom_deep(
        FakeModel([deep_mapping_response(none)]), atom(), deep_candidate(v2_kb), v2_kb
    )
    assert none_outcome.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE

    for evidence_id, strength in (
        ("EV-NOVA-INDIRECT", EvidenceStrength.INDIRECT),
        ("EV-NOVA-UNKNOWN", EvidenceStrength.NONE),
    ):
        weak_kb = with_nova_evidence(v2_kb, evidence_id, strength=strength)
        weak = responsibility_selection(
            evidence_ids=[evidence_id], support_status="supported"
        )
        invalid = deep_mapping_response(weak)
        weak_outcome = map_atom_deep(
            FakeModel([invalid, invalid]),
            atom(),
            deep_candidate(weak_kb, "EV-NOVA-CREATE", evidence_id),
            weak_kb,
        )
        assert weak_outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
        assert "INDIRECT_SUPPORT_CLAIM" in weak_outcome.atom_result.diagnostics[0]


@pytest.mark.parametrize(
    "version_scope",
    [
        {
            "source_release": "2025.1",
            "target_release": "2026.1",
            "kolla_ansible_release": "2025.1",
            "host_profile": "rocky_linux_9",
            "version_constraint": "2026.1",
        },
        {
            "source_release": "2025.1",
            "target_release": "2025.1",
            "kolla_ansible_release": "2025.1",
            "host_profile": "ubuntu_24_04",
            "version_constraint": "2025.1",
        },
    ],
)
def test_version_or_host_scope_escape_is_validation_failed(v2_kb, version_scope) -> None:
    from reqmap.deep_mapping import map_atom_deep

    invalid = deep_mapping_response(responsibility_selection(version_scope=version_scope))
    outcome = map_atom_deep(
        FakeModel([invalid, invalid]), atom(), deep_candidate(v2_kb), v2_kb
    )
    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED


def test_loaded_2026_upgrade_action_is_allowed_only_with_upgrade_phase(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep
    from reqmap.deep_models import DeepCandidate, DeepRetrievalResult, LifecyclePhase, VersionScope

    scope = VersionScope("2025.1", "2026.1", "2025.1", "rocky_linux_9", "2026.1")
    action = replace(v2_kb.actions["ACTION-NOVA-CREATE"], version_scope=scope)
    procedure = v2_kb.procedures["PROC-NOVA-CREATE"]
    step = replace(procedure.steps[0], phase=LifecyclePhase.UPGRADE)
    procedure = replace(
        procedure, lifecycle_phase=LifecyclePhase.UPGRADE, steps=(step,)
    )
    kb = replace(
        v2_kb,
        actions=MappingProxyType({action.action_id: action}),
        procedures=MappingProxyType({procedure.template_id: procedure}),
    )
    retrieval = DeepRetrievalResult(
        (
            DeepCandidate(
                "nova",
                "CAP-NOVA-CREATE",
                action.action_id,
                "EFFECT-NOVA-SERVER-ACTIVE",
                action.evidence_ids,
                scope,
                1.0,
                ("fixture",),
            ),
        ),
        (),
    )
    selection = responsibility_selection(
        lifecycle_phase="upgrade",
        version_scope={
            "source_release": "2025.1",
            "target_release": "2026.1",
            "kolla_ansible_release": "2025.1",
            "host_profile": "rocky_linux_9",
            "version_constraint": "2026.1",
        },
    )
    outcome = map_atom_deep(
        FakeModel([deep_mapping_response(selection)]), atom(), retrieval, kb
    )
    assert outcome.atom_result.analysis_state is AnalysisState.COMPLETED
    assert outcome.responsibility_records[0].lifecycle_phase is LifecyclePhase.UPGRADE


def test_corpus_candidates_are_not_prompted_or_accepted_as_evidence(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep
    from reqmap.deep_models import CorpusCandidate, DeepRetrievalResult

    source = v2_kb.sources["SRC-MINIMAL"]
    retrieval = deep_candidate(v2_kb)
    retrieval = DeepRetrievalResult(
        retrieval.normalized_candidates,
        (CorpusCandidate(source.source_id, source.local_path, source.content_sha256, 1.0, ("x",)),),
    )
    invalid = deep_mapping_response(
        responsibility_selection(evidence_ids=["SRC-MINIMAL"])
    )
    model = FakeModel([invalid, invalid])
    outcome = map_atom_deep(model, atom(), retrieval, v2_kb)

    assert "corpus" not in str(model.calls[0][2]).lower()
    assert source.local_path not in str(model.calls[0][2])
    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED


def _mixed_selection(contour: str, component_ref: str, related: list[int]):
    return responsibility_selection(
        contour=contour,
        component_ref=component_ref,
        executor_ref="actor:kolla_ansible",
        target_contour="host_os",
        target_ref="rocky_linux_9.kernel_sysctl",
        action_ref="ACTION-KOLLA-SYSCTL",
        effect_ref="EFFECT-HOST-SYSCTL",
        lifecycle_phase="reconfigure",
        evidence_ids=["EV-KOLLA-SYSCTL"],
        related_indexes=related,
    )


def test_mixed_host_change_creates_sorted_symmetric_kolla_and_host_records(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep
    from reqmap.deep_models import ResponsibilityContour

    kb, retrieval = mixed_kolla_host_kb(v2_kb)
    host = _mixed_selection("host_os", "rocky_linux_9", [2])
    kolla = _mixed_selection("kolla_ansible", "kolla_ansible", [1])
    outcome = map_atom_deep(
        FakeModel([deep_mapping_response(host, kolla, procedure_template_ids=())]),
        atom(),
        retrieval,
        kb,
    )

    assert [item.contour for item in outcome.responsibility_records] == [
        ResponsibilityContour.KOLLA_ANSIBLE,
        ResponsibilityContour.HOST_OS,
    ]
    assert [item.record_id for item in outcome.responsibility_records] == [
        "REQ-0001-A001-R001",
        "REQ-0001-A001-R002",
    ]
    first, second = outcome.responsibility_records
    assert first.related_record_ids == (second.record_id,)
    assert second.related_record_ids == (first.record_id,)
    assert second.executor_ref == "actor:kolla_ansible"
    assert second.target_ref == "rocky_linux_9.kernel_sysctl"


def test_kolla_only_host_response_is_rejected(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    kb, retrieval = mixed_kolla_host_kb(v2_kb)
    invalid = deep_mapping_response(
        _mixed_selection("kolla_ansible", "kolla_ansible", []),
        procedure_template_ids=(),
    )
    outcome = map_atom_deep(FakeModel([invalid, invalid]), atom(), retrieval, kb)

    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert "HOST_OS_RECORD_REQUIRED" in outcome.atom_result.diagnostics[0]


@pytest.mark.parametrize(
    "related_indexes",
    ([1], [2], [3], [2, 2]),
)
def test_invalid_related_indexes_fail_closed(v2_kb, related_indexes) -> None:
    from reqmap.deep_mapping import map_atom_deep

    kb, retrieval = mixed_kolla_host_kb(v2_kb)
    first = _mixed_selection("kolla_ansible", "kolla_ansible", related_indexes)
    second = _mixed_selection("host_os", "rocky_linux_9", [])
    invalid = deep_mapping_response(first, second, procedure_template_ids=())
    outcome = map_atom_deep(FakeModel([invalid, invalid]), atom(), retrieval, kb)
    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED


def test_reversed_model_order_produces_identical_ids_and_records(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep
    from reqmap.models import to_dict

    kb, retrieval = mixed_kolla_host_kb(v2_kb)
    kolla_first = deep_mapping_response(
        _mixed_selection("kolla_ansible", "kolla_ansible", [2]),
        _mixed_selection("host_os", "rocky_linux_9", [1]),
        procedure_template_ids=(),
    )
    host_first = deep_mapping_response(
        _mixed_selection("host_os", "rocky_linux_9", [2]),
        _mixed_selection("kolla_ansible", "kolla_ansible", [1]),
        procedure_template_ids=(),
    )
    first = map_atom_deep(FakeModel([kolla_first]), atom(), retrieval, kb)
    second = map_atom_deep(FakeModel([host_first]), atom(), retrieval, kb)
    assert to_dict(first) == to_dict(second)


def test_duplicate_semantic_responsibility_is_rejected_before_ids(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep

    duplicate = responsibility_selection()
    invalid = deep_mapping_response(
        duplicate,
        responsibility_selection(),
        procedure_template_ids=("PROC-NOVA-CREATE",),
    )
    outcome = map_atom_deep(
        FakeModel([invalid, invalid]), atom(), deep_candidate(v2_kb), v2_kb
    )

    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert "RESPONSIBILITY_SEMANTIC_DUPLICATE" in outcome.atom_result.diagnostics[0]


@pytest.mark.parametrize("extra_contour", ("kolla_ansible", "host_os"))
def test_kolla_host_bundle_rejects_extra_record_cardinality(v2_kb, extra_contour) -> None:
    from reqmap.deep_mapping import map_atom_deep

    kb, retrieval = mixed_kolla_host_kb(v2_kb)
    component = "kolla_ansible" if extra_contour == "kolla_ansible" else "rocky_linux_9"
    records = [
        _mixed_selection("kolla_ansible", "kolla_ansible", [2, 3]),
        _mixed_selection("host_os", "rocky_linux_9", [1, 3]),
        _mixed_selection(extra_contour, component, [1, 2]),
    ]
    records[2]["evidence_ids"] = []
    records[2]["support_status"] = "insufficient_evidence"
    invalid = deep_mapping_response(*records, procedure_template_ids=())

    outcome = map_atom_deep(FakeModel([invalid, invalid]), atom(), retrieval, kb)

    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert "HOST_OS_BUNDLE_CARDINALITY" in outcome.atom_result.diagnostics[0]


def test_template_from_unselected_retrieval_candidate_is_rejected(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep
    from reqmap.deep_models import DeepRetrievalResult

    kb, kolla_retrieval = mixed_kolla_host_kb(v2_kb)
    nova_retrieval = deep_candidate(kb)
    retrieval = DeepRetrievalResult(
        (*nova_retrieval.normalized_candidates, *kolla_retrieval.normalized_candidates),
        (),
    )
    selected = (
        _mixed_selection("kolla_ansible", "kolla_ansible", [2]),
        _mixed_selection("host_os", "rocky_linux_9", [1]),
    )
    invalid = deep_mapping_response(
        *selected,
        procedure_template_ids=("PROC-NOVA-CREATE",),
    )

    outcome = map_atom_deep(FakeModel([invalid, invalid]), atom(), retrieval, kb)

    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert "PROCEDURE_TEMPLATE_TRIGGER_NOT_SELECTED" in outcome.atom_result.diagnostics[0]


def test_prompt_payload_excludes_urls_local_excerpts_locators_and_commands(v2_kb) -> None:
    from reqmap.deep_mapping import map_atom_deep
    from reqmap.prompts import PROMPT_DEEP_MAPPING_VERSION, PROMPT_MAPPING_VERSION

    model = FakeModel([deep_mapping_response()])
    map_atom_deep(model, atom(), deep_candidate(v2_kb), v2_kb)
    serialized = str(model.calls[0][2])
    assert PROMPT_DEEP_MAPPING_VERSION == "2.2"
    assert PROMPT_MAPPING_VERSION == "1.4"
    assert "https://" not in serialized
    assert "local_excerpt" not in serialized
    assert "locator" not in serialized
    assert "POST /v2.1/servers" not in serialized


def test_model_cannot_hide_applicable_conflicting_candidate_evidence(v2_kb):
    from reqmap.deep_mapping import map_atom_deep
    kb = with_nova_evidence(v2_kb, "EV-NOVA-NEGATIVE", polarity=EvidencePolarity.NEGATIVE)
    outcome = map_atom_deep(
        FakeModel([deep_mapping_response()]), atom(),
        deep_candidate(kb, "EV-NOVA-CREATE", "EV-NOVA-NEGATIVE"), kb,
    )
    assert outcome.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "evidence_conflict" in outcome.atom_result.diagnostics
    assert set(outcome.responsibility_records[0].evidence_ids) == {"EV-NOVA-CREATE", "EV-NOVA-NEGATIVE"}
