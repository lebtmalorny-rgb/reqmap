"""Versioned local-agent records; no transport or model execution."""
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING
if TYPE_CHECKING:
    from reqmap.source_context_models import SourceContextSnapshot
    from reqmap.config import KnowledgeTrustConfig
from reqmap.agent_input import InputSnapshot
from reqmap.config import AnalysisProfile, InputProfile


@dataclass(frozen=True)
class ToolError:
    code: str
    message_ru: str
    details: dict[str, object]


@dataclass(frozen=True)
class ToolReply:
    ok: bool
    data: dict[str, object]
    error: ToolError | None = None


def failure(code: str, message_ru: str, **details) -> ToolReply:
    return ToolReply(False, {}, ToolError(code, message_ru, details))


@dataclass(frozen=True)
class SessionSettings:
    analysis_profile: AnalysisProfile
    top_k: int
    input_profile: InputProfile | None
    knowledge_sha256: str
    snapshot_id: str | None
    tool_contract_version: str
    workflow_version: str
    binding_engine_version: str | None = None
    grammar_version: str | None = None
    grammar_sha256: str | None = None
    binding_catalog_sha256: str | None = None
    binding_catalog_schema_version: int | None = None
    proposal_schema_version: int | None = None
    result_schema_version: str | None = None
    source_context_version: str | None = None
    resolver_version: str | None = None
    resolver_sha256: str | None = None


@dataclass(frozen=True)
class SessionSeed:
    input_snapshot: InputSnapshot
    settings: SessionSettings
    clarifications: tuple[str, ...]
    parent_session_id: str | None
    reported_client: str
    source_context: 'SourceContextSnapshot | None' = None
    source_context_record: dict | None = None
    seed_version: int | None = None


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    request_id: str
    operation: str
    arguments: dict[str, object]
    accepted: bool
    reply: ToolReply


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    revision: int
    status: Literal['active', 'finalized', 'failed']
    seed: SessionSeed
    events: tuple[JournalEvent, ...]


@dataclass(frozen=True)
class MutationCommand:
    session_id: str
    request_id: str
    expected_revision: int
    operation: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class MutationDecision:
    accepted: bool
    reply: ToolReply
    status: Literal['active', 'finalized', 'failed']


@dataclass(frozen=True)
class SessionView:
    record: SessionRecord
    requirements: tuple['Requirement', ...]
    atoms_by_requirement: dict[str, tuple['AtomicClaim', ...]]
    mappings_by_atom: dict[str, 'AtomResult | DeepMappingOutcome']
    source_context_trust: 'KnowledgeTrustConfig | None' = None
