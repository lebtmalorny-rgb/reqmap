"""Reviewed API proofs preserve list detail and role-assignment scope."""
from dataclasses import replace
from pathlib import Path
import shutil

import pytest

from reqmap.binding_catalog import load_binding_catalog
from reqmap.binding_engine import check_binding
from reqmap.binding_models import BoundObligation, SourceSpan
from reqmap.errors import ReqmapError
from reqmap.knowledge import load_knowledge
from reqmap.models import EvidenceClaimScope, EvidencePolarity, EvidenceStrength, SupportStatus
from reqmap.retrieval import retrieve


KB = Path("knowledge/epoxy-2025.1")
CATALOG = Path("knowledge/bindings/epoxy-2025.1-api")
# Hand-checked operation contracts, independent of production annotations.
CASES = [
    ("CINDER-VOLUME-LIST", "cinder", "list", "volume",
     "Список доступных томов", "GET /v3/{project_id}/volumes",
     "SRC-API-CINDER-VOLUME-LIST-2025.1", "CINDER-VOLUME-LIST-DETAIL"),
    ("CINDER-VOLUME-LIST-DETAIL", "cinder", "list_detail", "volume",
     "Список доступных томов с деталями", "GET /v3/{project_id}/volumes/detail",
     "SRC-API-CINDER-VOLUME-LIST-2025.1", "CINDER-VOLUME-LIST"),
    ("KEYSTONE-PROJECT-USER-ROLE-ASSIGN", "keystone", "assign", "project_user_role",
     "Назначение роли пользователю проекта",
     "PUT /v3/projects/{project_id}/users/{user_id}/roles/{role_id}",
     "SRC-API-KEYSTONE-USER-ROLE-ASSIGN-2025.1", "KEYSTONE-DOMAIN-USER-ROLE-ASSIGN"),
    ("KEYSTONE-DOMAIN-USER-ROLE-ASSIGN", "keystone", "assign", "domain_user_role",
     "Назначение роли пользователю домена",
     "PUT /v3/domains/{domain_id}/users/{user_id}/roles/{role_id}",
     "SRC-API-KEYSTONE-USER-ROLE-ASSIGN-2025.1", "KEYSTONE-PROJECT-USER-ROLE-ASSIGN"),
]


@pytest.fixture(scope="module")
def package():
    kb = load_knowledge(KB)
    return kb, load_binding_catalog(CATALOG, kb)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
@pytest.mark.parametrize("include_endpoint", [False, True])
def test_real_operation_label_retrieves_its_specific_official_proof(package, case, include_endpoint):
    key, actor, _, _, label, operation, source_id, _ = case
    text = label + (": " + operation + ";" if include_endpoint else "")
    kb, _ = package
    matches = [item for item in retrieve(kb, text, (), 8) if item.capability_id == "CAP-" + key]
    assert len(matches) == 1, "The operation must be discoverable in the normal top-8 context"
    assert matches[0].component_id == actor
    assert matches[0].evidence_ids == ("E-" + key,)
    evidence = kb.evidence["E-" + key]
    assert evidence.claim_scope is EvidenceClaimScope.SPECIFIC
    assert evidence.strength is EvidenceStrength.DIRECT
    assert evidence.polarity is EvidencePolarity.POSITIVE
    assert evidence.provenance == "official"
    assert evidence.version_constraint == "2025.1"
    assert evidence.source_id == source_id
    assert operation in evidence.claim_ru
    assert operation in evidence.locator


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
def test_predicate_proves_only_its_exact_api_operation_scope(package, case):
    key, actor, action, obj, label, operation, source_id, other_key = case
    text = label + ": " + operation + ";"
    own = BoundObligation("REQ-0001-O001", "REQ-0001", (SourceSpan(0, len(text), text),),
        text, "bound", "test.explicit-api", True, actor, action, obj, "capability",
        "api", "openstack_runtime", "runtime", "2025.1")
    kb, catalog = package
    result = check_binding(own, ("P-" + key,), catalog, SupportStatus.SUPPORTED, ("E-" + key,))
    assert result.support_status is SupportStatus.SUPPORTED, result.diagnostics
    assert result.predicate_ids == ("P-" + key,)
    predicate = catalog.predicates["P-" + key]
    assert predicate.source_ids == (source_id,)
    assert predicate.source_sha256s == (kb.sources[source_id].sha256,)
    assert predicate.evidence_ids == ("E-" + key,)
    wrong_scope = check_binding(own, ("P-" + other_key,), catalog,
        SupportStatus.SUPPORTED, ("E-" + other_key,))
    assert wrong_scope.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert any(d.code in {"SOURCE_ACTION_UNPROVEN", "SOURCE_OBJECT_UNPROVEN"}
               for d in wrong_scope.diagnostics)
    gui = check_binding(replace(own, interface="gui"), ("P-" + key,), catalog,
        SupportStatus.SUPPORTED, ("E-" + key,))
    assert gui.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert any(d.code == "SOURCE_INTERFACE_UNPROVEN" for d in gui.diagnostics)


@pytest.mark.parametrize("source_id", [
    "SRC-API-CINDER-VOLUME-LIST-2025.1", "SRC-API-KEYSTONE-USER-ROLE-ASSIGN-2025.1",
])
def test_new_source_note_cannot_change_without_integrity_failure(tmp_path, package, source_id):
    kb, _ = package
    assert source_id in kb.sources, "New proof must have its own immutable source identity"
    copied = tmp_path / "kb"
    shutil.copytree(KB, copied)
    source_path = copied / kb.sources[source_id].local_path
    source_path.write_bytes(source_path.read_bytes() + b"\nchanged proof\n")
    with pytest.raises(ReqmapError) as exc:
        load_knowledge(copied, verify_snapshot_hash=False)
    assert exc.value.code == "SOURCE_SHA256_MISMATCH"
