"""The same source binding gate protects both public mapping acceptors."""
from dataclasses import replace

import pytest

from reqmap.binding_catalog import load_binding_catalog
from reqmap.binding_models import BindingContext
from reqmap.binding_source import bind_source, canonical_atoms
from reqmap.models import EvidenceClaimScope, SupportStatus
from reqmap.retrieval import retrieve
from tests.binding_factories import deep_knowledge, loaded_catalog, sign_catalog, write_catalog
from tests.deep_factories import deep_candidate, deep_mapping_response
from tests.factories import requirement
from tests.test_mapping import kb, raw_mapping, response


@pytest.fixture(params=["legacy", "deep"])
def setup_binding(request, tmp_path, kb):
    if request.param == "legacy":
        return kb, loaded_catalog(tmp_path, kb), response(raw_mapping())
    deep, signers, key = deep_knowledge(tmp_path)
    root = write_catalog(tmp_path / "bindings", deep)
    sign_catalog(root, key)
    return deep, load_binding_catalog(root, deep, signers), deep_mapping_response()


def submit(setup, text, *, context=True, catalog=True, proposal_changes=None):
    from reqmap.knowledge import KnowledgeBase
    from reqmap.mapping import accept_mapping
    from reqmap.deep_mapping import accept_deep_mapping
    knowledge, predicates, proposal = setup
    binding = bind_source(replace(requirement(), text=text))
    atom = canonical_atoms(binding)[0]
    proposal = {**proposal, "proposal_schema_version": 2, "obligation_id": atom.obligation_id,
                "predicate_ids": list(predicates.predicates) if catalog else []}
    proposal.update(proposal_changes or {})
    kwargs = {"binding_context": BindingContext(binding, predicates if catalog else None)} if context else {}
    if type(knowledge) is KnowledgeBase:
        result = accept_mapping(atom, retrieve(knowledge, text, (), 8), knowledge, proposal, **kwargs)
        records = result.mappings
    else:
        outcome = accept_deep_mapping(atom, deep_candidate(knowledge), knowledge, proposal, **kwargs)
        result, records = outcome.atom_result, outcome.responsibility_records
    return result, records


@pytest.mark.parametrize("text", [
    "Nova должна удалять ВМ через API",
    "Nova должна создавать пользователя через API",
    "Nova не должна создавать ВМ через API",
    "Nova должна создавать ВМ через API за одну миллисекунду",
    "Nova должна создавать ВМ через GUI",
    "Nova должна создавать ВМ через API при отказе узла",
    "Nova должна создавать ВМ через API с GPU",
    "Nova должна создавать ВМ через API кроме тестовых",
])
def test_specific_create_does_not_prove_other_obligation(setup_binding, text):
    result, records = submit(setup_binding, text)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert result.supported_aspects == ()
    assert all(r.support_status is SupportStatus.INSUFFICIENT_EVIDENCE for r in records)
    assert result.binding_decision.uncovered
    for record in records:
        assert "SOURCE_" in (getattr(record, "reason_ru", "") + " ".join(getattr(record, "diagnostics", ())))


@pytest.mark.parametrize("text", [
    "Nova должна создавать виртуальную машину через API",
    "Nova должна создавать ВМ через API",
    "Nova должна обеспечивать создание ВМ через API",
    "Nova должна создавать ВМ",
])
def test_supported_aspects_are_computed_from_obligations(setup_binding, text):
    result, records = submit(setup_binding, text)
    assert result.support_status is SupportStatus.SUPPORTED
    assert result.supported_aspects == (text,)
    assert result.binding_decision.predicate_ids == ("P-NOVA-CREATE",)
    assert not result.binding_decision.uncovered
    assert all(r.support_status is SupportStatus.SUPPORTED for r in records)


@pytest.mark.parametrize("context,catalog", [(False, True), (True, False)])
def test_no_context_or_catalog_blocks_public_python_calls(setup_binding, context, catalog):
    result, _ = submit(setup_binding, "Nova должна создавать ВМ через API", context=context, catalog=catalog)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE


def test_claimed_not_applicable_cannot_hide_obligation(setup_binding):
    knowledge, catalog, proposal = setup_binding
    empty = {k: [] for k in proposal if k in {"supported_aspects", "unconfirmed_aspects", "mappings", "responsibilities", "procedure_template_ids"}}
    empty["support_status"] = "not_applicable"
    result, _ = submit(setup_binding, "Nova должна создавать ВМ через API", proposal_changes=empty)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize("status", ["supported", "insufficient_evidence"])
def test_context_diagnostic_is_independent_of_proposed_status(tmp_path, kb, status):
    changed = dict(kb.evidence)
    changed["E-NOVA"] = replace(changed["E-NOVA"], claim_scope=EvidenceClaimScope.CONTEXT)
    changed_kb = replace(kb, evidence=changed)
    catalog = loaded_catalog(tmp_path, kb)
    proposal = response(raw_mapping(support_status=status), status=status)
    result, _ = submit((changed_kb, catalog, proposal), "Nova должна создавать ВМ через API", catalog=False)
    assert "EVIDENCE_CONTEXT_ONLY" in {d.code for d in result.binding_decision.diagnostics}


def test_binding_context_cannot_be_reused_for_another_source(setup_binding):
    from reqmap.mapping import accept_mapping
    from reqmap.deep_mapping import accept_deep_mapping
    from reqmap.knowledge import KnowledgeBase
    from reqmap.proposals import ProposalError
    knowledge, catalog, proposal = setup_binding
    original = bind_source(replace(requirement(), text="Nova должна создавать ВМ через API"))
    other = bind_source(replace(requirement(), text="Nova должна удалять ВМ через API"))
    atom = canonical_atoms(other)[0]
    proposal = {**proposal, "proposal_schema_version": 2, "obligation_id": atom.obligation_id,
                "predicate_ids": ["P-NOVA-CREATE"]}
    acceptor = accept_mapping if type(knowledge) is KnowledgeBase else accept_deep_mapping
    candidates = retrieve(knowledge, atom.text, (), 8) if type(knowledge) is KnowledgeBase else deep_candidate(knowledge)
    with pytest.raises(ProposalError, match="SOURCE_"):
        acceptor(atom, candidates, knowledge, proposal, binding_context=BindingContext(original, catalog))


def test_prepared_binding_context_exposes_only_selection_metadata(setup_binding):
    from reqmap.mapping import prepare_mapping
    from reqmap.deep_mapping import prepare_deep_mapping
    from reqmap.knowledge import KnowledgeBase
    knowledge, catalog, _ = setup_binding
    binding = bind_source(replace(requirement(), text="Nova должна создавать ВМ через API"))
    atom = canonical_atoms(binding)[0]
    prepare = prepare_mapping if type(knowledge) is KnowledgeBase else prepare_deep_mapping
    candidates = retrieve(knowledge, atom.text, (), 8) if type(knowledge) is KnowledgeBase else deep_candidate(knowledge)
    payload = prepare(atom, candidates, knowledge, binding_context=BindingContext(binding, catalog))
    assert payload["response_schema"]["obligation_id"] == atom.obligation_id
    assert payload["predicates"][0]["predicate_id"] == "P-NOVA-CREATE"
    for predicate in payload["predicates"]:
        assert not {"locators", "source_ids", "source_sha256s", "reviewed_by", "reviewed_at"}.intersection(predicate)


def test_unbound_additional_records_do_not_leave_supported_atom(tmp_path):
    from reqmap.deep_mapping import accept_deep_mapping
    from reqmap.deep_models import DeepRetrievalResult
    from tests.deep_factories import mixed_kolla_host_kb, responsibility_selection
    from tests.test_deep_mapping import _mixed_selection
    knowledge, signers, key = deep_knowledge(tmp_path)
    root = write_catalog(tmp_path/"bindings", knowledge)
    sign_catalog(root, key)
    catalog = load_binding_catalog(root, knowledge, signers)
    knowledge, other = mixed_kolla_host_kb(knowledge)
    candidates = DeepRetrievalResult((*deep_candidate(knowledge).normalized_candidates, *other.normalized_candidates), ())
    binding = bind_source(replace(requirement(), text="Nova должна создавать ВМ через API"))
    atom = canonical_atoms(binding)[0]
    raw = deep_mapping_response(responsibility_selection(),
        _mixed_selection("kolla_ansible", "kolla_ansible", [3]),
        _mixed_selection("host_os", "rocky_linux_9", [2]))
    raw.update(proposal_schema_version=2, obligation_id=atom.obligation_id, predicate_ids=["P-NOVA-CREATE"])
    outcome = accept_deep_mapping(atom, candidates, knowledge, raw, binding_context=BindingContext(binding, catalog))
    assert any(r.support_status is SupportStatus.INSUFFICIENT_EVIDENCE for r in outcome.responsibility_records)
    assert outcome.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize("hide_evidence", [False, True])
def test_loaded_legacy_catalog_conflict_cannot_be_hidden(tmp_path, kb, hide_evidence):
    from types import MappingProxyType
    from reqmap.models import EvidencePolarity
    from tests.binding_factories import predicate_raw
    ev=replace(kb.evidence['E-NOVA'],evidence_id='E-NEG',polarity=EvidencePolarity.NEGATIVE)
    knowledge=replace(kb,evidence=MappingProxyType({**kb.evidence,ev.evidence_id:ev}))
    p=predicate_raw(knowledge)
    q={**p,'predicate_id':'P-NEG','evidence_ids':['E-NEG'],'polarity':'negative'}
    catalog=load_binding_catalog(write_catalog(tmp_path/'catalog',knowledge,predicates=[p,q]),knowledge)
    raw=response(raw_mapping(evidence_ids=['E-NOVA'] if hide_evidence else ['E-NOVA','E-NEG']))
    result,_=submit((knowledge,catalog,raw),'Nova должна создавать ВМ через API',proposal_changes={'predicate_ids':['P-NOVA-CREATE']})
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert 'EVIDENCE_CONFLICT' in {d.code for d in result.binding_decision.diagnostics}
    assert 'E-NEG' in next(d.evidence_ids for d in result.binding_decision.diagnostics if d.code=='EVIDENCE_CONFLICT')
    from reqmap.binding_runtime import validate_persisted_binding
    source=bind_source(replace(requirement(),text='Nova должна создавать ВМ через API'))
    validate_persisted_binding(result,BindingContext(source,catalog),knowledge,result.mappings,SupportStatus.SUPPORTED)


def test_assumptions_do_not_cover_metric_in_either_public_acceptor(setup_binding):
    from types import MappingProxyType
    from reqmap.binding_models import BoundConstraint
    knowledge,catalog,raw=setup_binding
    p=replace(catalog.predicates['P-NOVA-CREATE'],assumptions=(BoundConstraint('duration','within','1','ms'),))
    catalog=replace(catalog,predicates=MappingProxyType({p.predicate_id:p}))
    result,_=submit((knowledge,catalog,raw),'Nova должна создавать ВМ через API за одну миллисекунду')
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
