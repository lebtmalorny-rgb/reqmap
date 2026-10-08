"""Capture and validate exact source input before deriving any context."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from reqmap.config import InputProfile
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.ids import generated_requirement_id
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.models import Requirement, SourceCoordinate, to_dict
from reqmap.source_context_models import SourceDocumentSnapshot


def _source_rows(requirements: tuple[Requirement, ...]) -> tuple[Requirement, ...]:
    return tuple(replace(row, parent_id=None, group_ids=()) for row in requirements)


def _rows_digest(requirements: tuple[Requirement, ...]) -> str:
    rows = []
    for row in _source_rows(requirements):
        raw = to_dict(row)
        del raw["parent_id"], raw["group_ids"]
        rows.append(raw)
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def capture_source_document(*, content: bytes, source_kind: str, source_name: str,
                            requirements: tuple[Requirement, ...],
                            input_profile: InputProfile | None,
                            text_mode: str | None = None) -> SourceDocumentSnapshot:
    """Reimport bytes; only derived grouping may differ from the caller's rows."""
    try:
        if (type(content) is not bytes or not content or type(source_name) is not str
                or not source_name.strip() or type(requirements) is not tuple
                or any(type(row) is not Requirement for row in requirements)):
            raise ValueError("invalid snapshot shape")
        if source_kind == "xlsx":
            if text_mode is not None or (input_profile is not None and type(input_profile) is not InputProfile):
                raise ValueError("invalid XLSX settings")
            imported = load_xlsx_bytes(content, input_profile, source_name)
        elif source_kind == "txt":
            if input_profile is not None or text_mode not in ("single", "lines"):
                raise ValueError("invalid TXT settings")
            imported = load_text(content.decode("utf-8"), text_mode, source_name)
        elif source_kind == "texts":
            if input_profile is not None or text_mode is not None:
                raise ValueError("invalid inline settings")
            values = json.loads(content)
            if (type(values) is not list or not values
                    or any(type(v) is not str or not v.strip() for v in values)
                    or canonical_json_bytes(values) != content):
                raise ValueError("inline texts must use canonical JSON bytes")
            imported = tuple(Requirement(generated_requirement_id(i), None, value, i,
                                         SourceCoordinate(source_name, None, i))
                             for i, value in enumerate(values, 1))
        else:
            raise ValueError("unknown source kind")
        if not imported or _source_rows(requirements) != imported:
            raise ValueError("rows differ from source import")
    except (ValueError, TypeError, KeyError, AttributeError, ReqmapError) as exc:
        raise ReqmapError("SOURCE_CONTEXT_INPUT_MISMATCH",
                          "Исходные строки или профиль не соответствуют сохранённым bytes.") from exc
    return SourceDocumentSnapshot(
        content, source_kind, source_name, text_mode, input_profile,
        hashlib.sha256(content).hexdigest(),
        hashlib.sha256(canonical_json_bytes(input_profile)).hexdigest() if input_profile is not None else None,
        _rows_digest(imported), imported)
