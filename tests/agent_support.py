"""Agent fixtures use real local loaders and disposable signing identities."""
from pathlib import Path
from reqmap.agent_config import AgentConfig, AgentLimits
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from tests.deep_factories import signed_v2_snapshot


def make_agent_config(tmp_path: Path, profile: AnalysisProfile = AnalysisProfile.LEGACY) -> AgentConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    trust = None
    knowledge = Path(__file__).resolve().parents[1] / 'knowledge/epoxy-2025.1'
    if profile is AnalysisProfile.DEEP:
        knowledge, allowed = signed_v2_snapshot(tmp_path)
        trust = KnowledgeTrustConfig(allowed)
    return AgentConfig(profile, knowledge, trust, None, 8, tmp_path,
                       tmp_path / 'sessions', tmp_path / 'outputs', AgentLimits())


def call_tool(service, name, **arguments):
    return service.call(name, arguments)


class StdioClient:
    def __init__(self, command, config_path):
        import subprocess
        self.process = subprocess.Popen([*command,'agent','serve','--config',str(config_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.sequence = 0
        self.rpc('initialize',dict(protocolVersion='2025-11-25',capabilities={},clientInfo=dict(name='scripted-test',version='1')))
        self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');self.process.stdin.flush()

    def rpc(self, method, params):
        import json
        import selectors
        self.sequence += 1
        self.process.stdin.write((json.dumps(dict(jsonrpc='2.0',id=self.sequence,method=method,params=params),ensure_ascii=False)+'\n').encode());self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout,selectors.EVENT_READ)
            assert selector.select(300), 'MCP response timed out'
        line=self.process.stdout.readline()
        assert line, 'MCP process exited without response'
        result=json.loads(line)
        assert 'error' not in result,result
        return result['result']

    def call(self,name,**args):
        result=self.rpc('tools/call',dict(name=name,arguments=args))
        return result['structuredContent']

    def close(self):
        self.process.stdin.close();self.process.stdin=None
        _,error=self.process.communicate(timeout=10)
        assert self.process.returncode == 0,error


def scripted_agent_flow(command: list[str], config_path: Path, source: dict[str, object], profile: AnalysisProfile) -> dict[str, object]:
    """Explicit synthetic proposals through actual MCP, including restart/rejection."""
    import json
    from tests.test_acceptance import _mapping_response
    from tests.deep_acceptance_support import scripted_response
    client=StdioClient(command,config_path)
    try:
        assert len(client.rpc('tools/list',{})['tools'])==9
        started=client.call('reqmap_start_session',request_id='start',source=source,reported_client='unknown')
        assert started['ok'],started
        sid=started['data']['session_id'];revision=0
        page=client.call('reqmap_get_session',session_id=sid)
        assert page['ok'],page
        for row in page['data']['requirements']:
            req=row['requirement'];rid=req['requirement_id'];text=req['text']
            reply=client.call('reqmap_submit_atoms',session_id=sid,requirement_id=rid,
                proposal=dict(atoms=[dict(text=text,source_quote=text,mandatory=True)]),request_id='atoms-'+rid,expected_revision=revision)
            assert reply['ok'],reply
            revision=reply['data']['revision']
            client.close();client=StdioClient(command,config_path)
            atom=reply['data']['atoms'][0]['atom_id']
            ctx=client.call('reqmap_get_atom_context',session_id=sid,atom_id=atom)
            assert ctx['ok'],ctx
            bad=client.call('reqmap_submit_mapping',session_id=sid,atom_id=atom,context_id=ctx['data']['context_id'],
                proposal={'shell':'do not execute'},request_id='invalid-'+rid,expected_revision=revision)
            assert not bad['ok'] and bad['error']['code']=='PROPOSAL_INVALID',bad
            assert bad['data']['revision']==revision
            proposal=scripted_response(ctx['data']['payload']) if profile is AnalysisProfile.DEEP else _mapping_response(text)
            mapped=client.call('reqmap_submit_mapping',session_id=sid,atom_id=atom,context_id=ctx['data']['context_id'],proposal=proposal,
                request_id='map-'+rid,expected_revision=revision)
            assert mapped['ok'],mapped
            revision=mapped['data']['revision']
        args=dict(session_id=sid,request_id='finalize',expected_revision=revision)
        final=client.call('reqmap_finalize',**args)
        if not final['ok']:
            assert final['error']['code']=='ANALYSIS_INCOMPLETE',final
            args.update(request_id='partial',allow_partial=True)
            final=client.call('reqmap_finalize',**args)
        assert final['ok'],final
        assert client.call('reqmap_finalize',**args)==final
        assert client.call('reqmap_get_result',session_id=sid)['ok']
        report=json.loads(Path(final['data']['artifacts']['result.json']).read_text())
        return dict(session_id=sid,artifacts=final['data']['artifacts'],report=report)
    finally:
        client.close()
