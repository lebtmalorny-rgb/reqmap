"""Deterministic lexical retrieval over an offline evidence snapshot."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from reqmap.knowledge import CapabilityRecord, KnowledgeBase
from reqmap.models import Candidate, Evidence, EvidenceStrength, SourceHint


_TOKEN = re.compile(r"[\w-]+", re.UNICODE)
_TRUSTED_STRENGTHS = frozenset({EvidenceStrength.DIRECT, EvidenceStrength.INDIRECT})
_GENERIC_TOKENS = frozenset(
    {
        "api", "rest", "через", "управление", "управления", "функция",
        "функции", "графический", "интерфейс", "балансировка", "нагрузка",
        "нагрузки", "поддержка", "полноценная", "создание", "группа",
        "группы", "групп", "безопасность", "безопасности", "пакеты",
        "пакетов", "пакет", "данные", "данных",
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
    """Return bounded reproducible candidates without inventing evidence."""
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    query = _query(text, kb)
    candidates: list[Candidate] = []
    for capability_id in sorted(kb.capabilities):
        capability = kb.capabilities[capability_id]
        component = kb.components[capability.component_id]
        capability_tokens = _content_tokens((*capability.terms, capability.name_ru, component.display_name))
        evidence_items = _capability_evidence(kb, capability)
        evidence_tokens = _content_tokens(evidence.claim_ru for evidence in evidence_items)
        identifiers = _identifiers(component.component_id, component.display_name, capability)
        exact_identifier = int(any(_contains_phrase(query.normalized, value) for value in identifiers))
        component_display_match = any(
            _contains_phrase(query.normalized, value)
            for value in (_normalize(component.component_id), _normalize(component.display_name))
        )
        synonym_hits = _synonym_hits(query, capability_tokens | _tokens(component.component_id))
        capability_overlap = float(len(query.expanded_tokens & capability_tokens))
        evidence_overlap = float(len(query.expanded_tokens & evidence_tokens))
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

        trusted_signal = bool(
            component_display_match
            or synonym_hits
            or _has_complete_capability_term(query.normalized, capability)
            or capability_overlap >= 2.0
        )
        candidates.append(
            Candidate(
                component_id=component.component_id,
                capability_id=capability.capability_id,
                evidence_ids=_evidence_ids(evidence_items, trusted_signal),
                score=score,
                reasons=_reasons(
                    exact_identifier,
                    synonym_hits,
                    capability_overlap,
                    evidence_overlap,
                    hint_match,
                ),
            )
        )

    selected = sorted(candidates, key=_candidate_sort_key)[:top_k]
    return _append_kolla_host_dependency(kb, selected)


@dataclass(frozen=True)
class _Query:
    normalized: str
    expanded_tokens: frozenset[str]
    synonym_groups: tuple[tuple[str, ...], ...]


def _query(text: str, kb: KnowledgeBase) -> _Query:
    normalized = _normalize(text)
    expanded = set(_tokens(normalized))
    matched_groups: list[tuple[str, ...]] = []
    for group in _synonym_groups(kb):
        if any(_contains_registered_synonym_phrase(normalized, phrase) for phrase in group):
            matched_groups.append(group)
            for phrase in group:
                expanded.update(_tokens(phrase))
    return _Query(normalized, frozenset(expanded - _GENERIC_TOKENS), tuple(matched_groups))


def _synonym_groups(kb: KnowledgeBase) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(sorted({_normalize(term), *(_normalize(value) for value in kb.synonyms[term])}))
        for term in sorted(kb.synonyms)
    )


def _synonym_hits(query: _Query, vocabulary: frozenset[str]) -> int:
    return sum(
        1
        for group in query.synonym_groups
        if any(_tokens(phrase) & vocabulary for phrase in group)
    )


def _capability_evidence(kb: KnowledgeBase, capability: CapabilityRecord) -> tuple[Evidence, ...]:
    return tuple(
        evidence
        for evidence in sorted(kb.evidence.values(), key=lambda item: item.evidence_id)
        if evidence.component_id == capability.component_id and evidence.capability_id == capability.capability_id
    )


def _evidence_ids(evidence_items: tuple[Evidence, ...], trusted_signal: bool) -> tuple[str, ...]:
    """Attach policy only as context for already supported official evidence."""
    if not trusted_signal:
        return ()
    official_ids = {
        evidence.evidence_id
        for evidence in evidence_items
        if evidence.provenance == "official" and evidence.strength in _TRUSTED_STRENGTHS
    }
    if not official_ids:
        return ()
    policy_ids = {
        evidence.evidence_id
        for evidence in evidence_items
        if evidence.provenance == "project_policy" and evidence.strength in _TRUSTED_STRENGTHS
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

    trusted_hosts = [candidate for candidate in host_candidates if _has_official_evidence(kb, candidate)]
    evidence_ids: tuple[str, ...] = ()
    if trusted_hosts:
        kolla_items = _capability_evidence(kb, kolla_capability)
        kolla_official = {
            evidence.evidence_id
            for evidence in kolla_items
            if evidence.provenance == "official" and evidence.strength in _TRUSTED_STRENGTHS
        }
        if kolla_official:
            host_policy = {
                evidence_id
                for candidate in trusted_hosts
                for evidence_id in candidate.evidence_ids
                if kb.evidence[evidence_id].provenance == "project_policy"
            }
            evidence_ids = tuple(sorted(kolla_official | host_policy))
    return tuple(
        [
            *selected,
            Candidate(
                component_id="kolla_ansible",
                capability_id=kolla_capability.capability_id,
                evidence_ids=evidence_ids,
                score=0.0,
                reasons=("host_os_policy_dependency",),
            ),
        ]
    )


def _has_official_evidence(kb: KnowledgeBase, candidate: Candidate) -> bool:
    return any(
        kb.evidence[evidence_id].provenance == "official"
        and kb.evidence[evidence_id].strength in _TRUSTED_STRENGTHS
        for evidence_id in candidate.evidence_ids
    )


def _has_complete_capability_term(text: str, capability: CapabilityRecord) -> bool:
    return any(
        bool(_tokens(term) - _GENERIC_TOKENS) and _contains_phrase(text, term)
        for term in capability.terms
    )


def _reasons(
    exact_identifier: int,
    synonym_hits: int,
    capability_overlap: float,
    evidence_overlap: float,
    hint_match: int,
) -> tuple[str, ...]:
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
    return tuple(reasons)


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
    return frozenset(_token_sequence(value))


def _token_sequence(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(_normalize(value)))


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower().replace("ё", "е")


def _contains_phrase(text: str, phrase: str) -> bool:
    """Match a complete contiguous token sequence, never a substring."""
    return _contains_token_sequence(_token_sequence(text), _token_sequence(phrase))


def _contains_registered_synonym_phrase(text: str, phrase: str) -> bool:
    """Allow inflection only while recognizing a complete registered synonym."""
    text_tokens = _token_sequence(text)
    phrase_tokens = _token_sequence(phrase)
    if not phrase_tokens or len(phrase_tokens) > len(text_tokens):
        return False
    return any(
        all(_synonym_token_equal(left, right) for left, right in zip(text_tokens[index:index + len(phrase_tokens)], phrase_tokens))
        for index in range(len(text_tokens) - len(phrase_tokens) + 1)
    )


def _contains_token_sequence(text_tokens: tuple[str, ...], phrase_tokens: tuple[str, ...]) -> bool:
    if not phrase_tokens or len(phrase_tokens) > len(text_tokens):
        return False
    return any(
        text_tokens[index:index + len(phrase_tokens)] == phrase_tokens
        for index in range(len(text_tokens) - len(phrase_tokens) + 1)
    )


def _synonym_token_equal(left: str, right: str) -> bool:
    return left == right or _synonym_stem(left) == _synonym_stem(right)


def _synonym_stem(token: str) -> str:
    if token.isascii():
        return token
    for suffix in (
        "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
        "ая", "яя", "ой", "ей", "ую", "юю", "ые", "ие", "ий", "ый",
        "ам", "ям", "ах", "ях", "ов", "ев", "ом", "ем", "ия", "ие",
        "ы", "и", "а", "я", "у", "ю", "е", "о",
    ):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _candidate_sort_key(candidate: Candidate) -> tuple[float, str, str]:
    return (-candidate.score, candidate.component_id, candidate.capability_id or "")
