"""Contract migration, complete-source identity, and historical read-only reports."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from reqmap.agent_service import AgentService
from reqmap.binding_source import atom_selection_proposal, bind_source
from reqmap.config import AnalysisProfile
from reqmap.models import AnalysisRequest, AnalysisState, SupportStatus
from tests.agent_support import make_agent_config
from tests.factories import requirement
from tests.test_agent_session import start
from tests.test_pipeline import config_for


TEXT = "Nova должна создавать ВМ через API за одну миллисекунду"


def select_source(svc, sid, text, rid="REQ-0001", revision=0):
    binding = bind_source(replace(requirement(requirement_id=rid), text=text))
    reply = svc.call("reqmap_submit_atoms", dict(session_id=sid, requirement_id=rid,
        proposal=atom_selection_proposal(binding), request_id="atoms-"+rid, expected_revision=revision))
    assert reply.ok, reply
    return svc.call("reqmap_get_atom_context", dict(session_id=sid, atom_id=rid+"-A001"))


def insufficient_proposal(payload, deep=False):
    return dict(proposal_schema_version=2, obligation_id=payload["atom"]["obligation_id"], predicate_ids=[],
                support_status="insufficient_evidence", supported_aspects=[], unconfirmed_aspects=[],
                **({"responsibilities": [], "procedure_template_ids": []} if deep else {"mappings": []}))


@pytest.mark.parametrize("profile", list(AnalysisProfile))
def test_cli_and_mcp_preserve_uncovered_condition(tmp_path, profile):
    config = make_agent_config(tmp_path / "agent", profile)
    svc = AgentService(config)
    sid = start(svc, (TEXT,))
    context = select_source(svc, sid, TEXT)
    assert context.ok, context
    payload = context.data["payload"]
    assert payload["source_binding"]["source_text"] == TEXT
    reply = svc.call("reqmap_submit_mapping", dict(session_id=sid, atom_id="REQ-0001-A001",
        context_id=context.data["context_id"], proposal=insufficient_proposal(payload, profile is AnalysisProfile.DEEP),
        request_id="mapping", expected_revision=1))
    assert reply.ok, reply
    mcp = reply.data["result"]
    if profile is AnalysisProfile.DEEP:
        mcp = mcp["atom_result"]
    app_config = replace(config_for(knowledge_path=config.knowledge_path), analysis_profile=profile,
                         knowledge_trust=config.knowledge_trust)
    req = replace(requirement(), text=TEXT)
    request = AnalysisRequest((req,), hashlib.sha256(TEXT.encode()).hexdigest(), "text", None, tmp_path/"cli")
    class Model:
        def preflight(self): pass
        def complete_json(self, stage, prompt, payload):
            if stage == "decomposition":
                return atom_selection_proposal(bind_source(req))
            return insufficient_proposal(payload, profile is AnalysisProfile.DEEP)
    if profile is AnalysisProfile.DEEP:
        from reqmap.deep_pipeline import analyze_deep as analyze
    else:
        from reqmap.pipeline import analyze
    cli = analyze(request, app_config, Model())
    assert cli.run_status in {"SUCCESS", "PARTIAL"}, cli
    result = cli.requirements[0].atom_results[0]
    assert result.analysis_state is AnalysisState.COMPLETED
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    from reqmap.models import to_dict
    assert to_dict(result.binding_decision) == mcp["binding_decision"]
    class ResumeModel(Model):
        def complete_json(self, *args): raise AssertionError("valid bound checkpoint must be reused")
    again = analyze(request, app_config, ResumeModel())
    assert again.requirements == cli.requirements


def test_restart_cannot_restore_pre_binding_support(tmp_path):
    svc = AgentService(make_agent_config(tmp_path))
    sid = start(svc, (TEXT,))
    context = select_source(svc, sid, TEXT)
    assert context.ok, context
    reply = svc.call("reqmap_submit_mapping", dict(session_id=sid, atom_id="REQ-0001-A001",
        context_id=context.data["context_id"], proposal=insufficient_proposal(context.data["payload"]),
        request_id="mapping", expected_revision=1))
    assert reply.ok, reply
    reopened = AgentService(svc.config).call("reqmap_get_session", dict(session_id=sid))
    assert reopened.ok, reopened
    assert reopened.data["requirements"][0]["atom_results"][0]["binding_decision"] == reply.data["result"]["binding_decision"]


@pytest.mark.parametrize("field,value", [("GRAMMAR_SHA256", "0"*64), ("GRAMMAR_VERSION", "next")])
def test_rule_change_invalidates_context(tmp_path, monkeypatch, field, value):
    import reqmap.binding_source as module
    svc = AgentService(make_agent_config(tmp_path))
    sid = start(svc, (TEXT,))
    assert select_source(svc, sid, TEXT).ok
    monkeypatch.setattr(module, field, value)
    reply = AgentService(svc.config).call("reqmap_get_atom_context", dict(session_id=sid, atom_id="REQ-0001-A001"))
    assert reply.error.code == "SESSION_CONTRACT_MISMATCH"


def restore_historical(tmp_path, active=False):
    svc = AgentService(make_agent_config(tmp_path))
    fixture = Path(__file__).parent / "fixtures/historical_binding_v1"
    raw = json.loads((fixture/"state.json").read_text())
    with svc.store._connect() as db:
        for table, rows in raw["tables"].items():
            if active and table != "sessions":
                continue
            for row in rows:
                if active and table == "sessions": row[-2:] = [0, "active"]
                db.execute("INSERT INTO "+table+" VALUES ("+",".join("?" for _ in row)+")", row)
    sid = raw["session_id"]
    if not active:
        destination = svc.config.output_root / svc.store.publication(sid)["final_name"]
        destination.mkdir(parents=True)
        for name in ("result.json", "result.xlsx", "report.md", "run.jsonl", "manifest.json"):
            shutil.copyfile(fixture/name, destination/name)
    return svc, sid


def test_old_active_session_requires_new_analysis(tmp_path):
    svc, sid = restore_historical(tmp_path, active=True)
    reply = svc.call("reqmap_get_session", dict(session_id=sid))
    assert reply.error.code == "SESSION_CONTRACT_MISMATCH"


def test_old_finalized_artifacts_remain_historical(tmp_path):
    svc, sid = restore_historical(tmp_path)
    before = {p: p.read_bytes() for p in svc.config.output_root.rglob("*") if p.is_file()}
    reply = svc.call("reqmap_get_result", dict(session_id=sid))
    assert reply.ok, reply
    assert reply.data["historical"] is True
    assert reply.data["schema_version"] == "1.0"
    blocked = svc.call("reqmap_finalize", dict(session_id=sid, request_id="new-finalize", expected_revision=3))
    assert blocked.error.code == "SESSION_CLOSED"
    assert {p: p.read_bytes() for p in before} == before


def test_repeated_source_id_does_not_share_binding(tmp_path):
    svc = AgentService(make_agent_config(tmp_path))
    sid = start(svc, (TEXT, TEXT))
    first = select_source(svc, sid, TEXT)
    second = select_source(svc, sid, TEXT, "REQ-0002", 1)
    assert first.ok and second.ok
    assert first.data["context_id"] != second.data["context_id"]
    assert first.data["payload"]["obligation"]["obligation_id"] != second.data["payload"]["obligation"]["obligation_id"]


def test_unknown_source_placeholder_needs_no_model_decomposition():
    from reqmap.decomposition import decompose
    req = replace(requirement(), text="Неподдержанная форма с неизвестным условием")
    class NoModel:
        def complete_json(self, *args): raise AssertionError("unknown grammar must not ask model to invent semantics")
    result = decompose(NoModel(), req, None)
    assert result.analysis_state is AnalysisState.COMPLETED
    assert result.atoms[0].source_quote == req.text
    assert result.atoms[0].mandatory is True


def test_legacy_proposals_cannot_bypass_runtime_contract(tmp_path):
    from reqmap.decomposition import accept_decomposition
    from reqmap.proposals import ProposalError
    with pytest.raises(ProposalError, match="PROPOSAL_SCHEMA"):
        accept_decomposition(replace(requirement(), text=TEXT), {"atoms": [{"text":TEXT,"source_quote":TEXT,"mandatory":True}]})


def test_catalog_change_invalidates_context(tmp_path):
    from reqmap.agent_knowledge import load_agent_knowledge
    from tests.binding_factories import write_catalog, sign_catalog, predicate_raw
    config = make_agent_config(tmp_path, AnalysisProfile.DEEP)
    knowledge = load_agent_knowledge(config).kb
    path = write_catalog(tmp_path/"bindings", knowledge)
    key = config.knowledge_trust.allowed_signers_path.parent/"signing_key"
    sign_catalog(path, key)
    svc = AgentService(replace(config, binding_catalog_path=path))
    sid = start(svc, (TEXT,))
    assert select_source(svc, sid, TEXT).ok
    changed = predicate_raw(knowledge)
    changed["annotation_version"] = "2"
    write_catalog(path, knowledge, predicates=[changed])
    sign_catalog(path, key)
    reply = AgentService(svc.config).call("reqmap_get_atom_context", dict(session_id=sid, atom_id="REQ-0001-A001"))
    assert reply.error.code == "SESSION_CONTRACT_MISMATCH"


@pytest.mark.parametrize("field,value", [("proposal_schema_version", None), ("binding_engine_version", None), ("grammar_sha256", None)])
def test_missing_current_contract_fields_are_not_defaulted(tmp_path, field, value):
    from reqmap.binding_runtime import require_session_contract
    from reqmap.errors import ReqmapError
    svc = AgentService(make_agent_config(tmp_path))
    sid = start(svc, (TEXT,))
    settings = replace(svc.store.read(sid).seed.settings, **{field:value})
    with pytest.raises(ReqmapError, match="Контракт") as failed:
        require_session_contract(settings)
    assert failed.value.code == "SESSION_CONTRACT_MISMATCH"


@pytest.mark.parametrize("profile", list(AnalysisProfile))
def test_unknown_source_completes_without_model_invention(tmp_path, profile):
    from tests.test_deep_pipeline import _request
    config = make_agent_config(tmp_path/"agent", profile)
    app_config = replace(config_for(knowledge_path=config.knowledge_path), analysis_profile=profile,
                         knowledge_trust=config.knowledge_trust)
    class NoModel:
        def preflight(self): pass
        def complete_json(self, *args): raise AssertionError("unknown source needs no model invention")
    if profile is AnalysisProfile.DEEP:
        from reqmap.deep_pipeline import analyze_deep as analyze
    else:
        from reqmap.pipeline import analyze
    result = analyze(_request(tmp_path, "Неподдержанная форма с условием"), app_config, NoModel())
    row = result.requirements[0]
    assert row.analysis_state is AnalysisState.COMPLETED
    assert row.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
