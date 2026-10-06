"""Agent preflight verifies local knowledge without constructing a model."""
from dataclasses import dataclass
from reqmap.agent_config import AgentConfig
from reqmap.config import AnalysisProfile
from reqmap.errors import ReqmapError
from reqmap.knowledge import KnowledgeBase, load_knowledge
from reqmap.knowledge_v2 import KnowledgeBaseV2, load_knowledge_v2
from reqmap.binding_models import BindingCatalog


@dataclass(frozen=True)
class VerifiedKnowledge:
    kb: KnowledgeBase | KnowledgeBaseV2
    knowledge_sha256: str
    snapshot_id: str | None
    binding_catalog: BindingCatalog | None = None


def load_agent_knowledge(config: AgentConfig) -> VerifiedKnowledge:
    from reqmap.binding_catalog import load_binding_catalog
    def catalog(kb):
        return None if config.binding_catalog_path is None else load_binding_catalog(
            config.binding_catalog_path, kb,
            None if config.knowledge_trust is None else config.knowledge_trust.allowed_signers_path)
    if config.analysis_profile is AnalysisProfile.LEGACY:
        kb = load_knowledge(config.knowledge_path)
        return VerifiedKnowledge(kb, kb.snapshot_sha256, None, catalog(kb))
    trust = config.knowledge_trust
    if trust is None:
        raise ReqmapError('SNAPSHOT_UNTRUSTED', 'Для deep требуется внешний trust config.')
    kb = load_knowledge_v2(config.knowledge_path, trust.allowed_signers_path)
    if kb.trust is None or kb.trust.signer_identity != trust.signer_identity:
        raise ReqmapError('SNAPSHOT_UNTRUSTED', 'Signer identity не совпадает с конфигурацией.')
    return VerifiedKnowledge(kb, kb.trust.manifest_sha256, kb.snapshot_id, catalog(kb))
