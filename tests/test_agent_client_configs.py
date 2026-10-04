import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
import pytest
from tests.test_cli_agent import config_file
from tests.test_mcp_stdio import initialize,frame

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('filename',['codex.config.toml','opencode.json','jetbrains-mcp.json'])
def test_client_example_launches_same_server_with_spaces(tmp_path,filename):
    checkout=tmp_path/'учебный проект';checkout.mkdir()
    binary=checkout/'.venv/bin';binary.mkdir(parents=True)
    shutil.copy2(ROOT/'.venv/bin/reqmap',binary/'reqmap')
    shutil.copy2(config_file(tmp_path),checkout/'config.agent.yaml')
    text=(ROOT/'examples/ide'/filename).read_text().replace('/ABSOLUTE/PATH/TO/reqmap',str(checkout))
    config=tomllib.loads(text) if filename.endswith('.toml') else json.loads(text)
    if filename=='codex.config.toml':
        entry=config['mcp_servers']['reqmap'];command=[entry['command'],*entry['args']]
    elif filename=='opencode.json':command=config['mcp']['reqmap']['command']
    else:
        entry=config['mcpServers']['reqmap'];command=[entry['command'],*entry['args']]
    assert command==[str(binary/'reqmap'),'agent','serve','--config',str(checkout/'config.agent.yaml')]
    result=subprocess.run(command,input=initialize()+frame('notifications/initialized',None)+frame('tools/list',2),capture_output=True,env={**os.environ,'PYTHONPATH':str(ROOT/'src')},timeout=10)
    assert result.returncode==0,result.stderr
    assert len(json.loads(result.stdout.splitlines()[-1])['result']['tools'])==9


def test_jetbrains_acp_uses_opencode_config_without_duplicate_forwarding():
    config=json.loads((ROOT/'examples/ide/jetbrains-acp.json').read_text())
    agent=config['agent_servers']['OpenCode']
    assert agent['args']==['acp']
    assert config['default_mcp_settings']=={'use_custom_mcp':False,'use_idea_mcp':False}
