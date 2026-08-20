"""Strict offline loader for versioned OpenStack evidence snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from reqmap.errors import ReqmapError
from reqmap.models import Evidence, EvidencePolarity, EvidenceStrength


EXPECTED_RELEASE = "2025.1"
_SNAPSHOT_NAMES = (
    "components.json",
    "capabilities.jsonl",
    "evidence.jsonl",
    "synonyms.json",
    "source-manifest.json",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeError(ReqmapError):
    """A knowledge snapshot is incomplete, inconsistent, or outside Epoxy 2025.1."""


@dataclass(frozen=True)
class ComponentRecord:
    component_id: str
    display_name: str
    kind: str
    release: str


@dataclass(frozen=True)
class CapabilityRecord:
    capability_id: str
    component_id: str
    name_ru: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    component_ids: tuple[str, ...]
    source_url: str | None
    retrieved_at: str
    version: str
    sha256: str
    local_path: str
    provenance: str


@dataclass(frozen=True)
class KnowledgeIssue:
    code: str
    object_id: str
    message_ru: str


@dataclass(frozen=True)
class KnowledgeBase:
    root: Path
    release: str
    snapshot_sha256: str
    components: Mapping[str, ComponentRecord]
    capabilities: Mapping[str, CapabilityRecord]
    evidence: Mapping[str, Evidence]
    sources: Mapping[str, SourceRecord]
    synonyms: Mapping[str, tuple[str, ...]]


def load_knowledge(path: Path, verify_snapshot_hash: bool = True) -> KnowledgeBase:
    """Load a complete local snapshot and reject every invalid runtime condition.

    ``verify_snapshot_hash=False`` is reserved for the maintenance builder.  It
    skips only the metadata aggregate check; each individual record, local file,
    source digest, reference, and release still undergoes the same validation.
    """
    root = path.resolve()
    if not root.is_dir():
        _raise("KNOWLEDGE_PATH", "KB", f"Каталог базы знаний не найден: {root}")

    metadata = _json_object(root / "metadata.json")
    _reject_unknown_fields(metadata, {"openstack_release", "snapshot_sha256"}, "metadata")
    release = _required_string(metadata, "openstack_release", "metadata")
    snapshot_sha256 = _required_sha256(metadata, "snapshot_sha256", "metadata")
    components = _load_components(root / "components.json")
    capabilities = _load_capabilities(root / "capabilities.jsonl")
    sources = _load_sources(root / "source-manifest.json")
    evidence = _load_evidence(root / "evidence.jsonl", sources)
    synonyms = _load_synonyms(root / "synonyms.json")
    kb = KnowledgeBase(
        root=root,
        release=release,
        snapshot_sha256=snapshot_sha256,
        components=MappingProxyType(components),
        capabilities=MappingProxyType(capabilities),
        evidence=MappingProxyType(evidence),
        sources=MappingProxyType(sources),
        synonyms=MappingProxyType(synonyms),
    )

    issues = list(validate_knowledge(kb))
    if verify_snapshot_hash:
        actual = snapshot_digest(root)
        if snapshot_sha256 != actual:
            issues.append(
                KnowledgeIssue(
                    "SNAPSHOT_SHA256_MISMATCH",
                    "metadata",
                    "SHA-256 snapshot не совпадает с содержимым базы знаний",
                )
            )
    if issues:
        issue = sorted(issues, key=lambda item: (item.code, item.object_id))[0]
        raise KnowledgeError(issue.code, issue.message_ru)
    return kb


def validate_knowledge(kb: KnowledgeBase) -> tuple[KnowledgeIssue, ...]:
    """Return deterministic diagnostics for a fully loaded knowledge graph."""
    issues: list[KnowledgeIssue] = []
    issues.extend(_validate_release(kb))
    issues.extend(_validate_references(kb))
    issues.extend(_validate_source_hashes(kb))
    return tuple(sorted(issues, key=lambda item: (item.code, item.object_id)))


def snapshot_digest(root: Path, names: Iterable[str] = _SNAPSHOT_NAMES) -> str:
    """Compute the stable aggregate digest used by metadata.json."""
    digest = hashlib.sha256()
    for name in names:
        file_path = root / name
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            _raise("SNAPSHOT_FILE_MISSING", name, f"Файл snapshot отсутствует: {name}", exc)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()


def _load_components(path: Path) -> dict[str, ComponentRecord]:
    raw = _json_object(path)
    _reject_unknown_fields(raw, {"components"}, "components.json")
    records = _required_list(raw, "components", "components.json")
    result: dict[str, ComponentRecord] = {}
    for index, item in enumerate(records):
        record = _require_object(item, f"components[{index}]")
        _reject_unknown_fields(record, {"id", "display_name", "kind", "release"}, f"components[{index}]")
        component_id = _required_string(record, "id", f"components[{index}]")
        _reject_duplicate(result, component_id, "component")
        result[component_id] = ComponentRecord(
            component_id=component_id,
            display_name=_required_string(record, "display_name", component_id),
            kind=_required_string(record, "kind", component_id),
            release=_required_string(record, "release", component_id),
        )
    return result


def _load_capabilities(path: Path) -> dict[str, CapabilityRecord]:
    result: dict[str, CapabilityRecord] = {}
    for index, item in enumerate(_json_lines(path)):
        record = _require_object(item, f"capabilities line {index}")
        _reject_unknown_fields(record, {"id", "component_id", "name_ru", "terms"}, f"capabilities line {index}")
        capability_id = _required_string(record, "id", f"capabilities line {index}")
        _reject_duplicate(result, capability_id, "capability")
        result[capability_id] = CapabilityRecord(
            capability_id=capability_id,
            component_id=_required_string(record, "component_id", capability_id),
            name_ru=_required_string(record, "name_ru", capability_id),
            terms=_string_tuple(record.get("terms"), f"{capability_id}.terms"),
        )
    return result


def _load_sources(path: Path) -> dict[str, SourceRecord]:
    raw = _json_object(path)
    _reject_unknown_fields(raw, {"sources"}, "source-manifest.json")
    records = _required_list(raw, "sources", "source-manifest.json")
    result: dict[str, SourceRecord] = {}
    for index, item in enumerate(records):
        record = _require_object(item, f"sources[{index}]")
        _reject_unknown_fields(
            record,
            {
                "id",
                "component_ids",
                "source_url",
                "retrieved_at",
                "version",
                "sha256",
                "local_path",
                "provenance",
            },
            f"sources[{index}]",
        )
        source_id = _required_string(record, "id", f"sources[{index}]")
        _reject_duplicate(result, source_id, "source")
        provenance = _required_string(record, "provenance", source_id)
        if provenance not in {"official", "project_policy"}:
            _raise("SOURCE_PROVENANCE", source_id, "provenance источника должен быть official или project_policy")
        source_url = record.get("source_url")
        if provenance == "official" and (not isinstance(source_url, str) or not source_url):
            _raise("SOURCE_URL", source_id, "Для official источника требуется source_url")
        if provenance == "project_policy" and source_url is not None:
            _raise("SOURCE_URL", source_id, "source_url project_policy должен быть строго null")
        result[source_id] = SourceRecord(
            source_id=source_id,
            component_ids=_string_tuple(record.get("component_ids"), f"{source_id}.component_ids"),
            source_url=source_url,
            retrieved_at=_required_string(record, "retrieved_at", source_id),
            version=_required_string(record, "version", source_id),
            sha256=_required_sha256(record, "sha256", source_id),
            local_path=_required_relative_path(record, "local_path", source_id),
            provenance=provenance,
        )
    return result


def _load_evidence(path: Path, sources: Mapping[str, SourceRecord]) -> dict[str, Evidence]:
    result: dict[str, Evidence] = {}
    for index, item in enumerate(_json_lines(path)):
        record = _require_object(item, f"evidence line {index}")
        _reject_unknown_fields(
            record,
            {
                "id",
                "component_id",
                "capability_id",
                "polarity",
                "strength",
                "claim_ru",
                "source_id",
                "locator",
                "version_constraint",
            },
            f"evidence line {index}",
        )
        evidence_id = _required_string(record, "id", f"evidence line {index}")
        _reject_duplicate(result, evidence_id, "evidence")
        source_id = _required_string(record, "source_id", evidence_id)
        source = sources.get(source_id)
        if source is None:
            _raise("EVIDENCE_SOURCE_UNKNOWN", evidence_id, f"Evidence ссылается на неизвестный источник: {source_id}")
        try:
            polarity = EvidencePolarity(_required_string(record, "polarity", evidence_id))
            strength = EvidenceStrength(_required_string(record, "strength", evidence_id))
        except ValueError as exc:
            _raise("EVIDENCE_ENUM", evidence_id, f"Некорректная полярность или сила evidence: {evidence_id}", exc)
        result[evidence_id] = Evidence(
            evidence_id=evidence_id,
            component_id=_required_string(record, "component_id", evidence_id),
            capability_id=_required_string(record, "capability_id", evidence_id),
            polarity=polarity,
            strength=strength,
            claim_ru=_required_string(
                record,
                "claim_ru",
                evidence_id,
                empty_message="пустое утверждение evidence",
                reject_whitespace=True,
            ),
            source_id=source_id,
            locator=_required_string(record, "locator", evidence_id),
            version_constraint=_required_string(record, "version_constraint", evidence_id),
            source_url=source.source_url,
            local_path=source.local_path,
            source_sha256=source.sha256,
            retrieved_at=source.retrieved_at,
            provenance=source.provenance,
        )
    return result


def _load_synonyms(path: Path) -> dict[str, tuple[str, ...]]:
    raw = _json_object(path)
    result: dict[str, tuple[str, ...]] = {}
    for term, values in raw.items():
        if not isinstance(term, str) or not term:
            _raise("SYNONYM_TERM", "synonyms", "Ключ синонимов должен быть непустой строкой")
        result[term] = _string_tuple(values, f"synonyms.{term}")
    return result


def _validate_release(kb: KnowledgeBase) -> list[KnowledgeIssue]:
    issues: list[KnowledgeIssue] = []
    if kb.release != EXPECTED_RELEASE:
        issues.append(
            KnowledgeIssue(
                "RELEASE_MISMATCH",
                "metadata",
                f"Для базы знаний ожидается Epoxy {EXPECTED_RELEASE}, получено {kb.release}",
            )
        )
    for component in kb.components.values():
        if component.release != EXPECTED_RELEASE:
            issues.append(
                KnowledgeIssue("RELEASE_MISMATCH", component.component_id, f"Компонент {component.component_id} имеет release не {EXPECTED_RELEASE}")
            )
    for source in kb.sources.values():
        if source.version != EXPECTED_RELEASE:
            issues.append(
                KnowledgeIssue("RELEASE_MISMATCH", source.source_id, f"Источник {source.source_id} имеет version не {EXPECTED_RELEASE}")
            )
    for evidence in kb.evidence.values():
        if evidence.version_constraint != EXPECTED_RELEASE:
            issues.append(
                KnowledgeIssue("RELEASE_MISMATCH", evidence.evidence_id, f"Evidence {evidence.evidence_id} имеет version_constraint не {EXPECTED_RELEASE}")
            )
    return issues


def _validate_references(kb: KnowledgeBase) -> list[KnowledgeIssue]:
    issues: list[KnowledgeIssue] = []
    for capability in kb.capabilities.values():
        if capability.component_id not in kb.components:
            issues.append(KnowledgeIssue("CAPABILITY_COMPONENT_UNKNOWN", capability.capability_id, f"Capability ссылается на неизвестный компонент: {capability.component_id}"))
    for source in kb.sources.values():
        for component_id in source.component_ids:
            if component_id not in kb.components:
                issues.append(KnowledgeIssue("SOURCE_COMPONENT_UNKNOWN", source.source_id, f"Источник ссылается на неизвестный компонент: {component_id}"))
    for evidence in kb.evidence.values():
        if evidence.component_id not in kb.components:
            issues.append(KnowledgeIssue("EVIDENCE_COMPONENT_UNKNOWN", evidence.evidence_id, f"Evidence ссылается на неизвестный компонент: {evidence.component_id}"))
        capability = kb.capabilities.get(evidence.capability_id)
        if capability is None:
            issues.append(KnowledgeIssue("EVIDENCE_CAPABILITY_UNKNOWN", evidence.evidence_id, f"Evidence ссылается на неизвестную capability: {evidence.capability_id}"))
        elif (
            evidence.component_id in kb.components
            and capability.component_id != evidence.component_id
        ):
            issues.append(KnowledgeIssue("EVIDENCE_COMPONENT_CAPABILITY_MISMATCH", evidence.evidence_id, "Evidence ссылается на capability другого компонента"))
        if evidence.source_id not in kb.sources:
            issues.append(KnowledgeIssue("EVIDENCE_SOURCE_UNKNOWN", evidence.evidence_id, f"Evidence ссылается на неизвестный источник: {evidence.source_id}"))
        elif evidence.component_id not in kb.sources[evidence.source_id].component_ids:
            issues.append(
                KnowledgeIssue(
                    "EVIDENCE_SOURCE_COMPONENT_MISMATCH",
                    evidence.evidence_id,
                    "Компонент evidence отсутствует в component_ids его источника",
                )
            )
        if not evidence.claim_ru:
            issues.append(KnowledgeIssue("EVIDENCE_CLAIM_EMPTY", evidence.evidence_id, "Evidence содержит пустое утверждение"))
    return issues


def _validate_source_hashes(kb: KnowledgeBase) -> list[KnowledgeIssue]:
    issues: list[KnowledgeIssue] = []
    for source in kb.sources.values():
        path = _safe_source_path(kb.root, source.local_path)
        try:
            is_file = path.is_file()
        except OSError:
            issues.append(
                KnowledgeIssue(
                    "SOURCE_FILE_READ",
                    source.source_id,
                    f"Не удалось проверить локальный источник: {source.local_path}",
                )
            )
            continue
        if not is_file:
            issues.append(KnowledgeIssue("SOURCE_FILE_MISSING", source.source_id, f"Локальный источник отсутствует: {source.local_path}"))
            continue
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            issues.append(
                KnowledgeIssue(
                    "SOURCE_FILE_READ",
                    source.source_id,
                    f"Не удалось прочитать локальный источник: {source.local_path}",
                )
            )
            continue
        if actual != source.sha256:
            issues.append(KnowledgeIssue("SOURCE_SHA256_MISMATCH", source.source_id, f"SHA-256 локального источника не совпадает: {source.local_path}"))
    return issues


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        _raise("KNOWLEDGE_JSON", path.name, f"Не удалось прочитать JSON базы знаний: {path.name}", exc)
    return _require_object(value, path.name)


def _json_lines(path: Path) -> tuple[Any, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        _raise("KNOWLEDGE_JSONL", path.name, f"Не удалось прочитать JSONL базы знаний: {path.name}", exc)
    result: list[Any] = []
    for number, line in enumerate(lines, start=1):
        if not line:
            _raise("KNOWLEDGE_JSONL", path.name, f"Пустая строка JSONL не допускается: {path.name}:{number}")
        try:
            result.append(_strict_json_loads(line))
        except (ValueError, json.JSONDecodeError) as exc:
            _raise("KNOWLEDGE_JSONL", path.name, f"Некорректная строка JSONL: {path.name}:{number}", exc)
    return tuple(result)


def _required_list(value: Mapping[str, Any], key: str, location: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        _raise("KNOWLEDGE_SCHEMA", location, f"{location}.{key} должен быть списком")
    return result


def _require_object(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise("KNOWLEDGE_SCHEMA", location, f"{location} должен быть JSON-объектом")
    return value


def _reject_unknown_fields(value: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _raise("KNOWLEDGE_SCHEMA", location, f"Неизвестные поля {location}: {', '.join(unknown)}")


def _required_string(
    value: Mapping[str, Any],
    key: str,
    location: str,
    *,
    empty_message: str | None = None,
    reject_whitespace: bool = False,
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result or (reject_whitespace and not result.strip()):
        _raise("KNOWLEDGE_SCHEMA", location, empty_message or f"{location}.{key} должен быть непустой строкой")
    return result


def _string_tuple(value: object, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        _raise("KNOWLEDGE_SCHEMA", location, f"{location} должен быть непустым списком непустых строк")
    return tuple(value)


def _required_sha256(value: Mapping[str, Any], key: str, location: str) -> str:
    result = _required_string(value, key, location)
    if not _SHA256.fullmatch(result):
        _raise("KNOWLEDGE_SHA256", location, f"{location}.{key} должен быть SHA-256 в lowercase hex")
    return result


def _required_relative_path(value: Mapping[str, Any], key: str, location: str) -> str:
    result = _required_string(value, key, location)
    if "\0" in result:
        _raise("SOURCE_PATH", location, "local_path источника не должен содержать NUL")
    try:
        candidate = Path(result)
    except (OSError, ValueError) as exc:
        _raise("SOURCE_PATH", location, "Некорректный local_path источника", exc)
    if candidate.is_absolute() or ".." in candidate.parts:
        _raise("SOURCE_PATH", location, "local_path источника должен быть относительным путём внутри snapshot")
    return result


def _safe_source_path(root: Path, local_path: str) -> Path:
    try:
        path = (root / local_path).resolve()
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        _raise("SOURCE_PATH", local_path, "local_path источника выходит за пределы snapshot", exc)
    return path


def _strict_json_loads(payload: str) -> object:
    return json.loads(
        payload,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Дублирующийся ключ JSON: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"Недопустимая JSON-константа: {value}")


def _reject_duplicate(records: Mapping[str, object], identifier: str, kind: str) -> None:
    if identifier in records:
        _raise("DUPLICATE_ID", identifier, f"Обнаружен дублирующийся ID {kind}: {identifier}")


def _raise(code: str, object_id: str, message: str, cause: Exception | None = None) -> None:
    error = KnowledgeError(code, message, {"object_id": object_id})
    if cause is None:
        raise error
    raise error from cause
