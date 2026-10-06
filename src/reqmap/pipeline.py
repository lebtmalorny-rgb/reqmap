"""Fail-closed orchestration, preflight and requirement-level resume."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from reqmap.binding_runtime import binding_contract, load_configured_catalog, requirement_context, validate_persisted_binding, catalog_output_diagnostics
from reqmap.binding_codec import decode_binding_decision, decode_source_binding
from reqmap.aggregation import aggregate_groups, aggregate_requirement
from reqmap.config import AppConfig, ModelConfig, AnalysisProfile
from reqmap.decomposition import decompose
from reqmap.errors import ModelError, ReqmapError
from reqmap.knowledge import KnowledgeBase, load_knowledge
from reqmap.llm import JsonModel
from reqmap.mapping import map_atom, validate_atom_result
from reqmap.models import (
    AnalysisRequest,
    AnalysisState,
    AtomResult,
    AtomicClaim,
    Evidence,
    ImplementationSource,
    ImplementationStep,
    Mapping,
    Phase,
    PreflightResult,
    RelationType,
    Requirement,
    RequirementResult,
    RunResult,
    SourceCoordinate,
    SourceField,
    SourceHint,
    SupportStatus,
    to_dict,
)
from reqmap.prompts import (
    PROMPT_DECOMPOSITION_VERSION,
    PROMPT_MAPPING_VERSION,
)
from reqmap.output_safety import (
    OUTPUT_NOT_CLEAN_CODES as _OUTPUT_NOT_CLEAN_CODES,
    PUBLISHED_ARTIFACTS as _PUBLISHED_ARTIFACTS,
    atomic_write as _atomic_write,
    canonical_bytes as _canonical_bytes,
    clear_published_artifacts,
    ensure_secure_directory as _ensure_secure_directory,
    same_path as _same_path,
    strict_json_object as _strict_json_object,
    validate_analysis_request,
)
from reqmap.retrieval import retrieve


SCHEMA_VERSION = "1.1"


def preflight(config: AppConfig, model: JsonModel) -> PreflightResult:
    """Validate immutable local knowledge and the configured model endpoint."""
    result, _knowledge = _perform_preflight(config, model, request=None)
    return result


def analyze(
    request: AnalysisRequest,
    config: AppConfig,
    model: JsonModel,
) -> RunResult:
    """Analyze all requirements while isolating per-requirement failures."""
    preflight_result, knowledge = _perform_preflight(config, model, request=request)
    if not preflight_result.ok or knowledge is None:
        run = failed_preflight_run(request, preflight_result)
        return _finish_failed_preflight(run, request, config)

    catalog = load_configured_catalog(config, knowledge)
    signature = _run_signature(request, config, knowledge.snapshot_sha256, catalog=catalog)
    work_dir = request.output_dir / ".work"
    signature_dir = work_dir / signature
    try:
        _ensure_secure_directory(work_dir)
        _ensure_secure_directory(signature_dir)
    except OSError as exc:
        failed = PreflightResult(
            ok=False,
            diagnostics=(f"OUTPUT_WORKDIR: не удалось создать рабочий каталог: {exc}",),
            knowledge_sha256=knowledge.snapshot_sha256,
        )
        run = failed_preflight_run(request, failed)
        return _finish_failed_preflight(run, request, config)

    parent_texts = {
        item.requirement_id: item.text for item in request.requirements
    }
    results: list[RequirementResult] = []
    for requirement in request.requirements:
        checkpoint_path = signature_dir / f"{requirement.requirement_id}.json"
        try:
            resumed = _load_checkpoint(
                checkpoint_path,
                signature,
                requirement,
                knowledge,
                catalog=catalog,
            )
            if resumed is not None:
                results.append(resumed)
                continue
            result = _analyze_requirement(
                requirement,
                parent_texts.get(requirement.parent_id),
                config,
                model,
                knowledge,
                catalog=catalog,
            )
            _write_checkpoint(checkpoint_path, signature, result)
        except (OSError, ValueError) as exc:
            result = _failed_requirement(
                requirement,
                AnalysisState.VALIDATION_FAILED,
                f"CHECKPOINT_FAILED: {exc}",
            )
        results.append(result)

    requirement_results = tuple(results)
    groups = aggregate_groups(requirement_results)
    cited_evidence = collect_cited_evidence(requirement_results, knowledge)
    status = run_status(requirement_results, preflight_ok=True)
    return RunResult(
        run_id=f"run-{signature[:16]}",
        schema_version=SCHEMA_VERSION,
        run_status=status,
        requirements=requirement_results,
        groups=groups,
        evidence=cited_evidence,
        metadata={**_metadata(request, config, knowledge.snapshot_sha256, signature),
                  "binding_contract": binding_contract(config.analysis_profile, catalog)},
        diagnostics=tuple(
            dict.fromkeys(
                diagnostic
                for result in requirement_results
                for diagnostic in result.diagnostics
            )
        ),
    )


def failed_preflight_run(
    request: AnalysisRequest,
    result: PreflightResult,
) -> RunResult:
    """Create a non-subject result retaining every source requirement."""
    requirements = tuple(
        _failed_requirement(
            requirement,
            AnalysisState.SKIPPED,
            "; ".join(result.diagnostics) or "Preflight не пройден.",
        )
        for requirement in request.requirements
    )
    seed = _canonical_bytes(
        {
            "input_sha256": request.input_sha256,
            "diagnostics": result.diagnostics,
        }
    )
    run_id = f"run-{hashlib.sha256(seed).hexdigest()[:16]}"
    return RunResult(
        run_id=run_id,
        schema_version=SCHEMA_VERSION,
        run_status="FAILED",
        requirements=requirements,
        groups=aggregate_groups(requirements),
        evidence=(),
        metadata={
            "binding_contract": binding_contract(AnalysisProfile.LEGACY),
            "input_sha256": request.input_sha256,
            "knowledge_sha256": result.knowledge_sha256,
            "preflight_ok": False,
        },
        diagnostics=result.diagnostics,
    )


def run_status(
    results: tuple[RequirementResult, ...],
    preflight_ok: bool,
) -> str:
    """Compute operational status independently from subject support status."""
    if not preflight_ok or not results:
        return "FAILED"
    completed = sum(
        item.analysis_state is AnalysisState.COMPLETED for item in results
    )
    if completed == len(results):
        return "SUCCESS"
    if completed > 0:
        return "PARTIAL"
    return "FAILED"


def _perform_preflight(
    config: AppConfig,
    model: JsonModel,
    request: AnalysisRequest | None,
) -> tuple[PreflightResult, KnowledgeBase | None]:
    diagnostics = list(_config_diagnostics(config))
    if request is not None:
        diagnostics.extend(validate_analysis_request(request))
        if not diagnostics:
            diagnostics.extend(catalog_output_diagnostics(config, request.output_dir))
    if diagnostics:
        return PreflightResult(False, tuple(diagnostics), None), None

    if request is not None:
        cleanup_diagnostics = clear_published_artifacts(request.output_dir)
        if cleanup_diagnostics:
            return PreflightResult(False, cleanup_diagnostics, None), None

    try:
        knowledge = load_knowledge(config.knowledge_path)
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
            knowledge.snapshot_sha256,
        ), knowledge
    except Exception:
        return PreflightResult(
            False,
            ("MODEL_PREFLIGHT_FAILED: проверка модели завершилась ошибкой.",),
            knowledge.snapshot_sha256,
        ), knowledge

    return PreflightResult(True, (), knowledge.snapshot_sha256), knowledge


def _config_diagnostics(config: AppConfig) -> tuple[str, ...]:
    if type(config) is not AppConfig or type(config.model) is not ModelConfig:
        return ("CONFIG_INVALID: ожидается canonical AppConfig.",)
    diagnostics: list[str] = []
    if not isinstance(config.knowledge_path, Path):
        diagnostics.append("CONFIG_INVALID: knowledge_path должен быть Path.")
    if type(config.top_k) is not int or not 1 <= config.top_k <= 50:
        diagnostics.append("CONFIG_INVALID: top_k должен быть от 1 до 50.")
    if type(config.model.model) is not str or not config.model.model:
        diagnostics.append("CONFIG_INVALID: имя модели должно быть непустым.")
    parsed = urlsplit(config.model.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        diagnostics.append("CONFIG_INVALID: base_url модели недопустим.")
    return tuple(diagnostics)


def _analyze_requirement(
    requirement: Requirement,
    parent_text: str | None,
    config: AppConfig,
    model: JsonModel,
    knowledge: KnowledgeBase,
    *, catalog=None,
) -> RequirementResult:
    try:
        decomposition = decompose(model, requirement, parent_text)
    except ModelError as exc:
        return _failed_requirement(
            requirement,
            AnalysisState.MODEL_FAILED,
            _safe_model_diagnostic(exc, config),
        )
    except (ReqmapError, ValueError, TypeError) as exc:
        return _failed_requirement(
            requirement,
            AnalysisState.VALIDATION_FAILED,
            _safe_diagnostic(exc),
        )

    if decomposition.analysis_state is not AnalysisState.COMPLETED:
        return _failed_requirement(
            requirement,
            decomposition.analysis_state,
            "; ".join(decomposition.diagnostics) or "Декомпозиция не завершена.",
        )

    atom_results: list[AtomResult] = []
    for claim in decomposition.atoms:
        try:
            candidates = retrieve(
                knowledge,
                claim.text,
                requirement.source_hints,
                config.top_k,
            )
            result = map_atom(model, claim, candidates, knowledge, binding_context=requirement_context(requirement, catalog))
        except ModelError as exc:
            result = AtomResult(
                atom=claim,
                analysis_state=AnalysisState.MODEL_FAILED,
                support_status=None,
                mappings=(),
                diagnostics=(_safe_model_diagnostic(exc, config),),
            )
        except (ReqmapError, ValueError, TypeError) as exc:
            result = AtomResult(
                atom=claim,
                analysis_state=AnalysisState.VALIDATION_FAILED,
                support_status=None,
                mappings=(),
                diagnostics=(_safe_diagnostic(exc),),
            )
        atom_results.append(result)

    try:
        return aggregate_requirement(requirement, tuple(atom_results))
    except ValueError as exc:
        return _failed_requirement(
            requirement,
            AnalysisState.VALIDATION_FAILED,
            f"AGGREGATION_FAILED: {exc}",
        )


def _failed_requirement(
    requirement: Requirement,
    state: AnalysisState,
    diagnostic: str,
) -> RequirementResult:
    return RequirementResult(
        requirement=requirement,
        analysis_state=state,
        support_status=None,
        atom_results=(),
        mappings=(),
        diagnostics=(diagnostic,),
        source_binding=requirement_context(requirement, None).source_binding,
    )


def _run_signature(
    request: AnalysisRequest,
    config: AppConfig,
    knowledge_sha256: str,
    *, catalog=None,
) -> str:
    payload = {
        "binding_contract": binding_contract(config.analysis_profile, catalog),
        "requirements": to_dict(request.requirements),
        "input_sha256": request.input_sha256,
        "knowledge_sha256": knowledge_sha256,
        "model": config.model.model,
        "decomposition_prompt": PROMPT_DECOMPOSITION_VERSION,
        "mapping_prompt": PROMPT_MAPPING_VERSION,
        "top_k": config.top_k,
        "seed": config.model.seed,
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _metadata(
    request: AnalysisRequest,
    config: AppConfig,
    knowledge_sha256: str,
    signature: str,
) -> dict[str, object]:
    return {
        "input_sha256": request.input_sha256,
        "knowledge_sha256": knowledge_sha256,
        "model": config.model.model,
        "endpoint_origin": _endpoint_origin(config.model.base_url),
        "prompt_versions": {
            "decomposition": PROMPT_DECOMPOSITION_VERSION,
            "mapping": PROMPT_MAPPING_VERSION,
        },
        "top_k": config.top_k,
        "seed": config.model.seed,
        "run_signature": signature,
    }


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


def _write_checkpoint(
    path: Path,
    signature: str,
    result: RequirementResult,
) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_signature": signature,
        "requirement": to_dict(result),
    }
    _atomic_write(path, _canonical_bytes(payload))


def _load_checkpoint(
    path: Path,
    signature: str,
    requirement: Requirement,
    knowledge: KnowledgeBase,
    *, catalog=None,
) -> RequirementResult | None:
    if path.is_symlink():
        raise ValueError("checkpoint не может быть symlink")
    if not path.exists():
        return None
    try:
        payload = _strict_json_object(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            return None
        if payload.get("run_signature") != signature:
            return None
        raw_result = payload.get("requirement")
        result = _decode_requirement_result(raw_result)
        if result.requirement != requirement:
            return None
        if result.analysis_state is not AnalysisState.COMPLETED:
            return None
        context = requirement_context(requirement, catalog)
        from reqmap.binding_source import canonical_atoms
        if tuple(item.atom for item in result.atom_results) != canonical_atoms(context.source_binding):
            return None
        for item in result.atom_results:
            evidence_input = replace(item, supported_aspects=tuple(r.role_ru for r in item.mappings
                if r.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}))
            validated = validate_atom_result(evidence_input, knowledge)
            validate_persisted_binding(item, context, knowledge, item.mappings, validated.support_status)
        if aggregate_requirement(
            result.requirement,
            result.atom_results,
        ) != result:
            return None
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        ReqmapError,
    ):
        return None
    return result


def _decode_requirement_result(raw: object) -> RequirementResult:
    item = _object(raw)
    requirement = _decode_requirement(item["requirement"])
    atom_results = tuple(
        _decode_atom_result(value) for value in _array(item["atom_results"])
    )
    mappings = tuple(_decode_mapping(value) for value in _array(item["mappings"]))
    return RequirementResult(
        requirement=requirement,
        analysis_state=AnalysisState(_string(item["analysis_state"])),
        support_status=_optional_status(item["support_status"]),
        atom_results=atom_results,
        mappings=mappings,
        diagnostics=_strings(item.get("diagnostics", [])),
        source_binding=decode_source_binding(item.get("source_binding"), _decode_requirement(item["requirement"])),
    )


def _decode_requirement(raw: object) -> Requirement:
    item = _object(raw)
    coordinate = _object(item["coordinate"])
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
            SourceField(
                column=_string(_object(value)["column"]),
                value=_string(_object(value)["value"]),
            )
            for value in _array(item["source_fields"])
        ),
        source_hints=tuple(
            SourceHint(
                value=_string(_object(value)["value"]),
                column=_string(_object(value)["column"]),
            )
            for value in _array(item["source_hints"])
        ),
    )


def _decode_atom_result(raw: object) -> AtomResult:
    item = _object(raw)
    from reqmap.binding_source import decode_atomic_claim
    atom = decode_atomic_claim(item["atom"])
    return AtomResult(
        atom=atom,
        analysis_state=AnalysisState(_string(item["analysis_state"])),
        support_status=_optional_status(item["support_status"]),
        mappings=tuple(
            _decode_mapping(value) for value in _array(item["mappings"])
        ),
        supported_aspects=_strings(item.get("supported_aspects", [])),
        unconfirmed_aspects=_strings(item.get("unconfirmed_aspects", [])),
        diagnostics=_strings(item.get("diagnostics", [])),
        binding_decision=decode_binding_decision(item.get("binding_decision")),
    )


def _decode_mapping(raw: object) -> Mapping:
    item = _object(raw)
    phase = Phase(_string(item["phase"]))
    return Mapping(
        mapping_id=_string(item["mapping_id"]),
        atom_id=_string(item["atom_id"]),
        component_id=_string(item["component_id"]),
        role_ru=_string(item["role_ru"]),
        relation=RelationType(_string(item["relation"])),
        phase=phase,
        implementation_source=ImplementationSource(
            _string(item["implementation_source"])
        ),
        mechanism=_string(item["mechanism"]),
        steps=tuple(_decode_step(value, phase) for value in _array(item["steps"])),
        evidence_ids=_strings(item["evidence_ids"]),
        support_status=SupportStatus(_string(item["support_status"])),
        reason_ru=_string(item["reason_ru"]),
    )


def _decode_step(raw: object, mapping_phase: Phase) -> ImplementationStep:
    item = _object(raw)
    phase = Phase(_string(item["phase"]))
    if phase is not mapping_phase:
        raise ValueError("phase шага не совпадает с mapping")
    return ImplementationStep(
        order=_integer(item["order"]),
        phase=phase,
        action_ru=_string(item["action_ru"]),
        mechanism=_string(item["mechanism"]),
        command=_optional_string(item["command"]),
        api_operation=_optional_string(item["api_operation"]),
    )


def collect_cited_evidence(
    requirements: tuple[RequirementResult, ...],
    knowledge: KnowledgeBase,
) -> tuple[Evidence, ...]:
    identifiers = {
        evidence_id
        for result in requirements
        for mapping in result.mappings
        for evidence_id in mapping.evidence_ids
    }
    return tuple(knowledge.evidence[evidence_id] for evidence_id in sorted(identifiers))


def _write_preflight_diagnostics(
    run: RunResult,
    request: AnalysisRequest,
    config: AppConfig,
) -> bool:
    output = request.output_dir
    if not isinstance(output, Path) or output.is_symlink():
        return False
    if request.source_path is not None and any(
        _same_path(request.source_path, output / name)
        for name in _PUBLISHED_ARTIFACTS
    ):
        return False
    try:
        _ensure_secure_directory(output)
        log = {
            "event": "preflight_failed",
            "level": "error",
            "message_ru": "Preflight не пройден; предметный анализ не запускался.",
            "diagnostics": list(run.diagnostics),
        }
        _atomic_write(output / "run.jsonl", _canonical_bytes(log))
        manifest = {
            "schema_version": run.schema_version,
            "run_id": run.run_id,
            "run_status": run.run_status,
            "input_sha256": request.input_sha256,
            "knowledge_sha256": run.metadata.get("knowledge_sha256"),
            "model": config.model.model if type(config) is AppConfig else None,
            "endpoint_origin": (
                _endpoint_origin(config.model.base_url)
                if type(config) is AppConfig
                else None
            ),
            "requirements": [
                {
                    "requirement_id": item.requirement.requirement_id,
                    "analysis_state": item.analysis_state.value,
                    "support_status": None,
                }
                for item in run.requirements
            ],
            "diagnostics": list(run.diagnostics),
        }
        _atomic_write(output / "manifest.json", _canonical_bytes(manifest))
        return True
    except (OSError, ValueError):
        return False


def _finish_failed_preflight(
    run: RunResult,
    request: AnalysisRequest,
    config: AppConfig,
) -> RunResult:
    output_not_clean = any(
        diagnostic.partition(":")[0] in _OUTPUT_NOT_CLEAN_CODES
        for diagnostic in run.diagnostics
    )
    written = False
    if not output_not_clean:
        written = _write_preflight_diagnostics(run, request, config)
    return replace(
        run,
        metadata={
            **run.metadata,
            "preflight_artifacts_written": written,
        },
    )


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise TypeError("Ожидался dict")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if type(value) is not list:
        raise TypeError("Ожидался list")
    return cast(list[object], value)


def _string(value: object) -> str:
    if type(value) is not str:
        raise TypeError("Ожидалась строка")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _strings(value: object) -> tuple[str, ...]:
    return tuple(_string(item) for item in _array(value))


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("Ожидалось целое число")
    return value


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    return _integer(value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise TypeError("Ожидался boolean")
    return value


def _optional_status(value: object) -> SupportStatus | None:
    if value is None:
        return None
    return SupportStatus(_string(value))


def _safe_diagnostic(exc: BaseException) -> str:
    if isinstance(exc, ReqmapError):
        return f"{exc.code}: {exc.message_ru}"
    return f"{type(exc).__name__}: ошибка локальной валидации."


def _safe_model_diagnostic(exc: ReqmapError, config: AppConfig) -> str:
    message = exc.message_ru
    origin = _endpoint_origin(config.model.base_url)
    message = message.replace(config.model.base_url, origin)
    parsed = urlsplit(config.model.base_url)
    redacted_values = [config.model.api_key, parsed.query]
    redacted_values.extend(value for _key, value in parse_qsl(parsed.query))
    for value in redacted_values:
        if value:
            message = message.replace(value, "[REDACTED]")
    if parsed.path and parsed.path != "/":
        message = message.replace(parsed.path, "[REDACTED_ENDPOINT_PATH]")
    return f"{exc.code}: {message}"
