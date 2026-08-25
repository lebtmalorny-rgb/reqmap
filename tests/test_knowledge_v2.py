"""Strict loading of immutable schema-v2 knowledge snapshots."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from types import MappingProxyType

import pytest

from reqmap.deep_models import LifecyclePhase, ResponsibilityContour
from reqmap.knowledge_v2 import (
    KnowledgeV2Error,
    V2_REQUIRED_FILES,
    load_knowledge_v2,
    load_knowledge_v2_for_maintenance,
)
from reqmap.snapshot_trust import SnapshotTrustError, verify_snapshot
from tests.deep_factories import signed_v2_snapshot


def _rewrite_json(path: Path, mutator) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps(mutator(value), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_load_knowledge_v2_returns_immutable_verified_graph(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)

    kb = load_knowledge_v2(root, allowed_signers)

    assert kb.schema_version == 2
    assert kb.snapshot_id == "epoxy-2025.1-deep-001"
    assert kb.snapshot_status == "approved"
    assert kb.base_release == "2025.1"
    assert kb.upgrade_target == "2026.1"
    assert kb.kolla_ansible_release == "2025.1"
    assert kb.host_profile == "rocky_linux_9"
    assert kb.trust is not None and kb.trust.manifest_sha256
    assert kb.actions["ACTION-NOVA-CREATE"].contour is ResponsibilityContour.OPENSTACK_RUNTIME
    assert kb.procedures["PROC-NOVA-CREATE"].lifecycle_phase is LifecyclePhase.RUNTIME
    assert kb.sources["SRC-MINIMAL"].local_path == "corpus/SRC-MINIMAL.md"
    assert isinstance(kb.actions, MappingProxyType)
    with pytest.raises(TypeError):
        kb.actions["other"] = kb.actions["ACTION-NOVA-CREATE"]  # type: ignore[index]
    with pytest.raises(TypeError):
        kb.synonyms["create server"][0] = "changed"  # type: ignore[index]


@pytest.mark.parametrize("filename", V2_REQUIRED_FILES)
def test_load_knowledge_v2_rejects_missing_required_file(
    tmp_path: Path, filename: str
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    (root / filename).unlink()

    with pytest.raises((KnowledgeV2Error, SnapshotTrustError)):
        load_knowledge_v2(root, allowed_signers)


def test_runtime_has_no_signature_disable_escape_hatch() -> None:
    assert tuple(inspect.signature(load_knowledge_v2).parameters) == (
        "path",
        "allowed_signers_path",
    )


def test_runtime_rejects_signed_draft_but_maintenance_loads_unsigned_draft(
    tmp_path: Path,
) -> None:
    draft_root, draft_signers = signed_v2_snapshot(tmp_path / "signed")
    _rewrite_json(
        draft_root / "metadata.json",
        lambda value: {**value, "snapshot_status": "draft"},
    )
    # Re-sign the deliberate draft after changing its metadata.
    from tests.deep_factories import sign_existing_v2_snapshot

    draft_signers = sign_existing_v2_snapshot(draft_root, tmp_path / "resigned")

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2(draft_root, draft_signers)
    assert error.value.code == "SNAPSHOT_NOT_APPROVED"

    unsigned_root, _ = signed_v2_snapshot(tmp_path / "unsigned")
    (unsigned_root / "snapshot-manifest.json").unlink()
    (unsigned_root / "snapshot-manifest.sig").unlink()
    _rewrite_json(
        unsigned_root / "metadata.json",
        lambda value: {**value, "snapshot_status": "draft"},
    )
    kb = load_knowledge_v2_for_maintenance(unsigned_root)
    assert kb.snapshot_status == "draft"
    assert kb.trust is None


def test_maintenance_loader_rejects_blank_metadata_key_id(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    _rewrite_json(root / "metadata.json", lambda value: {**value, "key_id": "   "})

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_SCHEMA"


@pytest.mark.parametrize(
    ("filename", "mutation", "expected_code"),
    [
        ("metadata.json", lambda value: {**value, "unexpected": "x"}, "KNOWLEDGE_V2_SCHEMA"),
        (
            "metadata.json",
            lambda value: {**value, "openstack_release": "2026.1"},
            "KNOWLEDGE_V2_RELEASE",
        ),
        (
            "metadata.json",
            lambda value: {**value, "upgrade_target": "2027.1"},
            "KNOWLEDGE_V2_RELEASE",
        ),
        (
            "metadata.json",
            lambda value: {**value, "kolla_ansible_release": "2026.1"},
            "KNOWLEDGE_V2_RELEASE",
        ),
        (
            "metadata.json",
            lambda value: {**value, "host_profile": "ubuntu_22_04"},
            "KNOWLEDGE_V2_RELEASE",
        ),
        (
            "components.json",
            lambda value: {
                "components": [
                    {**value["components"][0], "display_name": "   "}
                ]
            },
            "KNOWLEDGE_V2_SCHEMA",
        ),
        (
            "components.json",
            lambda value: {
                "components": [
                    {**value["components"][0], "releases": [True]}
                ]
            },
            "KNOWLEDGE_V2_SCHEMA",
        ),
        (
            "actions.jsonl",
            lambda value: {**value, "procedure_required": 1},
            "KNOWLEDGE_V2_SCHEMA",
        ),
        (
            "actions.jsonl",
            lambda value: {**value, "contour": "unknown"},
            "KNOWLEDGE_V2_SCHEMA",
        ),
        (
            "source-manifest.json",
            lambda value: {
                "sources": [
                    {**value["sources"][0], "local_path": "../outside.md"}
                ]
            },
            "KNOWLEDGE_V2_PATH",
        ),
    ],
)
def test_maintenance_loader_rejects_strict_schema_and_release_violations(
    tmp_path: Path, filename: str, mutation, expected_code: str
) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    path = root / filename
    if filename.endswith(".jsonl"):
        value = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(
            json.dumps(mutation(value), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        _rewrite_json(path, mutation)

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == expected_code


def test_maintenance_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    metadata = root / "metadata.json"
    source = metadata.read_text(encoding="utf-8")
    metadata.write_text(
        source.replace("{", '{"snapshot_id":"duplicate",', 1),
        encoding="utf-8",
    )

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_JSON"


def test_maintenance_loader_rejects_duplicate_record_ids(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    actions = root / "actions.jsonl"
    actions.write_bytes(actions.read_bytes() * 2)

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_DUPLICATE_ID"


def test_maintenance_loader_rejects_duplicate_local_step_ids(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    procedures = root / "procedures.jsonl"
    template = json.loads(procedures.read_text(encoding="utf-8"))
    template["steps"].append(dict(template["steps"][0]))
    procedures.write_text(
        json.dumps(template, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_DUPLICATE_ID"


def test_maintenance_loader_rejects_required_file_symlink(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    components = root / "components.json"
    target = tmp_path / "components.json"
    target.write_bytes(components.read_bytes())
    components.unlink()
    components.symlink_to(target)

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_FILE"


def test_maintenance_loader_rejects_symlinked_corpus_file(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    source = root / "corpus" / "SRC-MINIMAL.md"
    target = tmp_path / "SRC-MINIMAL.md"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_FILE"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_maintenance_loader_rejects_required_special_file(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    targets = root / "targets.jsonl"
    targets.unlink()
    os.mkfifo(targets)

    with pytest.raises(KnowledgeV2Error) as error:
        load_knowledge_v2_for_maintenance(root)
    assert error.value.code == "KNOWLEDGE_V2_FILE"


def test_runtime_detects_file_swap_after_snapshot_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)

    def verify_then_swap(path: Path, signers: Path):
        trust = verify_snapshot(path, signers)
        components = path / "components.json"
        components.write_bytes(b"X" * components.stat().st_size)
        return trust

    monkeypatch.setattr("reqmap.knowledge_v2.verify_snapshot", verify_then_swap)

    with pytest.raises(SnapshotTrustError) as error:
        load_knowledge_v2(root, allowed_signers)
    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"
