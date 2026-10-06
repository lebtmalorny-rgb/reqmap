"""Every subject artifact carries the same immutable binding and decision."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook
import pytest

from reqmap.agent_knowledge import VerifiedKnowledge
from reqmap.agent_service import AgentService
from reqmap.binding_catalog import knowledge_digest
from reqmap.config import AnalysisProfile
from reqmap.knowledge import KnowledgeBase
from reqmap.models import to_dict
from tests.agent_support import make_agent_config
from tests.test_agent_session import start
from tests.test_binding_sessions import select_source
from tests.test_binding_mapping import setup_binding, kb


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("suffix,status", [("", "supported"), (" за одну миллисекунду", "insufficient_evidence")])
def test_all_exports_preserve_exact_gap_and_predicate_refs(tmp_path, monkeypatch, setup_binding, restart, suffix, status):
    knowledge, catalog, raw = setup_binding
    deep = type(knowledge) is not KnowledgeBase
    profile = AnalysisProfile.DEEP if deep else AnalysisProfile.LEGACY
    verified = VerifiedKnowledge(knowledge, knowledge_digest(knowledge), knowledge.snapshot_id if deep else None, catalog)
    monkeypatch.setattr("reqmap.agent_service.load_agent_knowledge", lambda config: verified)
    svc = AgentService(replace(make_agent_config(tmp_path/"agent"), analysis_profile=profile))
    text = "Nova должна создавать ВМ через API" + suffix
    sid = start(svc, (text,))
    context = select_source(svc, sid, text)
    assert context.ok, context
    proposal = {**raw, "proposal_schema_version":2, "obligation_id":"REQ-0001-O001", "predicate_ids":["P-NOVA-CREATE"]}
    submitted = svc.call("reqmap_submit_mapping", dict(session_id=sid, atom_id="REQ-0001-A001",
        context_id=context.data["context_id"], proposal=proposal, request_id="map", expected_revision=1))
    assert submitted.ok, submitted
    result = submitted.data["result"]["atom_result"] if deep else submitted.data["result"]
    decision = result["binding_decision"]
    assert result["support_status"] == status
    if restart:
        svc = AgentService(svc.config)
    from reqmap.agent_finalize import build_agent_run
    from reqmap.publication import publish_artifacts
    view, verified = svc.verified_view(svc.store.read(sid))
    run = build_agent_run(view, verified)
    if restart:
        reply = svc.call("reqmap_finalize", dict(session_id=sid, request_id="finish", expected_revision=2, allow_partial=True))
        assert reply.ok, reply
        output = Path(reply.data["artifacts"]["result.json"]).parent
        assert svc.call("reqmap_get_result", dict(session_id=sid)).ok
    else:
        output = tmp_path/"after-submit"
        output.mkdir()
        assert not publish_artifacts(run, output)
    payload = json.loads((output/"result.json").read_text())
    assert payload["schema_version"] == ("2.1" if deep else "1.1")
    row = payload["requirements"][0]
    assert row["source_binding"] == context.data["payload"]["source_binding"]
    assert row["atom_results"][0]["binding_decision"] == decision
    assert row["atom_results"][0]["atom"]["source_spans"] == result["atom"]["source_spans"]
    book = load_workbook(output/"result.xlsx")
    assert len(book.sheetnames) == (7 if deep else 5)
    atoms = book["Атомарные утверждения"]
    headers = [c.value for c in atoms[1]]
    cell = atoms.cell(2, headers.index("Binding decision")+1)
    assert json.loads(cell.value) == decision
    report = (output/"report.md").read_text()
    from reqmap.export_json import canonical_json_bytes
    assert canonical_json_bytes(decision).decode().strip() in report
    manifest = json.loads((output/"manifest.json").read_text())
    assert manifest["binding_contract"] == payload["metadata"]["binding_contract"]
    journal = [json.loads(line) for line in (output/"run.jsonl").read_text().splitlines()]
    assert any(event.get("binding_contract") == manifest["binding_contract"] for event in journal)
    for name, digest in manifest["artifact_hashes"].items():
        assert hashlib.sha256((output/name).read_bytes()).hexdigest() == digest
    from reqmap.crosscheck import crosscheck
    from reqmap.crosscheck_deep import crosscheck_deep
    check = crosscheck_deep if deep else crosscheck
    assert not check(run, output/"result.json", output/"result.xlsx", output/"report.md")
    # A changed gap is detected even when all status/count columns are unchanged.
    forged = dict(decision)
    forged["uncovered"] = [] if decision["uncovered"] else [{"start":0,"end":1,"quote":"N"}]
    cell.value = json.dumps(forged, ensure_ascii=False)
    book.save(output/"result.xlsx")
    book.close()
    assert check(run, output/"result.json", output/"result.xlsx", output/"report.md")
