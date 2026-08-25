"""Stable technical identifiers for canonical reqmap records."""

import hashlib
import re


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def generated_requirement_id(ordinal: int) -> str:
    return f"REQ-{ordinal:04d}"


def atom_id(requirement_id: str, ordinal: int) -> str:
    return f"{requirement_id}-A{ordinal:03d}"


def mapping_id(atom_id: str, ordinal: int) -> str:
    return f"{atom_id}-M{ordinal:03d}"


def responsibility_id(atom_identifier: str, ordinal: int) -> str:
    """Return a canonical responsibility-record identifier."""
    return f"{_safe_identifier(atom_identifier)}-R{_positive_ordinal(ordinal):03d}"


def procedure_graph_id(requirement_identifier: str, ordinal: int) -> str:
    """Return a canonical procedure-graph identifier."""
    return f"{_safe_identifier(requirement_identifier)}-P{_positive_ordinal(ordinal):03d}"


def procedure_step_id(graph_identifier: str, ordinal: int) -> str:
    """Return a canonical procedure-step identifier."""
    return f"{_safe_identifier(graph_identifier)}-S{_positive_ordinal(ordinal):03d}"


def _safe_identifier(value: str) -> str:
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier must be a non-empty safe technical identifier")
    return value


def _positive_ordinal(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("ordinal must be a positive integer")
    return value


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
