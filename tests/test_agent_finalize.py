import hashlib
import json
from pathlib import Path
from dataclasses import replace
import pytest
from reqmap.agent_service import AgentService
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from tests.agent_support import make_agent_config
from tests.test_agent_session import service, start, atoms
from tests.test_agent_context import prepared
from tests.test_agent_mapping import submit
from tests.test_acceptance import _mapping_response
from tests.deep_acceptance_support import gold_snapshot, scripted_response


def finalize(svc, sid, revision=2, partial=False, request='finish'):
    return svc.call('reqmap_finalize', dict(session_id=sid, request_id=request,
        expected_revision=revision, allow_partial=partial))


def finished(svc, sid, text):
    assert atoms(svc,sid,text).ok
    context = svc.call('reqmap_get_atom_context',dict(session_id=sid,atom_id='REQ-0001-A001')).data
    proposal = scripted_response(context['payload']) if svc.config.analysis_profile is AnalysisProfile.DEEP else _mapping_response(text)
    result = submit(svc,sid,context,proposal)
    assert result.ok, result


def test_full_finalize_is_immutable_idempotent_and_detects_tampering(tmp_path):
    svc,sid,context = prepared(tmp_path)
    assert submit(svc,sid,context,_mapping_response('создание виртуальной машины через Nova REST API')).ok
    reply = finalize(svc,sid)
    assert reply.ok, reply
    paths = {k:Path(v) for k,v in reply.data['artifacts'].items()}
    assert set(paths) == {'result.json','result.xlsx','report.md','manifest.json','run.jsonl'}
    original = {k:p.read_bytes() for k,p in paths.items()}
    assert finalize(AgentService(svc.config),sid) == reply
    result = svc.call('reqmap_get_result',dict(session_id=sid,page_size=1))
    assert result.ok and result.data['run_status'] == 'SUCCESS'
    assert {k:p.read_bytes() for k,p in paths.items()} == original
    data = json.loads(original['result.json'])
    assert data['metadata']['analysis_origin']['revision'] == 3
    paths['result.xlsx'].write_bytes(b'tampered')
    broken = svc.call('reqmap_get_result',dict(session_id=sid))
    assert broken.error.code == 'ARTIFACTS_CHANGED'
    assert not broken.data


@pytest.mark.parametrize('deep',[False,True])
@pytest.mark.parametrize('completed',[False,True])
def test_partial_preserves_all_source_rows_and_never_claims_success(tmp_path,deep,completed):
    config = make_agent_config(tmp_path)
    text = 'создание виртуальной машины через Nova REST API'
    if deep:
        kb,trust = gold_snapshot(tmp_path)
        config = replace(config,analysis_profile=AnalysisProfile.DEEP,knowledge_path=kb,knowledge_trust=KnowledgeTrustConfig(trust))
        text = 'Противоречивое создание сервера conflict'
    svc = AgentService(config)
    sid = start(svc,(text,'Не обработано'))
    before = svc.call('reqmap_get_result',dict(session_id=sid))
    assert before.ok and 'artifacts' not in before.data
    if completed:
        finished(svc,sid,text)
    revision = 2 if completed else 0
    blocked = finalize(svc,sid,revision)
    assert blocked.error.code == 'ANALYSIS_INCOMPLETE'
    assert not config.output_root.exists()
    reply = finalize(svc,sid,revision,True,'partial')
    assert reply.ok, reply
    data = json.loads(Path(reply.data['artifacts']['result.json']).read_text())
    assert len(data['requirements']) == 2
    assert data['run_status'] == ('PARTIAL' if completed else 'FAILED')
    assert data['requirements'][1]['analysis_state'] == 'skipped'
    assert data['requirements'][1]['support_status'] is None
    assert svc.store.read(sid).revision == revision+1


class Crash(BaseException):
    pass


@pytest.mark.parametrize('window',['before_rename','after_rename','before_receipt'])
def test_recovery_reuses_one_directory_and_blocks_intervening_mutations(tmp_path,monkeypatch,window):
    import reqmap.agent_finalize as module
    svc,sid,ctx = prepared(tmp_path)
    assert submit(svc,sid,ctx,_mapping_response('создание виртуальной машины через Nova REST API')).ok
    original_rename = module.os.rename
    original_complete = svc.store.complete_publication
    def rename(source,target):
        if window == 'before_rename':
            raise Crash()
        original_rename(source,target)
        raise Crash()
    def complete(*args,**kwargs):
        raise Crash()
    with monkeypatch.context() as patch:
        if window == 'before_receipt':
            patch.setattr(svc.store,'complete_publication',complete)
        else:
            patch.setattr(module.os,'rename',rename)
        with pytest.raises(Crash):
            finalize(svc,sid)
    blocked = atoms(svc,sid,'Nova REST API','intervening',2)
    assert blocked.error.code == 'PUBLICATION_PENDING'
    assert atoms(svc,sid,'Nova REST API','stale-intervening',0).error.code == 'PUBLICATION_PENDING'
    recovered = finalize(AgentService(svc.config),sid)
    assert recovered.ok, recovered
    assert finalize(svc,sid) == recovered
    assert svc.store.read(sid).revision == 3
    assert len(list(svc.config.output_root.iterdir())) == 1


def test_failed_crosscheck_returns_diagnostics_only_and_closes_session(tmp_path,monkeypatch):
    import reqmap.agent_finalize as module
    from reqmap.crosscheck import CrosscheckIssue
    svc,sid,ctx = prepared(tmp_path)
    assert submit(svc,sid,ctx,_mapping_response('создание виртуальной машины через Nova REST API')).ok
    monkeypatch.setattr(module,'publish_artifacts',lambda *a,**kw: (CrosscheckIssue('BROKEN','bad'),))
    result = finalize(svc,sid)
    assert result.error.code == 'PUBLICATION_FAILED'
    assert 'artifacts' not in result.data
    assert svc.store.read(sid).status == 'failed'
    assert finalize(svc,sid) == result


@pytest.mark.parametrize('text,expected',[
    ('Создание сервера через Nova API','SUCCESS'),
    ('Восстановить HA виртуальную машину после отказа.','PARTIAL'),
    ('Противоречивое создание сервера conflict','PARTIAL'),
])
def test_deep_finalization_uses_procedure_and_conflict_gates(tmp_path,text,expected):
    kb,trust = gold_snapshot(tmp_path)
    config = replace(make_agent_config(tmp_path),analysis_profile=AnalysisProfile.DEEP,
        knowledge_path=kb,knowledge_trust=KnowledgeTrustConfig(trust))
    svc = AgentService(config); sid = start(svc,(text,)); finished(svc,sid,text)
    result = finalize(svc,sid)
    if expected != 'SUCCESS':
        assert result.error.code == 'ANALYSIS_INCOMPLETE'
        result = finalize(svc,sid,partial=True,request='partial')
    assert result.ok, result
    assert result.data['run_status'] == expected
