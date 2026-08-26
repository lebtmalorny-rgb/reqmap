"""Contracts for deterministic, offline schema-v2 retrieval."""

from __future__ import annotations

from dataclasses import replace
import math
from types import MappingProxyType

import pytest

from reqmap.deep_models import VersionScope
from reqmap.knowledge_v2 import ActionRecord, DeepCapabilityRecord
from reqmap.models import SourceHint, to_dict


@pytest.fixture
def v2_kb(tmp_path):
    from tests.deep_factories import immutable_v2_kb

    return immutable_v2_kb(tmp_path)


class _CorpusProvider:
    def __init__(self, candidates):
        self.candidates = candidates
        self.top_ks: list[int] = []

    def retrieve(self, text: str, *, top_k: int):
        self.top_ks.append(top_k)
        return self.candidates


def _with_tied_capability(kb):
    from reqmap.deep_models import DeepEvidence

    original = kb.capabilities["CAP-NOVA-CREATE"]
    tied = DeepCapabilityRecord(
        "CAP-NOVA-ALPHA",
        original.component_ref,
        original.name_ru,
        original.terms,
        original.evidence_ids,
    )
    evidence = kb.evidence["EV-NOVA-CREATE"]
    assert isinstance(evidence, DeepEvidence)
    changed_evidence = replace(
        evidence,
        supports_entity_refs=("CAP-NOVA-ALPHA", *evidence.supports_entity_refs),
    )
    return replace(
        kb,
        capabilities=MappingProxyType({**kb.capabilities, tied.capability_id: tied}),
        evidence=MappingProxyType({changed_evidence.evidence_id: changed_evidence}),
    )


def _with_upgrade_action(kb):
    baseline = kb.actions["ACTION-NOVA-CREATE"]
    upgrade = ActionRecord(
        "ACTION-NOVA-UPGRADE",
        baseline.component_ref,
        baseline.contour,
        baseline.interface_type,
        "upgrade to 2026.1",
        baseline.target_ref,
        baseline.effect_refs,
        VersionScope("2025.1", "2026.1", "2025.1", "rocky_linux_9", "upgrade 2026.1"),
        baseline.evidence_ids,
        baseline.procedure_required,
    )
    evidence = replace(
        kb.evidence["EV-NOVA-CREATE"],
        supports_entity_refs=(
            *kb.evidence["EV-NOVA-CREATE"].supports_entity_refs,
            upgrade.action_id,
        ),
    )
    return replace(
        kb,
        actions=MappingProxyType({**kb.actions, upgrade.action_id: upgrade}),
        evidence=MappingProxyType({evidence.evidence_id: evidence}),
    )


def test_deep_retrieval_is_deterministic_stably_tied_and_version_filtered(v2_kb) -> None:
    """Unstable sort order or inclusion of an upgrade action makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    kb = _with_tied_capability(_with_upgrade_action(v2_kb))
    first = retrieve_deep(kb, "создать виртуальную машину через API", (), 8)
    second = retrieve_deep(kb, "создать виртуальную машину через API", (), 8)

    assert to_dict(first) == to_dict(second)
    assert [item.capability_ref for item in first.normalized_candidates] == [
        "CAP-NOVA-ALPHA",
        "CAP-NOVA-CREATE",
    ]
    assert all(item.version_scope.target_release == "2025.1" for item in first.normalized_candidates)


def test_deep_retrieval_requires_complete_tokens_and_expands_existing_synonyms(v2_kb) -> None:
    """Substring matching or omitted KB synonym expansion makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    assert retrieve_deep(v2_kb, "supernova", (), 8).normalized_candidates == ()
    synonym = retrieve_deep(v2_kb, "ЗАПУСК ВМ", (), 8)
    assert synonym.normalized_candidates[0].capability_ref == "CAP-NOVA-CREATE"
    assert retrieve_deep(v2_kb, "ＮＯＶＡ", (), 8).normalized_candidates


def test_deep_retrieval_joins_only_explicit_capability_action_effect_links(v2_kb) -> None:
    """Dropping direct evidence/action/effect joins makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    candidate = retrieve_deep(v2_kb, "создание сервера", (), 1).normalized_candidates[0]

    assert candidate.component_ref == "nova"
    assert candidate.capability_ref == "CAP-NOVA-CREATE"
    assert candidate.action_ref == "ACTION-NOVA-CREATE"
    assert candidate.effect_ref == "EFFECT-NOVA-SERVER-ACTIVE"
    assert candidate.evidence_ids == ("EV-NOVA-CREATE",)


def test_explicit_upgrade_query_may_retain_only_loaded_upgrade_scoped_action(v2_kb) -> None:
    """Inferring 2026.1 support without the action record makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    result = retrieve_deep(_with_upgrade_action(v2_kb), "upgrade 2026.1", (), 8)

    assert {item.action_ref for item in result.normalized_candidates} == {"ACTION-NOVA-UPGRADE"}
    assert {item.version_scope.target_release for item in result.normalized_candidates} == {"2026.1"}


def test_source_hints_are_lexical_input_without_untrusted_evidence(v2_kb) -> None:
    """Ignoring a supplied hint or manufacturing evidence from it makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    result = retrieve_deep(v2_kb, "редкий термин", (SourceHint("запуск ВМ", "hint"),), 8)

    assert result.normalized_candidates[0].evidence_ids == ("EV-NOVA-CREATE",)
    assert "source_hint" in result.normalized_candidates[0].reasons


def test_source_hint_cannot_reclassify_a_generic_query_as_upgrade(v2_kb) -> None:
    """Letting a hint bypass the release boundary makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    result = retrieve_deep(
        _with_upgrade_action(v2_kb),
        "редкий термин",
        (SourceHint("upgrade", "hint"),),
        8,
    )

    assert all(item.version_scope.target_release == "2025.1" for item in result.normalized_candidates)


def test_corpus_candidate_is_discovery_only_and_bounded(v2_kb) -> None:
    """Turning provider output into deep evidence or ignoring top_k makes this fail."""
    from reqmap.deep_retrieval import CorpusCandidate, retrieve_deep

    source = v2_kb.sources["SRC-MINIMAL"]
    provider = _CorpusProvider(
        (
            CorpusCandidate(source.source_id, source.local_path, source.content_sha256, 2.0, ("second",)),
            CorpusCandidate(source.source_id, source.local_path, source.content_sha256, 3.0, ("first",)),
        )
    )

    result = retrieve_deep(v2_kb, "редкий термин", (), 1, provider)

    assert provider.top_ks == [1]
    assert len(result.corpus_candidates) == 1
    assert result.corpus_candidates[0].score == 3.0
    assert not hasattr(result.corpus_candidates[0], "evidence_ids")
    assert result.normalized_candidates == ()


@pytest.mark.parametrize(
    "candidate",
    [
        ("UNKNOWN", "corpus/SRC-MINIMAL.md", "a" * 64, 1.0, ("reason",)),
        ("SRC-MINIMAL", "https://example.invalid/source", "a" * 64, 1.0, ("reason",)),
        ("SRC-MINIMAL", "corpus/SRC-MINIMAL.md", "0" * 64, 1.0, ("reason",)),
        ("SRC-MINIMAL", "corpus/SRC-MINIMAL.md", "a" * 64, math.nan, ("reason",)),
        ("SRC-MINIMAL", "corpus/SRC-MINIMAL.md", "a" * 64, 1.0, ["reason"]),
    ],
)
def test_deep_retrieval_fails_closed_on_invalid_corpus_candidates(v2_kb, candidate) -> None:
    """Any malformed injected candidate must be rejected before it can influence output."""
    from reqmap.deep_retrieval import CorpusCandidate, retrieve_deep

    provider = _CorpusProvider((CorpusCandidate(*candidate),))

    with pytest.raises(ValueError, match="corpus candidate"):
        retrieve_deep(v2_kb, "редкий термин", (), 8, provider)


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_deep_retrieval_rejects_invalid_top_k(v2_kb, top_k) -> None:
    """Permitting a non-positive or non-integer retrieval bound makes this fail."""
    from reqmap.deep_retrieval import retrieve_deep

    with pytest.raises(ValueError, match="top_k"):
        retrieve_deep(v2_kb, "nova", (), top_k)
