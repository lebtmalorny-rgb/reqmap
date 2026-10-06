import json
from dataclasses import replace
from reqmap.agent_service import AgentService
from tests.test_agent_session import service,start,atoms


def test_tools_describe_nine_exact_contracts_and_equivalent_output(tmp_path):
    from reqmap.agent_tools import tool_definitions, call_tool
    from reqmap.agent_service import TOOL_FIELDS
    definitions = tool_definitions()
    assert {t['name'] for t in definitions} == set(TOOL_FIELDS)
    assert len(definitions) == 9
    for item in definitions:
        assert item['inputSchema']['additionalProperties'] is False
        assert item['annotations']['readOnlyHint'] is ('get_' in item['name'] or 'search_' in item['name'])
    reply = call_tool(service(tmp_path),'reqmap_start_session',dict(request_id='start',source=dict(kind='texts',texts=['Test'])))
    assert json.loads(reply['content'][0]['text']) == reply['structuredContent']
    assert reply['isError'] is False


def test_oversized_mutation_reply_does_not_commit(tmp_path):
    svc = service(tmp_path); text = 'я'*3000; sid = start(svc,(text,))
    limited = AgentService(replace(svc.config,limits=replace(svc.config.limits,max_response_bytes=2000)))
    reply = atoms(limited,sid,text)
    assert reply.error.code == 'RESPONSE_TOO_LARGE'
    assert svc.store.read(sid).revision == 0
    assert svc.call('reqmap_get_session',dict(session_id=sid)).data['requirements'][0]['atoms'] == []


def test_pagination_shrinks_to_wire_limit_without_losing_rows(tmp_path):
    from reqmap.agent_tools import call_tool
    svc = service(tmp_path); sid = start(svc,tuple('Текст '+str(i)+' я'*100 for i in range(10)))
    limited = AgentService(replace(svc.config,limits=replace(svc.config.limits,max_response_bytes=16000)))
    ids=[]; cursor=None
    while True:
        reply = call_tool(limited,'reqmap_get_session',dict(session_id=sid,cursor=cursor,page_size=10))
        assert len(json.dumps(reply,ensure_ascii=False).encode()) < 16000
        data = reply['structuredContent']['data']; assert reply['structuredContent']['ok']
        assert len(data['requirements']) < 10  # full binding context requires a smaller page
        ids.extend(r['requirement']['requirement_id'] for r in data['requirements'])
        cursor=data['next_cursor']
        if cursor is None: break
    assert len(ids) == len(set(ids)) == 10
