"""Immutable source-to-evidence contracts, independent of model proposals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from reqmap.models import EvidencePolarity, SourceCoordinate, SupportStatus


BINDING_ENGINE_VERSION = "1.0"
PROPOSAL_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    quote: str

    def __post_init__(self):
        if (type(self.start) is not int or type(self.end) is not int
                or self.start < 0 or self.end <= self.start
                or type(self.quote) is not str or len(self.quote) != self.end - self.start):
            raise ValueError("SOURCE_SPAN_INVALID: некорректное вхождение цитаты.")


@dataclass(frozen=True)
class SourceFragment:
    span: SourceSpan
    kind: str
    rule_id: str | None


@dataclass(frozen=True)
class BoundConstraint:
    name: str
    operator: str
    value: str
    unit: str | None = None
    source_spans: tuple[SourceSpan, ...] = ()

    @property
    def semantic_key(self):
        return self.name, self.operator, self.value, self.unit


@dataclass(frozen=True)
class BoundObligation:
    obligation_id: str
    requirement_id: str
    source_spans: tuple[SourceSpan, ...]
    source_quote: str
    parse_state: str
    rule_id: str | None
    mandatory: bool
    actor: str | None
    action: str | None
    object: str | None
    direction: str | None
    interface: str | None
    contour: str | None
    lifecycle_phase: str | None
    release_scope: str | None
    constraints: tuple[BoundConstraint, ...] = ()


@dataclass(frozen=True)
class SourceBinding:
    requirement_id: str
    coordinate: SourceCoordinate
    source_text: str
    source_sha256: str
    fragments: tuple[SourceFragment, ...]
    obligations: tuple[BoundObligation, ...]
    unresolved_fragments: tuple[SourceSpan, ...]
    grammar_version: str
    grammar_sha256: str
    contract_version: str = "1.0"


@dataclass(frozen=True)
class BindingDiagnostic:
    code: str
    message_ru: str
    requirement_id: str
    obligation_id: str
    field: str
    source_spans: tuple[SourceSpan, ...]
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BindingDecision:
    obligation_id: str
    source_sha256: str
    support_status: SupportStatus
    predicate_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    diagnostics: tuple[BindingDiagnostic, ...]
    uncovered: tuple[SourceSpan, ...]
    catalog_sha256: str | None
    engine_version: str = BINDING_ENGINE_VERSION


@dataclass(frozen=True)
class EvidencePredicate:
    predicate_id: str
    evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    locators: tuple[str, ...]
    source_sha256s: tuple[str, ...]
    component_ref: str
    capability_ref: str
    action_ref: str | None
    effect_ref: str | None
    target_ref: str | None
    actor: str
    action: str
    object: str
    direction: str
    interface: str
    contour: str
    lifecycle_phase: str
    release_scope: str
    assumptions: tuple[BoundConstraint, ...]
    constraints: tuple[BoundConstraint, ...]
    polarity: EvidencePolarity
    review_state: str
    reviewed_by: str
    reviewed_at: str
    annotation_version: str


@dataclass(frozen=True)
class BindingCatalog:
    catalog_id: str
    knowledge_sha256: str
    release_scope: str
    predicates: Mapping[str, EvidencePredicate]
    catalog_sha256: str
    schema_version: int = 1
    signer_identity: str | None = None


@dataclass(frozen=True)
class BindingContext:
    source_binding: SourceBinding
    catalog: BindingCatalog | None
    engine_version: str = BINDING_ENGINE_VERSION
