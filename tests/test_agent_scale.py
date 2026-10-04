import json
from pathlib import Path
import resource
import sys
import time
from reqmap.agent_service import AgentService
from tests.test_agent_session import service,start,atoms


def test_1950_requirements_with_atoms_finalize_and_page_without_loss(tmp_path):
    began=time.perf_counter()
    svc=service(tmp_path)
    texts=tuple(f'Учебное требование {i}: создать сервер' for i in range(1950))
    sid=start(svc,texts)
    for i,text in enumerate(texts):
        reply=atoms(svc,sid,text,'atoms-'+str(i),i,f'REQ-{i+1:04d}')
        assert reply.ok,reply
    svc=AgentService(svc.config)
    before_finalize=time.perf_counter()
    final=svc.call('reqmap_finalize',dict(session_id=sid,request_id='final',expected_revision=1950,allow_partial=True))
    assert final.ok,final
    finalize_seconds=time.perf_counter()-before_finalize
    data=json.loads(Path(final.data['artifacts']['result.json']).read_text())
    assert len(data['requirements'])==1950 and data['run_status']=='FAILED'
    assert all(len(row['atom_results'])==1 and row['support_status'] is None for row in data['requirements'])
    seen=[];cursor=None
    while True:
        reply=svc.call('reqmap_get_result',dict(session_id=sid,cursor=cursor,page_size=50))
        assert reply.ok,reply
        seen.extend(row['requirement']['requirement_id'] for row in reply.data['requirements'])
        cursor=reply.data['next_cursor']
        if cursor is None:break
    assert seen==[f'REQ-{i+1:04d}' for i in range(1950)]
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
    print(json.dumps(dict(scale_rows=1950,atoms=1950,wall_seconds=round(time.perf_counter()-began,3),finalize_seconds=round(finalize_seconds,3),peak_rss_mib=round(peak,2),status=data['run_status'])))
