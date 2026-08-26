"""Deterministic in-memory retrieval over the already loaded schema-v2 KB."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from pathlib import PurePosixPath
import re
from typing import Protocol
import unicodedata

from reqmap.deep_models import (
    CorpusCandidate,
    DeepCandidate,
    DeepRetrievalResult,
    VersionScope,
)
from reqmap.knowledge_v2 import (
    ActionRecord,
    DeepCapabilityRecord,
    EffectRecord,
    KnowledgeBaseV2,
)
from reqmap.models import SourceHint


_TOKEN = re.compile(r"[\w-]+", re.UNICODE)
_BM25_K1 = 1.2
_BM25_B = 0.75


class CorpusCandidateProvider(Protocol):
    """Injected discovery boundary; implementations must not create evidence."""

    def retrieve(self, text: str, *, top_k: int) -> tuple[CorpusCandidate, ...]:
        raise NotImplementedError


class NullCorpusCandidateProvider:
    """The offline default performs no corpus discovery."""

    def retrieve(self, text: str, *, top_k: int) -> tuple[CorpusCandidate, ...]:
        return ()


@dataclass(frozen=True)
class _Document:
    candidate: DeepCandidate
    tokens: tuple[str, ...]
    capability_tokens: frozenset[str]
    action_tokens: frozenset[str]
    evidence_tokens: frozenset[str]


@dataclass(frozen=True)
class _Query:
    raw_tokens: frozenset[str]
    tokens: frozenset[str]
    hint_tokens: frozenset[str]
    synonym_expanded: bool


def retrieve_deep(
    kb: KnowledgeBaseV2,
    text: str,
    hints: tuple[SourceHint, ...],
    top_k: int,
    corpus_provider: CorpusCandidateProvider | None = None,
) -> DeepRetrievalResult:
    """Retrieve normalized KB candidates and keep corpus discovery separate.

    All normalized candidates are assembled solely from the supplied, already
    loaded KB.  This function deliberately does not open source locators or
    invoke any network, embedding, model, index, or filesystem operation.
    """
    _validate_inputs(kb, text, hints, top_k)
    query = _query(text, hints, kb)
    documents = _documents(kb, query)
    normalized = _rank_documents(documents, query, top_k)
    provider = corpus_provider if corpus_provider is not None else NullCorpusCandidateProvider()
    corpus = _retrieve_corpus(provider, text, top_k, kb)
    return DeepRetrievalResult(normalized, corpus)


def _validate_inputs(
    kb: KnowledgeBaseV2, text: str, hints: tuple[SourceHint, ...], top_k: int
) -> None:
    if type(kb) is not KnowledgeBaseV2:
        raise ValueError("kb must be KnowledgeBaseV2")
    if type(text) is not str:
        raise ValueError("text must be str")
    if type(hints) is not tuple or any(type(hint) is not SourceHint for hint in hints):
        raise ValueError("hints must be a tuple of SourceHint")
    if any(type(hint.value) is not str or type(hint.column) is not str for hint in hints):
        raise ValueError("hints must contain text")
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")


def _query(text: str, hints: tuple[SourceHint, ...], kb: KnowledgeBaseV2) -> _Query:
    normalized = _normalize(text)
    raw_tokens = set(_token_sequence(normalized))
    hint_tokens = set()
    normalized_hints: list[str] = []
    for hint in hints:
        normalized_hint = _normalize(hint.value)
        normalized_hints.append(normalized_hint)
        hint_tokens.update(_token_sequence(normalized_hint))
    query_expanded, query_synonym = _expand_synonyms((normalized,), raw_tokens, kb)
    hint_expanded, hint_synonym = _expand_synonyms(
        tuple(normalized_hints), hint_tokens, kb
    )
    return _Query(
        frozenset(raw_tokens),
        frozenset(query_expanded | hint_expanded),
        frozenset(hint_expanded),
        query_synonym or hint_synonym,
    )


def _expand_synonyms(
    normalized_texts: tuple[str, ...], tokens: set[str], kb: KnowledgeBaseV2
) -> tuple[set[str], bool]:
    expanded = set(tokens)
    matched = False
    input_tokens = _token_sequence(" ".join(normalized_texts))
    for key in sorted(kb.synonyms):
        group = tuple(sorted({_normalize(key), *(_normalize(value) for value in kb.synonyms[key])}))
        if any(_contains_token_sequence(input_tokens, _token_sequence(value)) for value in group):
            matched = True
            for value in group:
                expanded.update(_token_sequence(value))
    return expanded, matched


def _documents(kb: KnowledgeBaseV2, query: _Query) -> tuple[_Document, ...]:
    explicit_upgrade = _is_explicit_upgrade_query(query.raw_tokens)
    documents: list[_Document] = []
    for capability_id in sorted(kb.capabilities):
        capability = kb.capabilities[capability_id]
        if capability.component_ref not in kb.components:
            continue
        actions = _linked_actions(kb, capability)
        if not actions:
            documents.append(_document_for(kb, capability, None, None))
            continue
        for action in actions:
            if action.version_scope.target_release == "2026.1" and not explicit_upgrade:
                continue
            effects = _linked_effects(kb, action)
            if not effects:
                documents.append(_document_for(kb, capability, action, None))
                continue
            for effect in effects:
                documents.append(_document_for(kb, capability, action, effect))
    return tuple(documents)


def _linked_actions(kb: KnowledgeBaseV2, capability: DeepCapabilityRecord) -> tuple[ActionRecord, ...]:
    capability_evidence = _direct_evidence_ids(kb, capability.evidence_ids, capability.capability_id)
    actions: list[ActionRecord] = []
    for action_id in sorted(kb.actions):
        action = kb.actions[action_id]
        if action.component_ref != capability.component_ref:
            continue
        action_evidence = _direct_evidence_ids(kb, action.evidence_ids, action.action_id)
        if capability_evidence.intersection(action_evidence):
            actions.append(action)
    return tuple(actions)


def _linked_effects(kb: KnowledgeBaseV2, action: ActionRecord) -> tuple[EffectRecord, ...]:
    effects: list[EffectRecord] = []
    for effect_id in sorted(action.effect_refs):
        effect = kb.effects.get(effect_id)
        if effect is not None and effect.target_ref == action.target_ref:
            effects.append(effect)
    return tuple(effects)


def _document_for(
    kb: KnowledgeBaseV2,
    capability: DeepCapabilityRecord,
    action: ActionRecord | None,
    effect: EffectRecord | None,
) -> _Document:
    capability_evidence = _direct_evidence_ids(kb, capability.evidence_ids, capability.capability_id)
    action_evidence = (
        _direct_evidence_ids(kb, action.evidence_ids, action.action_id) if action is not None else set()
    )
    effect_evidence = (
        _direct_evidence_ids(kb, effect.evidence_ids, effect.effect_id) if effect is not None else set()
    )
    evidence_ids = tuple(sorted(capability_evidence | action_evidence | effect_evidence))
    capability_values = (capability.name_ru, *capability.terms)
    action_values = (
        (action.action_id, action.target_ref) if action is not None else ()
    )
    evidence_values = tuple(kb.evidence[evidence_id].claim for evidence_id in evidence_ids)
    capability_tokens = _content_tokens(capability_values)
    action_tokens = frozenset(_identifier_tokens(action_values))
    evidence_tokens = _content_tokens(evidence_values)
    tokens = tuple(
        token
        for value in (*capability_values, *evidence_values)
        for token in _token_sequence(value)
    ) + _identifier_tokens(action_values)
    scope = action.version_scope if action is not None else _baseline_scope(kb)
    candidate = DeepCandidate(
        capability.component_ref,
        capability.capability_id,
        action.action_id if action is not None else None,
        effect.effect_id if effect is not None else None,
        evidence_ids,
        scope,
        0.0,
        (),
    )
    return _Document(candidate, tokens, capability_tokens, action_tokens, evidence_tokens)


def _baseline_scope(kb: KnowledgeBaseV2) -> VersionScope:
    return VersionScope(
        kb.base_release,
        kb.base_release,
        kb.kolla_ansible_release,
        kb.host_profile,
        kb.base_release,
    )


def _direct_evidence_ids(
    kb: KnowledgeBaseV2, evidence_ids: Iterable[str], entity_ref: str
) -> set[str]:
    return {
        evidence_id
        for evidence_id in evidence_ids
        if evidence_id in kb.evidence
        and entity_ref in kb.evidence[evidence_id].supports_entity_refs
    }


def _rank_documents(
    documents: tuple[_Document, ...], query: _Query, top_k: int
) -> tuple[DeepCandidate, ...]:
    if not documents or not query.tokens:
        return ()
    lengths = [len(document.tokens) for document in documents]
    average_length = sum(lengths) / len(lengths)
    document_frequency = {
        token: sum(token in document.tokens for document in documents)
        for token in query.tokens
    }
    scored: list[DeepCandidate] = []
    for document in documents:
        score = _bm25(
            document.tokens,
            query.tokens,
            document_frequency,
            len(documents),
            average_length,
        )
        if score == 0.0:
            continue
        reasons = _reasons(document, query)
        scored.append(
            DeepCandidate(
                document.candidate.component_ref,
                document.candidate.capability_ref,
                document.candidate.action_ref,
                document.candidate.effect_ref,
                document.candidate.evidence_ids,
                document.candidate.version_scope,
                round(score, 12),
                reasons,
            )
        )
    return tuple(sorted(scored, key=_deep_sort_key)[:top_k])


def _bm25(
    document_tokens: tuple[str, ...],
    query_tokens: frozenset[str],
    document_frequency: dict[str, int],
    document_count: int,
    average_length: float,
) -> float:
    score = 0.0
    for token in query_tokens:
        frequency = document_tokens.count(token)
        if frequency == 0:
            continue
        frequency_in_documents = document_frequency[token]
        idf = math.log(
            1.0
            + (document_count - frequency_in_documents + 0.5)
            / (frequency_in_documents + 0.5)
        )
        score += idf * (frequency * (_BM25_K1 + 1.0)) / (
            frequency
            + _BM25_K1
            * (1.0 - _BM25_B + _BM25_B * len(document_tokens) / average_length)
        )
    return score


def _reasons(document: _Document, query: _Query) -> tuple[str, ...]:
    reasons: list[str] = []
    if document.capability_tokens.intersection(query.tokens):
        reasons.append("capability_terms")
    if document.action_tokens.intersection(query.tokens):
        reasons.append("action_or_target_identifier")
    if document.evidence_tokens.intersection(query.tokens):
        reasons.append("evidence_claim")
    if query.synonym_expanded:
        reasons.append("synonym")
    if document.tokens and query.hint_tokens.intersection(document.tokens):
        reasons.append("source_hint")
    return tuple(reasons)


def _is_explicit_upgrade_query(tokens: frozenset[str]) -> bool:
    return "upgrade" in tokens or {"2026", "1"}.issubset(tokens)


def _retrieve_corpus(
    provider: CorpusCandidateProvider,
    text: str,
    top_k: int,
    kb: KnowledgeBaseV2,
) -> tuple[CorpusCandidate, ...]:
    retrieve = getattr(provider, "retrieve", None)
    if not callable(retrieve):
        raise ValueError("corpus provider must define retrieve")
    raw = retrieve(text, top_k=top_k)
    if type(raw) is not tuple:
        raise ValueError("corpus provider must return a tuple")
    validated = tuple(_validated_corpus_candidate(candidate, kb) for candidate in raw)
    return tuple(sorted(validated, key=_corpus_sort_key)[:top_k])


def _validated_corpus_candidate(
    candidate: object, kb: KnowledgeBaseV2
) -> CorpusCandidate:
    if type(candidate) is not CorpusCandidate:
        raise ValueError("corpus candidate has an invalid type")
    if type(candidate.source_id) is not str or candidate.source_id not in kb.sources:
        raise ValueError("corpus candidate has an unknown source_id")
    source = kb.sources[candidate.source_id]
    if not _is_canonical_locator(candidate.locator) or candidate.locator != source.local_path:
        raise ValueError("corpus candidate has an unsafe locator")
    if type(candidate.content_sha256) is not str or candidate.content_sha256 != source.content_sha256:
        raise ValueError("corpus candidate content_sha256 does not match source")
    if type(candidate.score) is not float or not math.isfinite(candidate.score):
        raise ValueError("corpus candidate score must be finite float")
    if type(candidate.reasons) is not tuple or any(type(reason) is not str for reason in candidate.reasons):
        raise ValueError("corpus candidate reasons must be a tuple of strings")
    return candidate


def _is_canonical_locator(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and str(path) == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _deep_sort_key(candidate: DeepCandidate) -> tuple[float, str, str, str, str]:
    return (
        -candidate.score,
        candidate.component_ref,
        candidate.capability_ref,
        candidate.action_ref or "",
        candidate.effect_ref or "",
    )


def _corpus_sort_key(candidate: CorpusCandidate) -> tuple[float, str, str, str, tuple[str, ...]]:
    return (
        -candidate.score,
        candidate.source_id,
        candidate.locator,
        candidate.content_sha256,
        candidate.reasons,
    )


def _content_tokens(values: Iterable[str]) -> frozenset[str]:
    return frozenset(token for value in values for token in _token_sequence(value))


def _identifier_tokens(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        part
        for value in values
        for token in _token_sequence(value)
        for part in (token, *token.split("-"))
        if part
    )


def _token_sequence(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(_normalize(value)))


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def _contains_token_sequence(text_tokens: tuple[str, ...], phrase_tokens: tuple[str, ...]) -> bool:
    if not phrase_tokens or len(phrase_tokens) > len(text_tokens):
        return False
    return any(
        text_tokens[index:index + len(phrase_tokens)] == phrase_tokens
        for index in range(len(text_tokens) - len(phrase_tokens) + 1)
    )
