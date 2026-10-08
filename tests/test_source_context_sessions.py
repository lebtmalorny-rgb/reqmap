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


@pytest.mark.parametrize('finalized', [False, True])
def test_grammar_change_does_not_reinterpret_frozen_finalized_session(tmp_path, monkeypatch, finalized):
    from tests.source_context_support import API_TEXT
    import reqmap.binding_source as grammar
    import reqmap.source_context_codec as codec
    from reqmap.source_context import source_context_resolver_sha256
    config = make_agent_config(tmp_path)
    svc = AgentService(config)
    sid = start(svc, (API_TEXT,))
    if finalized:
        reply = svc.call('reqmap_finalize', dict(session_id=sid, request_id='finish',
                         expected_revision=0, allow_partial=True))
        assert reply.ok, reply
        assert svc.call('reqmap_get_result', dict(session_id=sid)).ok
        artifacts = {name:Path(path).read_bytes() for name,path in reply.data['artifacts'].items()}
    digest = source_context_resolver_sha256()
    original = grammar.bind_source
    def changed(requirement):
        binding = original(requirement)
        return replace(binding, obligations=tuple(replace(o, interface='gui') for o in binding.obligations))
    monkeypatch.setattr(grammar, 'bind_source', changed)
    monkeypatch.setattr(grammar, 'GRAMMAR_SHA256', '0'*64)
    codec._inspect_context_cached.cache_clear()
    codec._validate_context_integrity.cache_clear()
    assert source_context_resolver_sha256() == digest
    reopened = AgentService(config)
    result = reopened.call('reqmap_get_result' if finalized else 'reqmap_get_session', dict(session_id=sid))
    if finalized:
        assert result.ok, result
        assert result.data['historical'] is True
        publication = svc.store.publication(sid)
        assert artifacts == {name:(config.output_root/publication['final_name']/name).read_bytes() for name in artifacts}
    else:
        assert result.error.code == 'SESSION_CONTRACT_MISMATCH'


@pytest.mark.parametrize('missing_map', [False, True])
def test_cli_selects_frozen_context_by_grammar_contract(tmp_path, monkeypatch, missing_map):
    from reqmap.binding_runtime import prepare_run_context, save_run_context
    from reqmap.errors import ReqmapError
    from reqmap.source_context import source_context_resolver_sha256
    import reqmap.binding_source as grammar
    import reqmap.source_context_codec as codec
    from tests.test_pipeline import request_for, config_for
    from tests.source_context_support import with_reviewed_request
    request = request_for(tmp_path)
    config = with_reviewed_request(config_for(), request)
    original = prepare_run_context(request, config)
    directory = request.output_dir/'.work/original'
    directory.mkdir(parents=True)
    save_run_context(directory, original)
    if missing_map:config.source_context_path.unlink()
    digest = source_context_resolver_sha256()
    bind = grammar.bind_source
    def changed(requirement):
        binding = bind(requirement)
        return replace(binding, obligations=tuple(replace(o, interface='gui') for o in binding.obligations))
    monkeypatch.setattr(grammar, 'bind_source', changed)
    monkeypatch.setattr(grammar, 'GRAMMAR_SHA256', '0'*64)
    codec._inspect_context_cached.cache_clear()
    codec._validate_context_integrity.cache_clear()
    assert source_context_resolver_sha256() == digest
    if missing_map:
        with pytest.raises(ReqmapError, match='') as error:
            prepare_run_context(request, config)
        assert error.value.code == 'SOURCE_CONTEXT_CONTRACT_MISMATCH'
    else:
        current = prepare_run_context(request, config)
        assert current.decisions[0].context_id != original.decisions[0].context_id
        assert current.decisions[0].effective_obligations[0].obligation.interface == 'gui'


def test_integrity_cache_is_invalidated_by_grammar_identity(tmp_path, monkeypatch):
    from reqmap.binding_runtime import prepare_run_context
    from reqmap.source_context_codec import encode_source_context_snapshot, inspect_source_context_record, validate_source_context_snapshot
    import reqmap.binding_source as grammar
    from reqmap.errors import ReqmapError
    from tests.test_pipeline import request_for, config_for
    snapshot = prepare_run_context(request_for(tmp_path), config_for())
    raw = encode_source_context_snapshot(snapshot)
    inspect_source_context_record(raw)
    validate_source_context_snapshot(snapshot, profile=AnalysisProfile.LEGACY, trust=None)
    monkeypatch.setattr(grammar, 'GRAMMAR_SHA256', '0'*64)
    with pytest.raises(ReqmapError) as error:
        inspect_source_context_record(raw)
    assert error.value.code == 'SOURCE_CONTEXT_CONTRACT_MISMATCH'
    with pytest.raises(ReqmapError) as error:
        validate_source_context_snapshot(snapshot, profile=AnalysisProfile.LEGACY, trust=None)
    assert error.value.code == 'SOURCE_CONTEXT_CONTRACT_MISMATCH'
