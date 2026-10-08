"""Canonical, allowlisted JSON export for schema-v2 deep results."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from pathlib import Path
import re

from reqmap.deep_aggregation import validate_deep_graph
from reqmap.analysis_origin import origin_metadata
from reqmap.deep_models import (
    DeepAtomResult,
    DeepEvidence,
    DeepGroupResult,
    DeepRequirementResult,
    DeepRunResult,
    LifecyclePhase,
    ProcedureGraph,
    ProcedureStep,
    ResponsibilityRecord,
    VersionScope,
)
from reqmap.export_json import atomic_write_bytes, canonical_json_bytes
from reqmap.models import to_dict
from reqmap.models import (
    AnalysisState,
    AtomicClaim,
    Requirement,
    SourceCoordinate,
    SourceField,
    SourceHint,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SAFE_VERSION = re.compile(
    r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?$"
)
_PROMPT_STAGES = ("decomposition", "deep_mapping")
_RELEASE_FIELDS = frozenset(
    {
        "source_release",
        "target_release",
        "kolla_ansible_release",
        "host_profile",
    }
)


def write_deep_canonical_json(run: DeepRunResult, path: Path) -> str:
    """Validate and atomically publish one canonical schema-v2 JSON result."""
    validate_deep_run_result(run)
    payload = canonical_json_bytes(_deep_run_payload(run))
    atomic_write_bytes(path, payload)
    return hashlib.sha256(payload).hexdigest()


def validate_deep_run_result(run: DeepRunResult) -> None:
    """Revalidate graph closure and safe run metadata before publication."""
    validate_deep_graph(run)
    from reqmap.binding_export import validate_binding_run
    validate_binding_run(run)
    _deep_metadata_payload(run)


def _deep_run_payload(run: DeepRunResult) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "schema_version": run.schema_version,
        "run_status": run.run_status,
        "requirements": [_requirement_result(item) for item in run.requirements],
        "groups": [_group_result(item) for item in run.groups],
        "responsibility_records": [
            _responsibility(item) for item in run.responsibility_records
        ],
        "procedure_graphs": [_procedure_graph(item) for item in run.procedure_graphs],
        "evidence": [_evidence(item) for item in run.evidence],
        "metadata": _deep_metadata_payload(run),
        "diagnostics": list(run.diagnostics),
    }


def _deep_metadata_payload(run: DeepRunResult) -> dict[str, object]:
    metadata = run.metadata
    if not isinstance(metadata, Mapping):
        raise ValueError("DeepRunResult.metadata must be a mapping")
    origin = origin_metadata(metadata)
    allow_missing_trust = run.run_status == "FAILED"
    input_sha256 = _sha256(metadata.get("input_sha256"), "input_sha256")
    snapshot_id = _trust_identifier(
        metadata.get("snapshot_id"), "snapshot_id", allow_missing_trust
    )
    manifest_sha256 = _sha256(
        metadata.get("manifest_sha256"),
        "manifest_sha256",
        allow_none=allow_missing_trust,
    )
    key_id = _trust_identifier(
        metadata.get("key_id"), "key_id", allow_missing_trust
    )
    signer_identity = _trust_identifier(
        metadata.get("signer_identity"), "signer_identity", allow_missing_trust
    )
    from reqmap.binding_export import contract_payload
    return {
        **({"binding_contract": contract_payload(metadata["binding_contract"])} if "binding_contract" in metadata else {}),
        **({"source_context": metadata["source_context"]} if "source_context" in metadata else {}),
        **({"analysis_origin": origin} if origin is not None else {}),
        "reqmap_version": _safe_version(
            metadata.get("reqmap_version"), "reqmap_version"
        ),
        "analysis_profile": _analysis_profile(metadata.get("analysis_profile")),
        "model": _safe_model(metadata.get("model")),
        "seed": _optional_int(metadata.get("seed"), "seed"),
        "top_k": _positive_int(metadata.get("top_k"), "top_k"),
        "input_sha256": input_sha256,
        "snapshot_id": snapshot_id,
        "knowledge_trust": {
            "key_id": key_id,
            "manifest_sha256": manifest_sha256,
            "signer_identity": signer_identity,
        },
        "prompt_versions": _stage_mapping(
            metadata.get("prompt_versions"), value_kind="version"
        ),
        "release_profile": _release_profile(
            metadata.get("release_profile"),
            run.responsibility_records,
            allow_empty_transition=_is_failed_preflight_shape(run),
        ),
        "retry_counts": _stage_mapping(
            metadata.get("retry_counts"), value_kind="count"
        ),
    }


def _is_failed_preflight_shape(run: DeepRunResult) -> bool:
    return (
        run.run_status == "FAILED"
        and not run.responsibility_records
        and not run.procedure_graphs
        and not run.evidence
        and all(
            item.analysis_state is AnalysisState.SKIPPED
            and item.support_status is None
            and not item.atom_results
            and not item.responsibility_ids
            and not item.procedure_graph_ids
            for item in run.requirements
        )
    )


def _requirement_result(value: DeepRequirementResult) -> dict[str, object]:
    return {
        "source_binding": to_dict(value.source_binding),
        "requirement": _requirement(value.requirement),
        "analysis_state": value.analysis_state.value,
        "support_status": (
            None if value.support_status is None else value.support_status.value
        ),
        "atom_results": [_atom_result(item) for item in value.atom_results],
        "responsibility_ids": list(value.responsibility_ids),
        "procedure_graph_ids": list(value.procedure_graph_ids),
        "diagnostics": list(value.diagnostics),
    }


def _requirement(value: Requirement) -> dict[str, object]:
    return {
        "requirement_id": value.requirement_id,
        "source_id": value.source_id,
        "text": value.text,
        "ordinal": value.ordinal,
        "coordinate": _coordinate(value.coordinate),
        "parent_id": value.parent_id,
        "group_ids": list(value.group_ids),
        "source_fields": [_source_field(item) for item in value.source_fields],
        "source_hints": [_source_hint(item) for item in value.source_hints],
    }


def _coordinate(value: SourceCoordinate) -> dict[str, object]:
    return {"source_name": value.source_name, "sheet": value.sheet, "row": value.row}


def _source_field(value: SourceField) -> dict[str, object]:
    return {"column": value.column, "value": value.value}


def _source_hint(value: SourceHint) -> dict[str, object]:
    return {"value": value.value, "column": value.column}


def _atom_result(value: DeepAtomResult) -> dict[str, object]:
    return {
        "binding_decision": to_dict(value.binding_decision),
        "atom": _atom(value.atom),
        "analysis_state": value.analysis_state.value,
        "support_status": (
            None if value.support_status is None else value.support_status.value
        ),
        "responsibility_ids": list(value.responsibility_ids),
        "supported_aspects": list(value.supported_aspects),
        "unconfirmed_aspects": list(value.unconfirmed_aspects),
        "diagnostics": list(value.diagnostics),
    }


def _atom(value: AtomicClaim) -> dict[str, object]:
    return {
        "obligation_id": value.obligation_id, "source_spans": to_dict(value.source_spans), "source_sha256": value.source_sha256,
        "atom_id": value.atom_id,
        "requirement_id": value.requirement_id,
        "text": value.text,
        "source_quote": value.source_quote,
        "mandatory": value.mandatory,
        "ordinal": value.ordinal,
    }


def _group_result(value: DeepGroupResult) -> dict[str, object]:
    return {
        "group_id": value.group_id,
        "source_requirement_ids": list(value.source_requirement_ids),
        "support_status": (
            None if value.support_status is None else value.support_status.value
        ),
        "component_refs": list(value.component_refs),
        "responsibility_ids": list(value.responsibility_ids),
        "analysis_states": [item.value for item in value.analysis_states],
    }


def _responsibility(value: ResponsibilityRecord) -> dict[str, object]:
    return {
        "record_id": value.record_id,
        "requirement_id": value.requirement_id,
        "atomic_claim_id": value.atomic_claim_id,
        "contour": value.contour.value,
        "component_ref": value.component_ref,
        "executor_ref": value.executor_ref,
        "target_contour": value.target_contour.value,
        "target_ref": value.target_ref,
        "action_ref": value.action_ref,
        "effect_ref": value.effect_ref,
        "lifecycle_phase": value.lifecycle_phase.value,
        "version_scope": _version_scope(value.version_scope),
        "evidence_ids": list(value.evidence_ids),
        "support_status": value.support_status.value,
        "related_record_ids": list(value.related_record_ids),
        "procedure_step_ids": list(value.procedure_step_ids),
        "diagnostics": list(value.diagnostics),
    }


def _version_scope(value: VersionScope) -> dict[str, object]:
    return {
        "source_release": value.source_release,
        "target_release": value.target_release,
        "kolla_ansible_release": value.kolla_ansible_release,
        "host_profile": value.host_profile,
        "version_constraint": value.version_constraint,
    }


def _procedure_graph(value: ProcedureGraph) -> dict[str, object]:
    return {
        "graph_id": value.graph_id,
        "requirement_id": value.requirement_id,
        "template_id": value.template_id,
        "steps": [_procedure_step(item) for item in value.steps],
        "diagnostics": list(value.diagnostics),
    }


def _procedure_step(value: ProcedureStep) -> dict[str, object]:
    return {
        "step_id": value.step_id,
        "phase": value.phase.value,
        "contour": value.contour.value,
        "executor_ref": value.executor_ref,
        "target_ref": value.target_ref,
        "action_ref": value.action_ref,
        "preconditions": list(value.preconditions),
        "success_criteria": list(value.success_criteria),
        "evidence_ids": list(value.evidence_ids),
        "depends_on": list(value.depends_on),
        "rollback_step_id": value.rollback_step_id,
    }


def _evidence(value: DeepEvidence) -> dict[str, object]:
    return {
        "evidence_id": value.evidence_id,
        "claim": value.claim,
        "claim_kind": value.claim_kind,
        "polarity": value.polarity.value,
        "strength": value.strength.value,
        "source_id": value.source_id,
        "locator": value.locator,
        "version_constraint": value.version_constraint,
        "applicable_contours": [item.value for item in value.applicable_contours],
        "supports_entity_refs": list(value.supports_entity_refs),
        "local_excerpt": value.local_excerpt,
        "review_state": value.review_state,
    }


def _sha256(value: object, field: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _trust_identifier(value: object, field: str, allow_none: bool) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a safe trust identifier")
    return value


def _safe_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or any(
        character in value for character in "\r\n\x00"
    ):
        raise ValueError(f"{field} must be safe non-empty text")
    return value


def _safe_model(value: object) -> str:
    model = _safe_text(value, "model")
    normalized = model.casefold()
    credential_fragments = (
        "api_key",
        "authorization",
        "bearer ",
        "credential",
        "password",
        "token=",
    )
    if (
        _SAFE_MODEL.fullmatch(model) is None
        or model.startswith(("/", "./", "../", "~"))
        or re.match(r"^[A-Za-z]:[\\/]", model) is not None
        or "://" in model
        or "@" in model
        or "?" in model
        or "#" in model
        or normalized.startswith("sk-")
        or any(fragment in normalized for fragment in credential_fragments)
    ):
        raise ValueError("model must be a safe model identifier, not a path or credential")
    return model


def _safe_version(value: object, field: str) -> str:
    if type(value) is not str or _SAFE_VERSION.fullmatch(value) is None:
        raise ValueError(f"{field} must be a strict version token")
    return value


def _analysis_profile(value: object) -> str:
    if value != "deep":
        raise ValueError("analysis_profile must be deep")
    return "deep"


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"{field} must be int or null")
    return value


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive int")
    return value


def _stage_mapping(value: object, *, value_kind: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{value_kind} stage metadata must be a mapping")
    result: dict[str, object] = {}
    for stage in _PROMPT_STAGES:
        item = value.get(stage)
        if value_kind == "version":
            result[stage] = _safe_version(item, f"prompt_versions.{stage}")
        elif type(item) is int and item >= 0:
            result[stage] = item
        else:
            raise ValueError(f"retry_counts.{stage} must be a non-negative int")
    return result


def _release_profile(
    value: object,
    records: tuple[ResponsibilityRecord, ...],
    *,
    allow_empty_transition: bool,
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _RELEASE_FIELDS:
        raise ValueError("release_profile must contain the exact release fields")
    expected = {
        "source_release": {"2025.1"},
        "target_release": {"2025.1", "2026.1"},
        "kolla_ansible_release": {"2025.1"},
        "host_profile": {"rocky_linux_9"},
    }
    result: dict[str, str] = {}
    for field, permitted in expected.items():
        item = value.get(field)
        if type(item) is not str or item not in permitted:
            raise ValueError(f"release_profile.{field} is unsupported")
        result[field] = item
    fixed_fields = ("source_release", "kolla_ansible_release", "host_profile")
    if any(
        result[field] != getattr(record.version_scope, field)
        for record in records
        for field in fixed_fields
    ):
        raise ValueError(
            "release_profile must match responsibility source, Kolla, and host scopes"
        )
    if result["target_release"] == "2025.1" and any(
        record.version_scope.target_release != "2025.1" for record in records
    ):
        raise ValueError("2025.1 release profile cannot contain 2026.1 records")
    if (
        result["target_release"] == "2026.1"
        and not allow_empty_transition
        and not any(
            record.lifecycle_phase is LifecyclePhase.UPGRADE
            and record.version_scope.target_release == "2026.1"
            for record in records
        )
    ):
        raise ValueError("2026.1 release profile requires a 2026.1 upgrade record")
    return result
