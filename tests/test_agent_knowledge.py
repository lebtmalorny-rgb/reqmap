import importlib
from dataclasses import replace
from pathlib import Path
import pytest
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from reqmap.errors import ReqmapError
from tests.test_agent_config import agent_module, config_file
from tests.deep_factories import signed_v2_snapshot


def test_legacy_agent_knowledge_needs_no_model(tmp_path):
    config = agent_module().load_agent_config(config_file(tmp_path, knowledge_path=str(Path('knowledge/epoxy-2025.1').resolve())))
    assert importlib.util.find_spec('reqmap.agent_knowledge')
    loaded = importlib.import_module('reqmap.agent_knowledge').load_agent_knowledge(config)
    assert loaded.kb.release == '2025.1'
    assert len(loaded.knowledge_sha256) == 64


@pytest.mark.parametrize('case', ['valid', 'signer', 'unsigned', 'missing_ssh'])
def test_deep_agent_keeps_signed_trust_boundary(tmp_path, monkeypatch, case):
    module = agent_module()
    root, allowed = signed_v2_snapshot(tmp_path)
    config = module.load_agent_config(config_file(tmp_path, knowledge_path=str(root), analysis_profile='deep', knowledge_trust={'allowed_signers_path':str(allowed)}))
    loader = importlib.import_module('reqmap.agent_knowledge').load_agent_knowledge
    if case == 'signer':
        config = replace(config, knowledge_trust=KnowledgeTrustConfig(allowed, 'untrusted'))
    elif case == 'unsigned':
        config = replace(config, knowledge_path=Path('tests/fixtures/kb_v2_minimal').resolve())
    elif case == 'missing_ssh':
        monkeypatch.setenv('PATH', '')
    if case == 'valid':
        assert loader(config).kb.snapshot_status == 'approved'
    else:
        with pytest.raises(ReqmapError):
            loader(config)
