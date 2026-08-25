"""Synthetic builders for canonical deep-analysis model tests."""

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
