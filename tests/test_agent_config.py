import importlib
import json
import pytest
from reqmap.errors import ConfigError


def agent_module():
    assert importlib.util.find_spec('reqmap.agent_config'), 'model-free configuration is missing'
    return importlib.import_module('reqmap.agent_config')


def config_file(tmp_path, **changes):
    raw = dict(knowledge_path='kb', input_root='.', session_root='sessions', output_root='outputs', top_k=8)
    raw.update(changes)
    p = tmp_path / 'config.yaml'
    p.write_text(json.dumps(raw))
    return p


def test_agent_source_context_path_and_write_boundaries(tmp_path):
    module = agent_module()
    config = module.load_agent_config(config_file(tmp_path, source_context_path='review/map.json'))
    assert config.source_context_path == tmp_path/'review/map.json'
    for value in ('outputs/map.json', 'sessions/map.json'):
        with pytest.raises(ConfigError):
            module.load_agent_config(config_file(tmp_path, source_context_path=value))


def test_agent_config_resolves_paths_and_has_no_model(tmp_path):
    module = agent_module()
    config = module.load_agent_config(config_file(tmp_path))
    assert config.input_root == tmp_path
    assert config.knowledge_path == tmp_path / 'kb'
    assert config.limits.max_input_bytes == 26214400
    assert config.limits.max_requirements == 10000
    assert config.limits.max_atoms_per_requirement == 64
    assert config.limits.max_frame_bytes == 2097152
    assert config.limits.max_response_bytes == 524288
    assert config.limits.max_page_size == 50
    assert not hasattr(config, 'model')


@pytest.mark.parametrize('changes', [dict(model={}), dict(base_url='http://localhost'), dict(api_key='secret'), dict(other=True), dict(top_k=True), dict(limits={'max_input_bytes': True}), dict(limits={'max_page_size': 0}), dict(analysis_profile='deep'), dict(session_root='kb/sessions'), dict(output_root='sessions/out')])
def test_agent_config_rejects_unsafe_or_model_configuration(tmp_path, changes):
    module = agent_module()
    with pytest.raises(ConfigError):
        module.load_agent_config(config_file(tmp_path, **changes))
