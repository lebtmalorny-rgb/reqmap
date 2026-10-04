import json
import os
from pathlib import Path
import subprocess
import pytest
from tests.agent_support import make_agent_config
from tests.test_mcp_stdio import frame,initialize

ROOT=Path(__file__).resolve().parents[1]


def config_file(tmp_path):
    config=make_agent_config(tmp_path)
    path=tmp_path/'config with spaces.json'
    path.write_text(json.dumps(dict(knowledge_path=str(config.knowledge_path),input_root=str(config.input_root),session_root=str(config.session_root),output_root=str(config.output_root))))
    return path


def process(path):
    return subprocess.Popen([str(ROOT/'.venv/bin/reqmap'),'agent','serve','--config',str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})


@pytest.mark.parametrize('version',['2025-06-18','2025-11-25'])
def test_real_cli_split_frame_and_eof(tmp_path,version):
    proc=process(config_file(tmp_path)); data=initialize(version)
    proc.stdin.write(data[:15]);proc.stdin.flush()
    proc.stdin.write(data[15:]+frame('notifications/initialized',None)+frame('tools/list',2)+frame('tools/call',3,dict(name='reqmap_start_session',arguments=dict(request_id='s',source=dict(kind='texts',texts=['Nova API'])))))
    proc.stdin.flush();proc.stdin.close();proc.stdin=None
    out,err=proc.communicate(timeout=10)
    assert proc.returncode==0,err
    responses=[json.loads(line) for line in out.splitlines()]
    assert len(responses)==3 and all(r['jsonrpc']=='2.0' for r in responses)
    assert len(responses[1]['result']['tools'])==9
    reply=responses[2]['result']
    assert json.loads(reply['content'][0]['text'])==reply['structuredContent']


def test_cli_startup_failure_has_no_stdout(tmp_path):
    proc=process(tmp_path/'missing.json');out,err=proc.communicate(timeout=10)
    assert proc.returncode==2 and not out and err
