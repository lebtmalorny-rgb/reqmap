"""One version identity for source binding, proposals, CLI checkpoints and MCP."""
from reqmap import binding_source
from reqmap.binding_models import BINDING_ENGINE_VERSION, PROPOSAL_SCHEMA_VERSION, BindingContext
from reqmap.config import AnalysisProfile
from reqmap.errors import ReqmapError


def binding_contract(profile, catalog=None):
    from reqmap.source_context import source_context_resolver_sha256
    return dict(binding_engine_version=BINDING_ENGINE_VERSION,
                source_context_version="1.0", resolver_version="1.0", resolver_sha256=source_context_resolver_sha256(),
                grammar_version=binding_source.GRAMMAR_VERSION,
                grammar_sha256=binding_source.GRAMMAR_SHA256,
                binding_catalog_sha256=None if catalog is None else catalog.catalog_sha256,
                binding_catalog_schema_version=None if catalog is None else catalog.schema_version,
                proposal_schema_version=PROPOSAL_SCHEMA_VERSION,
                result_schema_version="2.2" if profile is AnalysisProfile.DEEP else "1.2")


def require_session_contract(settings, knowledge=None):
    expected = binding_contract(settings.analysis_profile, None if knowledge is None else knowledge.binding_catalog)
    if knowledge is None:
        expected.pop("binding_catalog_sha256")
        expected.pop("binding_catalog_schema_version")
    if ((settings.tool_contract_version, settings.workflow_version) != ("3.0", "3.0")
            or any(getattr(settings, key, None) != value for key, value in expected.items())):
        raise ReqmapError("SESSION_CONTRACT_MISMATCH", "Контракт исходных обязательств изменился; начните новый анализ. Старые готовые отчёты доступны как исторические.")


def load_configured_catalog(config, knowledge):
    from reqmap.binding_catalog import load_binding_catalog
    if config.binding_catalog_path is None:
        return None
    signers = None if config.knowledge_trust is None else config.knowledge_trust.allowed_signers_path
    return load_binding_catalog(config.binding_catalog_path, knowledge, signers)


def requirement_context(requirement, catalog, source_context=None, *, source_context_trust=None):
    from dataclasses import replace
    binding = binding_source.bind_source(requirement)
    decision = None if source_context is None else next((d for d in source_context.decisions
        if d.target.requirement_id == requirement.requirement_id), None)
    if source_context is not None and decision is None:
        raise ReqmapError("SOURCE_CONTEXT_REF_INVALID", "Строка отсутствует в snapshot контекста.")
    return BindingContext(replace(binding, context_decision=decision), catalog,
                          source_context=source_context, source_context_trust=source_context_trust)


def validate_persisted_binding(result, context, knowledge, records, prior_status):
    from reqmap.binding_engine import mapping_decision, apply_decision
    decision = result.binding_decision
    if decision is None:
        raise ValueError("SESSION_CONTRACT_MISMATCH: checkpoint has no binding decision")
    # The previous status is an upper bound: persistence must never promote it.
    from reqmap.models import SupportStatus
    if prior_status is not result.support_status and prior_status is SupportStatus.INSUFFICIENT_EVIDENCE:
        raise ValueError("BINDING_EVIDENCE_CHANGED")
    expected = mapping_decision(result.atom, context, knowledge, decision.predicate_ids,
                                result.support_status, records)
    if expected != decision or apply_decision(result, expected) != result:
        raise ValueError("BINDING_DECISION_CHANGED")


def catalog_output_diagnostics(config, output):
    """Reject overlap before any cleanup can delete catalog or knowledge inputs."""
    path = config.binding_catalog_path
    if path is not None:
        path, output = path.absolute(), output.absolute()
        if path == output or path.is_relative_to(output) or output.is_relative_to(path):
            return ("OUTPUT_PATH_CONFLICT: output пересекается с binding catalog.",)
    from reqmap.source_context import source_context_output_diagnostics
    return source_context_output_diagnostics(config.source_context_path, (output,), config.knowledge_trust)


def prepare_run_context(request, config):
    """Capture a new map, or recover one unambiguous matching frozen run."""
    from reqmap.source_context import capture_source_document, load_source_context_map, freeze_source_context
    from reqmap.source_context_codec import decode_source_context_snapshot
    from reqmap.agent_input import read_regular_bytes
    from reqmap.output_safety import strict_json_object
    from dataclasses import replace
    if catalog_output_diagnostics(config, request.output_dir):
        raise ReqmapError("SOURCE_CONTEXT_INVALID", "Output пересекается с защищённым входом контекста.")
    document = request.source_document
    if document is None:
        raise ReqmapError("SOURCE_CONTEXT_INPUT_MISMATCH", "Для анализа требуется snapshot исходных bytes.")
    verified = capture_source_document(content=document.content, source_kind=document.source_kind,
        source_name=document.source_name, text_mode=document.text_mode, input_profile=document.input_profile,
        requirements=request.requirements)
    if (verified != document or request.input_sha256 != document.input_sha256
            or document.input_profile != (config.input_profile if document.source_kind == "xlsx" else None)):
        raise ReqmapError("SOURCE_CONTEXT_INPUT_MISMATCH", "Запрос или профиль отличаются от snapshot входа.")
    path = config.source_context_path
    missing = path is not None and not path.exists() and not path.is_symlink()
    current = None if missing else freeze_source_context(document, load_source_context_map(path, document,
        profile=config.analysis_profile, trust=config.knowledge_trust))
    matches = []
    incompatible = False
    for saved in sorted((request.output_dir/".work").glob("*/source-context.json")):
        raw = strict_json_object(read_regular_bytes(saved, 512*1024*1024).decode("utf-8"))
        identity = raw.get("document", {})
        if (identity.get("input_sha256") != document.input_sha256
                or identity.get("requirements_sha256") != document.requirements_sha256
                or identity.get("input_profile_sha256") != document.input_profile_sha256):
            continue
        if (raw.get("grammar_version"), raw.get("grammar_sha256")) != (
                binding_source.GRAMMAR_VERSION, binding_source.GRAMMAR_SHA256):
            incompatible = True
            continue
        if not missing:
            from reqmap.source_context_codec import encode_source_context_snapshot
            expected = encode_source_context_snapshot(current)
            if (raw.get("loaded", {}).get("map_bytes") != expected["loaded"]["map_bytes"]
                    or raw.get("loaded", {}).get("signature_bytes") != expected["loaded"]["signature_bytes"]):
                continue
            if any(d.get("resolver_sha256") != current.decisions[0].resolver_sha256 for d in raw.get("decisions", [])):
                continue
        restored = decode_source_context_snapshot(raw, profile=config.analysis_profile, trust=config.knowledge_trust)
        if restored.document == document:
            matches.append(restored)
    if matches:
        if any(item != matches[0] for item in matches[1:]):
            raise ReqmapError("SOURCE_CONTEXT_INVALID", "Несколько сохранённых карт; укажите карту для нового запуска.")
        return matches[0]
    if missing:
        if incompatible:
            raise ReqmapError("SOURCE_CONTEXT_CONTRACT_MISMATCH", "Грамматика сохранённого запуска изменена; укажите карту для нового анализа.")
        raise ReqmapError("SOURCE_CONTEXT_INVALID", "Карта отсутствует и подходящий snapshot не найден.")
    return current


def save_run_context(directory, snapshot):
    from reqmap.export_json import atomic_write_bytes, canonical_json_bytes
    from reqmap.source_context_codec import encode_source_context_snapshot
    atomic_write_bytes(directory/"source-context.json", canonical_json_bytes(encode_source_context_snapshot(snapshot)))
