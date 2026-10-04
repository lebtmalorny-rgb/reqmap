"""Persistence failures must never acknowledge uncommitted or duplicate work."""
import importlib
import multiprocessing
from dataclasses import replace
import json
import os
import sqlite3
import pytest
from reqmap.errors import ReqmapError
from reqmap.agent_input import import_agent_input
from reqmap.agent_knowledge import load_agent_knowledge
from tests.agent_support import make_agent_config


def store_setup(tmp_path):
    assert importlib.util.find_spec('reqmap.agent_store'), 'durable store is missing'
    from reqmap.agent_store import SessionStore
    from reqmap.agent_types import SessionSeed, SessionSettings
    config = make_agent_config(tmp_path)
    kb = load_agent_knowledge(config)
    seed = SessionSeed(import_agent_input({'kind':'texts','texts':['Первое']}, config), SessionSettings(config.analysis_profile, 8, None, kb.knowledge_sha256, None, '1.0', '1.0'), (), None, 'unknown')
    store = SessionStore(config.session_root)
    reply = store.create('start-1', {'source':'original'}, lambda:seed)
    return store, reply.data['session_id'], seed, reply


def accepted(record):
    from reqmap.agent_types import MutationDecision, ToolReply
    return MutationDecision(True, ToolReply(True, {'accepted':True}, None), 'active')


def command(sid, request='change-1', revision=0, value='one'):
    from reqmap.agent_types import MutationCommand
    return MutationCommand(sid, request, revision, 'reqmap_submit_atoms', {'value':value})


def test_creation_receipt_does_not_reload_deleted_input(tmp_path):
    store, sid, seed, reply = store_setup(tmp_path)
    def unavailable():
        raise AssertionError('immutable input must not be re-read')
    assert store.create('start-1', {'source':'original'}, unavailable) == reply
    assert store.create('start-1', {'source':'changed'}, unavailable).error.code == 'REQUEST_ID_REUSED'
    assert store.read(sid).seed.input_snapshot.content == seed.input_snapshot.content


def test_replay_receipt_precedes_revision_check_and_survives_restart(tmp_path):
    store, sid, _, _ = store_setup(tmp_path)
    first = store.transact(command(sid), accepted)
    from reqmap.agent_store import SessionStore
    reopened = SessionStore(store.root)
    assert reopened.transact(command(sid), accepted) == first
    assert reopened.read(sid).revision == 1
    assert reopened.transact(command(sid, value='two'), accepted).error.code == 'REQUEST_ID_REUSED'
    assert reopened.transact(command(sid, 'stale'), accepted).error.code == 'REVISION_CONFLICT'
    assert reopened.read(sid).revision == 1


def test_rejected_attempt_is_durable_without_changing_revision(tmp_path):
    store, sid, _, _ = store_setup(tmp_path)
    from reqmap.agent_types import MutationDecision, ToolReply, ToolError
    reject = lambda r: MutationDecision(False, ToolReply(False, {}, ToolError('PROPOSAL_INVALID','Ошибка',{})), 'active')
    reply = store.transact(command(sid), reject)
    assert not reply.ok
    assert store.read(sid).revision == 0
    assert len(store.read(sid).events) == 1
    assert store.transact(command(sid), accepted) == reply


def writer(root, sid, request, queue):
    from reqmap.agent_store import SessionStore
    queue.put(SessionStore(root).transact(command(sid, request), accepted).ok)


def test_two_processes_can_only_accept_one_revision(tmp_path):
    store, sid, _, _ = store_setup(tmp_path)
    ctx = multiprocessing.get_context('spawn')
    q = ctx.Queue()
    procs = [ctx.Process(target=writer, args=(store.root,sid,f'worker-{i}',q)) for i in range(2)]
    for p in procs: p.start()
    for p in procs:
        p.join(10)
        assert p.exitcode == 0
    assert sorted([q.get(timeout=2),q.get(timeout=2)]) == [False,True]
    assert store.read(sid).revision == 1


def crashing_writer(root, sid):
    from reqmap.agent_store import SessionStore
    def crash(record):
        os._exit(31)
    SessionStore(root).transact(command(sid), crash)


def test_crash_releases_lock_without_half_transaction(tmp_path):
    store, sid, _, _ = store_setup(tmp_path)
    p = multiprocessing.get_context('spawn').Process(target=crashing_writer,args=(store.root,sid))
    p.start(); p.join(10)
    assert p.exitcode == 31
    assert store.read(sid).revision == 0
    assert store.transact(command(sid), accepted).ok


@pytest.mark.parametrize('field', ['seed', 'event', 'version'])
def test_state_tampering_is_rejected(tmp_path, field):
    store, sid, _, _ = store_setup(tmp_path)
    store.transact(command(sid), accepted)
    with sqlite3.connect(store.root / 'sessions.sqlite3') as db:
        if field == 'seed': db.execute('UPDATE sessions SET seed_json=? WHERE session_id=?', ('{}',sid))
        elif field == 'event': db.execute('UPDATE events SET event_json=? WHERE session_id=?', ('{}',sid))
        else: db.execute('PRAGMA user_version=999')
    with pytest.raises(ReqmapError) as failure:
        store.read(sid)
    assert failure.value.code == 'SESSION_CORRUPT'
