"""Synthetic builders for canonical deep-analysis model tests."""

from pathlib import Path
import shutil
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
    """Copy and sign the static minimal schema-v2 fixture with an ephemeral key."""
    root = tmp_path / "snapshot"
    fixture = Path(__file__).parent / "fixtures" / "kb_v2_minimal"
    shutil.copytree(fixture, root)

    allowed_signers = sign_existing_v2_snapshot(
        root,
        tmp_path / "trust",
        signer_identity=signer_identity,
        signature_namespace=signature_namespace,
    )
    return root, allowed_signers


def immutable_v2_kb(tmp_path: Path):
    """Load the real minimal schema-v2 fixture as immutable maintenance records."""
    from reqmap.knowledge_v2 import load_knowledge_v2_for_maintenance

    root, _ = signed_v2_snapshot(tmp_path)
    return load_knowledge_v2_for_maintenance(root)


def sign_existing_v2_snapshot(
    root: Path,
    trust: Path,
    *,
    signer_identity: str = "reqmap-snapshot",
    signature_namespace: str = "reqmap-snapshot",
) -> Path:
    """Sign an existing raw schema-v2 snapshot using a fresh ephemeral key."""

    trust.mkdir(parents=True)
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
    return allowed_signers
