"""One version identity for source binding, proposals, CLI checkpoints and MCP."""
from reqmap import binding_source
from reqmap.binding_models import BINDING_ENGINE_VERSION, PROPOSAL_SCHEMA_VERSION, BindingContext
from reqmap.config import AnalysisProfile
from reqmap.errors import ReqmapError


def binding_contract(profile, catalog=None):
    return dict(binding_engine_version=BINDING_ENGINE_VERSION,
                grammar_version=binding_source.GRAMMAR_VERSION,
                grammar_sha256=binding_source.GRAMMAR_SHA256,
                binding_catalog_sha256=None if catalog is None else catalog.catalog_sha256,
                binding_catalog_schema_version=None if catalog is None else catalog.schema_version,
                proposal_schema_version=PROPOSAL_SCHEMA_VERSION,
                result_schema_version="2.1" if profile is AnalysisProfile.DEEP else "1.1")


def require_session_contract(settings, knowledge=None):
    expected = binding_contract(settings.analysis_profile, None if knowledge is None else knowledge.binding_catalog)
    if knowledge is None:
        expected.pop("binding_catalog_sha256")
        expected.pop("binding_catalog_schema_version")
    if ((settings.tool_contract_version, settings.workflow_version) != ("2.0", "2.0")
            or any(getattr(settings, key, None) != value for key, value in expected.items())):
        raise ReqmapError("SESSION_CONTRACT_MISMATCH", "Контракт исходных обязательств изменился; начните новый анализ. Старые готовые отчёты доступны как исторические.")


def load_configured_catalog(config, knowledge):
    from reqmap.binding_catalog import load_binding_catalog
    if config.binding_catalog_path is None:
        return None
    signers = None if config.knowledge_trust is None else config.knowledge_trust.allowed_signers_path
    return load_binding_catalog(config.binding_catalog_path, knowledge, signers)


def requirement_context(requirement, catalog):
    return BindingContext(binding_source.bind_source(requirement), catalog)


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
    return ()
