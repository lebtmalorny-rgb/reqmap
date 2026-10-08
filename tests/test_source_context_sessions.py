"""Historical publications and frozen active context have different trust paths."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from reqmap.agent_service import AgentService
from reqmap.config import AnalysisProfile
from tests.agent_support import make_agent_config
from tests.test_agent_session import start
from tests.test_source_context_runtime import context_fixture


def test_active_session_uses_frozen_map_and_current_trust(tmp_path):
    doc,path,_=context_fixture(tmp_path,AnalysisProfile.LEGACY,'independent')
    config=replace(make_agent_config(tmp_path/'agent'),source_context_path=path)
    svc=AgentService(config)
    sid=start(svc,tuple(r.text for r in doc.requirements))
    snapshot=svc.store.read(sid).seed.source_context
    assert snapshot is not None
    path.unlink()
    reopened=AgentService(config)
    result=reopened.call('reqmap_get_session',dict(session_id=sid))
    assert result.ok,result
    assert reopened.store.read(sid).seed.source_context==snapshot
    new=reopened.call('reqmap_start_session',dict(request_id='new',source=dict(kind='texts',texts=[r.text for r in doc.requirements])))
    assert new.error.code=='SOURCE_CONTEXT_INVALID'


@pytest.mark.parametrize('profile',['legacy','deep','deep20'])
@pytest.mark.parametrize('active',[False,True])
def test_old_active_rejected_and_finalized_returned_historically(tmp_path,profile,active,monkeypatch):
    config=make_agent_config(tmp_path)
    svc=AgentService(config)
    fixture=Path(__file__).parent/'fixtures/historical_source_context'/profile
    saved=json.loads((fixture/'state.json').read_bytes())
    with svc.store._connect() as db:
        for table,rows in saved['tables'].items():
            if active and table!='sessions':continue
            for row in rows:
                if active:row[-2:]=[0,'active']
                db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in row)+')',row)
    sid=saved['session_id']
    if active:
        reply=svc.call('reqmap_get_session',dict(session_id=sid))
        assert reply.error.code=='SESSION_CONTRACT_MISMATCH'
    else:
        output=config.output_root/svc.store.publication(sid)['final_name']
        output.mkdir(parents=True)
        for name in ('result.json','result.xlsx','report.md','run.jsonl','manifest.json'):shutil.copyfile(fixture/name,output/name)
        def forbidden(*args,**kwargs):raise AssertionError('historical read must not resolve')
        monkeypatch.setattr('reqmap.source_context.resolve_source_context',forbidden)
        reply=svc.call('reqmap_get_result',dict(session_id=sid))
        assert reply.ok,reply
        assert reply.data['historical'] is True
        assert reply.data['schema_version']=={'legacy':'1.1','deep':'2.1','deep20':'2.0'}[profile]


def test_active_deep_session_rechecks_revoked_context_signer(tmp_path, monkeypatch):
    from reqmap.export_json import canonical_json_bytes
    from tests.source_context_support import map_for
    from tests.agent_support import make_agent_config
    from reqmap.agent_knowledge import load_agent_knowledge
    doc, path, trust = context_fixture(tmp_path, AnalysisProfile.DEEP, 'independent')
    config = make_agent_config(tmp_path / 'agent', AnalysisProfile.DEEP)
    # Knowledge verification is independent; isolate the context signer revocation.
    verified = load_agent_knowledge(config)
    monkeypatch.setattr('reqmap.agent_service.load_agent_knowledge', lambda config: verified)
    config = replace(config, source_context_path=path, knowledge_trust=trust)
    svc = AgentService(config)
    sid = start(svc, tuple(r.text for r in doc.requirements))
    path.unlink()
    Path(str(path)+'.sig').unlink()
    assert AgentService(config).call('reqmap_get_session', dict(session_id=sid)).ok
    trust.allowed_signers_path.write_text('')
    reply = AgentService(config).call('reqmap_get_session', dict(session_id=sid))
    assert not reply.ok
    assert reply.error.code == 'SOURCE_CONTEXT_UNTRUSTED'


def test_new_session_reads_new_map_but_active_keeps_original(tmp_path):
    from reqmap.export_json import canonical_json_bytes
    from tests.source_context_support import map_for
    doc, path, _ = context_fixture(tmp_path, AnalysisProfile.LEGACY, 'independent')
    config = replace(make_agent_config(tmp_path/'agent'), source_context_path=path)
    svc = AgentService(config)
    sid = start(svc, tuple(r.text for r in doc.requirements))
    old = svc.store.read(sid).seed.source_context
    path.write_bytes(canonical_json_bytes(map_for(doc, disposition='unresolved')))
    reopened = AgentService(config)
    assert reopened.call('reqmap_get_session', dict(session_id=sid)).ok
    assert reopened.store.read(sid).seed.source_context == old
    new = reopened.call('reqmap_start_session', dict(request_id='new', source=dict(kind='texts', texts=[r.text for r in doc.requirements])))
    assert new.ok, new
    snapshot = reopened.store.read(new.data['session_id']).seed.source_context
    assert snapshot.loaded.map_sha256 != old.loaded.map_sha256
    assert snapshot.decisions[0].state == 'unresolved'


@pytest.mark.parametrize('profile', list(AnalysisProfile))
def test_cli_checkpoint_uses_frozen_map_and_new_map_changes_run(tmp_path, profile):
    from tests.test_pipeline import config_for, FakeModel, valid_decomposition, not_applicable_mapping
    from tests.test_deep_pipeline import _signed_config, FakeModel as DeepModel, _completed_responses
    from reqmap.models import AnalysisRequest
    from reqmap.export_json import canonical_json_bytes
    from tests.source_context_support import API_TEXT, text_document, with_reviewed_request, map_for, sign_test_map
    from reqmap.source_context import capture_source_document
    from reqmap.pipeline import analyze
    from reqmap.deep_pipeline import analyze_deep
    doc = capture_source_document(**text_document((API_TEXT,)))
    request = AnalysisRequest(doc.requirements, doc.input_sha256, 'text', None, tmp_path/'output', source_document=doc)
    deep = profile is AnalysisProfile.DEEP
    config = with_reviewed_request(_signed_config(tmp_path) if deep else config_for(), request)
    runner = analyze_deep if deep else analyze
    model = (lambda: DeepModel(_completed_responses(API_TEXT))) if deep else (lambda: FakeModel((valid_decomposition(API_TEXT), not_applicable_mapping())))
    first = runner(request, config, model())
    assert first.run_status in ('SUCCESS', 'PARTIAL')
    path = config.source_context_path
    path.unlink()
    if deep: Path(str(path)+'.sig').unlink()
    forbidden = DeepModel() if deep else FakeModel()
    again = runner(request, config, forbidden)
    assert again.requirements == first.requirements
    assert forbidden.calls == []
    path.write_bytes(canonical_json_bytes(map_for(doc, disposition='unresolved')))
    if deep: sign_test_map(path, config.knowledge_trust)
    changed = runner(request, config, model())
    assert changed.run_id != first.run_id
    assert len(tuple((request.output_dir/'.work').glob('*/source-context.json'))) == 2
