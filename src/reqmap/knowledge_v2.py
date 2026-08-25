"""Strict immutable loader for signed schema-v2 knowledge snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Any, NoReturn

from reqmap.deep_models import (
    DeepEvidence,
    LifecyclePhase,
    ResponsibilityContour,
    VersionScope,
)
from reqmap.errors import ReqmapError
from reqmap.models import EvidencePolarity, EvidenceStrength
from reqmap.snapshot_trust import (
    SnapshotTrust,
    read_verified_snapshot_file,
    verify_snapshot,
)


V2_REQUIRED_FILES = (
    "metadata.json",
    "components.json",
    "actors.json",
    "targets.jsonl",
    "capabilities.jsonl",
    "actions.jsonl",
    "effects.jsonl",
    "evidence.jsonl",
    "procedures.jsonl",
    "synonyms.json",
    "source-manifest.json",
)
_METADATA_FIELDS = {
    "knowledge_schema_version",
    "snapshot_id",
    "snapshot_status",
    "openstack_release",
    "upgrade_target",
    "kolla_ansible_release",
    "host_profile",
    "key_id",
}
_VERSION_SCOPE_FIELDS = {
    "source_release",
    "target_release",
    "kolla_ansible_release",
    "host_profile",
    "version_constraint",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeV2Error(ReqmapError):
    """Schema-v2 knowledge is unsafe, malformed, or outside the fixed baseline."""


@dataclass(frozen=True)
class DeepComponentRecord:
    component_id: str
    display_name: str
    kind: str
    releases: tuple[str, ...]


@dataclass(frozen=True)
class ActorRecord:
    actor_id: str
    kind: str
    component_ref: str | None


@dataclass(frozen=True)
class TargetRecord:
    target_id: str
    contour: ResponsibilityContour
    component_ref: str | None
    host_profile: str | None


@dataclass(frozen=True)
class DeepCapabilityRecord:
    capability_id: str
    component_ref: str
    name_ru: str
    terms: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    component_ref: str
    contour: ResponsibilityContour
    interface_type: str
    operation: str
    target_ref: str
    effect_refs: tuple[str, ...]
    version_scope: VersionScope
    evidence_ids: tuple[str, ...]
    procedure_required: bool


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    target_ref: str
    before_state: str
    after_state: str
    verification_criteria: tuple[str, ...]
    reversible: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProcedureTemplateStepRecord:
    local_step_id: str
    phase: LifecyclePhase
    contour: ResponsibilityContour
    executor_ref: str
    target_ref: str
    action_ref: str
    preconditions: tuple[str, ...]
    success_criteria: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    depends_on: tuple[str, ...]
    rollback_step_local_id: str | None


@dataclass(frozen=True)
class ProcedureTemplateRecord:
    template_id: str
    lifecycle_phase: LifecyclePhase
    action_refs: tuple[str, ...]
    steps: tuple[ProcedureTemplateStepRecord, ...]


@dataclass(frozen=True)
class SourceArtifactRecord:
    source_id: str
    source_type: str
    project: str
    release: str
    git_tag: str | None
    git_commit: str | None
    source_url: str | None
    retrieved_at: str
    local_path: str
    content_sha256: str
    provenance: str


@dataclass(frozen=True)
class KnowledgeBaseV2:
    root: Path
    schema_version: int
    snapshot_id: str
    snapshot_status: str
    base_release: str
    upgrade_target: str
    kolla_ansible_release: str
    host_profile: str
    components: Mapping[str, DeepComponentRecord]
    actors: Mapping[str, ActorRecord]
    targets: Mapping[str, TargetRecord]
    capabilities: Mapping[str, DeepCapabilityRecord]
    actions: Mapping[str, ActionRecord]
    effects: Mapping[str, EffectRecord]
    evidence: Mapping[str, DeepEvidence]
    procedures: Mapping[str, ProcedureTemplateRecord]
    sources: Mapping[str, SourceArtifactRecord]
    synonyms: Mapping[str, tuple[str, ...]]
    trust: SnapshotTrust | None


@dataclass(frozen=True)
class KnowledgeV2Issue:
    code: str
    object_id: str
    message_ru: str


def load_knowledge_v2(path: Path, allowed_signers_path: Path) -> KnowledgeBaseV2:
    """Load only a signed, intact, approved schema-v2 snapshot."""
    trust = verify_snapshot(path, allowed_signers_path)
    kb = _load_v2_records(path, trust)
    if kb.snapshot_status != "approved":
        _fail("SNAPSHOT_NOT_APPROVED", "Deep snapshot не утверждён.")
    if kb.snapshot_id != trust.snapshot_id:
        _fail("KNOWLEDGE_V2_TRUST", "snapshot_id metadata не совпадает с manifest.")
    return _validated(kb)


def load_knowledge_v2_for_maintenance(path: Path) -> KnowledgeBaseV2:
    """Load an unsigned approved or draft snapshot for local maintenance only."""
    return _validated(_load_v2_records(path, trust=None), allow_draft=True)


def _load_v2_records(path: Path, trust: SnapshotTrust | None) -> KnowledgeBaseV2:
    root = _validated_root(path)
    if trust is None:
        _reject_unsafe_snapshot_entries(root)
    read = _reader(root, trust)
    payloads = {name: read(name) for name in V2_REQUIRED_FILES}
    metadata = _json_object(payloads["metadata.json"], "metadata.json")
    _exact_fields(metadata, _METADATA_FIELDS, "metadata.json")
    key_id = _required_text(metadata, "key_id", "metadata.json")
    if trust is not None and key_id != trust.key_id:
        _fail("KNOWLEDGE_V2_TRUST", "key_id metadata не совпадает с manifest.")

    components = _load_components(payloads["components.json"])
    actors = _load_actors(payloads["actors.json"])
    targets = _load_targets(payloads["targets.jsonl"])
    capabilities = _load_capabilities(payloads["capabilities.jsonl"])
    actions = _load_actions(payloads["actions.jsonl"])
    effects = _load_effects(payloads["effects.jsonl"])
    evidence = _load_evidence(payloads["evidence.jsonl"])
    procedures = _load_procedures(payloads["procedures.jsonl"])
    sources = _load_sources(payloads["source-manifest.json"])
    synonyms = _load_synonyms(payloads["synonyms.json"])
    return KnowledgeBaseV2(
        root=root,
        schema_version=_required_int(metadata, "knowledge_schema_version", "metadata.json"),
        snapshot_id=_required_text(metadata, "snapshot_id", "metadata.json"),
        snapshot_status=_required_text(metadata, "snapshot_status", "metadata.json"),
        base_release=_required_text(metadata, "openstack_release", "metadata.json"),
        upgrade_target=_required_text(metadata, "upgrade_target", "metadata.json"),
        kolla_ansible_release=_required_text(
            metadata, "kolla_ansible_release", "metadata.json"
        ),
        host_profile=_required_text(metadata, "host_profile", "metadata.json"),
        components=MappingProxyType(components),
        actors=MappingProxyType(actors),
        targets=MappingProxyType(targets),
        capabilities=MappingProxyType(capabilities),
        actions=MappingProxyType(actions),
        effects=MappingProxyType(effects),
        evidence=MappingProxyType(evidence),
        procedures=MappingProxyType(procedures),
        sources=MappingProxyType(sources),
        synonyms=MappingProxyType(synonyms),
        trust=trust,
    )


def _validated(kb: KnowledgeBaseV2, allow_draft: bool = False) -> KnowledgeBaseV2:
    if kb.schema_version != 2:
        _fail("KNOWLEDGE_V2_SCHEMA", "knowledge_schema_version должен быть 2.")
    allowed_statuses = {"approved", "draft"} if allow_draft else {"approved"}
    if kb.snapshot_status not in allowed_statuses:
        _fail("KNOWLEDGE_V2_SCHEMA", "snapshot_status должен быть approved или draft.")
    if (
        kb.base_release != "2025.1"
        or kb.upgrade_target != "2026.1"
        or kb.kolla_ansible_release != "2025.1"
        or kb.host_profile != "rocky_linux_9"
    ):
        _fail("KNOWLEDGE_V2_RELEASE", "Версии schema-v2 snapshot не поддерживаются.")
    for component in kb.components.values():
        if any(release not in {"2025.1", "2026.1"} for release in component.releases):
            _fail("KNOWLEDGE_V2_RELEASE", "Версия component не поддерживается.")
    for action in kb.actions.values():
        scope = action.version_scope
        if (
            scope.source_release != "2025.1"
            or scope.target_release not in {"2025.1", "2026.1"}
            or scope.kolla_ansible_release != "2025.1"
            or scope.host_profile != "rocky_linux_9"
        ):
            _fail("KNOWLEDGE_V2_RELEASE", "Version scope action не поддерживается.")
    for source in kb.sources.values():
        if source.release not in {"2025.1", "2026.1"}:
            _fail("KNOWLEDGE_V2_RELEASE", "Версия source не поддерживается.")
    issues = validate_knowledge_v2(kb, allow_draft=allow_draft)
    if issues:
        issue = issues[0]
        _fail(issue.code, issue.message_ru)
    return kb


def validate_knowledge_v2(
    kb: KnowledgeBaseV2, *, allow_draft: bool = False
) -> tuple[KnowledgeV2Issue, ...]:
    """Return every deterministic graph, evidence, and version diagnostic."""
    issues: list[KnowledgeV2Issue] = []

    def issue(code: str, object_id: str, message_ru: str) -> None:
        issues.append(KnowledgeV2Issue(code, object_id, message_ru))

    allowed_statuses = {"approved", "draft"} if allow_draft else {"approved"}
    if kb.snapshot_status not in allowed_statuses:
        issue(
            "SNAPSHOT_STATUS_INVALID",
            "metadata",
            "Runtime принимает только утверждённый snapshot.",
        )
    if (
        kb.schema_version != 2
        or kb.base_release != "2025.1"
        or kb.upgrade_target != "2026.1"
        or kb.kolla_ansible_release != "2025.1"
    ):
        issue(
            "VERSION_SCOPE_INVALID",
            "metadata",
            "Базовые версии schema-v2 snapshot должны оставаться "
            "2025.1/2026.1.",
        )
    if kb.host_profile != "rocky_linux_9":
        issue("HOST_PROFILE_INVALID", "metadata", "Ожидается Rocky Linux 9.")

    for component in kb.components.values():
        if any(release not in {"2025.1", "2026.1"} for release in component.releases):
            issue(
                "VERSION_SCOPE_INVALID",
                component.component_id,
                "Версия component выходит за поддерживаемый scope.",
            )
    for target in kb.targets.values():
        if target.host_profile is not None and target.host_profile != "rocky_linux_9":
            issue("HOST_PROFILE_INVALID", target.target_id, "Ожидается Rocky Linux 9.")
    for source in kb.sources.values():
        if source.release not in {"2025.1", "2026.1"}:
            issue(
                "VERSION_SCOPE_INVALID",
                source.source_id,
                "Версия source выходит за поддерживаемый scope.",
            )

    source_is_local = _validate_v2_sources(kb, issue)
    _validate_v2_references(kb, issue)
    _validate_v2_evidence(kb, source_is_local, issue)
    _validate_v2_versions(kb, issue)
    _validate_v2_procedures(kb, issue)
    return tuple(
        sorted(
            set(issues),
            key=lambda item: (item.code, item.object_id, item.message_ru),
        )
    )


def _validate_v2_sources(
    kb: KnowledgeBaseV2, issue: Callable[[str, str, str], None]
) -> dict[str, bool]:
    valid: dict[str, bool] = {}
    for source in kb.sources.values():
        try:
            if kb.trust is None:
                payload = _read_regular_file(kb.root / source.local_path)
            else:
                payload = read_verified_snapshot_file(
                    kb.root, kb.trust, source.local_path
                )
        except KnowledgeV2Error:
            valid[source.source_id] = False
            issue(
                "SOURCE_FILE_MISSING",
                source.source_id,
                f"Локальный source artifact недоступен: {source.local_path}.",
            )
            continue
        actual = hashlib.sha256(payload).hexdigest()
        valid[source.source_id] = actual == source.content_sha256
        if not valid[source.source_id]:
            issue(
                "SOURCE_SHA256_MISMATCH",
                source.source_id,
                f"SHA-256 local source не совпадает: {source.local_path}.",
            )
    return valid


def _validate_v2_references(
    kb: KnowledgeBaseV2, issue: Callable[[str, str, str], None]
) -> None:
    for capability in kb.capabilities.values():
        if capability.component_ref not in kb.components:
            issue(
                "CAPABILITY_COMPONENT_UNKNOWN",
                capability.capability_id,
                "Capability ссылается на неизвестный component: "
                f"{capability.component_ref}.",
            )
        _unknown_evidence_refs(capability.capability_id, capability.evidence_ids, kb, issue)

    for action in kb.actions.values():
        if action.component_ref not in kb.components:
            issue(
                "ACTION_COMPONENT_UNKNOWN",
                action.action_id,
                "Action ссылается на неизвестный component: "
                f"{action.component_ref}.",
            )
        target_known = action.target_ref in kb.targets
        if not target_known:
            issue(
                "ACTION_TARGET_UNKNOWN",
                action.action_id,
                f"Action ссылается на неизвестный target: {action.target_ref}.",
            )
        for effect_ref in action.effect_refs:
            effect = kb.effects.get(effect_ref)
            if effect is None:
                issue(
                    "ACTION_EFFECT_UNKNOWN",
                    action.action_id,
                    f"Action ссылается на неизвестный effect: {effect_ref}.",
                )
            elif target_known and effect.target_ref != action.target_ref:
                issue(
                    "ACTION_EFFECT_MISMATCH",
                    action.action_id,
                    f"Effect {effect_ref} принадлежит другому target.",
                )
        _unknown_evidence_refs(action.action_id, action.evidence_ids, kb, issue)

    for effect in kb.effects.values():
        if effect.target_ref not in kb.targets:
            issue(
                "EFFECT_TARGET_UNKNOWN",
                effect.effect_id,
                f"Effect ссылается на неизвестный target: {effect.target_ref}.",
            )
        _unknown_evidence_refs(effect.effect_id, effect.evidence_ids, kb, issue)

    known_entities = (
        set(kb.capabilities) | set(kb.actions) | set(kb.effects) | set(kb.procedures)
    )
    for evidence in kb.evidence.values():
        if evidence.source_id not in kb.sources:
            issue(
                "EVIDENCE_SOURCE_UNKNOWN",
                evidence.evidence_id,
                f"Evidence ссылается на неизвестный source: {evidence.source_id}.",
            )
        known_supported = False
        for entity_ref in evidence.supports_entity_refs:
            if entity_ref not in known_entities:
                issue(
                    "EVIDENCE_ENTITY_UNKNOWN",
                    evidence.evidence_id,
                    f"Evidence ссылается на неизвестную entity: {entity_ref}.",
                )
            else:
                known_supported = True
        if not known_supported:
            issue(
                "EVIDENCE_ENTITY_REQUIRED",
                evidence.evidence_id,
                "Evidence должен поддерживать хотя бы одну "
                "известную entity.",
            )


def _unknown_evidence_refs(
    owner_id: str,
    evidence_ids: tuple[str, ...],
    kb: KnowledgeBaseV2,
    issue: Callable[[str, str, str], None],
) -> None:
    for evidence_id in evidence_ids:
        if evidence_id not in kb.evidence:
            issue(
                "EVIDENCE_UNKNOWN",
                owner_id,
                f"Объект ссылается на неизвестный evidence: {evidence_id}.",
            )


def _validate_v2_evidence(
    kb: KnowledgeBaseV2,
    source_is_local: Mapping[str, bool],
    issue: Callable[[str, str, str], None],
) -> None:
    positive_entities = (
        tuple(kb.capabilities.values()),
        tuple(kb.actions.values()),
        tuple(kb.effects.values()),
    )
    for records in positive_entities:
        for record in records:
            object_id = _positive_entity_id(record)
            direct = False
            for evidence_id in record.evidence_ids:
                evidence = kb.evidence.get(evidence_id)
                if evidence is None or object_id not in evidence.supports_entity_refs:
                    continue
                source = kb.sources.get(evidence.source_id)
                if (
                    evidence.polarity is EvidencePolarity.POSITIVE
                    and evidence.strength is EvidenceStrength.DIRECT
                    and source is not None
                    and source.provenance == "official"
                    and source.source_type != "project_policy"
                    and source_is_local.get(source.source_id, False)
                ):
                    direct = True
                    break
            if not direct:
                issue(
                    "DIRECT_EVIDENCE_REQUIRED",
                    object_id,
                    "Положительное утверждение требует "
                    "direct positive evidence.",
                )

    known_entities = (
        set(kb.capabilities) | set(kb.actions) | set(kb.effects) | set(kb.procedures)
    )
    upstream_entities = set(kb.capabilities) | set(kb.actions) | set(kb.effects)
    for evidence in kb.evidence.values():
        source = kb.sources.get(evidence.source_id)
        supported = set(evidence.supports_entity_refs)
        if evidence.polarity is EvidencePolarity.NEGATIVE and (
            evidence.strength is not EvidenceStrength.DIRECT
            or not (supported & known_entities)
        ):
            issue(
                "NEGATIVE_DIRECT_EVIDENCE_REQUIRED",
                evidence.evidence_id,
                "Negative claim требует direct evidence для известной entity.",
            )
        if (
            source is not None
            and (
                source.provenance == "project_policy"
                or source.source_type == "project_policy"
            )
            and supported & upstream_entities
        ):
            issue(
                "EVIDENCE_POLICY_SCOPE_INVALID",
                evidence.evidence_id,
                "Project policy не подтверждает upstream capability, "
                "action или effect.",
            )


def _positive_entity_id(record: object) -> str:
    if isinstance(record, DeepCapabilityRecord):
        return record.capability_id
    if isinstance(record, ActionRecord):
        return record.action_id
    if isinstance(record, EffectRecord):
        return record.effect_id
    raise TypeError("unknown positive entity record")


def _validate_v2_versions(
    kb: KnowledgeBaseV2, issue: Callable[[str, str, str], None]
) -> None:
    action_phases: dict[str, set[LifecyclePhase]] = {
        action_id: set() for action_id in kb.actions
    }
    for procedure in kb.procedures.values():
        referenced = set(procedure.action_refs) | {
            step.action_ref for step in procedure.steps
        }
        for action_ref in referenced:
            if action_ref in action_phases:
                action_phases[action_ref].add(procedure.lifecycle_phase)

    for action in kb.actions.values():
        scope = action.version_scope
        invalid = (
            scope.source_release != "2025.1"
            or scope.target_release not in {"2025.1", "2026.1"}
            or scope.kolla_ansible_release != "2025.1"
        )
        if scope.target_release == "2026.1" and action_phases[action.action_id] != {
            LifecyclePhase.UPGRADE
        }:
            invalid = True
        if invalid:
            issue(
                "VERSION_SCOPE_INVALID",
                action.action_id,
                "2026.1 разрешён только для upgrade; "
                "base/Kolla release должен быть 2025.1.",
            )
        if scope.host_profile != "rocky_linux_9":
            issue("HOST_PROFILE_INVALID", action.action_id, "Ожидается Rocky Linux 9.")


def _validate_v2_procedures(
    kb: KnowledgeBaseV2, issue: Callable[[str, str, str], None]
) -> None:
    for procedure in kb.procedures.values():
        for action_ref in procedure.action_refs:
            if action_ref not in kb.actions:
                issue(
                    "PROCEDURE_ACTION_UNKNOWN",
                    procedure.template_id,
                    f"Procedure ссылается на неизвестный action: {action_ref}.",
                )
        local_ids = {step.local_step_id for step in procedure.steps}
        adjacency: dict[str, set[str]] = {step_id: set() for step_id in local_ids}
        for step in procedure.steps:
            owner_id = f"{procedure.template_id}:{step.local_step_id}"
            if step.action_ref not in kb.actions:
                issue(
                    "PROCEDURE_ACTION_UNKNOWN",
                    owner_id,
                    f"Step ссылается на неизвестный action: {step.action_ref}.",
                )
            elif step.action_ref not in procedure.action_refs:
                issue(
                    "PROCEDURE_ACTION_MISMATCH",
                    owner_id,
                    "Action step отсутствует в action_refs template.",
                )
            _unknown_evidence_refs(owner_id, step.evidence_ids, kb, issue)
            for dependency in step.depends_on:
                if dependency not in local_ids:
                    issue(
                        "PROCEDURE_STEP_UNKNOWN",
                        owner_id,
                        f"Dependency не принадлежит template: {dependency}.",
                    )
                else:
                    adjacency[step.local_step_id].add(dependency)
            rollback = step.rollback_step_local_id
            if rollback is not None:
                if rollback not in local_ids:
                    issue(
                        "PROCEDURE_ROLLBACK_UNKNOWN",
                        owner_id,
                        f"Rollback step не принадлежит template: {rollback}.",
                    )
                else:
                    adjacency[step.local_step_id].add(rollback)
        if _has_cycle(adjacency):
            issue(
                "PROCEDURE_CYCLE",
                procedure.template_id,
                "Procedure template содержит цикл dependencies/rollback.",
            )


def _has_cycle(adjacency: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(neighbor) for neighbor in sorted(adjacency[node])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(adjacency) if node not in visited)


def _reader(root: Path, trust: SnapshotTrust | None) -> Callable[[str], bytes]:
    if trust is not None:
        return lambda relative: read_verified_snapshot_file(root, trust, relative)
    return lambda relative: _read_regular_file(root / relative)


def _load_components(payload: bytes) -> dict[str, DeepComponentRecord]:
    raw = _json_object(payload, "components.json")
    _exact_fields(raw, {"components"}, "components.json")
    result: dict[str, DeepComponentRecord] = {}
    for index, item in enumerate(_required_list(raw, "components", "components.json")):
        location = f"components[{index}]"
        record = _object(item, location)
        _exact_fields(record, {"id", "display_name", "kind", "releases"}, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "component")
        result[identifier] = DeepComponentRecord(
            identifier,
            _required_text(record, "display_name", location),
            _required_text(record, "kind", location),
            _text_tuple(record["releases"], f"{location}.releases"),
        )
    return result


def _load_actors(payload: bytes) -> dict[str, ActorRecord]:
    raw = _json_object(payload, "actors.json")
    _exact_fields(raw, {"actors"}, "actors.json")
    result: dict[str, ActorRecord] = {}
    for index, item in enumerate(_required_list(raw, "actors", "actors.json")):
        location = f"actors[{index}]"
        record = _object(item, location)
        _exact_fields(record, {"id", "kind", "component_ref"}, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "actor")
        result[identifier] = ActorRecord(
            identifier,
            _required_text(record, "kind", location),
            _optional_text(record, "component_ref", location),
        )
    return result


def _load_targets(payload: bytes) -> dict[str, TargetRecord]:
    result: dict[str, TargetRecord] = {}
    for index, item in enumerate(_json_lines(payload, "targets.jsonl")):
        location = f"targets[{index}]"
        record = _object(item, location)
        _exact_fields(record, {"id", "contour", "component_ref", "host_profile"}, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "target")
        result[identifier] = TargetRecord(
            identifier,
            _enum(record, "contour", ResponsibilityContour, location),
            _optional_text(record, "component_ref", location),
            _optional_text(record, "host_profile", location),
        )
    return result


def _load_capabilities(payload: bytes) -> dict[str, DeepCapabilityRecord]:
    result: dict[str, DeepCapabilityRecord] = {}
    fields = {"id", "component_ref", "name_ru", "terms", "evidence_ids"}
    for index, item in enumerate(_json_lines(payload, "capabilities.jsonl")):
        location = f"capabilities[{index}]"
        record = _object(item, location)
        _exact_fields(record, fields, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "capability")
        result[identifier] = DeepCapabilityRecord(
            identifier,
            _required_text(record, "component_ref", location),
            _required_text(record, "name_ru", location),
            _text_tuple(record["terms"], f"{location}.terms"),
            _text_tuple(record["evidence_ids"], f"{location}.evidence_ids"),
        )
    return result


def _load_actions(payload: bytes) -> dict[str, ActionRecord]:
    result: dict[str, ActionRecord] = {}
    fields = {
        "id", "component_ref", "contour", "interface_type", "operation",
        "target_ref", "effect_refs", "version_scope", "evidence_ids",
        "procedure_required",
    }
    for index, item in enumerate(_json_lines(payload, "actions.jsonl")):
        location = f"actions[{index}]"
        record = _object(item, location)
        _exact_fields(record, fields, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "action")
        result[identifier] = ActionRecord(
            identifier,
            _required_text(record, "component_ref", location),
            _enum(record, "contour", ResponsibilityContour, location),
            _required_text(record, "interface_type", location),
            _required_text(record, "operation", location),
            _required_text(record, "target_ref", location),
            _text_tuple(record["effect_refs"], f"{location}.effect_refs"),
            _version_scope(record["version_scope"], f"{location}.version_scope"),
            _text_tuple(record["evidence_ids"], f"{location}.evidence_ids"),
            _required_bool(record, "procedure_required", location),
        )
    return result


def _load_effects(payload: bytes) -> dict[str, EffectRecord]:
    result: dict[str, EffectRecord] = {}
    fields = {
        "id", "target_ref", "before_state", "after_state",
        "verification_criteria", "reversible", "evidence_ids",
    }
    for index, item in enumerate(_json_lines(payload, "effects.jsonl")):
        location = f"effects[{index}]"
        record = _object(item, location)
        _exact_fields(record, fields, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "effect")
        result[identifier] = EffectRecord(
            identifier,
            _required_text(record, "target_ref", location),
            _required_text(record, "before_state", location),
            _required_text(record, "after_state", location),
            _text_tuple(record["verification_criteria"], f"{location}.verification_criteria"),
            _required_bool(record, "reversible", location),
            _text_tuple(record["evidence_ids"], f"{location}.evidence_ids"),
        )
    return result


def _load_evidence(payload: bytes) -> dict[str, DeepEvidence]:
    result: dict[str, DeepEvidence] = {}
    fields = {
        "id", "claim", "claim_kind", "polarity", "strength", "source_id",
        "locator", "version_constraint", "applicable_contours",
        "supports_entity_refs", "local_excerpt", "review_state",
    }
    for index, item in enumerate(_json_lines(payload, "evidence.jsonl")):
        location = f"evidence[{index}]"
        record = _object(item, location)
        _exact_fields(record, fields, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "evidence")
        try:
            result[identifier] = DeepEvidence(
                identifier,
                _required_text(record, "claim", location),
                _required_text(record, "claim_kind", location),
                _enum(record, "polarity", EvidencePolarity, location),
                _enum(record, "strength", EvidenceStrength, location),
                _required_text(record, "source_id", location),
                _required_text(record, "locator", location),
                _required_text(record, "version_constraint", location),
                _enum_tuple(
                    record["applicable_contours"],
                    ResponsibilityContour,
                    f"{location}.applicable_contours",
                ),
                _text_tuple(record["supports_entity_refs"], f"{location}.supports_entity_refs"),
                _required_text(record, "local_excerpt", location),
                _required_text(record, "review_state", location),
            )
        except ValueError as exc:
            _fail("KNOWLEDGE_V2_SCHEMA", f"Некорректный evidence: {location}.", exc)
    return result


def _load_procedures(payload: bytes) -> dict[str, ProcedureTemplateRecord]:
    result: dict[str, ProcedureTemplateRecord] = {}
    for index, item in enumerate(_json_lines(payload, "procedures.jsonl")):
        location = f"procedures[{index}]"
        record = _object(item, location)
        _exact_fields(record, {"id", "lifecycle_phase", "action_refs", "steps"}, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "procedure")
        raw_steps = record["steps"]
        if type(raw_steps) is not list:
            _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.steps должен быть list.")
        steps: list[ProcedureTemplateStepRecord] = []
        step_ids: set[str] = set()
        for step_index, raw_step in enumerate(raw_steps):
            step = _procedure_step(raw_step, f"{location}.steps[{step_index}]")
            if step.local_step_id in step_ids:
                _fail(
                    "KNOWLEDGE_V2_DUPLICATE_ID",
                    f"Дублирующийся local_step_id procedure: {step.local_step_id}.",
                )
            step_ids.add(step.local_step_id)
            steps.append(step)
        result[identifier] = ProcedureTemplateRecord(
            identifier,
            _enum(record, "lifecycle_phase", LifecyclePhase, location),
            _text_tuple(record["action_refs"], f"{location}.action_refs"),
            tuple(steps),
        )
    return result


def _procedure_step(value: object, location: str) -> ProcedureTemplateStepRecord:
    record = _object(value, location)
    fields = {
        "local_step_id", "phase", "contour", "executor_ref", "target_ref",
        "action_ref", "preconditions", "success_criteria", "evidence_ids",
        "depends_on", "rollback_step_local_id",
    }
    _exact_fields(record, fields, location)
    return ProcedureTemplateStepRecord(
        _required_text(record, "local_step_id", location),
        _enum(record, "phase", LifecyclePhase, location),
        _enum(record, "contour", ResponsibilityContour, location),
        _required_text(record, "executor_ref", location),
        _required_text(record, "target_ref", location),
        _required_text(record, "action_ref", location),
        _text_tuple(record["preconditions"], f"{location}.preconditions"),
        _text_tuple(record["success_criteria"], f"{location}.success_criteria"),
        _text_tuple(record["evidence_ids"], f"{location}.evidence_ids"),
        _text_tuple(record["depends_on"], f"{location}.depends_on"),
        _optional_text(record, "rollback_step_local_id", location),
    )


def _load_sources(payload: bytes) -> dict[str, SourceArtifactRecord]:
    raw = _json_object(payload, "source-manifest.json")
    _exact_fields(raw, {"sources"}, "source-manifest.json")
    result: dict[str, SourceArtifactRecord] = {}
    fields = {
        "id", "source_type", "project", "release", "git_tag", "git_commit",
        "source_url", "retrieved_at", "local_path", "content_sha256", "provenance",
    }
    for index, item in enumerate(_required_list(raw, "sources", "source-manifest.json")):
        location = f"sources[{index}]"
        record = _object(item, location)
        _exact_fields(record, fields, location)
        identifier = _required_text(record, "id", location)
        _unique_id(result, identifier, "source")
        digest = _required_text(record, "content_sha256", location)
        if _SHA256.fullmatch(digest) is None:
            _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.content_sha256 некорректен.")
        result[identifier] = SourceArtifactRecord(
            identifier,
            _required_text(record, "source_type", location),
            _required_text(record, "project", location),
            _required_text(record, "release", location),
            _optional_text(record, "git_tag", location),
            _optional_text(record, "git_commit", location),
            _optional_text(record, "source_url", location),
            _required_text(record, "retrieved_at", location),
            _relative_path(record, "local_path", location),
            digest,
            _required_text(record, "provenance", location),
        )
    return result


def _load_synonyms(payload: bytes) -> dict[str, tuple[str, ...]]:
    raw = _json_object(payload, "synonyms.json")
    result: dict[str, tuple[str, ...]] = {}
    for term, values in raw.items():
        if type(term) is not str or not term.strip():
            _fail("KNOWLEDGE_V2_SCHEMA", "Ключ synonyms должен быть непустой строкой.")
        result[term] = _text_tuple(values, f"synonyms.{term}")
    return result


def _version_scope(value: object, location: str) -> VersionScope:
    record = _object(value, location)
    _exact_fields(record, _VERSION_SCOPE_FIELDS, location)
    return VersionScope(
        _required_text(record, "source_release", location),
        _required_text(record, "target_release", location),
        _required_text(record, "kolla_ansible_release", location),
        _required_text(record, "host_profile", location),
        _required_text(record, "version_constraint", location),
    )


def _validated_root(path: Path) -> Path:
    if not isinstance(path, Path):
        _fail("KNOWLEDGE_V2_PATH", "Snapshot root должен быть Path.")
    try:
        root_stat = path.lstat()
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            _fail("KNOWLEDGE_V2_PATH", "Snapshot root должен быть обычным каталогом.")
        return path.resolve(strict=True)
    except KnowledgeV2Error:
        raise
    except OSError as exc:
        _fail("KNOWLEDGE_V2_PATH", "Snapshot root отсутствует или небезопасен.", exc)


def _read_regular_file(path: Path) -> bytes:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail("KNOWLEDGE_V2_FILE", f"Файл snapshot небезопасен: {path.name}.")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev != before.st_dev or opened.st_ino != before.st_ino
            ):
                _fail("KNOWLEDGE_V2_FILE", f"Файл snapshot изменён: {path.name}.")
            payload = stream.read()
            after = os.fstat(stream.fileno())
    except KnowledgeV2Error:
        raise
    except OSError as exc:
        _fail("KNOWLEDGE_V2_FILE", f"Файл snapshot недоступен: {path.name}.", exc)
    if (
        opened.st_dev != after.st_dev
        or opened.st_ino != after.st_ino
        or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
        or len(payload) != after.st_size
    ):
        _fail("KNOWLEDGE_V2_FILE", f"Файл snapshot нестабилен: {path.name}.")
    return payload


def _reject_unsafe_snapshot_entries(root: Path) -> None:
    def walk_error(exc: OSError) -> NoReturn:
        _fail("KNOWLEDGE_V2_FILE", "Не удалось безопасно перечислить draft snapshot.", exc)

    try:
        for directory, directory_names, file_names in os.walk(
            root, topdown=True, onerror=walk_error, followlinks=False
        ):
            base = Path(directory)
            for name in directory_names:
                mode = (base / name).lstat().st_mode
                if not stat.S_ISDIR(mode):
                    _fail("KNOWLEDGE_V2_FILE", "Draft snapshot содержит небезопасный каталог.")
            for name in file_names:
                mode = (base / name).lstat().st_mode
                if not stat.S_ISREG(mode):
                    _fail("KNOWLEDGE_V2_FILE", "Draft snapshot содержит небезопасный файл.")
    except KnowledgeV2Error:
        raise
    except OSError as exc:
        _fail("KNOWLEDGE_V2_FILE", "Не удалось безопасно перечислить draft snapshot.", exc)


def _json_object(payload: bytes, location: str) -> dict[str, Any]:
    return _object(_strict_json(payload, location), location)


def _json_lines(payload: bytes, location: str) -> tuple[object, ...]:
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("KNOWLEDGE_V2_JSON", f"{location} не является UTF-8.", exc)
    lines = source.splitlines()
    result: list[object] = []
    for number, line in enumerate(lines, start=1):
        if not line:
            _fail("KNOWLEDGE_V2_JSON", f"Пустая строка не разрешена: {location}:{number}.")
        result.append(_strict_json(line.encode("utf-8"), f"{location}:{number}"))
    return tuple(result)


def _strict_json(payload: bytes, location: str) -> object:
    try:
        source = payload.decode("utf-8")
        return json.loads(
            source,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        _fail("KNOWLEDGE_V2_JSON", f"Некорректный JSON: {location}.", exc)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _object(value: object, location: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location} должен быть JSON object.")
    return value


def _exact_fields(value: Mapping[str, Any], fields: set[str], location: str) -> None:
    if set(value) != fields:
        _fail("KNOWLEDGE_V2_SCHEMA", f"Поля {location} не соответствуют schema.")


def _required_list(value: Mapping[str, Any], key: str, location: str) -> list[object]:
    result = value[key]
    if type(result) is not list:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.{key} должен быть list.")
    return result


def _required_text(value: Mapping[str, Any], key: str, location: str) -> str:
    result = value[key]
    if type(result) is not str or not result.strip():
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.{key} должен быть непустой строкой.")
    return result


def _optional_text(value: Mapping[str, Any], key: str, location: str) -> str | None:
    result = value[key]
    if result is None:
        return None
    if type(result) is not str or not result.strip():
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.{key} должен быть строкой или null.")
    return result


def _required_int(value: Mapping[str, Any], key: str, location: str) -> int:
    result = value[key]
    if type(result) is not int:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.{key} должен быть int.")
    return result


def _required_bool(value: Mapping[str, Any], key: str, location: str) -> bool:
    result = value[key]
    if type(result) is not bool:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.{key} должен быть bool.")
    return result


def _text_tuple(value: object, location: str) -> tuple[str, ...]:
    if type(value) is not list:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location} должен быть list строк.")
    result: list[str] = []
    for item in value:
        if type(item) is not str or not item.strip():
            _fail("KNOWLEDGE_V2_SCHEMA", f"{location} содержит некорректную строку.")
        result.append(item)
    return tuple(result)


def _enum(
    value: Mapping[str, Any], key: str, enum_type: type[Any], location: str
) -> Any:
    raw = _required_text(value, key, location)
    try:
        return enum_type(raw)
    except ValueError as exc:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location}.{key} имеет неизвестное значение.", exc)


def _enum_tuple(value: object, enum_type: type[Any], location: str) -> tuple[Any, ...]:
    raw = _text_tuple(value, location)
    try:
        return tuple(enum_type(item) for item in raw)
    except ValueError as exc:
        _fail("KNOWLEDGE_V2_SCHEMA", f"{location} имеет неизвестное значение.", exc)


def _relative_path(value: Mapping[str, Any], key: str, location: str) -> str:
    raw = _required_text(value, key, location)
    if "\\" in raw or "\x00" in raw:
        _fail("KNOWLEDGE_V2_PATH", f"{location}.{key} небезопасен.")
    candidate = PurePosixPath(raw)
    if (
        candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != raw
    ):
        _fail("KNOWLEDGE_V2_PATH", f"{location}.{key} выходит за snapshot.")
    return raw


def _unique_id(records: Mapping[str, object], identifier: str, kind: str) -> None:
    if identifier in records:
        _fail("KNOWLEDGE_V2_DUPLICATE_ID", f"Дублирующийся ID {kind}: {identifier}.")


def _fail(code: str, message: str, cause: BaseException | None = None) -> NoReturn:
    error = KnowledgeV2Error(code, message)
    if cause is None:
        raise error
    raise error from cause
