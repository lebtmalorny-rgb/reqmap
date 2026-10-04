"""The same evidence gate must protect external proposals and model loops."""
from dataclasses import replace
import pytest
from reqmap import decomposition, mapping, deep_mapping
from reqmap.models import SupportStatus, EvidencePolarity
from tests.factories import requirement, atom
from tests.deep_factories import immutable_v2_kb, deep_candidate, deep_mapping_response, with_nova_evidence
from tests.test_deep_mapping import FakeModel
from tests.test_mapping import kb, candidate, raw_mapping, response, atom as legacy_atom


def test_decomposition_keeps_exact_quote_and_rejects_foreign_text():
    req = requirement()
    raw = {"atoms": [{"text": "invented label", "source_quote": req.text, "mandatory": True}]}
    assert hasattr(decomposition, "accept_decomposition")
    actual = decomposition.accept_decomposition(req, raw)
    assert actual[0].text == "Synthetic requirement"
    assert actual == decomposition.decompose(FakeModel([raw]), req, None).atoms
    assert decomposition.prepare_decomposition(req, "parent")["parent_text"] == "parent"
    from reqmap.proposals import ProposalError
    raw["atoms"][0]["source_quote"] = "foreign quote"
    with pytest.raises(ProposalError) as failure:
        decomposition.accept_decomposition(req, raw)
    assert failure.value.kind == "shape"


def test_legacy_proposal_uses_same_gate_and_ids(kb):
    assert hasattr(mapping, "accept_mapping")
    a, candidates, raw = legacy_atom(), (candidate("nova", "E-NOVA"),), response(raw_mapping())
    result = mapping.accept_mapping(a, candidates, kb, raw)
    assert result == mapping.map_atom(FakeModel([raw]), a, candidates, kb)
    assert result.support_status is SupportStatus.SUPPORTED
    assert result.mappings[0].mapping_id == "REQ-0001-A001-M001"
    assert mapping.prepare_mapping(a, candidates, kb)["atom"]["text"] == a.text
    from reqmap.proposals import ProposalError
    raw["mappings"][0]["evidence_ids"] = ["foreign"]
    with pytest.raises(ProposalError):
        mapping.accept_mapping(a, candidates, kb, raw)


def test_deep_proposal_restores_conflict_and_classifies_errors(tmp_path):
    assert hasattr(deep_mapping, "accept_deep_mapping")
    original = immutable_v2_kb(tmp_path)
    kb = with_nova_evidence(original, "EV-NOVA-NEGATIVE", polarity=EvidencePolarity.NEGATIVE)
    retrieval = deep_candidate(kb, "EV-NOVA-CREATE", "EV-NOVA-NEGATIVE")
    raw = deep_mapping_response()
    actual = deep_mapping.accept_deep_mapping(atom(), retrieval, kb, raw)
    assert actual == deep_mapping.map_atom_deep(FakeModel([raw]), atom(), retrieval, kb)
    assert actual.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert set(actual.responsibility_records[0].evidence_ids) == {"EV-NOVA-CREATE", "EV-NOVA-NEGATIVE"}
    assert deep_mapping.prepare_deep_mapping(atom(), retrieval, kb)["atom"]
    from reqmap.proposals import ProposalError
    with pytest.raises(ProposalError) as shape:
        deep_mapping.accept_deep_mapping(atom(), retrieval, kb, [])
    assert shape.value.kind == "shape"
    raw["responsibilities"][0]["evidence_ids"] = ["foreign"]
    with pytest.raises(ProposalError) as semantic:
        deep_mapping.accept_deep_mapping(atom(), retrieval, kb, raw)
    assert semantic.value.kind == "semantic"
