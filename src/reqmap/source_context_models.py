"""Immutable source identities and reviewed annotations, separate from evidence."""
from __future__ import annotations

from dataclasses import dataclass

from reqmap.binding_models import BoundConstraint, SourceSpan
from reqmap.config import InputProfile
from reqmap.models import Requirement, SourceCoordinate


SOURCE_CONTEXT_VERSION = "1.0"
SOURCE_CONTEXT_RESOLVER_VERSION = "1.0"
MAX_CONTEXT_BYTES = 25 * 1024 * 1024
MAX_CONTEXT_LINKS = 64


@dataclass(frozen=True)
class SourceDocumentSnapshot:
    content: bytes
    source_kind: str
    source_name: str
    text_mode: str | None
    input_profile: InputProfile | None
    input_sha256: str
    input_profile_sha256: str | None
    requirements_sha256: str
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True)
class SourceRowRef:
    requirement_id: str
    coordinate: SourceCoordinate
    source_sha256: str


@dataclass(frozen=True)
class SourceFragmentRef:
    row: SourceRowRef
    span: SourceSpan


@dataclass(frozen=True)
class SourceContextLink:
    link_id: str
    origin: SourceFragmentRef
    field: str
    value: str | BoundConstraint


@dataclass(frozen=True)
class SourceContextEntry:
    target: SourceRowRef
    disposition: str
    review_state: str
    reviewed_by: str | None
    reviewed_at: str | None
    reason_ru: str
    context_complete: bool
    links: tuple[SourceContextLink, ...]


@dataclass(frozen=True)
class SourceContextMap:
    schema_version: str
    map_id: str
    source_kind: str
    source_name: str
    input_sha256: str
    input_profile_sha256: str | None
    requirements_sha256: str
    rows: tuple[SourceContextEntry, ...]


@dataclass(frozen=True)
class ContextTrust:
    profile: str
    namespace: str | None
    signer_identity: str | None
    signature_sha256: str | None
    allowed_signers_sha256: str | None


@dataclass(frozen=True)
class LoadedSourceContext:
    map_bytes: bytes | None
    signature_bytes: bytes | None
    map_sha256: str | None
    mapping: SourceContextMap | None
    trust: ContextTrust
