"""Canonical, deterministic data model for requirement analysis results."""

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
import typing


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    NOT_SUPPORTED = "not_supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


class AnalysisState(str, Enum):
    COMPLETED = "completed"
    MODEL_FAILED = "model_failed"
    VALIDATION_FAILED = "validation_failed"
    SKIPPED = "skipped"


class Phase(str, Enum):
    RUNTIME = "runtime"
    DESIGNTIME = "designtime"


class RelationType(str, Enum):
    IMPLEMENTS = "implements"
    CONFIGURES = "configures"
    PREREQUISITE = "prerequisite"
    INTEGRATES = "integrates"
    HOST_OS_CHANGE = "host_os_change"


class ImplementationSource(str, Enum):
    UPSTREAM = "upstream"
    KOLLA_ANSIBLE = "kolla_ansible"
    PRODUCT_EXTENSION = "product_extension"
    EXTERNAL_COMPONENT = "external_component"


class EvidenceStrength(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    NONE = "none"


class EvidenceClaimScope(str, Enum):
    CONTEXT = "context"
    SPECIFIC = "specific"


class EvidencePolarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class SourceCoordinate:
    source_name: str
    sheet: str | None
    row: int | None


@dataclass(frozen=True)
class SourceField:
    column: str
    value: str


@dataclass(frozen=True)
class SourceHint:
    value: str
    column: str


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    source_id: str | None
    text: str
    ordinal: int
    coordinate: SourceCoordinate
    parent_id: str | None = None
    group_ids: tuple[str, ...] = ()
    source_fields: tuple[SourceField, ...] = ()
    source_hints: tuple[SourceHint, ...] = ()


@dataclass(frozen=True)
class AtomicClaim:
    atom_id: str
    requirement_id: str
    text: str
    source_quote: str
    mandatory: bool
    ordinal: int
    obligation_id: str | None = None
    source_spans: tuple["SourceSpan", ...] = ()
    source_sha256: str | None = None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    component_id: str
    capability_id: str
    polarity: EvidencePolarity
    strength: EvidenceStrength
    claim_ru: str
    source_id: str
    locator: str
    version_constraint: str
    source_url: str | None
    local_path: str
    source_sha256: str
    retrieved_at: str
    provenance: str
    claim_scope: EvidenceClaimScope = EvidenceClaimScope.CONTEXT


@dataclass(frozen=True)
class Candidate:
    component_id: str
    capability_id: str | None
    evidence_ids: tuple[str, ...]
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImplementationStep:
    order: int
    phase: Phase
    action_ru: str
    mechanism: str
    command: str | None = None
    api_operation: str | None = None


@dataclass(frozen=True)
class Mapping:
    mapping_id: str
    atom_id: str
    component_id: str
    role_ru: str
    relation: RelationType
    phase: Phase
    implementation_source: ImplementationSource
    mechanism: str
    steps: tuple[ImplementationStep, ...]
    evidence_ids: tuple[str, ...]
    support_status: SupportStatus
    reason_ru: str


@dataclass(frozen=True)
class DecompositionOutcome:
    atoms: tuple[AtomicClaim, ...]
    analysis_state: AnalysisState
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtomResult:
    atom: AtomicClaim
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    mappings: tuple[Mapping, ...]
    supported_aspects: tuple[str, ...] = ()
    unconfirmed_aspects: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementResult:
    requirement: Requirement
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    atom_results: tuple[AtomResult, ...]
    mappings: tuple[Mapping, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupResult:
    group_id: str
    source_requirement_ids: tuple[str, ...]
    support_status: SupportStatus | None
    component_ids: tuple[str, ...]
    mapping_ids: tuple[str, ...]
    analysis_states: tuple[AnalysisState, ...]


@dataclass(frozen=True)
class AnalysisRequest:
    requirements: tuple[Requirement, ...]
    input_sha256: str
    input_kind: str
    source_path: Path | None
    output_dir: Path


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    diagnostics: tuple[str, ...]
    knowledge_sha256: str | None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    schema_version: str
    run_status: str
    requirements: tuple[RequirementResult, ...]
    groups: tuple[GroupResult, ...]
    evidence: tuple[Evidence, ...]
    metadata: typing.Mapping[str, object]
    diagnostics: tuple[str, ...] = ()


def to_dict(value: object) -> object:
    """Convert canonical model values to deterministic JSON-compatible data."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, MappingABC):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_dict(item) for item in value]
    return value
