"""All public acceptors and runners must enforce the same reviewed context gate."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from reqmap.agent_knowledge import VerifiedKnowledge
from reqmap.agent_service import AgentService
from reqmap.binding_catalog import knowledge_digest
from reqmap.binding_source import atom_selection_proposal, bind_source, canonical_atoms
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from reqmap.export_json import canonical_json_bytes
from reqmap.knowledge import KnowledgeBase
from reqmap.models import AnalysisRequest, SupportStatus, to_dict
from reqmap.source_context import capture_source_document
from tests.agent_support import make_agent_config
from tests.source_context_support import API_TEXT, DETACHED_TEXT, text_document, map_for, link_for
from tests.test_binding_mapping import setup_binding, kb, submit
from tests.test_binding_sessions import insufficient_proposal
from tests.test_pipeline import config_for


def test_direct_acceptor_without_snapshot_is_unreviewed(setup_binding):
    result,_ = submit(setup_binding,API_TEXT,reviewed=False)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert 'SOURCE_CONTEXT_UNREVIEWED' in {d.code for d in result.binding_decision.diagnostics}


def context_fixture(tmp_path, profile, case, source_name='agent-texts'):
    own = API_TEXT if case in ('independent','no-map','draft','unresolved','conflict') else DETACHED_TEXT
    doc=capture_source_document(**text_document((own,'GUI'),source_name))
    raw=map_for(doc,disposition='unresolved' if case=='unresolved' else 'independent',reviewed=case!='draft')
    if case in ('api','gui','conflict'):
        raw=map_for(doc,disposition='linked',links=(link_for(doc,value='api' if case=='api' else 'gui'),))
    path=None;trust=None
    if case!='no-map':
        path=tmp_path/'context.json';path.write_bytes(canonical_json_bytes(raw))
    if profile is AnalysisProfile.DEEP:
        key=tmp_path/'context-key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True)
        signers=tmp_path/'context-signers'
        signers.write_text('reqmap-snapshot namespaces="reqmap-source-context" '+key.with_suffix('.pub').read_text())
        trust=KnowledgeTrustConfig(signers)
        if path:
            subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n','reqmap-source-context',str(path)],check=True,capture_output=True)
    return doc,path,trust


@pytest.mark.parametrize('route',['cli','mcp'])
@pytest.mark.parametrize('case,status,code',[
    ('independent','supported',None),('api','supported',None),
    ('gui','insufficient_evidence','SOURCE_INTERFACE_UNPROVEN'),
    ('no-map','insufficient_evidence','SOURCE_CONTEXT_UNREVIEWED'),
    ('draft','insufficient_evidence','SOURCE_CONTEXT_UNREVIEWED'),
    ('unresolved','insufficient_evidence','SOURCE_CONTEXT_UNRESOLVED'),
    ('conflict','insufficient_evidence','SOURCE_CONTEXT_CONFLICT'),
])
def test_context_gate_is_shared_by_all_entrypoints(tmp_path,monkeypatch,setup_binding,route,case,status,code):
    knowledge,catalog,proposal=setup_binding
    deep=type(knowledge) is not KnowledgeBase
    profile=AnalysisProfile.DEEP if deep else AnalysisProfile.LEGACY
    doc,path,trust=context_fixture(tmp_path,profile,case)
    raw={**proposal,'proposal_schema_version':2,'obligation_id':'REQ-0001-O001','predicate_ids':['P-NOVA-CREATE']}
    verified=VerifiedKnowledge(knowledge,knowledge_digest(knowledge),knowledge.snapshot_id if deep else None,catalog)
    if route=='mcp':
        monkeypatch.setattr('reqmap.agent_service.load_agent_knowledge',lambda config:verified)
        config=replace(make_agent_config(tmp_path/'agent'),analysis_profile=profile,knowledge_trust=trust,source_context_path=path)
        svc=AgentService(config)
        started=svc.call('reqmap_start_session',dict(request_id='start',source=dict(kind='texts',texts=[r.text for r in doc.requirements])))
        assert started.ok,started
        sid=started.data['session_id']
        selected=svc.call('reqmap_submit_atoms',dict(session_id=sid,request_id='atoms',expected_revision=0,requirement_id='REQ-0001',proposal=atom_selection_proposal(bind_source(doc.requirements[0]))))
        assert selected.ok,selected
        context=svc.call('reqmap_get_atom_context',dict(session_id=sid,atom_id='REQ-0001-A001'))
        assert context.ok,context
        reply=svc.call('reqmap_submit_mapping',dict(session_id=sid,request_id='map',expected_revision=1,atom_id='REQ-0001-A001',context_id=context.data['context_id'],proposal=raw))
        assert reply.ok,reply
        result=reply.data['result']['atom_result'] if deep else reply.data['result']
        view,_=svc.verified_view(svc.store.read(sid))
        assert len(view.requirements)==2
        assert [r.text for r in view.requirements]==[r.text for r in doc.requirements]
    else:
        module=__import__('reqmap.deep_pipeline' if deep else 'reqmap.pipeline',fromlist=['analyze'])
        monkeypatch.setattr(module,'load_configured_catalog',lambda config,kb:catalog)
        monkeypatch.setattr(module,'load_knowledge_v2' if deep else 'load_knowledge',lambda *a,**kw:knowledge)
        config=replace(config_for(),knowledge_path=knowledge.root,analysis_profile=profile,knowledge_trust=trust,source_context_path=path)
        request=AnalysisRequest(doc.requirements,doc.input_sha256,'text',None,tmp_path/'cli',source_document=doc)
        class Model:
            def preflight(self):pass
            def complete_json(self,stage,prompt,payload):
                if stage=='decomposition': return payload['canonical_proposal']
                return raw if payload['atom']['requirement_id']=='REQ-0001' else insufficient_proposal(payload,deep)
        run=(module.analyze_deep if deep else module.analyze)(request,config,Model())
        assert len(run.requirements)==2
        result=to_dict(run.requirements[0].atom_results[0])
    assert result['support_status']==status
    decision=result['binding_decision']
    if code:assert code in {d['code'] for d in decision['diagnostics']}
    assert result['atom']['source_quote']==doc.requirements[0].text


def test_model_cannot_approve_or_remove_context(tmp_path):
    svc=AgentService(make_agent_config(tmp_path))
    reply=svc.call('reqmap_start_session',dict(request_id='start',source=dict(kind='texts',texts=[API_TEXT]),source_context={'reviewed':True}))
    assert reply.error.code=='TOOL_ARGUMENTS'


@pytest.mark.parametrize('status',list(SupportStatus))
def test_context_gate_never_converts_missing_review_to_a_subject_conclusion(setup_binding,status):
    from reqmap.binding_engine import mapping_decision
    from reqmap.binding_models import BindingContext
    knowledge,catalog,_=setup_binding
    doc=capture_source_document(**text_document())
    binding=bind_source(doc.requirements[0])
    decision=mapping_decision(canonical_atoms(binding)[0],BindingContext(binding,catalog),knowledge,[],status,())
    assert decision.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert 'SOURCE_CONTEXT_UNREVIEWED' in {d.code for d in decision.diagnostics}


@pytest.mark.parametrize('tamper', ['decision', 'snapshot', 'missing-snapshot'])
def test_manual_binding_context_cannot_forge_review(setup_binding, tamper):
    from tests.source_context_support import reviewed_context
    from reqmap.binding_engine import context_obligation
    from reqmap.errors import ReqmapError
    knowledge, catalog, _ = setup_binding
    binding = bind_source(capture_source_document(**text_document()).requirements[0])
    context = reviewed_context(binding, catalog)
    decision = context.source_binding.context_decision
    if tamper == 'decision':
        context = replace(context, source_binding=replace(context.source_binding,
                          context_decision=replace(decision, context_id='forged')))
    elif tamper == 'snapshot':
        context = replace(context, source_context=replace(context.source_context,
                          decisions=(replace(decision, state='unreviewed'),)))
    else:
        context = replace(context, source_context=None)
    from reqmap.proposals import ProposalError
    with pytest.raises((ReqmapError, ProposalError)):
        context_obligation(canonical_atoms(binding)[0], context, knowledge)


def test_model_proposals_cannot_override_review_or_drop_atoms(tmp_path):
    svc = AgentService(make_agent_config(tmp_path))
    text = API_TEXT + '; ' + API_TEXT
    started = svc.call('reqmap_start_session', dict(request_id='start', source=dict(kind='texts', texts=[text])))
    assert started.ok
    sid = started.data['session_id']
    binding = bind_source(capture_source_document(**text_document((text,))).requirements[0])
    original = atom_selection_proposal(binding)
    for n, proposal in enumerate(({**original, 'source_context': {'reviewed': True}},
                                  {**original, 'obligation_ids': [binding.obligations[0].obligation_id]})):
        reply = svc.call('reqmap_submit_atoms', dict(session_id=sid, request_id=f'bad-{n}',
                         expected_revision=0, requirement_id='REQ-0001', proposal=proposal))
        assert not reply.ok
    assert svc.store.read(sid).revision == 0
