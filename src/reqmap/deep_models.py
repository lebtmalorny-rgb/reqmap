"""Canonical schema-v2 data model for deep requirement analysis."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import re

from reqmap.models import (
    AnalysisState,
    AtomicClaim,
    EvidencePolarity,
    EvidenceStrength,
    Requirement,
    SupportStatus,
)


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ResponsibilityContour(str, Enum):
    OPENSTACK_RUNTIME = "openstack_runtime"
    KOLLA_ANSIBLE = "kolla_ansible"
    HOST_OS = "host_os"


class LifecyclePhase(str, Enum):
    PREFLIGHT = "preflight"
    DEPLOY = "deploy"
    RUNTIME = "runtime"
    RECONFIGURE = "reconfigure"
    UPGRADE = "upgrade"
    MIGRATE = "migrate"
    RECOVER = "recover"
    VERIFY = "verify"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class VersionScope:
    source_release: str
    target_release: str
    kolla_ansible_release: str
    host_profile: str
    version_constraint: str

    def __post_init__(self) -> None:
        _required_text(self.version_constraint, "version_constraint")


def validate_version_scope(scope: VersionScope, phase: LifecyclePhase) -> None:
    """Validate the fixed 2025.1 baseline and the upgrade-only 2026.1 target."""
    if type(scope) is not VersionScope:
        raise ValueError("version_scope must be VersionScope")
    if type(phase) is not LifecyclePhase:
        raise ValueError("lifecycle_phase must be LifecyclePhase")
    if scope.source_release != "2025.1":
        raise ValueError("source_release must be 2025.1")
    if scope.target_release not in {"2025.1", "2026.1"}:
        raise ValueError("target_release must be 2025.1 or 2026.1")
    if scope.target_release == "2026.1" and phase is not LifecyclePhase.UPGRADE:
        raise ValueError("target_release 2026.1 is allowed only for upgrade")
    if scope.kolla_ansible_release != "2025.1":
        raise ValueError("kolla_ansible_release must be 2025.1")
    if scope.host_profile != "rocky_linux_9":
        raise ValueError("host_profile must be rocky_linux_9")


@dataclass(frozen=True)
class ResponsibilityRecord:
    record_id: str
    requirement_id: str
    atomic_claim_id: str
    contour: ResponsibilityContour
    component_ref: str
    executor_ref: str
    target_contour: ResponsibilityContour
    target_ref: str
    action_ref: str | None
    effect_ref: str | None
    lifecycle_phase: LifecyclePhase
    version_scope: VersionScope
    evidence_ids: tuple[str, ...]
    support_status: SupportStatus
    related_record_ids: tuple[str, ...] = ()
    procedure_step_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _stable_child_id(self.record_id, self.atomic_claim_id, "R", "record_id")
        _safe_identifier(self.requirement_id, "requirement_id")
        _safe_identifier(self.atomic_claim_id, "atomic_claim_id")
        _contour(self.contour, "contour")
        _required_text(self.component_ref, "component_ref")
        _required_text(self.executor_ref, "executor_ref")
        _contour(self.target_contour, "target_contour")
        _required_text(self.target_ref, "target_ref")
        _optional_text(self.action_ref, "action_ref")
        _optional_text(self.effect_ref, "effect_ref")
        _phase(self.lifecycle_phase, "lifecycle_phase")
        validate_version_scope(self.version_scope, self.lifecycle_phase)
        _identifier_tuple(self.evidence_ids, "evidence_ids")
        _support_status(self.support_status)
        _identifier_tuple(self.related_record_ids, "related_record_ids")
        _identifier_tuple(self.procedure_step_ids, "procedure_step_ids")
        _text_tuple(self.diagnostics, "diagnostics")


@dataclass(frozen=True)
class DeepEvidence:
    evidence_id: str
    claim: str
    claim_kind: str
    polarity: EvidencePolarity
    strength: EvidenceStrength
    source_id: str
    locator: str
    version_constraint: str
    applicable_contours: tuple[ResponsibilityContour, ...]
    supports_entity_refs: tuple[str, ...]
    local_excerpt: str
    review_state: str

    def __post_init__(self) -> None:
        _safe_identifier(self.evidence_id, "evidence_id")
        _required_text(self.claim, "claim")
        _required_text(self.claim_kind, "claim_kind")
        if type(self.polarity) is not EvidencePolarity:
            raise ValueError("polarity must be EvidencePolarity")
        if type(self.strength) is not EvidenceStrength:
            raise ValueError("strength must be EvidenceStrength")
        _safe_identifier(self.source_id, "source_id")
        _required_text(self.locator, "locator")
        _required_text(self.version_constraint, "version_constraint")
        _contour_tuple(self.applicable_contours, "applicable_contours")
        _text_tuple(self.supports_entity_refs, "supports_entity_refs")
        _required_text(self.local_excerpt, "local_excerpt")
        _required_text(self.review_state, "review_state")


@dataclass(frozen=True)
class ProcedureStep:
    step_id: str
    phase: LifecyclePhase
    contour: ResponsibilityContour
    executor_ref: str
    target_ref: str
    action_ref: str
    preconditions: tuple[str, ...]
    success_criteria: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    rollback_step_id: str | None = None

    def __post_init__(self) -> None:
        _stable_step_id(self.step_id, "step_id")
        _phase(self.phase, "phase")
        _contour(self.contour, "contour")
        _required_text(self.executor_ref, "executor_ref")
        _required_text(self.target_ref, "target_ref")
        _required_text(self.action_ref, "action_ref")
        _text_tuple(self.preconditions, "preconditions")
        _text_tuple(self.success_criteria, "success_criteria")
        _identifier_tuple(self.evidence_ids, "evidence_ids")
        _identifier_tuple(self.depends_on, "depends_on")
        if self.rollback_step_id is not None:
            _safe_identifier(self.rollback_step_id, "rollback_step_id")


@dataclass(frozen=True)
class ProcedureGraph:
    graph_id: str
    requirement_id: str
    template_id: str
    steps: tuple[ProcedureStep, ...]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _stable_child_id(self.graph_id, self.requirement_id, "P", "graph_id")
        _safe_identifier(self.requirement_id, "requirement_id")
        _safe_identifier(self.template_id, "template_id")
        _tuple_of(self.steps, ProcedureStep, "steps")
        for step in self.steps:
            _stable_child_id(step.step_id, self.graph_id, "S", "step_id")
        _text_tuple(self.diagnostics, "diagnostics")


@dataclass(frozen=True)
class DeepAtomResult:
    atom: AtomicClaim
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    responsibility_ids: tuple[str, ...]
    supported_aspects: tuple[str, ...] = ()
    unconfirmed_aspects: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    binding_decision: "BindingDecision | None" = None

    def __post_init__(self) -> None:
        if type(self.atom) is not AtomicClaim:
            raise ValueError("atom must be AtomicClaim")
        _analysis_state(self.analysis_state)
        _optional_support_status(self.support_status)
        _identifier_tuple(self.responsibility_ids, "responsibility_ids")
        _text_tuple(self.supported_aspects, "supported_aspects")
        _text_tuple(self.unconfirmed_aspects, "unconfirmed_aspects")
        _text_tuple(self.diagnostics, "diagnostics")


@dataclass(frozen=True)
class DeepRequirementResult:
    requirement: Requirement
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    atom_results: tuple[DeepAtomResult, ...]
    responsibility_ids: tuple[str, ...]
    procedure_graph_ids: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()
    source_binding: "SourceBinding | None" = None

    def __post_init__(self) -> None:
        if type(self.requirement) is not Requirement:
            raise ValueError("requirement must be Requirement")
        _analysis_state(self.analysis_state)
        _optional_support_status(self.support_status)
        _tuple_of(self.atom_results, DeepAtomResult, "atom_results")
        _identifier_tuple(self.responsibility_ids, "responsibility_ids")
        _identifier_tuple(self.procedure_graph_ids, "procedure_graph_ids")
        _text_tuple(self.diagnostics, "diagnostics")


@dataclass(frozen=True)
class DeepGroupResult:
    group_id: str
    source_requirement_ids: tuple[str, ...]
    support_status: SupportStatus | None
    component_refs: tuple[str, ...]
    responsibility_ids: tuple[str, ...]
    analysis_states: tuple[AnalysisState, ...]

    def __post_init__(self) -> None:
        _safe_identifier(self.group_id, "group_id")
        _identifier_tuple(self.source_requirement_ids, "source_requirement_ids")
        _optional_support_status(self.support_status)
        _text_tuple(self.component_refs, "component_refs")
        _identifier_tuple(self.responsibility_ids, "responsibility_ids")
        _enum_tuple(self.analysis_states, AnalysisState, "analysis_states")


@dataclass(frozen=True)
class DeepRunResult:
    run_id: str
    schema_version: str
    run_status: str
    requirements: tuple[DeepRequirementResult, ...]
    groups: tuple[DeepGroupResult, ...]
    responsibility_records: tuple[ResponsibilityRecord, ...]
    procedure_graphs: tuple[ProcedureGraph, ...]
    evidence: tuple[DeepEvidence, ...]
    metadata: Mapping[str, object]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_identifier(self.run_id, "run_id")
        if self.schema_version not in {"2.0", "2.1"}:
            raise ValueError("schema_version must be 2.0 or 2.1")
        _required_text(self.run_status, "run_status")
        _tuple_of(self.requirements, DeepRequirementResult, "requirements")
        _tuple_of(self.groups, DeepGroupResult, "groups")
        _tuple_of(self.responsibility_records, ResponsibilityRecord, "responsibility_records")
        _tuple_of(self.procedure_graphs, ProcedureGraph, "procedure_graphs")
        _tuple_of(self.evidence, DeepEvidence, "evidence")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        _text_tuple(self.diagnostics, "diagnostics")


@dataclass(frozen=True)
class CorpusCandidate:
    """An untrusted, discovery-only reference to an already loaded source."""

    source_id: str
    locator: str
    content_sha256: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DeepCandidate:
    """A normalized KB-only retrieval candidate with direct evidence references."""

    component_ref: str
    capability_ref: str
    action_ref: str | None
    effect_ref: str | None
    evidence_ids: tuple[str, ...]
    version_scope: VersionScope
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DeepRetrievalResult:
    """Separate normalized KB evidence from untrusted corpus discovery results."""

    normalized_candidates: tuple[DeepCandidate, ...]
    corpus_candidates: tuple[CorpusCandidate, ...]


def _safe_identifier(value: str, field_name: str) -> None:
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a non-empty safe technical identifier")


def _stable_child_id(value: str, parent: str, kind: str, field_name: str) -> None:
    _safe_identifier(parent, field_name)
    _safe_identifier(value, field_name)
    prefix = f"{parent}-{kind}"
    suffix = value.removeprefix(prefix)
    if not value.startswith(prefix) or not suffix.isdigit():
        raise ValueError(f"{field_name} must be a stable {kind} identifier for its parent")
    ordinal = int(suffix)
    if ordinal <= 0:
        raise ValueError(f"{field_name} must have a positive ordinal")
    if suffix != f"{ordinal:03d}":
        raise ValueError(f"{field_name} must be a stable {kind} identifier for its parent")


def _stable_step_id(value: str, field_name: str) -> None:
    parent, marker, _suffix = value.rpartition("-S")
    if not marker:
        raise ValueError(f"{field_name} must be a stable S identifier")
    _stable_child_id(value, parent, "S", field_name)


def _required_text(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")


def _optional_text(value: str | None, field_name: str) -> None:
    if value is not None:
        _required_text(value, field_name)


def _contour(value: ResponsibilityContour, field_name: str) -> None:
    if type(value) is not ResponsibilityContour:
        raise ValueError(f"{field_name} must be ResponsibilityContour")


def _phase(value: LifecyclePhase, field_name: str) -> None:
    if type(value) is not LifecyclePhase:
        raise ValueError(f"{field_name} must be LifecyclePhase")


def _support_status(value: SupportStatus) -> None:
    if type(value) is not SupportStatus:
        raise ValueError("support_status must be SupportStatus")


def _optional_support_status(value: SupportStatus | None) -> None:
    if value is not None:
        _support_status(value)


def _analysis_state(value: AnalysisState) -> None:
    if type(value) is not AnalysisState:
        raise ValueError("analysis_state must be AnalysisState")


def _identifier_tuple(values: tuple[str, ...], field_name: str) -> None:
    if type(values) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    for value in values:
        _safe_identifier(value, field_name)


def _text_tuple(values: tuple[str, ...], field_name: str) -> None:
    if type(values) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    for value in values:
        _required_text(value, field_name)


def _contour_tuple(values: tuple[ResponsibilityContour, ...], field_name: str) -> None:
    if type(values) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    for value in values:
        _contour(value, field_name)


def _enum_tuple(values: tuple[Enum, ...], expected: type[Enum], field_name: str) -> None:
    if type(values) is not tuple or any(type(value) is not expected for value in values):
        raise ValueError(f"{field_name} must be a tuple of {expected.__name__}")


def _tuple_of(values: tuple[object, ...], expected: type[object], field_name: str) -> None:
    if type(values) is not tuple or any(type(value) is not expected for value in values):
        raise ValueError(f"{field_name} must be a tuple of {expected.__name__}")
