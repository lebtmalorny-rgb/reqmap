"""Fail-closed deep preflight, orchestration, and schema-v2 resume."""

from __future__ import annotations

from reqmap.binding_runtime import binding_contract, load_configured_catalog, requirement_context, validate_persisted_binding, catalog_output_diagnostics
from reqmap.binding_codec import decode_binding_decision
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from reqmap import __version__
from reqmap.config import AnalysisProfile, AppConfig, KnowledgeTrustConfig, ModelConfig
from reqmap.decomposition import decompose
from reqmap.deep_aggregation import (
    aggregate_deep_groups,
    aggregate_deep_requirement,
    deep_run_status,
    validate_deep_graph,
)
from reqmap.deep_mapping import (
    DeepMappingOutcome,
    map_atom_deep,
    validate_responsibility_records,
)
from reqmap.deep_models import (
    DeepAtomResult,
    DeepEvidence,
    DeepRequirementResult,
    DeepRunResult,
    LifecyclePhase,
    ProcedureGraph,
    ProcedureStep,
    ResponsibilityContour,
    ResponsibilityRecord,
    VersionScope,
)
from reqmap.deep_retrieval import retrieve_deep
from reqmap.errors import ModelError, ReqmapError
from reqmap.export_deep_json import validate_deep_run_result
from reqmap.ids import generated_requirement_id
from reqmap.knowledge_v2 import KnowledgeBaseV2, load_knowledge_v2
from reqmap.llm import JsonModel
from reqmap.manifest import write_deep_preflight_artifacts
from reqmap.models import (
    AnalysisRequest,
    AnalysisState,
    AtomicClaim,
    EvidencePolarity,
    EvidenceStrength,
    PreflightResult,
    Requirement,
    SourceCoordinate,
    SourceField,
    SourceHint,
    SupportStatus,
    to_dict,
)
from reqmap.output_safety import (
    OUTPUT_NOT_CLEAN_CODES,
    atomic_write,
    canonical_bytes,
    clear_published_artifacts,
    ensure_secure_directory,
    strict_json_object,
    symlink_component,
    validate_analysis_request,
)
from reqmap.procedure import instantiate_procedure_graphs, validate_procedure_graph
from reqmap.prompts import (
    PROMPT_DECOMPOSITION_VERSION,
    PROMPT_DEEP_MAPPING_VERSION,
)


SCHEMA_VERSION = "2.0"
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NO_PREFLIGHT_OUTPUT_CODES = frozenset(
    {
        "ANALYSIS_PROFILE_INVALID",
        "CONFIG_INVALID",
        "INPUT_UNREADABLE",
        "OUTPUT_INPUT_COLLISION",
        "OUTPUT_INVALID",
        "OUTPUT_SYMLINK",
        "OUTPUT_UNWRITABLE",
        "REQUEST_INVALID",
        *OUTPUT_NOT_CLEAN_CODES,
    }
)


@dataclass
class _CountingModel:
    inner: JsonModel
    decomposition_calls: int = 0
    deep_mapping_calls: int = 0

    def complete_json(
        self, stage: str, system_prompt: str, payload: dict[str, object]
    ) -> dict[str, object]:
        if stage == "decomposition":
            self.decomposition_calls += 1
        elif stage == "deep_mapping":
            self.deep_mapping_calls += 1
        return self.inner.complete_json(stage, system_prompt, payload)


@dataclass(frozen=True)
class _RequirementBuild:
    result: DeepRequirementResult
    records: tuple[ResponsibilityRecord, ...]
    graphs: tuple[ProcedureGraph, ...]
    evidence: tuple[DeepEvidence, ...]


def preflight_deep(config: AppConfig, model: JsonModel) -> PreflightResult:
    """Verify deep configuration, signed knowledge, and model in fixed order."""
    result, _knowledge = _perform_deep_preflight(config, model)
    return result


def analyze_deep(
    request: AnalysisRequest,
    config: AppConfig,
    model: JsonModel,
) -> DeepRunResult:
    """Analyze requirements with the schema-v2 deep pipeline."""
    preflight_result, knowledge = _perform_deep_preflight(
        config, model, request=request
    )
    if not preflight_result.ok or knowledge is None:
        run = _failed_preflight_run(request, config, preflight_result, knowledge)
        return _finish_failed_preflight(run, request)

    catalog = load_configured_catalog(config, knowledge)
    signature = _deep_run_signature(request, config, knowledge, catalog=catalog)
    work_dir = request.output_dir / ".work"
    signature_dir = work_dir / signature
    try:
        ensure_secure_directory(work_dir)
        ensure_secure_directory(signature_dir)
    except OSError:
        failed = PreflightResult(
            False,
            ("OUTPUT_WORKDIR: не удалось создать безопасный рабочий каталог.",),
            knowledge.trust.manifest_sha256 if knowledge.trust is not None else None,
        )
        run = _failed_preflight_run(request, config, failed, knowledge)
        return _finish_failed_preflight(run, request)

    counted_model = _CountingModel(model)
    expected_initials = {"decomposition": 0, "deep_mapping": 0}
    parent_texts = {
        item.requirement_id: item.text for item in request.requirements
    }
    results: list[DeepRequirementResult] = []
    all_records: list[ResponsibilityRecord] = []
    all_graphs: list[ProcedureGraph] = []
    for requirement in request.requirements:
        checkpoint_path = signature_dir / f"{requirement.requirement_id}.json"
        resumed = _load_deep_checkpoint(
            checkpoint_path,
            signature,
            request,
            requirement,
            knowledge,
            config,
            catalog=catalog,
        )
        if resumed is None:
            build = _analyze_deep_requirement(
                requirement,
                parent_texts.get(requirement.parent_id),
                config,
                counted_model,
                knowledge,
                expected_initials,
                catalog=catalog,
            )
            if build.result.analysis_state is AnalysisState.COMPLETED:
                try:
                    _write_deep_checkpoint(
                        checkpoint_path, signature, build, request
                    )
                except (OSError, TypeError, ValueError):
                    build = _RequirementBuild(
                        _failed_deep_requirement(
                            requirement,
                            AnalysisState.VALIDATION_FAILED,
                            "CHECKPOINT_FAILED: checkpoint не удалось безопасно записать.",
                        ),
                        (),
                        (),
                        (),
                    )
        else:
            build = resumed
        results.append(build.result)
        all_records.extend(build.records)
        all_graphs.extend(build.graphs)

    requirement_results = tuple(results)
    records = tuple(all_records)
    graphs = tuple(all_graphs)
    evidence = cited_deep_evidence(records, graphs, knowledge)
    status = deep_run_status(requirement_results, preflight_ok=True)
    run = DeepRunResult(
        run_id=f"run-{signature[:16]}",
        schema_version=SCHEMA_VERSION,
        run_status=status,
        requirements=requirement_results,
        groups=aggregate_deep_groups(requirement_results, records),
        responsibility_records=records,
        procedure_graphs=graphs,
        evidence=evidence,
        metadata={**_deep_metadata(
            request,
            config,
            knowledge,
            records,
            _retry_counts(counted_model, expected_initials),
        ), "binding_contract": binding_contract(config.analysis_profile, catalog)},
        diagnostics=_ordered_unique(
            message
            for result in requirement_results
            for message in result.diagnostics
        ),
    )
    validate_deep_run_result(run)
    return run


def _perform_deep_preflight(
    config: AppConfig,
    model: JsonModel,
    request: AnalysisRequest | None = None,
) -> tuple[PreflightResult, KnowledgeBaseV2 | None]:
    diagnostics = _deep_config_diagnostics(config)
    if diagnostics:
        return PreflightResult(False, diagnostics, None), None

    if request is not None:
        diagnostics = validate_analysis_request(request)
        if not diagnostics:
            diagnostics = catalog_output_diagnostics(config, request.output_dir)
        if not diagnostics:
            diagnostics = _deep_request_diagnostics(request)
        if diagnostics:
            return PreflightResult(False, diagnostics, None), None
        cleanup_diagnostics = clear_published_artifacts(request.output_dir)
        if cleanup_diagnostics:
            return PreflightResult(False, cleanup_diagnostics, None), None

    if _recognizable_legacy_v1(config.knowledge_path):
        return PreflightResult(
            False,
            (
                "KNOWLEDGE_SCHEMA_UNSUPPORTED: analysis_profile=deep требует "
                "подписанную базу знаний schema v2.",
            ),
            None,
        ), None

    trust = config.knowledge_trust
    assert trust is not None
    try:
        knowledge = load_knowledge_v2(
            config.knowledge_path,
            trust.allowed_signers_path,
        )
        load_configured_catalog(config, knowledge)
    except ReqmapError as exc:
        return PreflightResult(
            False,
            (f"{exc.code}: {exc.message_ru}",),
            None,
        ), None
    except OSError:
        return PreflightResult(
            False,
            ("KNOWLEDGE_IO: не удалось прочитать локальную базу знаний.",),
            None,
        ), None

    assert knowledge.trust is not None
    if knowledge.trust.signer_identity != trust.signer_identity:
        return PreflightResult(
            False,
            (
                "SNAPSHOT_UNTRUSTED: signer identity не совпадает с deep configuration.",
            ),
            knowledge.trust.manifest_sha256,
        ), knowledge
    manifest_sha256 = knowledge.trust.manifest_sha256
    try:
        model_preflight = getattr(model, "preflight", None)
        if not callable(model_preflight):
            raise ModelError(
                "MODEL_PREFLIGHT_MISSING",
                "Клиент модели не поддерживает обязательный preflight.",
            )
        model_preflight()
    except ReqmapError as exc:
        return PreflightResult(
            False,
            (_safe_model_diagnostic(exc, config),),
            manifest_sha256,
        ), knowledge
    except Exception:
        return PreflightResult(
            False,
            ("MODEL_PREFLIGHT_FAILED: проверка модели завершилась ошибкой.",),
            manifest_sha256,
        ), knowledge
    return PreflightResult(True, (), manifest_sha256), knowledge


def _deep_config_diagnostics(config: AppConfig) -> tuple[str, ...]:
    if type(config) is not AppConfig or type(config.model) is not ModelConfig:
        return ("CONFIG_INVALID: ожидается canonical AppConfig.",)
    diagnostics: list[str] = []
    if config.analysis_profile is not AnalysisProfile.DEEP:
        diagnostics.append(
            "ANALYSIS_PROFILE_INVALID: analysis_profile должен быть deep."
        )
    if not isinstance(config.knowledge_path, Path):
        diagnostics.append("CONFIG_INVALID: knowledge_path должен быть Path.")
    if type(config.top_k) is not int or not 1 <= config.top_k <= 50:
        diagnostics.append("CONFIG_INVALID: top_k должен быть от 1 до 50.")
    if type(config.model.model) is not str or not config.model.model:
        diagnostics.append("CONFIG_INVALID: имя модели должно быть непустым.")
    elif not _safe_model_identifier(config.model.model):
        diagnostics.append("CONFIG_INVALID: имя модели содержит небезопасные данные.")
    if config.model.seed is not None and type(config.model.seed) is not int:
        diagnostics.append("CONFIG_INVALID: seed должен быть int или null.")
    if type(config.model.base_url) is not str:
        diagnostics.append("CONFIG_INVALID: base_url модели недопустим.")
    else:
        parsed = urlsplit(config.model.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            diagnostics.append("CONFIG_INVALID: base_url модели недопустим.")
    trust = config.knowledge_trust
    if type(trust) is not KnowledgeTrustConfig:
        diagnostics.append("CONFIG_INVALID: knowledge_trust обязателен для deep.")
    elif not isinstance(trust.allowed_signers_path, Path):
        diagnostics.append(
            "CONFIG_INVALID: allowed_signers_path должен быть Path."
        )
    return tuple(diagnostics)


def _deep_request_diagnostics(request: AnalysisRequest) -> tuple[str, ...]:
    diagnostics: list[str] = []
    expected = tuple(
        generated_requirement_id(index)
        for index in range(1, len(request.requirements) + 1)
    )
    actual = tuple(item.requirement_id for item in request.requirements)
    if actual != expected:
        diagnostics.append(
            "REQUEST_INVALID: deep requirement_id должны быть canonical REQ-XXXX.",
        )
    if not isinstance(request.output_dir, Path):
        return tuple(diagnostics)
    work_dir = request.output_dir / ".work"
    if work_dir.exists() and not work_dir.is_dir():
        diagnostics.append(
            "OUTPUT_INVALID: зарезервированный .work не является каталогом."
        )
    return tuple(diagnostics)


def _safe_model_identifier(value: str) -> bool:
    normalized = value.casefold()
    credential_fragments = (
        "api_key",
        "authorization",
        "bearer ",
        "credential",
        "password",
        "token=",
    )
    return not (
        _SAFE_MODEL.fullmatch(value) is None
        or value.startswith(("/", "./", "../", "~"))
        or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        or "://" in value
        or "@" in value
        or "?" in value
        or "#" in value
        or normalized.startswith("sk-")
        or any(fragment in normalized for fragment in credential_fragments)
    )


def _analyze_deep_requirement(
    requirement: Requirement,
    parent_text: str | None,
    config: AppConfig,
    model: _CountingModel,
    knowledge: KnowledgeBaseV2,
    expected_initials: dict[str, int],
    *, catalog=None,
) -> _RequirementBuild:
    expected_initials["decomposition"] += 1
    try:
        decomposition = decompose(model, requirement, parent_text)
    except ModelError as exc:
        return _RequirementBuild(
            _failed_deep_requirement(
                requirement,
                AnalysisState.MODEL_FAILED,
                _safe_model_diagnostic(exc, config),
            ),
            (),
            (),
            (),
        )
    except (ReqmapError, TypeError, ValueError) as exc:
        return _RequirementBuild(
            _failed_deep_requirement(
                requirement,
                AnalysisState.VALIDATION_FAILED,
                _safe_local_diagnostic(exc),
            ),
            (),
            (),
            (),
        )
    if decomposition.analysis_state is not AnalysisState.COMPLETED:
        return _RequirementBuild(
            _failed_deep_requirement(
                requirement,
                decomposition.analysis_state,
                "; ".join(decomposition.diagnostics)
                or "Декомпозиция не завершена.",
            ),
            (),
            (),
            (),
        )

    outcomes: list[DeepMappingOutcome] = []
    for atom in decomposition.atoms:
        try:
            retrieval = retrieve_deep(
                knowledge,
                atom.text,
                requirement.source_hints,
                config.top_k,
            )
            expected_initials["deep_mapping"] += 1
            outcome = map_atom_deep(model, atom, retrieval, knowledge, binding_context=requirement_context(requirement, catalog))
        except ModelError as exc:
            outcome = _failed_mapping_outcome(
                atom,
                AnalysisState.MODEL_FAILED,
                _safe_model_diagnostic(exc, config),
            )
        except (ReqmapError, TypeError, ValueError) as exc:
            outcome = _failed_mapping_outcome(
                atom,
                AnalysisState.VALIDATION_FAILED,
                _safe_local_diagnostic(exc),
            )
        outcomes.append(outcome)

    records = tuple(
        record
        for outcome in outcomes
        for record in outcome.responsibility_records
    )
    template_ids = tuple(
        sorted(
            {
                template_id
                for outcome in outcomes
                for template_id in outcome.procedure_template_ids
            }
        )
    )
    try:
        procedure = instantiate_procedure_graphs(
            requirement.requirement_id,
            template_ids,
            records,
            knowledge,
        )
        result, linked_records = aggregate_deep_requirement(
            requirement,
            tuple(outcomes),
            procedure,
        )
    except (ReqmapError, TypeError, ValueError) as exc:
        return _RequirementBuild(
            _failed_deep_requirement(
                requirement,
                AnalysisState.VALIDATION_FAILED,
                _safe_local_diagnostic(exc),
            ),
            (),
            (),
            (),
        )
    evidence = cited_deep_evidence(linked_records, procedure.graphs, knowledge)
    return _RequirementBuild(result, linked_records, procedure.graphs, evidence)


def _failed_mapping_outcome(
    atom: AtomicClaim,
    state: AnalysisState,
    diagnostic: str,
) -> DeepMappingOutcome:
    return DeepMappingOutcome(
        DeepAtomResult(
            atom=atom,
            analysis_state=state,
            support_status=None,
            responsibility_ids=(),
            diagnostics=(diagnostic,),
        ),
        (),
        (),
    )


def _failed_deep_requirement(
    requirement: Requirement,
    state: AnalysisState,
    diagnostic: str,
) -> DeepRequirementResult:
    return DeepRequirementResult(
        requirement=requirement,
        analysis_state=state,
        support_status=None,
        atom_results=(),
        responsibility_ids=(),
        procedure_graph_ids=(),
        diagnostics=(diagnostic,),
    )


def cited_deep_evidence(
    records: tuple[ResponsibilityRecord, ...],
    graphs: tuple[ProcedureGraph, ...],
    knowledge: KnowledgeBaseV2,
) -> tuple[DeepEvidence, ...]:
    identifiers = _ordered_unique(
        evidence_id
        for record in records
        for evidence_id in record.evidence_ids
    )
    identifiers = _ordered_unique(
        (
            *identifiers,
            *(
                evidence_id
                for graph in graphs
                for step in graph.steps
                for evidence_id in step.evidence_ids
            ),
        )
    )
    return tuple(knowledge.evidence[evidence_id] for evidence_id in identifiers)


def _deep_metadata(
    request: object,
    config: object,
    knowledge: KnowledgeBaseV2 | None,
    records: tuple[ResponsibilityRecord, ...],
    retry_counts: dict[str, int],
) -> dict[str, object]:
    trust = knowledge.trust if knowledge is not None else None
    model_config = (
        config.model
        if type(config) is AppConfig and type(config.model) is ModelConfig
        else None
    )
    model_name = (
        model_config.model
        if model_config is not None
        and type(model_config.model) is str
        and _safe_model_identifier(model_config.model)
        else "invalid-model"
    )
    seed = (
        model_config.seed
        if model_config is not None
        and (model_config.seed is None or type(model_config.seed) is int)
        else None
    )
    top_k = (
        config.top_k
        if type(config) is AppConfig
        and type(config.top_k) is int
        and config.top_k > 0
        else 1
    )
    input_sha256 = (
        request.input_sha256
        if type(request) is AnalysisRequest
        and type(request.input_sha256) is str
        and _SHA256.fullmatch(request.input_sha256) is not None
        else "0" * 64
    )
    target_release = (
        "2026.1"
        if any(
            record.lifecycle_phase is LifecyclePhase.UPGRADE
            and record.version_scope.target_release == "2026.1"
            for record in records
        )
        else "2025.1"
    )
    return {
        "reqmap_version": __version__,
        "analysis_profile": "deep",
        "model": model_name,
        "seed": seed,
        "top_k": top_k,
        "input_sha256": input_sha256,
        "snapshot_id": knowledge.snapshot_id if knowledge is not None else None,
        "manifest_sha256": trust.manifest_sha256 if trust is not None else None,
        "key_id": trust.key_id if trust is not None else None,
        "signer_identity": trust.signer_identity if trust is not None else None,
        "prompt_versions": {
            "decomposition": PROMPT_DECOMPOSITION_VERSION,
            "deep_mapping": PROMPT_DEEP_MAPPING_VERSION,
        },
        "release_profile": {
            "source_release": (
                knowledge.base_release if knowledge is not None else "2025.1"
            ),
            "target_release": target_release,
            "kolla_ansible_release": (
                knowledge.kolla_ansible_release if knowledge is not None else "2025.1"
            ),
            "host_profile": (
                knowledge.host_profile
                if knowledge is not None
                else "rocky_linux_9"
            ),
        },
        "retry_counts": retry_counts,
    }


def _retry_counts(
    model: _CountingModel, expected_initials: dict[str, int]
) -> dict[str, int]:
    return {
        "decomposition": max(
            0, model.decomposition_calls - expected_initials["decomposition"]
        ),
        "deep_mapping": max(
            0, model.deep_mapping_calls - expected_initials["deep_mapping"]
        ),
    }


def _failed_preflight_run(
    request: object,
    config: object,
    result: PreflightResult,
    knowledge: KnowledgeBaseV2 | None,
) -> DeepRunResult:
    diagnostic = "; ".join(result.diagnostics) or "Preflight не пройден."
    source_requirements = _canonical_deep_source_requirements(request)
    requirements = tuple(
        _failed_deep_requirement(
            requirement,
            AnalysisState.SKIPPED,
            f"SKIPPED: {diagnostic}",
        )
        for requirement in source_requirements
    )
    input_sha256 = (
        request.input_sha256
        if type(request) is AnalysisRequest
        and type(request.input_sha256) is str
        else "0" * 64
    )
    seed = canonical_bytes(
        {
            "input_sha256": input_sha256,
            "diagnostics": result.diagnostics,
        }
    )
    run = DeepRunResult(
        run_id=f"run-{hashlib.sha256(seed).hexdigest()[:16]}",
        schema_version=SCHEMA_VERSION,
        run_status="FAILED",
        requirements=requirements,
        groups=aggregate_deep_groups(requirements, ()),
        responsibility_records=(),
        procedure_graphs=(),
        evidence=(),
        metadata=_deep_metadata(
            request,
            config,
            knowledge,
            (),
            {"decomposition": 0, "deep_mapping": 0},
        ),
        diagnostics=result.diagnostics,
    )
    if not any(
        diagnostic.partition(":")[0] in _NO_PREFLIGHT_OUTPUT_CODES
        for diagnostic in run.diagnostics
    ):
        validate_deep_run_result(run)
    return run


def _canonical_deep_source_requirements(
    request: object,
) -> tuple[Requirement, ...]:
    if type(request) is not AnalysisRequest or type(request.requirements) is not tuple:
        return ()
    requirements = request.requirements
    if any(type(item) is not Requirement for item in requirements):
        return ()
    expected_ids = tuple(
        generated_requirement_id(index) for index in range(1, len(requirements) + 1)
    )
    if tuple(item.requirement_id for item in requirements) != expected_ids:
        return ()
    if tuple(item.ordinal for item in requirements) != tuple(
        range(1, len(requirements) + 1)
    ):
        return ()
    return requirements


def _finish_failed_preflight(
    run: DeepRunResult, request: object
) -> DeepRunResult:
    forbidden = any(
        diagnostic.partition(":")[0] in _NO_PREFLIGHT_OUTPUT_CODES
        for diagnostic in run.diagnostics
    )
    if not forbidden and type(request) is AnalysisRequest:
        try:
            write_deep_preflight_artifacts(run, request.output_dir)
        except (OSError, TypeError, ValueError):
            pass
    return run


def _deep_run_signature(
    request: AnalysisRequest,
    config: AppConfig,
    knowledge: KnowledgeBaseV2,
    *, catalog=None,
) -> str:
    trust = knowledge.trust
    assert trust is not None
    payload = {
        "binding_contract": binding_contract(config.analysis_profile, catalog),
        "requirements": to_dict(request.requirements),
        "input_sha256": request.input_sha256,
        "manifest_sha256": trust.manifest_sha256,
        "snapshot_id": trust.snapshot_id,
        "key_id": trust.key_id,
        "model": config.model.model,
        "seed": config.model.seed,
        "top_k": config.top_k,
        "analysis_profile": config.analysis_profile.value,
        "prompt_versions": {
            "decomposition": PROMPT_DECOMPOSITION_VERSION,
            "deep_mapping": PROMPT_DEEP_MAPPING_VERSION,
        },
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _write_deep_checkpoint(
    path: Path,
    signature: str,
    build: _RequirementBuild,
    request: AnalysisRequest,
) -> None:
    validate_deep_graph(_checkpoint_validation_run(build, request))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_signature": signature,
        "requirement": to_dict(build.result),
        "responsibility_records": to_dict(build.records),
        "procedure_graphs": to_dict(build.graphs),
        "evidence": to_dict(build.evidence),
    }
    checkpoint_bytes = canonical_bytes(payload)
    atomic_write(path, checkpoint_bytes)
    atomic_write(
        _checkpoint_seal_path(path),
        (hashlib.sha256(checkpoint_bytes).hexdigest() + "\n").encode("ascii"),
    )


def _load_deep_checkpoint(
    path: Path,
    signature: str,
    request: AnalysisRequest,
    requirement: Requirement,
    knowledge: KnowledgeBaseV2,
    config: AppConfig,
    *, catalog=None,
) -> _RequirementBuild | None:
    seal_path = _checkpoint_seal_path(path)
    if path.is_symlink():
        try:
            path.unlink()
        except OSError:
            pass
        return None
    if seal_path.is_symlink():
        try:
            seal_path.unlink()
        except OSError:
            pass
        return None
    if (
        symlink_component(path) is not None
        or symlink_component(seal_path) is not None
        or not path.exists()
        or not seal_path.exists()
    ):
        return None
    try:
        checkpoint_text = _read_checkpoint_text(path)
        seal = _read_checkpoint_text(seal_path)
        expected_seal = hashlib.sha256(checkpoint_text.encode("utf-8")).hexdigest()
        if seal != expected_seal + "\n":
            return None
        payload = strict_json_object(checkpoint_text)
        item = _exact_object(
            payload,
            {
                "schema_version",
                "run_signature",
                "requirement",
                "responsibility_records",
                "procedure_graphs",
                "evidence",
            },
        )
        if _string(item["schema_version"]) != SCHEMA_VERSION:
            return None
        if _string(item["run_signature"]) != signature:
            return None
        result = _decode_deep_requirement_result(item["requirement"])
        records = tuple(
            _decode_responsibility(value)
            for value in _array(item["responsibility_records"])
        )
        graphs = tuple(
            _decode_procedure_graph(value)
            for value in _array(item["procedure_graphs"])
        )
        evidence = tuple(
            _decode_deep_evidence(value)
            for value in _array(item["evidence"])
        )
        build = _RequirementBuild(result, records, graphs, evidence)
        if result.requirement != requirement:
            return None
        if result.analysis_state is not AnalysisState.COMPLETED:
            return None
        validate_deep_graph(_checkpoint_validation_run(build, request))
        _validate_checkpoint_kb(build, knowledge, config, catalog=catalog)
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        ReqmapError,
    ):
        return None
    return build


def _checkpoint_seal_path(path: Path) -> Path:
    return path.with_suffix(".sha256")


def _read_checkpoint_text(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("checkpoint must be a regular file")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise ValueError("checkpoint permissions are not private")
        if details.st_size > 32 * 1024 * 1024:
            raise ValueError("checkpoint is too large")
        chunks: list[bytes] = []
        remaining = details.st_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != details.st_size:
            raise ValueError("checkpoint changed while being read")
        return payload.decode("utf-8")
    finally:
        os.close(descriptor)


def _validate_checkpoint_kb(
    build: _RequirementBuild,
    knowledge: KnowledgeBaseV2,
    config: AppConfig,
    *, catalog=None,
) -> None:
    if build.evidence != cited_deep_evidence(
        build.records, build.graphs, knowledge
    ):
        raise ValueError("checkpoint evidence differs from verified knowledge")
    for record in build.records:
        if record.component_ref not in knowledge.components:
            raise ValueError("checkpoint references an unknown component")
        if record.executor_ref not in knowledge.actors:
            raise ValueError("checkpoint references an unknown actor")
        target = knowledge.targets.get(record.target_ref)
        if target is None or target.contour is not record.target_contour:
            raise ValueError("checkpoint references an unknown or mismatched target")
        if record.action_ref is None or record.effect_ref is None:
            raise ValueError("checkpoint responsibility action/effect is missing")
        action = knowledge.actions.get(record.action_ref)
        effect = knowledge.effects.get(record.effect_ref)
        if (
            action is None
            or effect is None
            or action.target_ref != record.target_ref
            or record.effect_ref not in action.effect_refs
            or effect.target_ref != record.target_ref
            or action.version_scope != record.version_scope
        ):
            raise ValueError("checkpoint action/effect relation is invalid")
    unlinked_records = tuple(
        replace(record, procedure_step_ids=()) for record in build.records
    )
    records_by_id = {record.record_id: record for record in unlinked_records}
    outcomes: list[DeepMappingOutcome] = []
    for atom_result in build.result.atom_results:
        retrieval = retrieve_deep(
            knowledge,
            atom_result.atom.text,
            build.result.requirement.source_hints,
            config.top_k,
        )
        atom_records = tuple(
            records_by_id[record_id]
            for record_id in atom_result.responsibility_ids
        )
        context = requirement_context(build.result.requirement, catalog)
        from reqmap.binding_source import canonical_atoms
        from reqmap.binding_engine import mapping_decision, apply_record_decision
        if tuple(item.atom for item in build.result.atom_results) != canonical_atoms(context.source_binding):
            raise ValueError("SOURCE_COVERAGE_GAP")
        normalized = validate_responsibility_records(atom_records, retrieval, knowledge)
        decision = atom_result.binding_decision
        if decision is None:
            raise ValueError("SESSION_CONTRACT_MISMATCH")
        gated = tuple(apply_record_decision(r, mapping_decision(atom_result.atom, context, knowledge,
                      decision.predicate_ids, r.support_status, (r,))) for r in normalized)
        if tuple(r.support_status for r in gated) != tuple(r.support_status for r in atom_records):
            raise ValueError("checkpoint responsibility differs from binding gate")
        validate_persisted_binding(atom_result, context, knowledge, atom_records, atom_result.support_status)
        outcomes.append(DeepMappingOutcome(atom_result, atom_records, ()))
    template_ids = tuple(graph.template_id for graph in build.graphs)
    procedure = instantiate_procedure_graphs(
        build.result.requirement.requirement_id,
        template_ids,
        unlinked_records,
        knowledge,
    )
    expected_result, expected_records = aggregate_deep_requirement(
        build.result.requirement,
        tuple(outcomes),
        procedure,
    )
    if (
        procedure.graphs != build.graphs
        or expected_records != build.records
        or expected_result != build.result
    ):
        raise ValueError(
            "checkpoint procedure closure differs from deterministic recomputation"
        )
    for graph in build.graphs:
        if graph.template_id not in knowledge.procedures:
            raise ValueError("checkpoint references an unknown procedure template")
        if validate_procedure_graph(graph, build.records, knowledge):
            raise ValueError("checkpoint procedure graph is invalid")


def _decode_deep_requirement_result(raw: object) -> DeepRequirementResult:
    item = _exact_object(
        raw,
        {
            "requirement",
            "analysis_state",
            "support_status",
            "atom_results",
            "responsibility_ids",
            "procedure_graph_ids",
            "diagnostics",
        },
    )
    return DeepRequirementResult(
        requirement=_decode_requirement(item["requirement"]),
        analysis_state=AnalysisState(_string(item["analysis_state"])),
        support_status=_optional_support_status(item["support_status"]),
        atom_results=tuple(
            _decode_deep_atom_result(value)
            for value in _array(item["atom_results"])
        ),
        responsibility_ids=_strings(item["responsibility_ids"]),
        procedure_graph_ids=_strings(item["procedure_graph_ids"]),
        diagnostics=_strings(item["diagnostics"]),
    )


def _decode_requirement(raw: object) -> Requirement:
    item = _exact_object(
        raw,
        {
            "requirement_id",
            "source_id",
            "text",
            "ordinal",
            "coordinate",
            "parent_id",
            "group_ids",
            "source_fields",
            "source_hints",
        },
    )
    coordinate = _exact_object(
        item["coordinate"], {"source_name", "sheet", "row"}
    )
    return Requirement(
        requirement_id=_string(item["requirement_id"]),
        source_id=_optional_string(item["source_id"]),
        text=_string(item["text"]),
        ordinal=_integer(item["ordinal"]),
        coordinate=SourceCoordinate(
            source_name=_string(coordinate["source_name"]),
            sheet=_optional_string(coordinate["sheet"]),
            row=_optional_integer(coordinate["row"]),
        ),
        parent_id=_optional_string(item["parent_id"]),
        group_ids=_strings(item["group_ids"]),
        source_fields=tuple(
            _decode_source_field(value)
            for value in _array(item["source_fields"])
        ),
        source_hints=tuple(
            _decode_source_hint(value)
            for value in _array(item["source_hints"])
        ),
    )


def _decode_source_field(raw: object) -> SourceField:
    item = _exact_object(raw, {"column", "value"})
    return SourceField(_string(item["column"]), _string(item["value"]))


def _decode_source_hint(raw: object) -> SourceHint:
    item = _exact_object(raw, {"value", "column"})
    return SourceHint(_string(item["value"]), _string(item["column"]))


def _decode_deep_atom_result(raw: object) -> DeepAtomResult:
    item = _exact_object(
        raw,
        {
            "atom",
            "analysis_state",
            "support_status",
            "responsibility_ids",
            "supported_aspects",
            "unconfirmed_aspects",
            "diagnostics",
            "binding_decision",
        },
    )
    return DeepAtomResult(
        atom=_decode_atomic_claim(item["atom"]),
        analysis_state=AnalysisState(_string(item["analysis_state"])),
        support_status=_optional_support_status(item["support_status"]),
        responsibility_ids=_strings(item["responsibility_ids"]),
        supported_aspects=_strings(item["supported_aspects"]),
        unconfirmed_aspects=_strings(item["unconfirmed_aspects"]),
        diagnostics=_strings(item["diagnostics"]),
        binding_decision=decode_binding_decision(item["binding_decision"]),
    )


def _decode_atomic_claim(raw: object) -> AtomicClaim:
    from reqmap.binding_source import decode_atomic_claim
    return decode_atomic_claim(raw)


def _decode_responsibility(raw: object) -> ResponsibilityRecord:
    item = _exact_object(
        raw,
        {
            "record_id",
            "requirement_id",
            "atomic_claim_id",
            "contour",
            "component_ref",
            "executor_ref",
            "target_contour",
            "target_ref",
            "action_ref",
            "effect_ref",
            "lifecycle_phase",
            "version_scope",
            "evidence_ids",
            "support_status",
            "related_record_ids",
            "procedure_step_ids",
            "diagnostics",
        },
    )
    return ResponsibilityRecord(
        record_id=_string(item["record_id"]),
        requirement_id=_string(item["requirement_id"]),
        atomic_claim_id=_string(item["atomic_claim_id"]),
        contour=ResponsibilityContour(_string(item["contour"])),
        component_ref=_string(item["component_ref"]),
        executor_ref=_string(item["executor_ref"]),
        target_contour=ResponsibilityContour(_string(item["target_contour"])),
        target_ref=_string(item["target_ref"]),
        action_ref=_optional_string(item["action_ref"]),
        effect_ref=_optional_string(item["effect_ref"]),
        lifecycle_phase=LifecyclePhase(_string(item["lifecycle_phase"])),
        version_scope=_decode_version_scope(item["version_scope"]),
        evidence_ids=_strings(item["evidence_ids"]),
        support_status=SupportStatus(_string(item["support_status"])),
        related_record_ids=_strings(item["related_record_ids"]),
        procedure_step_ids=_strings(item["procedure_step_ids"]),
        diagnostics=_strings(item["diagnostics"]),
    )


def _decode_version_scope(raw: object) -> VersionScope:
    item = _exact_object(
        raw,
        {
            "source_release",
            "target_release",
            "kolla_ansible_release",
            "host_profile",
            "version_constraint",
        },
    )
    return VersionScope(
        source_release=_string(item["source_release"]),
        target_release=_string(item["target_release"]),
        kolla_ansible_release=_string(item["kolla_ansible_release"]),
        host_profile=_string(item["host_profile"]),
        version_constraint=_string(item["version_constraint"]),
    )


def _decode_procedure_graph(raw: object) -> ProcedureGraph:
    item = _exact_object(
        raw, {"graph_id", "requirement_id", "template_id", "steps", "diagnostics"}
    )
    return ProcedureGraph(
        graph_id=_string(item["graph_id"]),
        requirement_id=_string(item["requirement_id"]),
        template_id=_string(item["template_id"]),
        steps=tuple(
            _decode_procedure_step(value) for value in _array(item["steps"])
        ),
        diagnostics=_strings(item["diagnostics"]),
    )


def _decode_procedure_step(raw: object) -> ProcedureStep:
    item = _exact_object(
        raw,
        {
            "step_id",
            "phase",
            "contour",
            "executor_ref",
            "target_ref",
            "action_ref",
            "preconditions",
            "success_criteria",
            "evidence_ids",
            "depends_on",
            "rollback_step_id",
        },
    )
    return ProcedureStep(
        step_id=_string(item["step_id"]),
        phase=LifecyclePhase(_string(item["phase"])),
        contour=ResponsibilityContour(_string(item["contour"])),
        executor_ref=_string(item["executor_ref"]),
        target_ref=_string(item["target_ref"]),
        action_ref=_string(item["action_ref"]),
        preconditions=_strings(item["preconditions"]),
        success_criteria=_strings(item["success_criteria"]),
        evidence_ids=_strings(item["evidence_ids"]),
        depends_on=_strings(item["depends_on"]),
        rollback_step_id=_optional_string(item["rollback_step_id"]),
    )


def _decode_deep_evidence(raw: object) -> DeepEvidence:
    item = _exact_object(
        raw,
        {
            "evidence_id",
            "claim",
            "claim_kind",
            "polarity",
            "strength",
            "source_id",
            "locator",
            "version_constraint",
            "applicable_contours",
            "supports_entity_refs",
            "local_excerpt",
            "review_state",
        },
    )
    return DeepEvidence(
        evidence_id=_string(item["evidence_id"]),
        claim=_string(item["claim"]),
        claim_kind=_string(item["claim_kind"]),
        polarity=EvidencePolarity(_string(item["polarity"])),
        strength=EvidenceStrength(_string(item["strength"])),
        source_id=_string(item["source_id"]),
        locator=_string(item["locator"]),
        version_constraint=_string(item["version_constraint"]),
        applicable_contours=tuple(
            ResponsibilityContour(_string(value))
            for value in _array(item["applicable_contours"])
        ),
        supports_entity_refs=_strings(item["supports_entity_refs"]),
        local_excerpt=_string(item["local_excerpt"]),
        review_state=_string(item["review_state"]),
    )


def _exact_object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise TypeError("checkpoint value must be an object")
    item = cast(dict[str, object], value)
    if set(item) != fields:
        raise ValueError("checkpoint object has unknown or missing fields")
    return item


def _array(value: object) -> list[object]:
    if type(value) is not list:
        raise TypeError("checkpoint value must be an array")
    return cast(list[object], value)


def _string(value: object) -> str:
    if type(value) is not str:
        raise TypeError("checkpoint value must be a string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _strings(value: object) -> tuple[str, ...]:
    return tuple(_string(item) for item in _array(value))


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("checkpoint value must be an integer")
    return value


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    return _integer(value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise TypeError("checkpoint value must be a boolean")
    return value


def _optional_support_status(value: object) -> SupportStatus | None:
    if value is None:
        return None
    return SupportStatus(_string(value))


def _checkpoint_validation_run(
    build: _RequirementBuild, request: AnalysisRequest
) -> DeepRunResult:
    ordinal = build.result.requirement.ordinal
    placeholders = tuple(
        _failed_deep_requirement(
            item,
            AnalysisState.SKIPPED,
            "CHECKPOINT_PREFIX: requirement не входит в checkpoint payload.",
        )
        for item in request.requirements[: ordinal - 1]
    )
    requirements = (*placeholders, build.result)
    return DeepRunResult(
        run_id="checkpoint-validation",
        schema_version=SCHEMA_VERSION,
        run_status=deep_run_status(requirements, True),
        requirements=requirements,
        groups=aggregate_deep_groups(requirements, build.records),
        responsibility_records=build.records,
        procedure_graphs=build.graphs,
        evidence=build.evidence,
        metadata={},
        diagnostics=(),
    )


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _safe_local_diagnostic(exc: BaseException) -> str:
    if isinstance(exc, ReqmapError):
        return f"{exc.code}: {exc.message_ru}"
    return f"{type(exc).__name__}: ошибка локальной валидации."


def _recognizable_legacy_v1(path: Path) -> bool:
    v2_only_names = (
        "snapshot-manifest.json",
        "snapshot-manifest.sig",
        "actors.json",
        "targets.jsonl",
        "actions.jsonl",
        "effects.jsonl",
        "procedures.jsonl",
    )
    if any(
        (path / name).exists() or (path / name).is_symlink()
        for name in v2_only_names
    ):
        return False
    v1_required_names = (
        "components.json",
        "capabilities.jsonl",
        "evidence.jsonl",
        "synonyms.json",
        "source-manifest.json",
    )
    if any(
        not (path / name).is_file() or (path / name).is_symlink()
        for name in v1_required_names
    ):
        return False
    metadata_path = path / "metadata.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return type(metadata) is dict and set(metadata) == {
        "openstack_release",
        "snapshot_sha256",
    }


def _safe_model_diagnostic(exc: ReqmapError, config: AppConfig) -> str:
    message = exc.message_ru
    origin = _endpoint_origin(config.model.base_url)
    message = message.replace(config.model.base_url, "[MODEL_ENDPOINT]")
    message = message.replace(origin, "[MODEL_ENDPOINT]")
    parsed = urlsplit(config.model.base_url)
    redacted_values = [config.model.api_key, parsed.query]
    redacted_values.extend(value for _key, value in parse_qsl(parsed.query))
    for value in redacted_values:
        if value:
            message = message.replace(value, "[REDACTED]")
    if parsed.path and parsed.path != "/":
        message = message.replace(parsed.path, "[REDACTED_ENDPOINT_PATH]")
    return f"{exc.code}: {message}"


def _endpoint_origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    hostname = parts.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    try:
        parsed_port = parts.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port is not None else ""
    return urlunsplit((parts.scheme, f"{hostname}{port}", "", "", ""))
