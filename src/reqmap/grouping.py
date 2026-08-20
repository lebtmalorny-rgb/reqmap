"""Deterministic enrichment of requirement parent relationships and groups."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
import hashlib

from reqmap.models import Requirement


_DUPLICATE_SOURCE_ID_RULE = "duplicate_source_id"


def build_groups(
    requirements: tuple[Requirement, ...], parent_column: str | None = None
) -> tuple[Requirement, ...]:
    """Add technical parent references and duplicate-source groups without changing source rows.

    A technical ``parent_id`` is retained only when it refers to an input requirement.
    Configured external parent values are resolved before dotted source-ID fallback, and
    the first input row is always selected for an ambiguous source ID.
    """
    by_source_id = _requirements_by_source_id(requirements)
    valid_requirement_ids = {item.requirement_id for item in requirements}
    duplicate_source_ids = {
        source_id for source_id, candidates in by_source_id.items() if len(candidates) > 1
    }

    grouped: list[Requirement] = []
    for item in requirements:
        parent_id = _resolve_parent_id(
            item,
            valid_requirement_ids=valid_requirement_ids,
            by_source_id=by_source_id,
            parent_column=parent_column,
        )
        group_ids = item.group_ids
        if item.source_id in duplicate_source_ids:
            group_id = _stable_group_id(_DUPLICATE_SOURCE_ID_RULE, item.source_id)
            if group_id not in group_ids:
                group_ids = (*group_ids, group_id)
        grouped.append(replace(item, parent_id=parent_id, group_ids=group_ids))
    return tuple(grouped)


def nearest_existing_parent(
    source_id: str, by_source_id: Mapping[str, list[Requirement]]
) -> str | None:
    """Return the first input row with the nearest dotted source-ID prefix."""
    parts = source_id.split(".")
    for size in range(len(parts) - 1, 0, -1):
        candidates = by_source_id.get(".".join(parts[:size]), [])
        if candidates:
            return candidates[0].requirement_id
    return None


def _requirements_by_source_id(
    requirements: tuple[Requirement, ...],
) -> dict[str, list[Requirement]]:
    by_source_id: dict[str, list[Requirement]] = defaultdict(list)
    for item in requirements:
        if item.source_id is not None:
            by_source_id[item.source_id].append(item)
    return dict(by_source_id)


def _resolve_parent_id(
    requirement: Requirement,
    *,
    valid_requirement_ids: set[str],
    by_source_id: Mapping[str, list[Requirement]],
    parent_column: str | None,
) -> str | None:
    if requirement.parent_id in valid_requirement_ids:
        return requirement.parent_id

    if parent_column is not None:
        explicit_parent = _explicit_parent_id(requirement, parent_column, by_source_id)
        if explicit_parent is not None:
            return explicit_parent

    if requirement.source_id is not None:
        return nearest_existing_parent(requirement.source_id, by_source_id)
    return None


def _explicit_parent_id(
    requirement: Requirement,
    parent_column: str,
    by_source_id: Mapping[str, list[Requirement]],
) -> str | None:
    for field in requirement.source_fields:
        if field.column == parent_column:
            candidates = by_source_id.get(field.value, [])
            if candidates:
                return candidates[0].requirement_id
            return None
    return None


def _stable_group_id(rule_type: str, source_id: str) -> str:
    payload = f"{rule_type}:{source_id}".encode("utf-8")
    return f"GRP-{hashlib.sha256(payload).hexdigest()[:12]}"
