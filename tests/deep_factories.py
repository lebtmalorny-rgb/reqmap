"""Synthetic builders for canonical deep-analysis model tests."""

from dataclasses import replace
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
from types import MappingProxyType

from reqmap.deep_models import (
    DeepEvidence,
    DeepCandidate,
    DeepRetrievalResult,
    DeepRunResult,
    LifecyclePhase,
    ResponsibilityContour,
    ResponsibilityRecord,
    VersionScope,
)
from reqmap.ids import responsibility_id
from reqmap.knowledge_v2 import (
    ActionRecord,
    ActorRecord,
    DeepCapabilityRecord,
    DeepComponentRecord,
    EffectRecord,
    TargetRecord,
)
from reqmap.models import EvidencePolarity, EvidenceStrength, SupportStatus
from reqmap.export_json import canonical_json_bytes
from reqmap.snapshot_trust import build_snapshot_manifest


def write_v1_knowledge_snapshot(
    root: Path,
    *,
    component_id: str,
    component_kind: str,
    capability_id: str,
    evidence_id: str,
    source_id: str,
    strength: str = "direct",
    source_bytes: bytes | None = None,
) -> Path:
    """Create a complete strict v1 fixture for migration integration tests."""
    root.mkdir()
    source_path = root / "sources" / f"{component_id}.md"
    source_path.parent.mkdir()
    source_bytes = source_bytes or f"# {component_id}\n\nMigration fixture.\n".encode("utf-8")
    source_path.write_bytes(source_bytes)

    def write_json(relative: str, value: object) -> None:
        (root / relative).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    write_json(
        "components.json",
        {
            "components": [
                {
                    "id": component_id,
                    "display_name": component_id,
                    "kind": component_kind,
                    "release": "2025.1",
                }
            ]
        },
    )
    write_json(
        "source-manifest.json",
        {
            "sources": [
                {
                    "id": source_id,
                    "component_ids": [component_id],
                    "source_url": f"https://example.invalid/{component_id}",
                    "retrieved_at": "2026-08-25",
                    "version": "2025.1",
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "local_path": f"sources/{component_id}.md",
                    "provenance": "official",
                }
            ]
        },
    )
    (root / "capabilities.jsonl").write_text(
        json.dumps(
            {
                "id": capability_id,
                "component_id": component_id,
                "name_ru": f"{component_id} capability",
                "terms": [component_id],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "evidence.jsonl").write_text(
        json.dumps(
            {
                "id": evidence_id,
                "component_id": component_id,
                "capability_id": capability_id,
                "polarity": "positive",
                "strength": strength,
                "claim_ru": f"{component_id} is supported",
                "source_id": source_id,
                "locator": f"{source_id}:claim",
                "version_constraint": "2025.1",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json("synonyms.json", {component_id: [f"{component_id} synonym"]})

    from reqmap.knowledge import snapshot_digest

    write_json(
        "metadata.json",
        {"openstack_release": "2025.1", "snapshot_sha256": snapshot_digest(root)},
    )
    return root


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


def deep_candidate(kb, *evidence_ids: str) -> DeepRetrievalResult:
    """Return a real, coherent Nova candidate from the loaded v2 fixture."""
    action = kb.actions["ACTION-NOVA-CREATE"]
    return DeepRetrievalResult(
        (
            DeepCandidate(
                "nova",
                "CAP-NOVA-CREATE",
                action.action_id,
                "EFFECT-NOVA-SERVER-ACTIVE",
                tuple(evidence_ids) or action.evidence_ids,
                action.version_scope,
                1.0,
                ("fixture",),
            ),
        ),
        (),
    )


def with_nova_evidence(
    kb,
    evidence_id: str,
    *,
    polarity: EvidencePolarity = EvidencePolarity.POSITIVE,
    strength: EvidenceStrength = EvidenceStrength.DIRECT,
):
    """Attach evidence to every entity in the real Nova candidate relation."""
    base = kb.evidence["EV-NOVA-CREATE"]
    item = replace(base, evidence_id=evidence_id, polarity=polarity, strength=strength)
    capability = replace(
        kb.capabilities["CAP-NOVA-CREATE"],
        evidence_ids=(*kb.capabilities["CAP-NOVA-CREATE"].evidence_ids, evidence_id),
    )
    action = replace(
        kb.actions["ACTION-NOVA-CREATE"],
        evidence_ids=(*kb.actions["ACTION-NOVA-CREATE"].evidence_ids, evidence_id),
    )
    effect = replace(
        kb.effects["EFFECT-NOVA-SERVER-ACTIVE"],
        evidence_ids=(*kb.effects["EFFECT-NOVA-SERVER-ACTIVE"].evidence_ids, evidence_id),
    )
    return replace(
        kb,
        capabilities=MappingProxyType({**kb.capabilities, capability.capability_id: capability}),
        actions=MappingProxyType({**kb.actions, action.action_id: action}),
        effects=MappingProxyType({**kb.effects, effect.effect_id: effect}),
        evidence=MappingProxyType({**kb.evidence, evidence_id: item}),
    )


def mixed_kolla_host_kb(kb):
    """Add one verified Kolla action whose explicit target is a Rocky Linux host entity."""
    component = DeepComponentRecord(
        "kolla_ansible", "Kolla-Ansible", "deployment_tool", ("2025.1",)
    )
    host = DeepComponentRecord(
        "rocky_linux_9", "Rocky Linux 9", "host_os_subsystem", ("2025.1",)
    )
    actor = ActorRecord("actor:kolla_ansible", "automation", "kolla_ansible")
    target = TargetRecord(
        "rocky_linux_9.kernel_sysctl",
        ResponsibilityContour.HOST_OS,
        "rocky_linux_9",
        "rocky_linux_9",
    )
    capability = DeepCapabilityRecord(
        "CAP-KOLLA-SYSCTL",
        "kolla_ansible",
        "Применение sysctl через Kolla-Ansible",
        ("sysctl", "kolla-ansible"),
        ("EV-KOLLA-SYSCTL",),
    )
    scope = VersionScope("2025.1", "2025.1", "2025.1", "rocky_linux_9", "2025.1")
    action = ActionRecord(
        "ACTION-KOLLA-SYSCTL",
        "kolla_ansible",
        ResponsibilityContour.KOLLA_ANSIBLE,
        "kolla_ansible_action",
        "kolla-ansible reconfigure",
        target.target_id,
        ("EFFECT-HOST-SYSCTL",),
        scope,
        ("EV-KOLLA-SYSCTL",),
        False,
    )
    effect = EffectRecord(
        "EFFECT-HOST-SYSCTL",
        target.target_id,
        "ip_forward=0",
        "ip_forward=1",
        ("sysctl value is 1",),
        True,
        ("EV-KOLLA-SYSCTL",),
    )
    evidence = replace(
        kb.evidence["EV-NOVA-CREATE"],
        evidence_id="EV-KOLLA-SYSCTL",
        claim="Kolla-Ansible applies the sysctl setting to the Rocky Linux host.",
        applicable_contours=(
            ResponsibilityContour.KOLLA_ANSIBLE,
            ResponsibilityContour.HOST_OS,
        ),
        supports_entity_refs=(
            capability.capability_id,
            action.action_id,
            effect.effect_id,
        ),
    )
    changed = replace(
        kb,
        components=MappingProxyType(
            {**kb.components, component.component_id: component, host.component_id: host}
        ),
        actors=MappingProxyType({**kb.actors, actor.actor_id: actor}),
        targets=MappingProxyType({**kb.targets, target.target_id: target}),
        capabilities=MappingProxyType({**kb.capabilities, capability.capability_id: capability}),
        actions=MappingProxyType({**kb.actions, action.action_id: action}),
        effects=MappingProxyType({**kb.effects, effect.effect_id: effect}),
        evidence=MappingProxyType({**kb.evidence, evidence.evidence_id: evidence}),
    )
    retrieval = DeepRetrievalResult(
        (
            DeepCandidate(
                component.component_id,
                capability.capability_id,
                action.action_id,
                effect.effect_id,
                action.evidence_ids,
                action.version_scope,
                1.0,
                ("fixture",),
            ),
        ),
        (),
    )
    return changed, retrieval


def responsibility_selection(**changes: object) -> dict[str, object]:
    item: dict[str, object] = {
        "contour": "openstack_runtime",
        "component_ref": "nova",
        "executor_ref": "ACTOR-NOVA-API",
        "target_contour": "openstack_runtime",
        "target_ref": "TARGET-NOVA-SERVER",
        "action_ref": "ACTION-NOVA-CREATE",
        "effect_ref": "EFFECT-NOVA-SERVER-ACTIVE",
        "lifecycle_phase": "runtime",
        "version_scope": {
            "source_release": "2025.1",
            "target_release": "2025.1",
            "kolla_ansible_release": "2025.1",
            "host_profile": "rocky_linux_9",
            "version_constraint": "2025.1",
        },
        "evidence_ids": ["EV-NOVA-CREATE"],
        "support_status": "supported",
        "related_indexes": [],
    }
    item.update(changes)
    return item


def deep_mapping_response(
    *responsibilities: dict[str, object],
    support_status: str = "supported",
    procedure_template_ids: tuple[str, ...] = ("PROC-NOVA-CREATE",),
) -> dict[str, object]:
    return {
        "support_status": support_status,
        "supported_aspects": ["Создание виртуальной машины"],
        "unconfirmed_aspects": [],
        "responsibilities": list(responsibilities or (responsibility_selection(),)),
        "procedure_template_ids": list(procedure_template_ids),
    }


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
