"""Stable technical identifiers for canonical reqmap records."""

import hashlib


def generated_requirement_id(ordinal: int) -> str:
    return f"REQ-{ordinal:04d}"


def atom_id(requirement_id: str, ordinal: int) -> str:
    return f"{requirement_id}-A{ordinal:03d}"


def mapping_id(atom_id: str, ordinal: int) -> str:
    return f"{atom_id}-M{ordinal:03d}"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
