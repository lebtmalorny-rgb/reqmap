"""Strict local agent configuration, deliberately without a model endpoint."""
from dataclasses import dataclass, fields
from pathlib import Path
import os
from reqmap.config import AnalysisProfile, InputProfile, KnowledgeTrustConfig
from reqmap.config_common import read_config_object, parse_analysis_profile, parse_input_profile, parse_knowledge_trust
from reqmap.errors import ConfigError
from reqmap.output_safety import symlink_component


@dataclass(frozen=True)
class AgentLimits:
    max_input_bytes: int = 26214400
    max_requirements: int = 10000
    max_atoms_per_requirement: int = 64
    max_frame_bytes: int = 2097152
    max_response_bytes: int = 524288
    max_page_size: int = 50


@dataclass(frozen=True)
class AgentConfig:
    analysis_profile: AnalysisProfile
    knowledge_path: Path
    knowledge_trust: KnowledgeTrustConfig | None
    input_profile: InputProfile | None
    top_k: int
    input_root: Path
    session_root: Path
    output_root: Path
    limits: AgentLimits
    binding_catalog_path: Path | None = None


def load_agent_config(path: Path) -> AgentConfig:
    raw = read_config_object(path)
    allowed = {'analysis_profile', 'knowledge_path', 'knowledge_trust', 'input_profile', 'top_k', 'input_root', 'session_root', 'output_root', 'limits', 'binding_catalog_path'}
    if set(raw) - allowed:
        raise ConfigError('CONFIG_INVALID', 'Неизвестные поля конфигурации агента.')
    base = path.absolute().parent
    def location(name):
        value = raw.get(name)
        if type(value) is not str or not value.strip() or '..' in Path(value).parts:
            raise ConfigError('CONFIG_INVALID', f'Некорректный путь {name}.')
        result = Path(os.path.abspath(base / value))
        if symlink_component(result):
            raise ConfigError('CONFIG_INVALID', f'Путь {name} содержит символическую ссылку.')
        return result
    paths = {name: location(name) for name in ('knowledge_path', 'input_root', 'session_root', 'output_root')}
    profile = parse_analysis_profile(raw.get('analysis_profile', 'legacy'))
    trust = parse_knowledge_trust(raw['knowledge_trust'], base) if raw.get('knowledge_trust') is not None else None
    if profile is AnalysisProfile.DEEP and trust is None:
        raise ConfigError('CONFIG_INVALID', 'Для deep требуется внешний knowledge_trust.')
    limits = raw.get('limits', {})
    if type(limits) is not dict or set(limits) - {f.name for f in fields(AgentLimits)} or any(type(v) is not int or v <= 0 for v in limits.values()):
        raise ConfigError('CONFIG_INVALID', 'Лимиты должны быть положительными целыми числами.')
    top_k = raw.get('top_k', 8)
    if type(top_k) is not int or not 1 <= top_k <= 50:
        raise ConfigError('CONFIG_INVALID', 'top_k должен быть целым от 1 до 50.')
    writes = (paths['session_root'], paths['output_root'])
    from reqmap.config_common import parse_binding_catalog_path
    binding_path = parse_binding_catalog_path(raw.get('binding_catalog_path'), base)
    protected = (paths['knowledge_path'],) + ((trust.allowed_signers_path.absolute(),) if trust else ()) + ((binding_path,) if binding_path else ())
    if any(a.is_relative_to(b) or b.is_relative_to(a) for a in writes for b in protected) or writes[0].is_relative_to(writes[1]) or writes[1].is_relative_to(writes[0]):
        raise ConfigError('CONFIG_INVALID', 'Каталоги записи должны быть раздельными и не пересекаться с KB/trust.')
    return AgentConfig(profile, paths['knowledge_path'], trust,
                       parse_input_profile(raw['input_profile']) if raw.get('input_profile') is not None else None,
                       top_k, paths['input_root'], *writes, AgentLimits(**limits), binding_path)
