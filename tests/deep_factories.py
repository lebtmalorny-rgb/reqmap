"""Synthetic builders for canonical deep-analysis model tests."""

import json
from pathlib import Path
import subprocess

from reqmap.deep_models import (
    DeepEvidence,
    DeepRunResult,
    LifecyclePhase,
    ResponsibilityContour,
    ResponsibilityRecord,
    VersionScope,
)
from reqmap.ids import responsibility_id
from reqmap.models import EvidencePolarity, EvidenceStrength, SupportStatus
from reqmap.export_json import canonical_json_bytes
from reqmap.snapshot_trust import build_snapshot_manifest


def responsibility(
    atom_identifier: str = "REQ-0001-A001",
    ordinal: int = 1,
    *,
    scope: VersionScope | None = None,
) -> ResponsibilityRecord:
    return ResponsibilityRecord(
        record_id=responsibility_id(atom_identifier, ordinal),
        requirement_id="REQ-0001",
        atomic_claim_id=atom_identifier,
        contour=ResponsibilityContour.OPENSTACK_RUNTIME,
        component_ref="nova-api",
        executor_ref="nova-api",
        target_contour=ResponsibilityContour.OPENSTACK_RUNTIME,
        target_ref="nova-compute",
        action_ref="schedule",
        effect_ref="placement decision",
        lifecycle_phase=LifecyclePhase.RUNTIME,
        version_scope=scope or VersionScope("2025.1", "2025.1", "2025.1", "rocky_linux_9", "2025.1"),
        evidence_ids=("EV-0001",),
        support_status=SupportStatus.SUPPORTED,
    )


def deep_evidence() -> DeepEvidence:
    return DeepEvidence(
        evidence_id="EV-0001",
        claim="Nova schedules a compute instance.",
        claim_kind="capability",
        polarity=EvidencePolarity.POSITIVE,
        strength=EvidenceStrength.DIRECT,
        source_id="upstream-nova",
        locator="admin-guide#scheduling",
        version_constraint="2025.1",
        applicable_contours=(ResponsibilityContour.OPENSTACK_RUNTIME,),
        supports_entity_refs=("nova-api",),
        local_excerpt="The scheduler chooses a host.",
        review_state="reviewed",
    )


def deep_run() -> DeepRunResult:
    return DeepRunResult(
        run_id="RUN-0001",
        schema_version="2.0",
        run_status="completed",
        requirements=(),
        groups=(),
        responsibility_records=(responsibility(),),
        procedure_graphs=(),
        evidence=(deep_evidence(),),
        metadata={"source_release": "2025.1"},
    )


def signed_v2_snapshot(
    tmp_path: Path,
    *,
    signer_identity: str = "reqmap-snapshot",
    signature_namespace: str = "reqmap-snapshot",
) -> tuple[Path, Path]:
    """Create and sign a minimal raw schema-v2 snapshot with an ephemeral key."""
    root = tmp_path / "snapshot"
    (root / "corpus").mkdir(parents=True)
    raw_files: dict[str, object] = {
        "metadata.json": {
            "knowledge_schema_version": 2,
            "snapshot_id": "epoxy-2025.1-deep-001",
            "snapshot_status": "approved",
            "openstack_release": "2025.1",
            "upgrade_target": "2026.1",
            "kolla_ansible_release": "2025.1",
            "host_profile": "rocky_linux_9",
            "key_id": "reqmap-maintenance-2026",
        },
        "components.json": {"components": []},
        "actors.json": {"actors": []},
        "targets.jsonl": None,
        "capabilities.jsonl": None,
        "actions.jsonl": None,
        "effects.jsonl": None,
        "evidence.jsonl": None,
        "procedures.jsonl": None,
        "synonyms.json": {},
        "source-manifest.json": {"sources": []},
    }
    for relative, value in raw_files.items():
        path = root / relative
        if value is None:
            path.write_bytes(b"")
        else:
            path.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    (root / "corpus" / "source.md").write_text("# Epoxy evidence\n", encoding="utf-8")

    trust = tmp_path / "trust"
    trust.mkdir()
    private_key = trust / "signing_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    allowed_signers = trust / "allowed_signers"
    allowed_signers.write_text(f"{signer_identity} {public_key}\n", encoding="utf-8")

    manifest_path = root / "snapshot-manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(build_snapshot_manifest(root)))
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            signature_namespace,
            str(manifest_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    manifest_path.with_suffix(manifest_path.suffix + ".sig").replace(
        root / "snapshot-manifest.sig"
    )
    return root, allowed_signers
