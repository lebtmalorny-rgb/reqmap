from dataclasses import replace
import pytest
from tests.test_agent_session import service, start, atoms


def prepared(tmp_path):
    svc = service(tmp_path)
    sid = start(svc, ('создание виртуальной машины через Nova REST API',))
    assert atoms(svc,sid,'создание виртуальной машины через Nova REST API').ok
    reply = svc.call('reqmap_get_atom_context', {'session_id':sid,'atom_id':'REQ-0001-A001'})
    assert reply.ok, reply
    return svc, sid, reply.data


def test_context_is_bound_to_exact_atom_and_restored_after_restart(tmp_path):
    svc, sid, context = prepared(tmp_path)
    assert len(context['context_id']) == 64
    assert context['payload']['atom']['text'] == 'создание виртуальной машины через Nova REST API'
    assert context['rules']
    assert type(svc)(svc.config).call('reqmap_get_atom_context', {'session_id':sid,'atom_id':'REQ-0001-A001'}).data['context_id'] == context['context_id']
    assert atoms(svc,sid,'Nova REST API','replace',1).ok
    changed = svc.call('reqmap_get_atom_context', {'session_id':sid,'atom_id':'REQ-0001-A001'})
    assert changed.data['context_id'] != context['context_id']


def test_search_is_advisory_and_evidence_is_id_scoped(tmp_path):
    svc, sid, context = prepared(tmp_path)
    found = svc.call('reqmap_search_knowledge', {'session_id':sid,'query':'Nova','page_size':1})
    assert found.ok, found
    assert found.data['authoritative_for_mapping'] is False
    eid = context['payload']['candidates'][0]['evidence_ids'][0]
    evidence = svc.call('reqmap_get_evidence', {'session_id':sid,'evidence_id':eid})
    assert evidence.ok
    assert evidence.data['evidence']['evidence_id'] == eid
    assert evidence.data['excerpt']
    assert svc.call('reqmap_get_evidence', {'session_id':sid,'evidence_id':'../../etc/passwd'}).error.code == 'EVIDENCE_NOT_FOUND'
