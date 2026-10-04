"""Versioned local-agent records; no transport or model execution."""
from dataclasses import dataclass
from typing import Literal
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


@dataclass(frozen=True)
class SessionSeed:
    input_snapshot: InputSnapshot
    settings: SessionSettings
    clarifications: tuple[str, ...]
    parent_session_id: str | None
    reported_client: str


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
