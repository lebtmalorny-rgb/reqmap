"""Behavioural contracts for deterministic local evidence retrieval."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from reqmap.knowledge import load_knowledge
from reqmap.models import SourceHint, to_dict
from reqmap.retrieval import candidate_score, retrieve


SNAPSHOT = Path("knowledge/epoxy-2025.1")


@pytest.fixture(scope="module")
def kb():
    return load_knowledge(SNAPSHOT)


def test_candidate_score_uses_the_published_weights() -> None:
    assert candidate_score(1, 2, 3, 4, 5) == 8.0 + 10.0 + 9.0 + 8.0 + 2.5


def test_retrieve_nova_server_api_uses_actual_epoxy_evidence(kb) -> None:
    candidates = retrieve(kb, "создание виртуальной машины через API", (), 5)

    assert candidates[0].component_id == "nova"
    assert candidates[0].capability_id == "CAP-NOVA-COMPUTE-API"
    assert candidates[0].evidence_ids == ("E-NOVA-SCOPE-001",)


def test_source_hint_creates_candidate_without_creating_evidence(kb) -> None:
    candidates = retrieve(kb, "неопределённая функция", (SourceHint("Nova", "Компонент"),), 5)

    nova = next(item for item in candidates if item.component_id == "nova")
    assert "source_hint" in nova.reasons
    assert nova.evidence_ids == ()


def test_evidence_only_word_does_not_attach_direct_evidence(kb) -> None:
    candidates = retrieve(kb, "vendor-specific", (), 5)

    ironic = next(item for item in candidates if item.component_id == "ironic")
    assert ironic.evidence_ids == ()


def test_registered_synonym_expansion_is_nfkc_case_and_yo_invariant(kb) -> None:
    candidates = retrieve(kb, "ВИРТУАЛЬНАЯ МАШИНА", (), 5)

    assert candidates[0].component_id == "nova"
    assert candidates[0].score > 0


@pytest.mark.parametrize("top_k", [0, -1])
def test_retrieve_rejects_non_positive_top_k(kb, top_k: int) -> None:
    with pytest.raises(ValueError, match="top_k"):
        retrieve(kb, "nova", (), top_k)


def test_retrieve_is_byte_for_byte_deterministic_and_bounded(kb) -> None:
    first = retrieve(kb, "виртуальная машина через API", (), 1)
    second = retrieve(kb, "виртуальная машина через API", (), 1)

    assert json.dumps(to_dict(first), ensure_ascii=False, sort_keys=True) == json.dumps(
        to_dict(second), ensure_ascii=False, sort_keys=True
    )
    assert len(first) <= 1
    assert all(item.component_id in kb.components for item in first)
    assert all(item.capability_id in kb.capabilities for item in first)
    assert all(evidence_id in kb.evidence for item in first for evidence_id in item.evidence_ids)


@pytest.mark.parametrize(
    ("text", "forbidden"),
    [
        ("графический интерфейс управления", "horizon"),
        ("балансировка нагрузки виртуальных машин", "octavia"),
        ("выставление счетов", "ceilometer"),
        ("полноценная поддержка vGPU", "nova"),
    ],
)
def test_generic_or_unrelated_terms_do_not_create_unproved_direct_evidence(kb, text: str, forbidden: str) -> None:
    candidates = retrieve(kb, text, (), 8)

    candidate = next((item for item in candidates if item.component_id == forbidden), None)
    assert candidate is None or candidate.evidence_ids == ()


def test_host_os_candidate_appends_kolla_with_official_and_policy_context(kb) -> None:
    candidates = retrieve(kb, "применить net.ipv4.ip_forward через sysctl", (), 1)

    host = next(item for item in candidates if item.component_id == "host_os_kernel_sysctl")
    kolla = next(item for item in candidates if item.component_id == "kolla_ansible")
    assert host.evidence_ids == (
        "E-HOST-SYSCTL-OFFICIAL-001",
        "E-HOST-SYSCTL-POLICY-001",
    )
    assert "E-KOLLA-RECONFIGURE-001" in kolla.evidence_ids
    assert "E-HOST-SYSCTL-POLICY-001" in kolla.evidence_ids
    assert "E-HOST-SYSCTL-OFFICIAL-001" not in kolla.evidence_ids
    assert "host_os_policy_dependency" in kolla.reasons
    assert len(candidates) == 2


def test_host_policy_is_not_emitted_without_official_kolla_evidence(kb) -> None:
    without_kolla_official = replace(
        kb,
        evidence=MappingProxyType(
            {
                evidence_id: evidence
                for evidence_id, evidence in kb.evidence.items()
                if evidence_id != "E-KOLLA-RECONFIGURE-001"
            }
        ),
    )

    candidates = retrieve(without_kolla_official, "применить sysctl", (), 1)

    kolla = next(item for item in candidates if item.component_id == "kolla_ansible")
    assert kolla.evidence_ids == ()
