"""Fail-closed migration of strict v1 knowledge snapshots into draft schema-v2."""

from __future__ import annotations

from dataclasses import dataclass
import errno
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
    parent = _safe_destination_parent(output)
    _assert_safe_destination(output)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.reqmap-migrate-", dir=parent)
    )
    try:
        _write_draft(knowledge, contours, temporary)
        load_knowledge_v2_for_maintenance(temporary)
        _install_staging_directory(temporary, output)
    except MigrationError:
        _cleanup_owned_staging(temporary, parent, output.name)
        raise
    except (OSError, ValueError, ReqmapError) as exc:
        _cleanup_owned_staging(temporary, parent, output.name)
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


def _assert_safe_destination(output: Path) -> None:
    try:
        mode = output.lstat().st_mode
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return
        raise MigrationError(
            "MIGRATION_DESTINATION", "Невозможно проверить destination миграции."
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise MigrationError(
            "MIGRATION_DESTINATION", "Destination должен быть пустым обычным каталогом."
        )
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


def _write_draft(
    knowledge: KnowledgeBase,
    contours: dict[str, ResponsibilityContour],
    root: Path,
) -> None:
    metadata = {
        "knowledge_schema_version": 2,
        "snapshot_id": "epoxy-2025.1-deep-001",
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
                "local_excerpt": evidence.claim_ru,
                "review_state": "needs_review",
            }
            for evidence in sorted(knowledge.evidence.values(), key=lambda item: item.evidence_id)
        ),
    )
    _write_jsonl(root / "procedures.jsonl", ())
    _write_json(root / "synonyms.json", dict(knowledge.synonyms))
    _copy_sources(knowledge, root)
    _write_json(root / "snapshot-manifest.json", build_snapshot_manifest(root))


def _copy_sources(knowledge: KnowledgeBase, root: Path) -> None:
    records = []
    for source in sorted(knowledge.sources.values(), key=lambda item: item.source_id):
        destination = root / source.local_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes((knowledge.root / source.local_path).read_bytes())
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


def _install_staging_directory(staging: Path, output: Path) -> None:
    _assert_safe_destination(output)
    if output.exists():
        try:
            output.rmdir()
        except OSError as exc:
            raise MigrationError(
                "MIGRATION_DESTINATION", "Невозможно заменить пустой destination миграции."
            ) from exc
    try:
        os.replace(staging, output)
    except OSError as exc:
        raise MigrationError(
            "MIGRATION_DESTINATION", "Невозможно опубликовать draft v2 snapshot."
        ) from exc


def _cleanup_owned_staging(staging: Path, parent: Path, output_name: str) -> None:
    prefix = f".{output_name}.reqmap-migrate-"
    try:
        if staging.parent != parent or not staging.name.startswith(prefix):
            return
        mode = staging.lstat().st_mode
        if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
            shutil.rmtree(staging)
    except OSError:
        pass
