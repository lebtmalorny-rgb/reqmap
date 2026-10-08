"""Reviewed annotations constrain all atoms; never infer a parent relationship."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib

import pytest

from reqmap.binding_source import bind_source
from reqmap.config import AnalysisProfile
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.source_context import capture_source_document, load_source_context_map
from tests.source_context_support import API_TEXT, DETACHED_TEXT, text_document, map_for, link_for


def resolve(doc, raw, tmp_path):
    module = importlib.import_module('reqmap.source_context')
    assert hasattr(module, 'resolve_source_context'), 'source context resolver is missing'
    path = None
    if raw is not None:
        path = tmp_path/'map.json'
        path.write_bytes(canonical_json_bytes(raw))
    loaded = load_source_context_map(path, doc, profile=AnalysisProfile.LEGACY, trust=None)
    return module.resolve_source_context(doc, loaded)


def make(texts=(DETACHED_TEXT, 'GUI')):
    return capture_source_document(**text_document(texts))


@pytest.mark.parametrize('kind,state,code', [('missing','unreviewed','SOURCE_CONTEXT_UNREVIEWED'), ('draft','unreviewed','SOURCE_CONTEXT_UNREVIEWED'), ('unresolved','unresolved','SOURCE_CONTEXT_UNRESOLVED')])
def test_missing_draft_and_unresolved_have_no_admissible_context(tmp_path, kind, state, code):
    doc = make()
    raw = None if kind == 'missing' else map_for(doc, disposition='unresolved' if kind=='unresolved' else 'independent', reviewed=kind!='draft')
    decision = resolve(doc, raw, tmp_path)[0]
    assert decision.state == state
    assert {i.code for i in decision.diagnostics} == {code}
    assert not decision.applied_links


def test_links_apply_to_all_own_atoms_without_rewriting_quotes(tmp_path):
    doc = make(('Nova должна создавать ВМ; Neutron должна создавать порт', 'GUI'))
    own = bind_source(doc.requirements[0]).obligations
    assert len(own) == 2
    raw = map_for(doc, disposition='linked', links=(link_for(doc),))
    decisions = resolve(doc, raw, tmp_path)
    decision = decisions[0]
    assert decision.state == 'linked'
    assert len(decisions) == 2  # Origin is still a separately analyzed row.
    assert decisions[1].state == 'unreviewed'
    for original, effective in zip(own, decision.effective_obligations, strict=True):
        assert effective.obligation == replace(original, interface='gui')
        assert effective.interface_refs[0].span.quote == 'GUI'


@pytest.mark.parametrize('kind', ['own-api','two-interfaces','duration'])
def test_conflicts_do_not_choose_nearest_or_stronger_condition(tmp_path, kind):
    text = API_TEXT if kind=='own-api' else DETACHED_TEXT
    if kind=='duration': text += ' за одну миллисекунду'
    doc = make((text, 'GUI', 'API', 'За две миллисекунды'))
    links = [link_for(doc)]
    if kind=='two-interfaces': links.append(link_for(doc, 2, value='api', link_id='api'))
    if kind=='duration': links = [link_for(doc,3,field='constraint',value=dict(name='duration',operator='within',value='2',unit='ms'))]
    decision = resolve(doc,map_for(doc,disposition='linked',links=links),tmp_path)[0]
    assert decision.state == 'conflict'
    assert {d.code for d in decision.diagnostics} == {'SOURCE_CONTEXT_CONFLICT'}
    assert all(d.source_refs for d in decision.diagnostics)


def test_constraints_deduplicate_semantics_and_preserve_all_origins(tmp_path):
    doc = make((DETACHED_TEXT, 'При отказе узла', 'С GPU', 'Ещё при отказе узла'))
    failure = dict(name='host_failure',operator='eq',value='true',unit=None)
    gpu = dict(name='gpu',operator='eq',value='true',unit=None)
    links = [link_for(doc, i, field='constraint',value=value,link_id=str(i)) for i,value in ((1,failure),(2,gpu),(3,failure))]
    decision = resolve(doc,map_for(doc,disposition='linked',links=links),tmp_path)[0]
    effective = decision.effective_obligations[0]
    assert {c.name for c in effective.obligation.constraints} == {'host_failure','gpu'}
    assert all(not c.source_spans for c in effective.obligation.constraints)
    assert len(next(c for c in effective.constraint_refs if c.semantic_key[0]=='host_failure').source_refs) == 2


def test_no_transitive_or_group_id_inheritance(tmp_path):
    doc = make((DETACHED_TEXT, 'API', 'GUI'))
    raw = map_for(doc, disposition='linked', links=(link_for(doc,value='api'),))
    raw['rows'] += map_for(doc,target=1,disposition='linked',links=(link_for(doc,2,link_id='grandparent'),))['rows']
    first = resolve(doc,raw,tmp_path)[0]
    assert first.state == 'linked'
    assert first.effective_obligations[0].obligation.interface == 'api'
    raw['rows'][0]['links'].append(link_for(doc,2,link_id='direct-grandparent'))
    assert resolve(doc,raw,tmp_path)[0].state == 'conflict'


@pytest.mark.parametrize('kind', ['self','cycle','draft','unresolved'])
def test_cycle_and_self_link_are_preflight_errors(tmp_path, kind):
    doc = make()
    raw = map_for(doc, disposition='linked', links=(link_for(doc,0 if kind=='self' else 1),))
    if kind!='self':
        raw['rows'] += map_for(doc,target=1,disposition='unresolved' if kind=='unresolved' else 'linked',reviewed=kind!='draft',links=(link_for(doc,0,link_id='back'),))['rows']
    if kind in ('self','cycle'):
        with pytest.raises(ReqmapError) as error: resolve(doc,raw,tmp_path)
        assert error.value.code == 'SOURCE_CONTEXT_CYCLE'
    else:
        assert resolve(doc,raw,tmp_path)[0].state == 'linked'


def test_context_identity_covers_annotation_and_code(tmp_path, monkeypatch):
    import reqmap.source_context as module
    doc = make()
    raw = map_for(doc,disposition='linked',links=(link_for(doc),))
    first = resolve(doc,raw,tmp_path)[0]
    for changes in ({'reason_ru':'Другая проверка'}, {'reviewed_by':'other-reviewer'}):
        changed=deepcopy(raw);changed['rows'][0].update(changes)
        assert resolve(doc,changed,tmp_path)[0].context_id != first.context_id
    changed=deepcopy(raw);changed['rows'][0]['links'][0]['value']='api'
    assert resolve(doc,changed,tmp_path)[0].context_id != first.context_id
    monkeypatch.setattr(module,'source_context_resolver_sha256',lambda:'0'*64)
    assert resolve(doc,raw,tmp_path)[0].context_id != first.context_id


def test_own_unparsed_source_stays_unparsed_with_reviewed_link(tmp_path):
    doc = make(('Вымышленное действие над неизвестным объектом', 'API'))
    raw = map_for(doc,disposition='linked',links=(link_for(doc,value='api'),))
    own=bind_source(doc.requirements[0]).obligations[0]
    effective=resolve(doc,raw,tmp_path)[0].effective_obligations[0].obligation
    assert effective.parse_state == own.parse_state == 'unresolved'
    assert effective.source_quote == own.source_quote
