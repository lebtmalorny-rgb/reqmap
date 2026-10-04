"""Agent fixtures use real local loaders and disposable signing identities."""
from pathlib import Path
from reqmap.agent_config import AgentConfig, AgentLimits
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from tests.deep_factories import signed_v2_snapshot


def make_agent_config(tmp_path: Path, profile: AnalysisProfile = AnalysisProfile.LEGACY) -> AgentConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    trust = None
    knowledge = Path(__file__).resolve().parents[1] / 'knowledge/epoxy-2025.1'
    if profile is AnalysisProfile.DEEP:
        knowledge, allowed = signed_v2_snapshot(tmp_path)
        trust = KnowledgeTrustConfig(allowed)
    return AgentConfig(profile, knowledge, trust, None, 8, tmp_path,
                       tmp_path / 'sessions', tmp_path / 'outputs', AgentLimits())


def call_tool(service, name, **arguments):
    return service.call(name, arguments)
