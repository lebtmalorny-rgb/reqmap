"""Installed offline contract; scripted transport is not a live IDE/model eval."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from reqmap.export_json import canonical_json_bytes
from tests.agent_support import StdioClient
from tests.binding_factories import write_catalog, sign_catalog, predicate_raw
from tests.deep_factories import signed_v2_snapshot, deep_mapping_response
from tests.source_context_support import sign_test_map, text_document, map_for
from tests.test_acceptance import _FakeOpenAIServer
from tests.test_mapping import raw_mapping, response, step

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/source_context/live_eval.json'
FROZEN=json.loads(FIXTURE.read_bytes())


def resources(tmp_path, deep):
    if deep:
        from reqmap.knowledge_v2 import load_knowledge_v2
        import shutil
        from tests.deep_factories import sign_existing_v2_snapshot
        root=tmp_path/'snapshot'
        shutil.copytree(ROOT/'tests/fixtures/kb_v2_minimal',root)
        def neutron(value):
            raw=json.dumps(value,ensure_ascii=False)
            for before,after in [('NOVA','NEUTRON'),('Nova','Neutron'),('nova','neutron'),('SERVER','PORT'),('servers','ports'),('server','port'),('сервера','порта')]:
                raw=raw.replace(before,after)
            return json.loads(raw)
        for name in ('actors','components'):
            path=root/(name+'.json');raw=json.loads(path.read_bytes())
            raw[name]+=[neutron(raw[name][0])];path.write_bytes(canonical_json_bytes(raw))
        for name in ('targets','actions','effects','capabilities','evidence'):
            path=root/(name+'.jsonl');values=[json.loads(x) for x in path.read_text().splitlines()]
            added=neutron(values[0])
            if name=='actions':added['procedure_required']=False
            path.write_bytes(b''.join(canonical_json_bytes(v) for v in [*values,added]))
        corpus=root/'corpus/SRC-MINIMAL.md'
        corpus.write_text(corpus.read_text()+'\n## create-port\nSynthetic Neutron API creates a port and produces ACTIVE port state.\n')
        path=root/'source-manifest.json';raw=json.loads(path.read_bytes())
        raw['sources'][0]['content_sha256']=hashlib.sha256(corpus.read_bytes()).hexdigest()
        path.write_bytes(canonical_json_bytes(raw))
        path=root/'procedures.jsonl';procedure=json.loads(path.read_text())
        original=procedure['steps'][0]
        procedure['steps']=[{**original,'local_step_id':name,'phase':phase,'depends_on':depends,
                             'rollback_step_local_id':'rollback' if name=='action' else None}
            for name,phase,depends in [('before','preflight',[]),('action','runtime',['before']),('verify','verify',['action']),('rollback','rollback',[])]]
        path.write_bytes(canonical_json_bytes(procedure))
        allowed=sign_existing_v2_snapshot(root,tmp_path/'trust')
        knowledge=load_knowledge_v2(root,allowed)
        predicate=neutron(predicate_raw(knowledge));predicate.update(predicate_id='P-NEUTRON-CREATE',actor='neutron',object='port')
        catalog=write_catalog(tmp_path/'bindings',knowledge,predicates=[predicate_raw(knowledge),predicate])
        sign_catalog(catalog,allowed.parent/'signing_key')
        return knowledge,catalog,KnowledgeTrustConfig(allowed)
    from reqmap.knowledge import load_knowledge
    return load_knowledge(ROOT/'knowledge/epoxy-2025.1'), ROOT/'knowledge/bindings/epoxy-2025.1-api',None


def proposal_for(payload,knowledge,deep):
    if 'original_payload' in payload:return proposal_for(payload['original_payload'],knowledge,deep)
    if 'canonical_proposal' in payload:return payload['canonical_proposal']
    atom=payload['atom']
    own=next(o for o in payload['source_binding']['obligations'] if o['obligation_id']==atom['obligation_id'])
    if own['parse_state']!='bound':
        return dict(proposal_schema_version=2,obligation_id=atom['obligation_id'],predicate_ids=[],support_status='insufficient_evidence',supported_aspects=[],unconfirmed_aspects=[],**({'responsibilities':[],'procedure_template_ids':[]} if deep else {'mappings':[]}))
    control=FROZEN['controls']['deep' if deep else 'legacy']
    neutron = own['actor']=='neutron'
    if neutron:
        control = dict(predicate_id='P-NEUTRON-CREATE' if deep else 'P-NEUTRON-PORT-CREATE',evidence_id='EV-NEUTRON-CREATE' if deep else 'E-NEUTRON-PORT-CREATE')
    if deep:
        proposal=deep_mapping_response()
        if neutron:
            proposal=json.loads(json.dumps(proposal).replace('NOVA','NEUTRON').replace('nova','neutron').replace('SERVER','PORT'))
            proposal['procedure_template_ids']=[]
    else:
        claim=knowledge.evidence[control['evidence_id']].claim_ru
        proposal=response(raw_mapping('neutron' if neutron else 'nova',(control['evidence_id'],),role_ru=claim,steps=[step('runtime',api_operation='POST /v2.0/ports' if neutron else 'POST /servers',action_ru=claim)]),supported_aspects=(claim,))
    return {**proposal,'proposal_schema_version':2,'obligation_id':atom['obligation_id'],'predicate_ids':[control['predicate_id']]}


def prepare(tmp_path, deep, route, *, positive=False):
    knowledge,catalog,trust=resources(tmp_path,deep)
    texts=FROZEN['texts'] if not positive else FROZEN['texts'][:1]
    if positive:
        from reqmap.source_context import capture_source_document
        mapping=map_for(capture_source_document(**text_document(tuple(texts),'agent-texts' if route=='mcp' else '--requirement')))
    else:mapping=FROZEN['maps'][route]['mapping']
    path=tmp_path/'context.json';path.write_bytes(canonical_json_bytes(mapping))
    if deep:sign_test_map(path,trust)
    config=dict(analysis_profile='deep' if deep else 'legacy',knowledge_path=str(knowledge.root.resolve()),binding_catalog_path=str(catalog.resolve()),source_context_path=str(path))
    if trust:config['knowledge_trust']=dict(allowed_signers_path=str(trust.allowed_signers_path))
    return knowledge,config,texts,path


def check_artifacts(output, texts, deep, *, positive=False):
    names={'result.json','result.xlsx','report.md','manifest.json','run.jsonl'}
    assert {p.name for p in output.iterdir() if p.is_file()}==names
    report=json.loads((output/'result.json').read_bytes())
    manifest=json.loads((output/'manifest.json').read_bytes())
    for name,digest in manifest['artifact_hashes'].items():
        assert hashlib.sha256((output/name).read_bytes()).hexdigest()==digest
    assert report['schema_version']==('2.2' if deep else '1.2')
    assert report['metadata']['binding_contract']['binding_engine_version']=='2.0'
    rows=report['requirements'];assert [r['requirement']['text'] for r in rows]==texts
    for expected in (FROZEN['expected'][:1] if positive else FROZEN['expected']):
        row=rows[expected['index']]
        assert row['support_status']==expected['status'],(expected['case_id'],row)
        assert len(row['atom_results'])==expected['atoms']
        for atom in row['atom_results']:
            codes={d['code'] for d in atom['binding_decision']['diagnostics']}
            context_codes={c for c in codes if c.startswith('SOURCE_CONTEXT_') or c in ('SOURCE_INTERFACE_UNPROVEN','SOURCE_CONDITION_UNPROVEN')}
            assert context_codes==set(expected['codes']),(expected['case_id'],codes)
        links=row['source_binding']['context_decision']['applied_links']
        assert list(dict.fromkeys(l['origin']['row']['coordinate']['row']-1 for l in links))==expected['origins']
        for link in links:
            ref=link['origin'];text=texts[ref['row']['coordinate']['row']-1]
            assert text[ref['span']['start']:ref['span']['end']]==ref['span']['quote']
    return report


def mcp_flow(tmp_path, command, deep, *, positive=False):
    knowledge,config,texts,map_path=prepare(tmp_path,deep,'mcp',positive=positive)
    config.update(input_root=str(tmp_path),session_root=str(tmp_path/'sessions'),output_root=str(tmp_path/'output'))
    cfg=tmp_path/'agent.json';cfg.write_bytes(canonical_json_bytes(config))
    client=StdioClient(command,cfg)
    try:
        assert len(client.rpc('tools/list',{})['tools'])==9
        start=client.call('reqmap_start_session',request_id='start',source=dict(kind='texts',texts=texts),reported_client='codex')
        assert start['ok'],start
        sid=start['data']['session_id'];revision=0
        page=client.call('reqmap_get_session',session_id=sid);assert page['ok'],page
        rows=page['data']['requirements'];assert len(rows)==len(texts)
        client.close();map_path.unlink()
        if deep:Path(str(map_path)+'.sig').unlink()
        client=StdioClient(command,cfg)
        for row in rows:
            rid=row['requirement']['requirement_id']
            selected=client.call('reqmap_submit_atoms',session_id=sid,requirement_id=rid,request_id='atoms-'+rid,expected_revision=revision,proposal=row['decomposition_context']['canonical_proposal'])
            assert selected['ok'],selected
            revision=selected['data']['revision']
            for atom in selected['data']['atoms']:
                context=client.call('reqmap_get_atom_context',session_id=sid,atom_id=atom['atom_id']);assert context['ok'],context
                mapped=client.call('reqmap_submit_mapping',session_id=sid,atom_id=atom['atom_id'],context_id=context['data']['context_id'],request_id='map-'+atom['atom_id'],expected_revision=revision,proposal=proposal_for(context['data']['payload'],knowledge,deep))
                assert mapped['ok'],mapped
                revision=mapped['data']['revision']
        final=client.call('reqmap_finalize',session_id=sid,request_id='strict-final',expected_revision=revision,allow_partial=False)
        if deep and not positive:
            assert final['error']['code']=='ANALYSIS_INCOMPLETE',final
            final=client.call('reqmap_finalize',session_id=sid,request_id='partial-final',expected_revision=revision,allow_partial=True)
        assert final['ok'],final
        assert client.call('reqmap_get_result',session_id=sid)['ok']
        report=check_artifacts(Path(final['data']['artifacts']['result.json']).parent,texts,deep,positive=positive)
        origin=report['metadata']['analysis_origin']
        assert origin['tool_contract_version']==origin['workflow_version']=='3.0'
    finally:client.close()


def cli_flow(tmp_path,command,deep):
    knowledge,config,texts,_=prepare(tmp_path,deep,'cli')
    server=_FakeOpenAIServer(lambda payload:proposal_for(payload,knowledge,deep))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        config['model']=dict(base_url=server.base_url,model=server.model_name,retries=0,max_prompt_chars=200000)
        cfg=tmp_path/'cli.json';cfg.write_bytes(canonical_json_bytes(config))
        output=tmp_path/'output'
        args=[*command,'analyze','--config',str(cfg),'--output',str(output)]
        for text in texts:args+=['--requirement',text]
        run=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=90)
        assert run.returncode in (0,4),(run.stdout,run.stderr)
        assert not server.errors
        check_artifacts(output,texts,deep)
        before=len([r for r in server.requests if r[0]=='POST'])
        resumed=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=90)
        assert resumed.returncode==run.returncode
        assert len([r for r in server.requests if r[0]=='POST'])==before
    finally:server.shutdown();thread.join();server.server_close()


@pytest.mark.parametrize('deep',[False,True])
@pytest.mark.parametrize('route',['cli','mcp'])
def test_cli_mcp_context_acceptance_matrix(tmp_path,deep,route):
    command=[sys.executable,'-c','from reqmap.cli import main; raise SystemExit(main())']
    (mcp_flow if route=='mcp' else cli_flow)(tmp_path,command,deep)


def test_installed_context_contract(tmp_path):
    venv=tmp_path/'venv'
    installed=subprocess.run([str(ROOT/'install.sh'),str(venv)],cwd=ROOT,env={**os.environ,'PYTHON_BIN':sys.executable,'PIP_NO_INDEX':'1'},capture_output=True,text=True,timeout=90)
    assert installed.returncode==0,installed.stderr
    command=['env','-u','PYTHONPATH',str(venv/'bin/reqmap')]
    origin=subprocess.run([str(venv/'bin/python'),'-I','-c','import reqmap; print(reqmap.__file__)'],capture_output=True,text=True,check=True)
    assert str(venv) in origin.stdout and str(ROOT/'src') not in origin.stdout
    version=subprocess.run([*command,'--version'],capture_output=True,text=True,check=True)
    assert 'reqmap 0.1.0' in version.stdout
    for deep in (False,True):
        for route in ('cli','mcp'):
            work=tmp_path/f'{deep}-{route}';work.mkdir()
            (mcp_flow if route=='mcp' else cli_flow)(work,command,deep)
        positive=tmp_path/f'{deep}-strict';positive.mkdir()
        mcp_flow(positive,command,deep,positive=True)


def test_live_fixture_is_frozen_before_model_evaluation():
    assert FROZEN['synthetic_only'] is True
    assert hashlib.sha256((FIXTURE.parent/'cases.json').read_bytes()).hexdigest()==FROZEN['baseline_cases_sha256']
    assert hashlib.sha256(canonical_json_bytes(FROZEN['texts'])).hexdigest()==FROZEN['input_sha256']
    for value in FROZEN['maps'].values():
        assert hashlib.sha256(canonical_json_bytes(value['mapping'])).hexdigest()==value['sha256']
