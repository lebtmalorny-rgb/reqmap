"""Public behavior tests for fail-closed legacy v1 migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reqmap.knowledge import load_knowledge
from reqmap.knowledge_v2 import load_knowledge_v2_for_maintenance
from reqmap.migrate_v2 import MigrationError, migrate_v1_to_v2
from tests.deep_factories import write_v1_knowledge_snapshot


LEGACY_FIXTURE = Path("tests/fixtures/kb_minimal")


def test_migration_preserves_actual_v1_fixture_records_as_unsigned_draft(tmp_path: Path) -> None:
    output = tmp_path / "v2"
    v1 = load_knowledge(LEGACY_FIXTURE)

    report = migrate_v1_to_v2(LEGACY_FIXTURE, output)
    migrated = load_knowledge_v2_for_maintenance(output)

    assert migrated.snapshot_status == "draft"
    assert set(migrated.components) == {"nova"}
    assert set(migrated.capabilities) == {"CAP-NOVA-SERVER-API"}
    assert set(migrated.evidence) == {"E-NOVA-API-001"}
    assert migrated.actions == {}
    assert migrated.effects == {}
    assert migrated.procedures == {}
    assert migrated.actors == {}
    assert migrated.targets == {}
    assert report == report.__class__(
        source_snapshot_sha256=v1.snapshot_sha256,
        output_path=output,
        components=1,
        capabilities=1,
        evidence=1,
        inferred_actions=0,
    )
    assert not (output / "snapshot-manifest.sig").exists()

    for source_id, source in v1.sources.items():
        migrated_source = migrated.sources[source_id]
        assert migrated_source.source_url == source.source_url
        assert migrated_source.local_path == source.local_path
        assert migrated_source.content_sha256 == source.sha256
        assert (output / source.local_path).read_bytes() == (v1.root / source.local_path).read_bytes()
    assert migrated.synonyms == v1.synonyms

    evidence = migrated.evidence["E-NOVA-API-001"]
    assert evidence.claim == v1.evidence["E-NOVA-API-001"].claim_ru
    assert evidence.locator == v1.evidence["E-NOVA-API-001"].locator
    assert evidence.polarity is v1.evidence["E-NOVA-API-001"].polarity
    assert evidence.strength is v1.evidence["E-NOVA-API-001"].strength
    assert evidence.review_state == "needs_review"
    assert evidence.supports_entity_refs == ("CAP-NOVA-SERVER-API",)

    manifest = json.loads((output / "snapshot-manifest.json").read_text(encoding="utf-8"))
    assert manifest["knowledge_schema_version"] == 2


@pytest.mark.parametrize(
    ("component_kind", "expected_contour"),
    [
        ("openstack_service", "openstack_runtime"),
        ("deployment_tool", "kolla_ansible"),
        ("host_os_subsystem", "host_os"),
    ],
)
def test_migration_derives_contour_only_from_explicit_v1_component_kind(
    tmp_path: Path, component_kind: str, expected_contour: str
) -> None:
    source = write_v1_knowledge_snapshot(
        tmp_path / "source",
        component_id="component",
        component_kind=component_kind,
        capability_id="CAP-COMPONENT",
        evidence_id="EV-COMPONENT",
        source_id="SRC-COMPONENT",
    )

    migrate_v1_to_v2(source, tmp_path / "output")

    migrated = load_knowledge_v2_for_maintenance(tmp_path / "output")
    assert migrated.evidence["EV-COMPONENT"].applicable_contours[0].value == expected_contour
    assert migrated.actors == {}
    assert migrated.targets == {}


def test_migration_accepts_an_existing_empty_real_directory(tmp_path: Path) -> None:
    output = tmp_path / "v2"
    output.mkdir()

    migrate_v1_to_v2(LEGACY_FIXTURE, output)

    assert load_knowledge_v2_for_maintenance(output).snapshot_status == "draft"


@pytest.mark.parametrize("destination_kind", ["nonempty_directory", "file", "symlink"])
def test_migration_refuses_unsafe_destination_without_touching_user_data(
    tmp_path: Path, destination_kind: str
) -> None:
    output = tmp_path / "v2"
    if destination_kind == "nonempty_directory":
        output.mkdir()
        protected = output / "keep.txt"
        protected.write_text("user data", encoding="utf-8")
    elif destination_kind == "file":
        output.write_text("user data", encoding="utf-8")
        protected = output
    else:
        protected = tmp_path / "keep.txt"
        protected.write_text("user data", encoding="utf-8")
        output.symlink_to(protected)

    with pytest.raises(MigrationError):
        migrate_v1_to_v2(LEGACY_FIXTURE, output)

    assert protected.read_text(encoding="utf-8") == "user data"


def test_migration_cleans_owned_staging_directory_when_maintenance_validation_fails(
    tmp_path: Path,
) -> None:
    source = write_v1_knowledge_snapshot(
        tmp_path / "source",
        component_id="component",
        component_kind="openstack_service",
        capability_id="CAP-COMPONENT",
        evidence_id="EV-COMPONENT",
        source_id="SRC-COMPONENT",
        strength="indirect",
    )
    output = tmp_path / "v2"

    with pytest.raises(MigrationError):
        migrate_v1_to_v2(source, output)

    assert not output.exists()
    assert not list(tmp_path.glob(".v2.reqmap-migrate-*"))
