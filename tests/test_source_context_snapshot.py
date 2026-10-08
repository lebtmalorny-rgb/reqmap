"""Snapshot must bind exact bytes, import settings and complete row identities."""
from dataclasses import asdict, replace
import hashlib
import importlib
import json

import pytest

from reqmap.config import InputProfile
from reqmap.errors import ReqmapError
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.models import SourceField, SourceHint
from tests.source_context_support import text_document, workbook_bytes


def capture(**kwargs):
    assert importlib.util.find_spec("reqmap.source_context"), "immutable source capture is missing"
    return importlib.import_module("reqmap.source_context").capture_source_document(**kwargs)


def test_snapshot_preserves_rows_and_ignores_derived_grouping():
    data = text_document(("😀е\u0308", "ё", "😀е\u0308"))
    snapshot = capture(**data)
    assert snapshot.requirements == data["requirements"]
    assert snapshot.content == data["content"]
    assert snapshot.input_sha256 == hashlib.sha256(data["content"]).hexdigest()
    grouped = tuple(replace(r, parent_id="technical-parent", group_ids=("same-id",))
                    for r in data["requirements"])
    other = capture(**{**data, "requirements": grouped})
    assert other.requirements == data["requirements"]
    assert other.requirements_sha256 == snapshot.requirements_sha256
    changed = capture(**text_document(("ё", "😀е\u0308", "😀е\u0308")))
    assert changed.requirements_sha256 != snapshot.requirements_sha256


def test_full_profile_defaults_are_hashed():
    content = workbook_bytes()
    profile = InputProfile(("Первый", "Второй"), (), 1, "ID", "Требование")
    data = dict(content=content, source_kind="xlsx", source_name="synthetic.xlsx",
                input_profile=profile,
                requirements=load_xlsx_bytes(content, profile, "synthetic.xlsx"))
    snapshot = capture(**data)
    expected = (json.dumps(asdict(profile), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n").encode()
    assert snapshot.input_profile_sha256 == hashlib.sha256(expected).hexdigest()
    assert len(snapshot.requirements) == 4
    assert len({r.requirement_id for r in snapshot.requirements}) == 4
    assert {r.source_id for r in snapshot.requirements} == {"same-id"}
    for updated in (replace(profile, expected_result_column="Условие"),
                    replace(profile, hint_columns=("Подсказка",)),
                    replace(profile, include_sheets=("Первый",))):
        rows = load_xlsx_bytes(content, updated, "synthetic.xlsx")
        other = capture(**{**data, "input_profile": updated, "requirements": rows})
        assert other.input_profile_sha256 != snapshot.input_profile_sha256
        assert other.requirements_sha256 != snapshot.requirements_sha256


@pytest.mark.parametrize("mode,count", [("single", 1), ("lines", 2)])
def test_snapshot_text_modes_roundtrip(mode, count):
    content = "😀е\u0308\n\nё\n".encode()
    rows = load_text(content.decode(), mode, "stdin")
    snapshot = capture(content=content, source_kind="txt", source_name="stdin",
                       input_profile=None, text_mode=mode, requirements=rows)
    assert snapshot.requirements == rows
    assert len(snapshot.requirements) == count
    assert snapshot.text_mode == mode
    assert snapshot.content == content
    assert snapshot.input_profile_sha256 is None


@pytest.mark.parametrize("field,value", [
    ("text", "other"), ("requirement_id", "forged"), ("ordinal", 3),
    ("source_fields", (SourceField("extra", "GUI"),)),
    ("source_hints", (SourceHint("GUI", "extra"),)),
])
def test_capture_reimports_instead_of_trusting_provided_rows(field, value):
    data = text_document()
    data["requirements"] = (replace(data["requirements"][0], **{field: value}),)
    with pytest.raises(ReqmapError) as error:
        capture(**data)
    assert error.value.code == "SOURCE_CONTEXT_INPUT_MISMATCH"


@pytest.mark.parametrize("changes", [
    {"source_kind": "unknown"}, {"text_mode": "lines"},
    {"content": b'not JSON'}, {"content": b'["a","b"]', "source_kind": "txt"},
    {"content": b'[]'}, {"content": b'[true]'},
])
def test_capture_rejects_ambiguous_or_invalid_input(changes):
    with pytest.raises(ReqmapError) as error:
        capture(**{**text_document(), **changes})
    assert error.value.code == "SOURCE_CONTEXT_INPUT_MISMATCH"
