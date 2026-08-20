"""Deterministic lexical retrieval over an offline evidence snapshot."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from reqmap.knowledge import CapabilityRecord, KnowledgeBase
from reqmap.models import Candidate, EvidenceStrength, SourceHint


_TOKEN = re.compile(r"[\w-]+", re.UNICODE)
_GENERIC_TOKENS = frozenset(
    {
        "api",
        "rest",
        "через",
        "управление",
        "управления",
        "функция",
        "функции",
        "графический",
        "интерфейс",
        "балансировка",
        "нагрузка",
        "нагрузки",
        "поддержка",
        "полноценная",
        "создание",
    }
)


def candidate_score(
    exact_identifier: int,
    exact_synonym_phrase: int,
    capability_token_overlap: float,
    evidence_token_overlap: float,
    source_hint_match: int,
) -> float:
    """Return the published, stable lexical ranking score."""
    return (
        8.0 * exact_identifier
        + 5.0 * exact_synonym_phrase
        + 3.0 * capability_token_overlap
        + 2.0 * evidence_token_overlap
        + 0.5 * source_hint_match
    )


def retrieve(
    kb: KnowledgeBase,
    text: str,
    hints: tuple[SourceHint, ...],
    top_k: int,
) -> tuple[Candidate, ...]:
    """Return bounded, reproducible candidates without inventing evidence.

    Source hints can rank or introduce a known component, but they are never
    treated as a claim and therefore never attach evidence by themselves.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    query = _query(text, kb)
    candidates: list[Candidate] = []
    for capability_id in sorted(kb.capabilities):
        capability = kb.capabilities[capability_id]
        component = kb.components[capability.component_id]
        identifiers = _identifiers(component.component_id, component.display_name, capability)
        capability_tokens = _content_tokens((*capability.terms, capability.name_ru, component.display_name))
        evidence_items = tuple(
            evidence
            for evidence in sorted(kb.evidence.values(), key=lambda item: item.evidence_id)
            if evidence.component_id == component.component_id and evidence.capability_id == capability.capability_id
        )
        evidence_tokens = _content_tokens(evidence.claim_ru for evidence in evidence_items)
        exact_identifier = int(any(_contains_phrase(query.normalized, value) for value in identifiers))
        synonym_hits = _synonym_hits(query, capability_tokens | _tokens(component.component_id))
        capability_overlap = float(len(_matching_tokens(query.expanded_tokens, capability_tokens)))
        evidence_overlap = float(len(_matching_tokens(query.expanded_tokens, evidence_tokens)))
        hint_match = int(_has_hint(component.component_id, component.display_name, capability, hints, kb))
        score = candidate_score(
            exact_identifier,
            synonym_hits,
            capability_overlap,
            evidence_overlap,
            hint_match,
        )
        if score == 0.0:
            continue

        # A claim-only word can locate a candidate, but cannot substantiate it:
        # evidence is attached only after a component/capability-side signal.
        semantic_match = bool(exact_identifier or synonym_hits or capability_overlap)
        direct_evidence = _direct_evidence_ids(evidence_items, semantic_match)
        reasons: list[str] = []
        if exact_identifier:
            reasons.append("exact_identifier")
        if synonym_hits:
            reasons.append("exact_synonym_phrase")
        if capability_overlap:
            reasons.append("capability_token_overlap")
        if evidence_overlap:
            reasons.append("evidence_token_overlap")
        if hint_match:
            reasons.append("source_hint")
        candidates.append(
            Candidate(
                component_id=component.component_id,
                capability_id=capability.capability_id,
                evidence_ids=direct_evidence,
                score=score,
                reasons=tuple(reasons),
            )
        )

    selected = sorted(candidates, key=_candidate_sort_key)[:top_k]
    return _append_kolla_host_dependency(kb, selected)


class _Query:
    def __init__(self, normalized: str, raw_tokens: frozenset[str], expanded_tokens: frozenset[str], synonym_groups: tuple[tuple[str, ...], ...]) -> None:
        self.normalized = normalized
        self.raw_tokens = raw_tokens
        self.expanded_tokens = expanded_tokens
        self.synonym_groups = synonym_groups


def _query(text: str, kb: KnowledgeBase) -> _Query:
    normalized = _normalize(text)
    raw_tokens = _tokens(normalized)
    expanded = set(raw_tokens)
    matched_groups: list[tuple[str, ...]] = []
    for group in _synonym_groups(kb):
        if any(_contains_phrase(normalized, phrase) for phrase in group):
            matched_groups.append(group)
            for phrase in group:
                expanded.update(_tokens(phrase))
    return _Query(normalized, raw_tokens, frozenset(expanded - _GENERIC_TOKENS), tuple(matched_groups))


def _synonym_groups(kb: KnowledgeBase) -> tuple[tuple[str, ...], ...]:
    groups = []
    for term in sorted(kb.synonyms):
        groups.append(tuple(sorted({_normalize(term), *(_normalize(value) for value in kb.synonyms[term])})))
    return tuple(groups)


def _synonym_hits(query: _Query, vocabulary: frozenset[str]) -> int:
    return sum(
        1
        for group in query.synonym_groups
        if any(_matching_tokens(_tokens(phrase), vocabulary) for phrase in group)
    )


def _direct_evidence_ids(evidence_items: tuple, semantic_match: bool) -> tuple[str, ...]:
    if not semantic_match:
        return ()
    official_ids = {
        evidence.evidence_id
        for evidence in evidence_items
        if evidence.strength is EvidenceStrength.DIRECT and evidence.provenance == "official"
    }
    if not official_ids:
        return ()
    policy_ids = {
        evidence.evidence_id
        for evidence in evidence_items
        if evidence.strength is EvidenceStrength.DIRECT and evidence.provenance == "project_policy"
    }
    return tuple(sorted(official_ids | policy_ids))


def _append_kolla_host_dependency(kb: KnowledgeBase, selected: list[Candidate]) -> tuple[Candidate, ...]:
    host_candidates = [
        candidate
        for candidate in selected
        if kb.components[candidate.component_id].kind == "host_os_subsystem"
    ]
    if not host_candidates or any(candidate.component_id == "kolla_ansible" for candidate in selected):
        return tuple(selected)

    kolla_capability = next(
        (
            capability
            for capability in sorted(kb.capabilities.values(), key=lambda item: item.capability_id)
            if capability.component_id == "kolla_ansible"
        ),
        None,
    )
    if kolla_capability is None:
        return tuple(selected)

    related_policy_ids = {
        evidence.evidence_id
        for candidate in host_candidates
        for evidence_id in candidate.evidence_ids
        for evidence in (kb.evidence[evidence_id],)
        if evidence.provenance == "project_policy"
    }
    if not related_policy_ids:
        related_policy_ids = {
            evidence.evidence_id
            for candidate in host_candidates
            for evidence in kb.evidence.values()
            if evidence.component_id == candidate.component_id
            and evidence.capability_id == candidate.capability_id
            and evidence.provenance == "project_policy"
        }
    kolla_official_ids = {
        evidence.evidence_id
        for evidence in kb.evidence.values()
        if evidence.component_id == "kolla_ansible"
        and evidence.capability_id == kolla_capability.capability_id
        and evidence.strength is EvidenceStrength.DIRECT
        and evidence.provenance == "official"
    }
    # Project policy is relational context only; without an official Kolla
    # record it must not look like standalone capability evidence.
    if not kolla_official_ids:
        related_policy_ids = set()
    return tuple(
        [
            *selected,
            Candidate(
                component_id="kolla_ansible",
                capability_id=kolla_capability.capability_id,
                evidence_ids=tuple(sorted(kolla_official_ids | related_policy_ids)),
                score=0.0,
                reasons=("host_os_policy_dependency",),
            ),
        ]
    )


def _has_hint(
    component_id: str,
    display_name: str,
    capability: CapabilityRecord,
    hints: tuple[SourceHint, ...],
    kb: KnowledgeBase,
) -> bool:
    identifiers = set(_identifiers(component_id, display_name, capability))
    for group in _synonym_groups(kb):
        if _tokens(component_id) & _content_tokens(group):
            identifiers.update(group)
    return any(_normalize(hint.value) in identifiers for hint in hints if hint.value.strip())


def _identifiers(component_id: str, display_name: str, capability: CapabilityRecord) -> tuple[str, ...]:
    values = {_normalize(component_id), _normalize(display_name)}
    for term in capability.terms:
        normalized = _normalize(term)
        if _is_technical_identifier(normalized):
            values.add(normalized)
    return tuple(sorted(values))


def _is_technical_identifier(value: str) -> bool:
    return any(character.isascii() and (character.isalpha() or character.isdigit()) for character in value)


def _content_tokens(values: Iterable[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(_tokens(value))
    return frozenset(tokens - _GENERIC_TOKENS)


def _tokens(value: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_normalize(value)))


def _matching_tokens(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    return _stem_tokens(left) & _stem_tokens(right)


def _stem_tokens(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(_stem(token) for token in tokens)


def _stem(token: str) -> str:
    """Use a deliberately small suffix fold after the required tokenization.

    This is not a thesaurus: it only lets Russian surface forms such as
    ``виртуальной машины`` meet registered vocabulary ``виртуальная машина``.
    """
    if not token.isascii():
        for suffix in (
            "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
            "ая", "яя", "ой", "ей", "ую", "юю", "ые", "ие", "ий", "ый",
            "ам", "ям", "ах", "ях", "ов", "ев", "ом", "ем", "ия", "ие",
            "ы", "и", "а", "я", "у", "ю", "е", "о",
        ):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                return token[: -len(suffix)]
    return token


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower().replace("ё", "е")


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase_tokens = _TOKEN.findall(phrase)
    if not phrase_tokens:
        return False
    return re.search(r"(?<!\\w)" + r"[\\W_]+".join(map(re.escape, phrase_tokens)) + r"(?!\\w)", text) is not None


def _candidate_sort_key(candidate: Candidate) -> tuple[float, str, str]:
    return (-candidate.score, candidate.component_id, candidate.capability_id or "")
