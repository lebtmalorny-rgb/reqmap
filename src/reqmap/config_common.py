"""Shared public config parsing; model-free consumers use only these fields."""
import json
from pathlib import Path
from reqmap.config import (
    AnalysisProfile, InputProfile, KnowledgeTrustConfig,
    _build_analysis_profile, _build_input_profile, _build_knowledge_trust,
    _reject_non_finite_constant, _require_mapping,
)
from reqmap.errors import ConfigError


def read_config_object(path: Path) -> dict[str, object]:
    try:
        source = path.read_text(encoding="utf-8")
        without_comments = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        raw = json.loads(without_comments, parse_constant=_reject_non_finite_constant)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            "CONFIG_INVALID", f"Не удалось прочитать конфигурацию: {exc}"
        ) from exc

    _require_mapping(raw, "конфигурация")
    return raw


def parse_input_profile(raw: object) -> InputProfile:
    return _build_input_profile(raw)


def parse_analysis_profile(raw: object) -> AnalysisProfile:
    return _build_analysis_profile(raw)


def parse_knowledge_trust(raw: object, base: Path) -> KnowledgeTrustConfig:
    return _build_knowledge_trust(raw, base)


def parse_binding_catalog_path(raw: object, base: Path) -> Path | None:
    import os
    from reqmap.output_safety import symlink_component
    if raw is None:
        return None
    if type(raw) is not str or not raw.strip() or ".." in Path(raw).parts:
        raise ConfigError("CONFIG_INVALID", "Некорректный binding_catalog_path.")
    path = Path(os.path.abspath(base / raw))
    if symlink_component(path):
        raise ConfigError("CONFIG_INVALID", "binding_catalog_path содержит symlink.")
    return path


def parse_source_context_path(raw: object, base: Path) -> Path | None:
    try:
        return parse_binding_catalog_path(raw, base)
    except ConfigError as exc:
        raise ConfigError("CONFIG_INVALID", "Некорректный source_context_path.") from exc
