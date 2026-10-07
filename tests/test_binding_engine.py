"""Whole-obligation proof is an intersection, never a bag of matching words."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from reqmap.binding_models import BoundConstraint
from reqmap.binding_source import bind_source
from reqmap.models import EvidencePolarity, SupportStatus
from tests.binding_factories import loaded_catalog
from tests.factories import requirement
from tests.test_mapping import kb


def obligation(text="Nova должна создавать ВМ через API"):
    return bind_source(replace(requirement(), text=text)).obligations[0]


def decide(source, catalog, prior=SupportStatus.SUPPORTED, ids=None, evidence=("E-NOVA",)):
    from reqmap.binding_engine import check_binding
    return check_binding(source, tuple(catalog.predicates) if ids is None else ids,
                         catalog, prior, evidence)


@pytest.mark.parametrize("text,code", [
    ("Nova должна удалять ВМ через API", "SOURCE_ACTION_UNPROVEN"),
    ("Nova должна создавать пользователя через API", "SOURCE_OBJECT_UNPROVEN"),
    ("Nova не должна создавать ВМ через API", "SOURCE_DIRECTION_UNPROVEN"),
    ("Nova должна создавать ВМ через GUI", "SOURCE_INTERFACE_UNPROVEN"),
    ("Nova должна создавать ВМ через API за одну миллисекунду", "SOURCE_CONDITION_UNPROVEN"),
    ("Nova должна создавать ВМ через API при отказе узла", "SOURCE_CONDITION_UNPROVEN"),
    ("Nova должна создавать ВМ через API с GPU", "SOURCE_CONDITION_UNPROVEN"),
    ("Nova должна создавать ВМ через API кроме тестовых", "SOURCE_UNPARSED"),
])
def test_each_source_dimension_must_be_proven(tmp_path, kb, text, code):
    result = decide(obligation(text), loaded_catalog(tmp_path, kb))
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert code in {d.code for d in result.diagnostics}
    assert result.uncovered


def test_no_union_of_create_api_and_delete_gui(tmp_path, kb):
    catalog = loaded_catalog(tmp_path, kb)
    p = next(iter(catalog.predicates.values()))
    q = replace(p, predicate_id="P-DELETE-GUI", action="delete", interface="gui")
    catalog = replace(catalog, predicates=MappingProxyType({p.predicate_id: p, q.predicate_id: q}))
    result = decide(obligation("Nova должна создавать ВМ через GUI"), catalog)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE


def test_conditional_fact_cannot_prove_unconditional_claim(tmp_path, kb):
    catalog = loaded_catalog(tmp_path, kb)
    p = next(iter(catalog.predicates.values()))
    p = replace(p, assumptions=(BoundConstraint("gpu", "eq", "true"),))
    catalog = replace(catalog, predicates=MappingProxyType({p.predicate_id: p}))
    assert decide(obligation(), catalog).support_status is SupportStatus.INSUFFICIENT_EVIDENCE


def test_foreign_version_is_not_negative_proof(tmp_path, kb):
    catalog = loaded_catalog(tmp_path, kb)
    p = next(iter(catalog.predicates.values()))
    p = replace(p, release_scope="2024.1", polarity=EvidencePolarity.NEGATIVE)
    catalog = replace(catalog, predicates=MappingProxyType({p.predicate_id: p}))
    result = decide(obligation(), catalog, SupportStatus.NOT_SUPPORTED)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "EVIDENCE_VERSION_MISMATCH" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize("prior", list(SupportStatus))
def test_binding_never_promotes_prior_evidence_status(tmp_path, kb, prior):
    result = decide(obligation(), loaded_catalog(tmp_path, kb), prior)
    expected = prior if prior in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL} else SupportStatus.INSUFFICIENT_EVIDENCE
    assert result.support_status is expected


@pytest.mark.parametrize("ids,evidence,code", [
    ((), ("E-NOVA",), "BINDING_UNREVIEWED"),
    (("P-UNKNOWN",), ("E-NOVA",), "BINDING_UNREVIEWED"),
    (("P-NOVA-CREATE",), (), "EVIDENCE_MISSING"),
])
def test_predicates_require_selected_evidence(tmp_path, kb, ids, evidence, code):
    result = decide(obligation(), loaded_catalog(tmp_path, kb), ids=ids, evidence=evidence)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert code in {d.code for d in result.diagnostics}


def test_negative_and_conflict_require_exact_complete_predicate(tmp_path, kb):
    catalog = loaded_catalog(tmp_path, kb)
    p = next(iter(catalog.predicates.values()))
    q = replace(p, predicate_id="P-NEG", polarity=EvidencePolarity.NEGATIVE)
    negative = replace(catalog, predicates=MappingProxyType({q.predicate_id: q}))
    assert decide(obligation(), negative, SupportStatus.NOT_SUPPORTED).support_status is SupportStatus.NOT_SUPPORTED
    conflict = replace(catalog, predicates=MappingProxyType({p.predicate_id: p, q.predicate_id: q}))
    result = decide(obligation(), conflict)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "EVIDENCE_CONFLICT" in {d.code for d in result.diagnostics}


def test_missing_catalog_is_insufficient():
    from reqmap.binding_engine import check_binding
    result = check_binding(obligation(), (), None, SupportStatus.SUPPORTED, ("E-NOVA",))
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert "BINDING_CATALOG_MISSING" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize("duration,expected", [("1", SupportStatus.SUPPORTED), ("2", SupportStatus.INSUFFICIENT_EVIDENCE)])
def test_conditions_require_exact_values_without_numeric_inference(tmp_path, kb, duration, expected):
    catalog = loaded_catalog(tmp_path, kb)
    p = next(iter(catalog.predicates.values()))
    p = replace(p, constraints=(BoundConstraint("duration", "within", duration, "ms"),))
    catalog = replace(catalog, predicates=MappingProxyType({p.predicate_id: p}))
    result = decide(obligation("Nova должна создавать ВМ через API за одну миллисекунду"), catalog)
    assert result.support_status is expected


@pytest.mark.parametrize('selected', [('P-NOVA-CREATE',), ('P-NEG',)])
def test_unselected_opposite_predicate_cannot_hide_conflict(tmp_path, kb, selected):
    catalog = loaded_catalog(tmp_path, kb)
    p = next(iter(catalog.predicates.values()))
    q = replace(p, predicate_id='P-NEG', polarity=EvidencePolarity.NEGATIVE, evidence_ids=('E-NEG',))
    catalog = replace(catalog, predicates=MappingProxyType({p.predicate_id:p,q.predicate_id:q}))
    prior = SupportStatus.SUPPORTED if selected[0] == p.predicate_id else SupportStatus.NOT_SUPPORTED
    result = decide(obligation(), catalog, prior, selected, ('E-NOVA','E-NEG'))
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert 'EVIDENCE_CONFLICT' in {d.code for d in result.diagnostics}
    assert set(result.predicate_ids) == {p.predicate_id,q.predicate_id}


@pytest.mark.parametrize('direction,polarity,prior', [
    ('capability', EvidencePolarity.NEGATIVE, SupportStatus.NOT_SUPPORTED),
    ('prohibition', EvidencePolarity.POSITIVE, SupportStatus.SUPPORTED),
])
def test_interface_specific_proof_cannot_establish_general_negative_or_prohibition(tmp_path, kb, direction, polarity, prior):
    catalog = loaded_catalog(tmp_path, kb)
    p = replace(next(iter(catalog.predicates.values())), direction=direction, polarity=polarity)
    catalog = replace(catalog,predicates=MappingProxyType({p.predicate_id:p}))
    text = 'Nova '+('не ' if direction == 'prohibition' else '')+'должна создавать ВМ'
    result = decide(obligation(text),catalog,prior)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert 'SOURCE_INTERFACE_UNPROVEN' in {d.code for d in result.diagnostics}


@pytest.mark.parametrize('guarantee', [False, True])
def test_assumed_metric_is_not_guaranteed_even_if_repeated_in_constraints(tmp_path, kb, guarantee):
    catalog=loaded_catalog(tmp_path,kb)
    metric=BoundConstraint('duration','within','1','ms')
    p=replace(next(iter(catalog.predicates.values())), assumptions=(metric,), constraints=(metric,) if guarantee else ())
    catalog=replace(catalog,predicates=MappingProxyType({p.predicate_id:p}))
    result=decide(obligation('Nova должна создавать ВМ через API за одну миллисекунду'),catalog)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert 'SOURCE_CONDITION_UNPROVEN' in {d.code for d in result.diagnostics}
