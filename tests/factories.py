"""Synthetic builders for requirement-model tests."""

from reqmap.models import (
    AtomicClaim,
    ImplementationSource,
    ImplementationStep,
    Mapping,
    Phase,
    RelationType,
    Requirement,
    SourceCoordinate,
    SupportStatus,
)


def requirement(*, ordinal: int = 1, requirement_id: str = "REQ-0001") -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        source_id="source-row-1",
        text="Synthetic requirement",
        ordinal=ordinal,
        coordinate=SourceCoordinate(source_name="synthetic.xlsx", sheet="Requirements", row=2),
    )


def atom(*, ordinal: int = 1, requirement_id: str = "REQ-0001") -> AtomicClaim:
    return AtomicClaim(
        atom_id=f"{requirement_id}-A{ordinal:03d}",
        requirement_id=requirement_id,
        text="Synthetic atomic claim",
        source_quote="Synthetic requirement",
        mandatory=True,
        ordinal=ordinal,
    )


def mapping(
    *,
    ordinal: int = 1,
    atom_id: str = "REQ-0001-A001",
    phase: Phase = Phase.RUNTIME,
) -> Mapping:
    return Mapping(
        mapping_id=f"{atom_id}-M{ordinal:03d}",
        atom_id=atom_id,
        component_id="nova",
        role_ru="Synthetic role",
        relation=RelationType.IMPLEMENTS,
        phase=phase,
        implementation_source=ImplementationSource.UPSTREAM,
        mechanism="Synthetic mechanism",
        steps=(
            ImplementationStep(
                order=1,
                phase=phase,
                action_ru="Synthetic action",
                mechanism="Synthetic mechanism",
            ),
        ),
        evidence_ids=("evidence-1",),
        support_status=SupportStatus.SUPPORTED,
        reason_ru="Synthetic reason",
    )
