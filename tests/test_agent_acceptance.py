import hashlib
import json
from pathlib import Path
import sys
import pytest
from openpyxl import Workbook
from reqmap.config import AnalysisProfile
from tests.agent_support import scripted_agent_flow
from tests.deep_acceptance_support import gold_snapshot

ROOT=Path(__file__).resolve().parents[1]


def write_flow_config(tmp_path,deep):
    knowledge=ROOT/'knowledge/epoxy-2025.1'
    cfg=dict(input_root=str(tmp_path),session_root=str(tmp_path/'sessions'),output_root=str(tmp_path/'reports'))
    if deep:
        knowledge,trust=gold_snapshot(tmp_path)
        cfg.update(analysis_profile='deep',knowledge_trust=dict(allowed_signers_path=str(trust)))
    cfg['knowledge_path']=str(knowledge)
    path=tmp_path/'agent.json';path.write_text(json.dumps(cfg));return path


@pytest.mark.parametrize('deep',[False,True])
@pytest.mark.parametrize('kind',['texts','txt','xlsx'])
def test_real_stdio_flow_without_model_or_network(tmp_path,kind,deep):
    config=write_flow_config(tmp_path,deep)
    text='Противоречивое создание сервера conflict' if deep else 'создание виртуальной машины через Nova REST API'
    path=None
    if kind=='texts':source=dict(kind=kind,texts=[text])
    elif kind=='txt':
        path=tmp_path/'requirements.txt';path.write_text(text+'\n');source=dict(kind=kind,path=str(path))
    else:
        path=tmp_path/'requirements.xlsx';wb=Workbook();wb.active.append(['ID','Требование']);wb.active.append(['one',text]);wb.save(path);wb.close();source=dict(kind=kind,path=str(path))
    original=path.read_bytes() if path else None
    # Any backend attempt trips the process; includes AF_UNIX/IPv4/IPv6 connects.
    guard="""import socket

def forbidden(*args,**kwargs): raise AssertionError('BACKEND_MODEL_OR_NETWORK_ATTEMPT')
socket.socket.connect=forbidden
socket.socket.connect_ex=forbidden
import reqmap.llm
reqmap.llm.OpenAICompatibleClient=forbidden
from reqmap.cli import main
raise SystemExit(main())
"""
    result=scripted_agent_flow([sys.executable,'-c',guard],config,source,AnalysisProfile.DEEP if deep else AnalysisProfile.LEGACY)
    assert set(result['artifacts'])=={'result.json','result.xlsx','report.md','run.jsonl','manifest.json'}
    assert result['report']['run_status']==('PARTIAL' if deep else 'SUCCESS')
    assert len(result['report']['requirements'])==1
    if path:assert path.read_bytes()==original
    if deep:
        assert 'EV-CONFLICT-NEGATIVE' in result['report']['responsibility_records'][0]['evidence_ids']


def test_frozen_real_agent_eval_references_existing_evidence(tmp_path):
    from reqmap.knowledge import load_knowledge
    from reqmap.knowledge_v2 import load_knowledge_v2
    root,trust=gold_snapshot(tmp_path)
    corpora={'legacy':load_knowledge(ROOT/'knowledge/epoxy-2025.1'),'deep':load_knowledge_v2(root,trust)}
    cases=json.loads((ROOT/'tests/fixtures/agent_eval.json').read_text())['cases']
    assert len({case['id'] for case in cases})==len(cases)==5
    for case in cases:
        for evidence_id in case['required_evidence_ids']:
            assert evidence_id in corpora[case['profile']].evidence,(case['id'],evidence_id)
