"""Fail-closed migration of strict v1 knowledge snapshots into draft schema-v2."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile

from reqmap.deep_models import ResponsibilityContour
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.knowledge import KnowledgeBase, load_knowledge
from reqmap.knowledge_v2 import load_knowledge_v2_for_maintenance
from reqmap.snapshot_trust import build_snapshot_manifest


KIND_TO_CONTOUR = {
    "openstack_service": ResponsibilityContour.OPENSTACK_RUNTIME,
    "deployment_tool": ResponsibilityContour.KOLLA_ANSIBLE,
    "host_os_subsystem": ResponsibilityContour.HOST_OS,
}


class MigrationError(ReqmapError):
    """A v1 snapshot cannot be safely transformed into a draft v2 snapshot."""


@dataclass(frozen=True)
class MigrationReport:
    source_snapshot_sha256: str
    output_path: Path
    components: int
    capabilities: int
    evidence: int
    inferred_actions: int


@dataclass(frozen=True)
class _DirectoryIdentity:
    device: int
    inode: int


def migrate_v1_to_v2(source: Path, output: Path) -> MigrationReport:
    """Create one unsigned draft schema-v2 snapshot without inferring operations.

    The destination must be absent or an empty ordinary directory.  All files
    are constructed in a private sibling directory and become visible only
    after the maintenance loader accepts the resulting graph.
    """
    if not isinstance(source, Path) or not isinstance(output, Path):
        raise MigrationError("MIGRATION_PATH", "Source и output должны быть Path.")
    try:
        knowledge = load_knowledge(source)
    except ReqmapError as exc:
        raise MigrationError(
            "MIGRATION_SOURCE_INVALID",
            "Исходный v1 snapshot не прошёл строгую проверку.",
        ) from exc

    contours = _component_contours(knowledge)
    source_artifacts = _read_source_artifacts(knowledge)
    parent = _safe_destination_parent(output)
    destination = _inspect_safe_destination(output)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.reqmap-migrate-", dir=parent)
    )
    staging_identity = _directory_identity(temporary, "MIGRATION_STAGING")
    try:
        _write_draft(knowledge, contours, source_artifacts, temporary)
        load_knowledge_v2_for_maintenance(temporary)
        _install_staging_directory(temporary, output, destination)
    except MigrationError:
        _cleanup_owned_staging(temporary, staging_identity)
        raise
    except (OSError, ValueError, ReqmapError) as exc:
        _cleanup_owned_staging(temporary, staging_identity)
        raise MigrationError(
            "MIGRATION_FAILED",
            "Не удалось безопасно сформировать draft v2 snapshot.",
        ) from exc

    return MigrationReport(
        source_snapshot_sha256=knowledge.snapshot_sha256,
        output_path=output,
        components=len(knowledge.components),
        capabilities=len(knowledge.capabilities),
        evidence=len(knowledge.evidence),
        inferred_actions=0,
    )


def _component_contours(knowledge: KnowledgeBase) -> dict[str, ResponsibilityContour]:
    contours: dict[str, ResponsibilityContour] = {}
    for component_id, component in knowledge.components.items():
        contour = KIND_TO_CONTOUR.get(component.kind)
        if contour is None:
            raise MigrationError(
                "MIGRATION_COMPONENT_KIND",
                "Невозможно вывести contour из kind компонента: "
                f"{component_id}.",
            )
        contours[component_id] = contour
    return contours


def _safe_destination_parent(output: Path) -> Path:
    parent = output.parent
    try:
        mode = parent.lstat().st_mode
    except OSError as exc:
        raise MigrationError(
            "MIGRATION_DESTINATION", "Родительский каталог output недоступен."
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise MigrationError(
            "MIGRATION_DESTINATION", "Родитель output должен быть обычным каталогом."
        )
    return parent


def _inspect_safe_destination(output: Path) -> _DirectoryIdentity | None:
    try:
        mode = output.lstat().st_mode
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise MigrationError(
            "MIGRATION_DESTINATION", "Невозможно проверить destination миграции."
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise MigrationError(
            "MIGRATION_DESTINATION", "Destination должен быть пустым обычным каталогом."
        )
    identity = _directory_identity(output, "MIGRATION_DESTINATION")
    try:
        if any(output.iterdir()):
            raise MigrationError(
                "MIGRATION_DESTINATION", "Destination миграции должен быть пустым."
            )
    except MigrationError:
        raise
    except OSError as exc:
        raise MigrationError(
            "MIGRATION_DESTINATION", "Невозможно проверить destination миграции."
        ) from exc
    return identity


def _directory_identity(path: Path, code: str) -> _DirectoryIdentity:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MigrationError(code, "Не удалось безопасно проверить каталог.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MigrationError(code, "Ожидался обычный каталог.")
    return _DirectoryIdentity(info.st_dev, info.st_ino)


def _read_source_artifacts(knowledge: KnowledgeBase) -> dict[str, tuple[bytes, str]]:
    artifacts: dict[str, tuple[bytes, str]] = {}
    for source in knowledge.sources.values():
        try:
            payload = (knowledge.root / source.local_path).read_bytes()
        except OSError as exc:
            raise MigrationError(
                "MIGRATION_SOURCE_COPY", "Не удалось прочитать local source artifact."
            ) from exc
        if hashlib.sha256(payload).hexdigest() != source.sha256:
            raise MigrationError(
                "MIGRATION_SOURCE_COPY", "Local source artifact изменён после валидации v1."
            )
        try:
            excerpt = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError(
                "MIGRATION_SOURCE_TEXT", "Local source artifact должен быть UTF-8 текстом."
            ) from exc
        if not excerpt.strip():
            raise MigrationError(
                "MIGRATION_SOURCE_TEXT", "Local source artifact не должен быть пустым."
            )
        artifacts[source.source_id] = (payload, excerpt)
    return artifacts


def _write_draft(
    knowledge: KnowledgeBase,
    contours: dict[str, ResponsibilityContour],
    source_artifacts: dict[str, tuple[bytes, str]],
    root: Path,
) -> None:
    metadata = {
        "knowledge_schema_version": 2,
        "snapshot_id": f"v1-migration-{knowledge.snapshot_sha256}",
        "snapshot_status": "draft",
        "openstack_release": knowledge.release,
        "upgrade_target": "2026.1",
        "kolla_ansible_release": "2025.1",
        "host_profile": "rocky_linux_9",
        "key_id": "reqmap-maintenance-2026",
    }
    _write_json(root / "metadata.json", metadata)
    _write_json(
        root / "components.json",
        {
            "components": [
                {
                    "id": component.component_id,
                    "display_name": component.display_name,
                    "kind": component.kind,
                    "releases": [component.release],
                }
                for component in sorted(knowledge.components.values(), key=lambda item: item.component_id)
            ]
        },
    )
    _write_json(root / "actors.json", {"actors": []})
    _write_jsonl(root / "targets.jsonl", ())

    evidence_by_capability: dict[str, list[str]] = {
        capability_id: [] for capability_id in knowledge.capabilities
    }
    for evidence in knowledge.evidence.values():
        evidence_by_capability[evidence.capability_id].append(evidence.evidence_id)
    _write_jsonl(
        root / "capabilities.jsonl",
        (
            {
                "id": capability.capability_id,
                "component_ref": capability.component_id,
                "name_ru": capability.name_ru,
                "terms": list(capability.terms),
                "evidence_ids": sorted(evidence_by_capability[capability.capability_id]),
            }
            for capability in sorted(knowledge.capabilities.values(), key=lambda item: item.capability_id)
        ),
    )
    _write_jsonl(root / "actions.jsonl", ())
    _write_jsonl(root / "effects.jsonl", ())
    _write_jsonl(
        root / "evidence.jsonl",
        (
            {
                "id": evidence.evidence_id,
                "claim": evidence.claim_ru,
                "claim_kind": "capability",
                "polarity": evidence.polarity.value,
                "strength": evidence.strength.value,
                "source_id": evidence.source_id,
                "locator": evidence.locator,
                "version_constraint": evidence.version_constraint,
                "applicable_contours": [contours[evidence.component_id].value],
                "supports_entity_refs": [evidence.capability_id],
                "local_excerpt": source_artifacts[evidence.source_id][1],
                "review_state": "needs_review",
            }
            for evidence in sorted(knowledge.evidence.values(), key=lambda item: item.evidence_id)
        ),
    )
    _write_jsonl(root / "procedures.jsonl", ())
    _write_json(root / "synonyms.json", dict(knowledge.synonyms))
    _copy_sources(knowledge, source_artifacts, root)
    _write_json(root / "snapshot-manifest.json", build_snapshot_manifest(root))


def _copy_sources(
    knowledge: KnowledgeBase, source_artifacts: dict[str, tuple[bytes, str]], root: Path
) -> None:
    records = []
    for source in sorted(knowledge.sources.values(), key=lambda item: item.source_id):
        destination = root / source.local_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(source_artifacts[source.source_id][0])
        except OSError as exc:
            raise MigrationError(
                "MIGRATION_SOURCE_COPY", "Не удалось скопировать local source artifact."
            ) from exc
        records.append(
            {
                "id": source.source_id,
                "source_type": "legacy_v1",
                "project": "legacy_v1",
                "release": source.version,
                "git_tag": None,
                "git_commit": None,
                "source_url": source.source_url,
                "retrieved_at": source.retrieved_at,
                "local_path": source.local_path,
                "content_sha256": source.sha256,
                "provenance": source.provenance,
            }
        )
    _write_json(root / "source-manifest.json", {"sources": records})


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _write_jsonl(path: Path, records: object) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(record) for record in records))


def _install_staging_directory(
    staging: Path, output: Path, expected_destination: _DirectoryIdentity | None
) -> None:
    current_destination = _inspect_safe_destination(output)
    if current_destination != expected_destination:
        raise MigrationError(
            "MIGRATION_DESTINATION", "Destination миграции изменился до публикации."
        )
    try:
        os.replace(staging, output)
    except OSError as exc:
        raise MigrationError(
            "MIGRATION_DESTINATION", "Невозможно опубликовать draft v2 snapshot."
        ) from exc


def _cleanup_owned_staging(staging: Path, identity: _DirectoryIdentity) -> None:
    try:
        current = _directory_identity(staging, "MIGRATION_STAGING")
        if current != identity:
            return
        shutil.rmtree(staging)
    except (OSError, MigrationError):
        pass
