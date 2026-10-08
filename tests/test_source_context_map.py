"""Reject ambiguous annotations and references before they can affect meaning."""
from copy import deepcopy
import importlib

import pytest

from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.source_context import capture_source_document
from tests.source_context_support import text_document, map_for, link_for


def decode(raw, doc):
    assert importlib.util.find_spec("reqmap.source_context_codec"), "strict map decoder is missing"
    return importlib.import_module("reqmap.source_context_codec").decode_source_context_map(raw, doc)


@pytest.fixture
def example():
    doc = capture_source_document(**text_document(("Nova должна создавать ВМ", "😀GUI и GUI")))
    raw = map_for(doc, disposition="linked", links=(link_for(doc, start=7, end=10),))
    return doc, raw


def rejects(doc, raw, code="SOURCE_CONTEXT_INVALID"):
    with pytest.raises(ReqmapError) as error:
        decode(raw if type(raw) is bytes else canonical_json_bytes(raw), doc)
    assert error.value.code == code


def test_map_exact_refs_and_unicode_spans(example):
    doc, raw = example
    mapping = decode(canonical_json_bytes(raw), doc)
    assert mapping.rows[0].links[0].origin.span.start == 7
    assert mapping.rows[0].links[0].origin.span.quote == "GUI"
    from reqmap.source_context_codec import encode_source_context_map
    assert encode_source_context_map(mapping) == raw


@pytest.mark.parametrize("mutation", ["coordinate", "sha", "utf16", "foreign", "span-bool"])
def test_map_exact_refs_reject_wrong_occurrence(example, mutation):
    doc, raw = example
    origin = raw["rows"][0]["links"][0]["origin"]
    if mutation == "coordinate": origin["row"]["coordinate"]["sheet"] = "excluded"
    if mutation == "sha": origin["row"]["source_sha256"] = "0"*64
    if mutation == "foreign": origin["row"]["requirement_id"] = "REQ-9999"
    if mutation == "utf16": origin["span"].update(start=8, end=11)
    if mutation == "span-bool": origin["span"].update(start=True)
    rejects(doc, raw, "SOURCE_CONTEXT_REF_INVALID")


@pytest.mark.parametrize("field", ["input_sha256", "input_profile_sha256", "requirements_sha256", "source_kind", "source_name"])
def test_map_rejects_other_source(example, field):
    doc, raw = example
    raw[field] = "other"
    rejects(doc, raw, "SOURCE_CONTEXT_INPUT_MISMATCH")


@pytest.mark.parametrize("value", [b'{"rows":[],"rows":[]}', b'{"n":NaN}', b'[]'])
def test_map_rejects_ambiguous_json(example, value):
    rejects(example[0], value)


@pytest.mark.parametrize("mutation", ["extra", "duplicate-target", "duplicate-link", "reviewer", "reason", "utc", "time", "draft-reviewer", "independent-links", "linked-empty", "unresolved-complete", "complete-bool", "unreviewed-complete", "evidence", "actor"])
def test_map_does_not_accept_semantic_bypass(example, mutation):
    doc, raw = example
    row = raw["rows"][0]
    if mutation == "extra": raw["supported"] = True
    if mutation == "duplicate-target": raw["rows"].append(deepcopy(row))
    if mutation == "duplicate-link": row["links"].append(deepcopy(row["links"][0]))
    if mutation == "reviewer": row["reviewed_by"] = " "
    if mutation == "reason": row["reason_ru"] = ""
    if mutation == "utc": row["reviewed_at"] = "2026-10-08T00:00:00+03:00"
    if mutation == "time": row["reviewed_at"] = "2026-02-30T00:00:00Z"
    if mutation == "draft-reviewer": row["review_state"] = "draft"
    if mutation == "independent-links": row["disposition"] = "independent"
    if mutation == "linked-empty": row["links"] = []
    if mutation == "unresolved-complete": row["disposition"] = "unresolved"
    if mutation == "complete-bool": row["context_complete"] = 1
    if mutation == "unreviewed-complete": row["context_complete"] = False
    if mutation == "evidence": row["links"][0]["evidence_ids"] = ["fake"]
    if mutation == "actor": row["links"][0]["field"] = "actor"
    rejects(doc, raw)


@pytest.mark.parametrize("value,valid", [("0", True), ("999999999999", True), ("01", False), ("-1", False), ("1.1", False), ("1000000000000", False), (1, False)])
def test_map_duration_boundaries(example, value, valid):
    doc, raw = example
    raw["rows"][0]["links"][0].update(field="constraint", value=dict(name="duration", operator="within", value=value, unit="ms"))
    if valid:
        assert decode(canonical_json_bytes(raw), doc).rows[0].links[0].value.value == value
    else:
        rejects(doc, raw)


def test_map_limits(example):
    doc, raw = example
    row = raw["rows"][0]
    row["links"] = [dict(deepcopy(row["links"][0]), link_id=f"link-{i}") for i in range(64)]
    assert len(decode(canonical_json_bytes(raw), doc).rows[0].links) == 64
    row["links"].append(dict(row["links"][0], link_id="link-64"))
    rejects(doc, raw)
    payload = canonical_json_bytes(map_for(doc))
    maximum = 25*1024*1024
    assert decode(payload+b' '*(maximum-len(payload)), doc).map_id == "synthetic-map"
    rejects(doc, payload+b' '*(maximum-len(payload)+1))


def test_draft_is_preserved_as_draft(example):
    doc, _ = example
    raw = map_for(doc, reviewed=False)
    mapping = decode(canonical_json_bytes(raw), doc)
    assert mapping.rows[0].review_state == "draft"
    assert mapping.rows[0].reviewed_by is None
