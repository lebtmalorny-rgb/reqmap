from dataclasses import replace

import pytest

from reqmap.deep_models import (
    DeepRunResult,
    LifecyclePhase,
    ProcedureStep,
    ResponsibilityContour,
    VersionScope,
    validate_version_scope,
)
from reqmap.ids import procedure_graph_id, procedure_step_id, responsibility_id
from reqmap.models import to_dict
from tests.deep_factories import deep_evidence, deep_run, responsibility


def test_deep_records_serialize_with_stable_ids() -> None:
    """A changed deep-record suffix or enum serialization must fail this test."""
    scope = VersionScope("2025.1", "2025.1", "2025.1", "rocky_linux_9", "2025.1")
    record = responsibility("REQ-0001-A001", 1, scope=scope)

    assert record.record_id == "REQ-0001-A001-R001"
    assert to_dict(record)["contour"] == "openstack_runtime"
    assert responsibility_id("REQ-0001-A001", 2) == "REQ-0001-A001-R002"
    assert procedure_graph_id("REQ-0001", 1) == "REQ-0001-P001"
    assert procedure_step_id("REQ-0001-P001", 3) == "REQ-0001-P001-S003"


def test_version_scope_rejects_2026_outside_upgrade() -> None:
    """Allowing the target release during runtime would make this fail."""
    with pytest.raises(ValueError, match="2026.1.*upgrade"):
        validate_version_scope(
            VersionScope("2025.1", "2026.1", "2025.1", "rocky_linux_9", "2025.1 -> 2026.1"),
            LifecyclePhase.RUNTIME,
        )


@pytest.mark.parametrize("ordinal", (0, -1))
def test_deep_id_helpers_reject_non_positive_ordinals(ordinal: int) -> None:
    """Formatting zero or negative ordinals as stable IDs would make this fail."""
    with pytest.raises(ValueError, match="positive"):
        responsibility_id("REQ-0001-A001", ordinal)


@pytest.mark.parametrize("unsafe_identifier", ("", "REQ 0001", "REQ/0001", "REQ-0001\n"))
def test_deep_id_helpers_reject_unsafe_identifiers(unsafe_identifier: str) -> None:
    """Permitting non-technical ID fragments would make this fail."""
    with pytest.raises(ValueError, match="safe"):
        procedure_graph_id(unsafe_identifier, 1)


@pytest.mark.parametrize(
    ("scope", "phase", "message"),
    (
        (
            VersionScope("2026.1", "2026.1", "2025.1", "rocky_linux_9", "2026.1"),
            LifecyclePhase.UPGRADE,
            "source_release",
        ),
        (
            VersionScope("2025.1", "2025.1", "2026.1", "rocky_linux_9", "2025.1"),
            LifecyclePhase.RUNTIME,
            "kolla_ansible_release",
        ),
        (
            VersionScope("2025.1", "2025.1", "2025.1", "ubuntu_22_04", "2025.1"),
            LifecyclePhase.RUNTIME,
            "host_profile",
        ),
    ),
)
def test_version_scope_rejects_unsupported_base_values(
    scope: VersionScope, phase: LifecyclePhase, message: str
) -> None:
    """Accepting a release or host other than the canonical baseline would fail this test."""
    with pytest.raises(ValueError, match=message):
        validate_version_scope(scope, phase)


def test_responsibility_rejects_empty_reference_and_string_contour() -> None:
    """Empty entity references and raw contour strings must not enter schema-v2 records."""
    with pytest.raises(ValueError, match="component_ref"):
        replace(responsibility(), component_ref="")
    with pytest.raises(ValueError, match="contour"):
        replace(responsibility(), contour="openstack_runtime")


def test_responsibility_rejects_non_positive_stable_suffix() -> None:
    """A direct record construction must not bypass the positive-ID ordinal rule."""
    with pytest.raises(ValueError, match="positive"):
        replace(responsibility(), record_id="REQ-0001-A001-R000")


def test_deep_evidence_rejects_string_contour() -> None:
    """A raw contour string in evidence applicability must be rejected."""
    with pytest.raises(ValueError, match="applicable_contours"):
        replace(deep_evidence(), applicable_contours=("host_os",))


def test_procedure_step_rejects_noncanonical_step_id() -> None:
    """A safe but non-`-SNNN` step ID must not bypass the stable-ID contract."""
    with pytest.raises(ValueError, match="step_id"):
        ProcedureStep(
            step_id="STEP-001",
            phase=LifecyclePhase.RUNTIME,
            contour=ResponsibilityContour.OPENSTACK_RUNTIME,
            executor_ref="nova-api",
            target_ref="nova-compute",
            action_ref="schedule",
            preconditions=(),
            success_criteria=(),
            evidence_ids=(),
        )


def test_deep_run_requires_schema_version_2() -> None:
    """A schema-v1 label on the deep result must be rejected."""
    with pytest.raises(ValueError, match="schema_version"):
        replace(deep_run(), schema_version="1.0")


def test_deep_run_serializes_references_instead_of_duplicate_records() -> None:
    """Deep results retain only responsibility IDs at atom and requirement boundaries."""
    result = DeepRunResult(
        run_id="RUN-0002",
        schema_version="2.0",
        run_status="completed",
        requirements=(),
        groups=(),
        responsibility_records=(responsibility(),),
        procedure_graphs=(),
        evidence=(),
        metadata={},
    )

    payload = to_dict(result)
    assert payload["responsibility_records"][0]["record_id"] == "REQ-0001-A001-R001"
    assert set(payload) == {
        "run_id",
        "schema_version",
        "run_status",
        "requirements",
        "groups",
        "responsibility_records",
        "procedure_graphs",
        "evidence",
        "metadata",
        "diagnostics",
    }
