"""Fail-closed validation of the immutable schema-v2 knowledge graph."""

from __future__ import annotations

from dataclasses import replace
import json
from types import MappingProxyType

import pytest

from reqmap.deep_models import LifecyclePhase
from reqmap.knowledge_v2 import (
    KnowledgeV2Error,
    TargetRecord,
    load_knowledge_v2,
    load_knowledge_v2_for_maintenance,
    validate_knowledge_v2,
)
from reqmap.models import EvidencePolarity, EvidenceStrength
from reqmap.snapshot_trust import SnapshotTrustError, verify_snapshot
from tests.deep_factories import immutable_v2_kb, signed_v2_snapshot


@pytest.fixture
def v2_kb(tmp_path):
    return immutable_v2_kb(tmp_path)


def _replace_record(kb, collection: str, object_id: str, **changes):
    records = getattr(kb, collection)
    updated = dict(records)
    updated[object_id] = replace(records[object_id], **changes)
    return replace(kb, **{collection: MappingProxyType(updated)})


def _replace_step(kb, template_id: str = "PROC-NOVA-CREATE", **changes):
    procedure = kb.procedures[template_id]
    changed = replace(procedure.steps[0], **changes)
    return _replace_record(kb, "procedures", template_id, steps=(changed,))


def _unknown_capability_component(kb):
    return _replace_record(
        kb, "capabilities", "CAP-NOVA-CREATE", component_ref="missing-component"
    )


def _unknown_action_component(kb):
    return _replace_record(
        kb, "actions", "ACTION-NOVA-CREATE", component_ref="missing-component"
    )


def _unknown_action_target(kb):
    return _replace_record(
        kb, "actions", "ACTION-NOVA-CREATE", target_ref="missing-target"
    )


def _unknown_action_effect(kb):
    return _replace_record(
        kb, "actions", "ACTION-NOVA-CREATE", effect_refs=("missing-effect",)
    )


def _foreign_effect_action(kb):
    targets = dict(kb.targets)
    targets["TARGET-OTHER"] = TargetRecord(
        "TARGET-OTHER",
        kb.targets["TARGET-NOVA-SERVER"].contour,
        "nova",
        None,
    )
    kb = replace(kb, targets=MappingProxyType(targets))
    return _replace_record(
        kb, "effects", "EFFECT-NOVA-SERVER-ACTIVE", target_ref="TARGET-OTHER"
    )


def _unknown_effect_target(kb):
    return _replace_record(
        kb, "effects", "EFFECT-NOVA-SERVER-ACTIVE", target_ref="missing-target"
    )


def _unknown_evidence_source(kb):
    return _replace_record(
        kb, "evidence", "EV-NOVA-CREATE", source_id="missing-source"
    )


def _unknown_supported_entity(kb):
    return _replace_record(
        kb,
        "evidence",
        "EV-NOVA-CREATE",
        supports_entity_refs=("missing-entity",),
    )


def _unknown_capability_evidence(kb):
    return _replace_record(
        kb, "capabilities", "CAP-NOVA-CREATE", evidence_ids=("missing-evidence",)
    )


def _unknown_action_evidence(kb):
    return _replace_record(
        kb, "actions", "ACTION-NOVA-CREATE", evidence_ids=("missing-evidence",)
    )


def _unknown_effect_evidence(kb):
    return _replace_record(
        kb, "effects", "EFFECT-NOVA-SERVER-ACTIVE", evidence_ids=("missing-evidence",)
    )


def _unknown_template_action(kb):
    return _replace_record(
        kb, "procedures", "PROC-NOVA-CREATE", action_refs=("missing-action",)
    )


def _unknown_step_action(kb):
    return _replace_step(kb, action_ref="missing-action")


def _unknown_step_evidence(kb):
    return _replace_step(kb, evidence_ids=("missing-evidence",))


def _unknown_dependency(kb):
    return _replace_step(kb, depends_on=("other-template-step",))


def _unknown_rollback(kb):
    return _replace_step(kb, rollback_step_local_id="other-template-step")


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            _unknown_capability_component,
            ("CAPABILITY_COMPONENT_UNKNOWN", "CAP-NOVA-CREATE"),
        ),
        (_unknown_action_component, ("ACTION_COMPONENT_UNKNOWN", "ACTION-NOVA-CREATE")),
        (_unknown_action_target, ("ACTION_TARGET_UNKNOWN", "ACTION-NOVA-CREATE")),
        (_unknown_action_effect, ("ACTION_EFFECT_UNKNOWN", "ACTION-NOVA-CREATE")),
        (_foreign_effect_action, ("ACTION_EFFECT_MISMATCH", "ACTION-NOVA-CREATE")),
        (
            _unknown_effect_target,
            ("EFFECT_TARGET_UNKNOWN", "EFFECT-NOVA-SERVER-ACTIVE"),
        ),
        (_unknown_evidence_source, ("EVIDENCE_SOURCE_UNKNOWN", "EV-NOVA-CREATE")),
        (_unknown_supported_entity, ("EVIDENCE_ENTITY_UNKNOWN", "EV-NOVA-CREATE")),
        (_unknown_capability_evidence, ("EVIDENCE_UNKNOWN", "CAP-NOVA-CREATE")),
        (_unknown_action_evidence, ("EVIDENCE_UNKNOWN", "ACTION-NOVA-CREATE")),
        (
            _unknown_effect_evidence,
            ("EVIDENCE_UNKNOWN", "EFFECT-NOVA-SERVER-ACTIVE"),
        ),
        (_unknown_template_action, ("PROCEDURE_ACTION_UNKNOWN", "PROC-NOVA-CREATE")),
        (
            _unknown_step_action,
            ("PROCEDURE_ACTION_UNKNOWN", "PROC-NOVA-CREATE:create-server"),
        ),
        (
            _unknown_step_evidence,
            ("EVIDENCE_UNKNOWN", "PROC-NOVA-CREATE:create-server"),
        ),
        (
            _unknown_dependency,
            ("PROCEDURE_STEP_UNKNOWN", "PROC-NOVA-CREATE:create-server"),
        ),
        (
            _unknown_rollback,
            ("PROCEDURE_ROLLBACK_UNKNOWN", "PROC-NOVA-CREATE:create-server"),
        ),
    ],
)
def test_validate_knowledge_v2_checks_reference_ownership(v2_kb, mutator, expected) -> None:
    broken = mutator(v2_kb)

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert expected in actual


@pytest.mark.parametrize(
    ("collection", "object_id"),
    [
        ("capabilities", "CAP-NOVA-CREATE"),
        ("actions", "ACTION-NOVA-CREATE"),
        ("effects", "EFFECT-NOVA-SERVER-ACTIVE"),
    ],
)
def test_positive_entities_require_explicit_direct_positive_evidence(
    v2_kb, collection: str, object_id: str
) -> None:
    evidence = replace(
        v2_kb.evidence["EV-NOVA-CREATE"], strength=EvidenceStrength.INDIRECT
    )
    broken = replace(v2_kb, evidence=MappingProxyType({evidence.evidence_id: evidence}))

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("DIRECT_EVIDENCE_REQUIRED", object_id) in actual


def test_direct_evidence_must_explicitly_support_the_positive_entity(v2_kb) -> None:
    broken = _replace_record(
        v2_kb,
        "evidence",
        "EV-NOVA-CREATE",
        supports_entity_refs=("CAP-NOVA-CREATE", "ACTION-NOVA-CREATE"),
    )

    issues = validate_knowledge_v2(broken)

    assert ("DIRECT_EVIDENCE_REQUIRED", "EFFECT-NOVA-SERVER-ACTIVE") in {
        (issue.code, issue.object_id) for issue in issues
    }


def test_project_policy_cannot_establish_upstream_capability(v2_kb) -> None:
    source = replace(
        v2_kb.sources["SRC-MINIMAL"],
        source_type="project_policy",
        provenance="project_policy",
        source_url=None,
    )
    broken = replace(v2_kb, sources=MappingProxyType({source.source_id: source}))

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("DIRECT_EVIDENCE_REQUIRED", "CAP-NOVA-CREATE") in actual


def test_direct_project_policy_may_support_a_workflow_relation(v2_kb) -> None:
    base = v2_kb.evidence["EV-NOVA-CREATE"]
    policy_source = replace(
        v2_kb.sources["SRC-MINIMAL"],
        source_id="SRC-POLICY",
        source_type="project_policy",
        project="reqmap",
        provenance="project_policy",
        source_url=None,
    )
    policy = replace(
        base,
        evidence_id="EV-POLICY",
        source_id="SRC-POLICY",
        supports_entity_refs=("PROC-NOVA-CREATE",),
    )
    sources = MappingProxyType({**v2_kb.sources, policy_source.source_id: policy_source})
    evidence = MappingProxyType({**v2_kb.evidence, policy.evidence_id: policy})
    kb = replace(v2_kb, sources=sources, evidence=evidence)
    kb = _replace_step(kb, evidence_ids=("EV-NOVA-CREATE", "EV-POLICY"))

    assert validate_knowledge_v2(kb) == ()


def test_negative_evidence_is_direct_and_supports_a_known_entity(v2_kb) -> None:
    negative = replace(
        v2_kb.evidence["EV-NOVA-CREATE"],
        polarity=EvidencePolarity.NEGATIVE,
        strength=EvidenceStrength.INDIRECT,
    )
    broken = replace(v2_kb, evidence=MappingProxyType({negative.evidence_id: negative}))

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("NEGATIVE_DIRECT_EVIDENCE_REQUIRED", "EV-NOVA-CREATE") in actual


def test_negative_evidence_without_supported_entity_fails_closed(v2_kb) -> None:
    negative = replace(
        v2_kb.evidence["EV-NOVA-CREATE"],
        polarity=EvidencePolarity.NEGATIVE,
        supports_entity_refs=(),
    )
    broken = replace(v2_kb, evidence=MappingProxyType({negative.evidence_id: negative}))

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("EVIDENCE_ENTITY_REQUIRED", "EV-NOVA-CREATE") in actual


def test_direct_negative_evidence_may_coexist_with_positive_proof(v2_kb) -> None:
    negative = replace(
        v2_kb.evidence["EV-NOVA-CREATE"],
        evidence_id="EV-NOVA-BOUNDARY",
        polarity=EvidencePolarity.NEGATIVE,
        supports_entity_refs=("ACTION-NOVA-CREATE",),
    )
    kb = replace(
        v2_kb,
        evidence=MappingProxyType({**v2_kb.evidence, negative.evidence_id: negative}),
    )

    assert validate_knowledge_v2(kb) == ()


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"local_path": "corpus/missing.md"}, "SOURCE_FILE_MISSING"),
        ({"content_sha256": "0" * 64}, "SOURCE_SHA256_MISMATCH"),
    ],
)
def test_evidence_source_must_resolve_to_verified_local_bytes(
    v2_kb, changes, expected_code: str
) -> None:
    broken = _replace_record(v2_kb, "sources", "SRC-MINIMAL", **changes)

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert (expected_code, "SRC-MINIMAL") in actual
    assert ("DIRECT_EVIDENCE_REQUIRED", "ACTION-NOVA-CREATE") in actual


def test_2026_1_action_scope_requires_upgrade_lifecycle(v2_kb) -> None:
    scope = replace(
        v2_kb.actions["ACTION-NOVA-CREATE"].version_scope,
        target_release="2026.1",
    )
    broken = _replace_record(
        v2_kb, "actions", "ACTION-NOVA-CREATE", version_scope=scope
    )

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("VERSION_SCOPE_INVALID", "ACTION-NOVA-CREATE") in actual


def test_2026_1_action_scope_is_allowed_for_upgrade(v2_kb) -> None:
    scope = replace(
        v2_kb.actions["ACTION-NOVA-CREATE"].version_scope,
        target_release="2026.1",
    )
    kb = _replace_record(v2_kb, "actions", "ACTION-NOVA-CREATE", version_scope=scope)
    procedure = kb.procedures["PROC-NOVA-CREATE"]
    step = replace(procedure.steps[0], phase=LifecyclePhase.UPGRADE)
    kb = _replace_record(
        kb,
        "procedures",
        procedure.template_id,
        lifecycle_phase=LifecyclePhase.UPGRADE,
        steps=(step,),
    )

    assert validate_knowledge_v2(kb) == ()


@pytest.mark.parametrize(
    ("mutator", "object_id"),
    [
        (lambda kb: replace(kb, host_profile="ubuntu_22_04"), "metadata"),
        (
            lambda kb: _replace_record(
                kb,
                "actions",
                "ACTION-NOVA-CREATE",
                version_scope=replace(
                    kb.actions["ACTION-NOVA-CREATE"].version_scope,
                    host_profile="ubuntu_22_04",
                ),
            ),
            "ACTION-NOVA-CREATE",
        ),
        (
            lambda kb: _replace_record(
                kb, "targets", "TARGET-NOVA-SERVER", host_profile="ubuntu_22_04"
            ),
            "TARGET-NOVA-SERVER",
        ),
    ],
)
def test_host_profile_is_rocky_linux_9(v2_kb, mutator, object_id: str) -> None:
    actual = {
        (issue.code, issue.object_id)
        for issue in validate_knowledge_v2(mutator(v2_kb))
    }

    assert ("HOST_PROFILE_INVALID", object_id) in actual


@pytest.mark.parametrize(
    "changes",
    [
        {"base_release": "2026.1"},
        {"kolla_ansible_release": "2026.1"},
        {"upgrade_target": "2027.1"},
    ],
)
def test_metadata_release_baseline_is_fixed(v2_kb, changes) -> None:
    issues = validate_knowledge_v2(replace(v2_kb, **changes))

    assert ("VERSION_SCOPE_INVALID", "metadata") in {
        (issue.code, issue.object_id) for issue in issues
    }


def test_procedure_dependencies_must_be_acyclic(v2_kb) -> None:
    broken = _replace_step(v2_kb, depends_on=("create-server",))

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("PROCEDURE_CYCLE", "PROC-NOVA-CREATE") in actual


def test_procedure_rollback_references_must_be_acyclic(v2_kb) -> None:
    broken = _replace_step(v2_kb, rollback_step_local_id="create-server")

    actual = {(issue.code, issue.object_id) for issue in validate_knowledge_v2(broken)}

    assert ("PROCEDURE_CYCLE", "PROC-NOVA-CREATE") in actual


def test_validation_reports_all_issues_once_in_deterministic_order(v2_kb) -> None:
    broken = _replace_record(
        v2_kb,
        "actions",
        "ACTION-NOVA-CREATE",
        component_ref="missing-component",
        target_ref="missing-target",
        effect_refs=("missing-effect", "missing-effect"),
        evidence_ids=("missing-evidence", "missing-evidence"),
    )

    issues = validate_knowledge_v2(broken)

    assert issues == tuple(
        sorted(
            set(issues),
            key=lambda item: (item.code, item.object_id, item.message_ru),
        )
    )
    assert {issue.code for issue in issues} >= {
        "ACTION_COMPONENT_UNKNOWN",
        "ACTION_TARGET_UNKNOWN",
        "ACTION_EFFECT_UNKNOWN",
        "EVIDENCE_UNKNOWN",
        "DIRECT_EVIDENCE_REQUIRED",
    }


def test_allow_draft_only_permits_declared_draft_status(v2_kb) -> None:
    draft = replace(v2_kb, snapshot_status="draft")
    broken_draft = _unknown_action_target(draft)

    assert ("SNAPSHOT_STATUS_INVALID", "metadata") in {
        (issue.code, issue.object_id) for issue in validate_knowledge_v2(draft)
    }
    assert validate_knowledge_v2(draft, allow_draft=True) == ()
    assert ("ACTION_TARGET_UNKNOWN", "ACTION-NOVA-CREATE") in {
        (issue.code, issue.object_id)
        for issue in validate_knowledge_v2(broken_draft, allow_draft=True)
    }


def test_loader_rejects_invalid_graph_with_first_deterministic_issue(tmp_path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    action_path = root / "actions.jsonl"
    action = json.loads(action_path.read_text(encoding="utf-8"))
    action["target_ref"] = "missing-target"
    action_path.write_text(json.dumps(action, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)

    assert error.value.code == "ACTION_TARGET_UNKNOWN"


def test_runtime_source_validation_uses_manifest_verified_bytes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)

    def verify_then_swap(path, signers):
        trust = verify_snapshot(path, signers)
        source = path / "corpus" / "SRC-MINIMAL.md"
        source.write_bytes(b"X" * source.stat().st_size)
        return trust

    monkeypatch.setattr("reqmap.knowledge_v2.verify_snapshot", verify_then_swap)

    with pytest.raises(SnapshotTrustError) as error:
        load_knowledge_v2(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"
