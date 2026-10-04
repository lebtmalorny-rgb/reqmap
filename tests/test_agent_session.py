"""Session progress is tied to the source, revision and accepted proposals."""
import importlib
from dataclasses import replace
import pytest
from tests.agent_support import make_agent_config


def service(tmp_path):
    assert importlib.util.find_spec('reqmap.agent_service'), 'session service is missing'
    from reqmap.agent_service import AgentService
    return AgentService(make_agent_config(tmp_path))


def start(svc, texts=('Первое', 'Второе')):
    reply = svc.call('reqmap_start_session', {'request_id':'start', 'source':{'kind':'texts','texts':list(texts)}})
    assert reply.ok, reply
    return reply.data['session_id']


def atoms(svc, sid, text='Первое', request='atoms', revision=0, requirement_id='REQ-0001'):
    return svc.call('reqmap_submit_atoms', dict(session_id=sid, requirement_id=requirement_id, request_id=request, expected_revision=revision,
        proposal={'atoms':[dict(text='non-authoritative label',source_quote=text,mandatory=True)]}))


def test_session_restores_source_atoms_and_decomposition_context(tmp_path):
    svc = service(tmp_path)
    sid = start(svc)
    first = svc.call('reqmap_get_session', {'session_id':sid})
    assert first.data['revision'] == 0
    assert first.data['pending_requirements_count'] == 2
    assert first.data['requirements'][0]['decomposition_context']['requirement_text'] == 'Первое'
    assert atoms(svc,sid).data['revision'] == 1
    reopened = type(svc)(svc.config)
    got = reopened.call('reqmap_get_session', {'session_id':sid,'requirement_id':'REQ-0001'})
    row = got.data['requirements'][0]
    assert row['atoms'][0]['text'] == row['atoms'][0]['source_quote'] == 'Первое'
    assert row['atoms'][0]['atom_id'] == 'REQ-0001-A001'
    assert got.data['revision'] == 1


def test_rejected_replacement_preserves_last_accepted_atoms(tmp_path):
    svc = service(tmp_path); sid = start(svc)
    assert atoms(svc,sid).ok
    invalid = atoms(svc,sid,'foreign','bad',1)
    assert invalid.error.code == 'PROPOSAL_INVALID'
    assert invalid.data['revision'] == 1
    accepted = atoms(svc,sid,'Пер','new',1)
    assert accepted.data['revision'] == 2
    unknown = atoms(svc,sid,'Второе','unknown',2,'REQ-9999')
    assert unknown.error.code == 'REQUIREMENT_NOT_FOUND'


def test_old_cursor_cannot_mix_different_revisions(tmp_path):
    svc = service(tmp_path); sid = start(svc)
    first = svc.call('reqmap_get_session', {'session_id':sid,'page_size':1})
    cursor = first.data['next_cursor']
    next_page = svc.call('reqmap_get_session', {'session_id':sid,'page_size':1,'cursor':cursor})
    assert next_page.data['requirements'][0]['requirement']['text'] == 'Второе'
    assert atoms(svc,sid).ok
    stale = svc.call('reqmap_get_session', {'session_id':sid,'page_size':1,'cursor':cursor})
    assert stale.error.code == 'CURSOR_STALE'


def test_atom_limit_and_unknown_arguments_fail_closed(tmp_path):
    svc = service(tmp_path); sid = start(svc, (' '.join(f'word{i}' for i in range(65)),))
    proposal = {'atoms':[dict(text=f'word{i}',source_quote=f'word{i}',mandatory=True) for i in range(65)]}
    reply = svc.call('reqmap_submit_atoms', dict(session_id=sid,requirement_id='REQ-0001',request_id='too-many',expected_revision=0,proposal=proposal))
    assert reply.error.code == 'ATOM_LIMIT'
    assert svc.call('reqmap_get_session', {'session_id':sid,'output':'/tmp/arbitrary'}).error.code == 'TOOL_ARGUMENTS'
    assert svc.call('reqmap_get_session', {'session_id':sid,'page_size':True}).error.code == 'TOOL_ARGUMENTS'
    assert svc.call('reqmap_get_session', {'session_id':sid}).data['revision'] == 0


def test_input_preflight_failure_leaves_no_active_session(tmp_path):
    svc = service(tmp_path)
    reply = svc.call('reqmap_start_session', {'request_id':'empty','source':{'kind':'texts','texts':[]}})
    assert not reply.ok
    assert 'session_id' not in reply.data
