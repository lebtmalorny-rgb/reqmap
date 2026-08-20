"""Tests for deterministic requirement grouping and parent relationships."""

import hashlib

from hypothesis import given, strategies as st

from reqmap.grouping import build_groups
from reqmap.models import Requirement, SourceCoordinate, SourceField, SourceHint


def requirement(
    source_id: str,
    text: str,
    *,
    ordinal: int,
    parent_id: str | None = None,
    source_fields: tuple[SourceField, ...] = (),
    source_hints: tuple[SourceHint, ...] = (),
) -> Requirement:
    return Requirement(
        requirement_id=f"REQ-{ordinal:04d}",
        source_id=source_id,
        text=text,
        ordinal=ordinal,
        coordinate=SourceCoordinate(source_name="synthetic.xlsx", sheet="Requirements", row=ordinal + 1),
        parent_id=parent_id,
        source_fields=source_fields,
        source_hints=source_hints,
    )


def test_grouping_keeps_every_source_row() -> None:
    """Deduplicating a repeated source ID would drop an independently traceable row."""
    rows = (
        requirement("1", "Родитель", ordinal=1),
        requirement("1.1", "Подпункт", ordinal=2),
        requirement("1.1", "Второй подпункт с тем же ID", ordinal=3),
    )

    grouped = build_groups(rows)

    assert len(grouped) == 3
    assert grouped[1].parent_id == grouped[0].requirement_id
    assert grouped[2].requirement_id != grouped[1].requirement_id
    assert grouped[1].group_ids == grouped[2].group_ids


def test_grouping_resolves_explicit_parent_before_dotted_id() -> None:
    """Ignoring a configured parent column would attach a row to the wrong requirement."""
    rows = (
        requirement("1", "Dotted parent", ordinal=1),
        requirement("2", "Explicit parent", ordinal=2),
        requirement(
            "1.1",
            "Child",
            ordinal=3,
            source_fields=(SourceField(column="Parent", value="2"),),
        ),
    )

    grouped = build_groups(rows, parent_column="Parent")

    assert grouped[2].parent_id == rows[1].requirement_id


def test_grouping_uses_dotted_parent_when_explicit_parent_is_unresolved() -> None:
    """An unresolved external parent must not suppress the available dotted fallback."""
    rows = (
        requirement("1", "Dotted parent", ordinal=1),
        requirement(
            "1.1",
            "Child",
            ordinal=2,
            source_fields=(SourceField(column="Parent", value="missing"),),
        ),
    )

    grouped = build_groups(rows, parent_column="Parent")

    assert grouped[1].parent_id == rows[0].requirement_id


def test_grouping_uses_first_source_row_for_duplicate_explicit_parent() -> None:
    """Choosing among repeated external IDs non-deterministically would change the tree between runs."""
    rows = (
        requirement("1", "First parent", ordinal=1),
        requirement("1", "Second parent", ordinal=2),
        requirement(
            "child",
            "Child",
            ordinal=3,
            source_fields=(SourceField(column="Parent", value="1"),),
        ),
    )

    grouped = build_groups(rows, parent_column="Parent")

    assert grouped[2].parent_id == rows[0].requirement_id


def test_grouping_preserves_valid_technical_parent_id() -> None:
    """Replacing a validated technical parent with an external ID would break referential integrity."""
    rows = (
        requirement("1", "Parent", ordinal=1),
        requirement(
            "1.1",
            "Child",
            ordinal=2,
            parent_id="REQ-0001",
            source_fields=(SourceField(column="Parent", value="missing"),),
        ),
    )

    grouped = build_groups(rows, parent_column="Parent")

    assert grouped[1].parent_id == "REQ-0001"


def test_grouping_adds_stable_group_for_repeated_source_id() -> None:
    """Unstable duplicate grouping would make aggregate reports non-reproducible."""
    rows = (
        requirement("7", "Первый", ordinal=1),
        requirement("7", "Второй", ordinal=2),
    )

    grouped = build_groups(rows)

    expected = "GRP-" + hashlib.sha256(b"duplicate_source_id:7").hexdigest()[:12]
    assert grouped[0].group_ids == (expected,)
    assert grouped[1].group_ids == (expected,)


def test_grouping_retains_full_and_partial_duplicate_rows() -> None:
    """Collapsing equal or near-equal rows would destroy separate source coordinates."""
    rows = (
        requirement("7", "Полный дубль", ordinal=1),
        requirement("7", "Полный дубль", ordinal=2),
        requirement("7", "Частичный дубль", ordinal=3),
    )

    grouped = build_groups(rows)

    assert [item.requirement_id for item in grouped] == ["REQ-0001", "REQ-0002", "REQ-0003"]
    assert [item.text for item in grouped] == ["Полный дубль", "Полный дубль", "Частичный дубль"]
    assert len({item.group_ids for item in grouped}) == 1


def test_grouping_never_emits_unresolved_external_parent_id() -> None:
    """Leaking an external parent value into parent_id would violate technical referential integrity."""
    row = requirement("standalone", "Child", ordinal=1, parent_id="external-parent")

    grouped = build_groups((row,))

    assert grouped[0].parent_id is None


def test_grouping_does_not_mutate_or_inherit_source_hints_or_fields() -> None:
    """Copying parent annotations would turn untrusted source hints into child context."""
    parent = requirement(
        "1",
        "Parent text",
        ordinal=1,
        source_fields=(SourceField(column="Priority", value="P1"),),
        source_hints=(SourceHint(column="Hint", value="parent hint"),),
    )
    child = requirement(
        "1.1",
        "Child text",
        ordinal=2,
        source_fields=(SourceField(column="Priority", value="P2"),),
        source_hints=(SourceHint(column="Hint", value="child hint"),),
    )

    grouped = build_groups((parent, child))

    assert grouped[0].text == parent.text
    assert grouped[0].source_fields == parent.source_fields
    assert grouped[0].source_hints == parent.source_hints
    assert grouped[1].text == child.text
    assert grouped[1].source_fields == child.source_fields
    assert grouped[1].source_hints == child.source_hints


@given(st.lists(st.text(min_size=1), min_size=1, max_size=50))
def test_grouping_never_drops_or_reorders_rows(texts: list[str]) -> None:
    """Grouping must only enrich source rows, never replace or reorder them."""
    source = tuple(
        requirement(str(index), text, ordinal=index) for index, text in enumerate(texts, start=1)
    )

    grouped = build_groups(source)

    assert [item.ordinal for item in grouped] == list(range(1, len(source) + 1))
    assert [item.requirement_id for item in grouped] == [item.requirement_id for item in source]
    assert [item.text for item in grouped] == texts
