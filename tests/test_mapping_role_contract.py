"""Reproduce the live client's false abstention without weakening the evidence gate."""
from dataclasses import replace
import json

import pytest

from reqmap.binding_models import BindingContext
from reqmap.binding_runtime import validate_persisted_binding
from reqmap.binding_source import bind_source, canonical_atoms
from reqmap.mapping import accept_mapping
from reqmap.models import SupportStatus
from reqmap.retrieval import retrieve
from tests.factories import requirement
from tests.test_api_evidence_package import CASES, CATALOG, package
from tests.test_mapping import raw_mapping, response, step
from tests.test_mapping import kb


def proposal_for(kb, case, quote, field=None):
    key, actor, _, _, _, operation = case
    claim = kb.evidence['E-' + key].claim_ru
    role = quote if field == 'role_ru' else claim
    action = quote if field == 'steps[0].action_ru' else claim
    proposal = response(raw_mapping(actor, ('E-' + key,), role_ru=role,
        steps=[step('runtime', api_operation=operation, action_ru=action)]), supported_aspects=(role,))
    proposal.update(predicate_ids=['P-' + key])
    return proposal


@pytest.mark.parametrize('case', CASES, ids=lambda case: case[0])
@pytest.mark.parametrize('field,code', [('role_ru', 'MAPPING_ROLE_UNGROUNDED'),
                                      ('steps[0].action_ru', 'MAPPING_STEP_UNGROUNDED')])
def test_copied_requirement_identifies_ungrounded_field_and_can_be_repaired(package, case, field, code):
    kb, catalog = package
    binding = bind_source(replace(requirement(), text=case[4]))
    atom, = canonical_atoms(binding)
    context = reviewed_context(binding, catalog)
    candidates = retrieve(kb, atom.text, (), 8)
    raw = proposal_for(kb, case, atom.text, field)
    raw['obligation_id'] = atom.obligation_id
    rejected = accept_mapping(atom, candidates, kb, raw, binding_context=context)
    assert rejected.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    diagnostic, = [d for d in rejected.binding_decision.diagnostics if d.code == code]
    assert diagnostic.field == 'mappings[0].' + field
    assert 'должна' in diagnostic.message_ru
    assert diagnostic.evidence_ids == ('E-' + case[0],)
    assert diagnostic.source_spans == binding.obligations[0].source_spans
    assert not any(d.code == 'EVIDENCE_MISSING' and d.field == 'prior_status'
                   for d in rejected.binding_decision.diagnostics)
    validate_persisted_binding(rejected, context, kb, rejected.mappings, rejected.support_status)
    corrected = proposal_for(kb, case, atom.text)
    corrected['obligation_id'] = atom.obligation_id
    accepted = accept_mapping(atom, candidates, kb, corrected, binding_context=context)
    assert accepted.support_status is SupportStatus.SUPPORTED
    assert accepted.atom == rejected.atom == atom
    assert accepted.supported_aspects == (atom.source_quote,)
    assert not accepted.binding_decision.diagnostics


def test_role_diagnostic_survives_mcp_restart_and_all_exports(tmp_path, package):
    from openpyxl import load_workbook
    from reqmap.agent_service import AgentService
    from tests.agent_support import make_agent_config
    from tests.test_agent_session import start
    from tests.test_binding_sessions import select_source

    svc = AgentService(replace(make_agent_config(tmp_path), binding_catalog_path=CATALOG.absolute()))
    text = CASES[0][4]
    sid = start(svc, (text,))
    context = select_source(svc, sid, text).data
    assert 'claim_ru' in context['rules'] and 'source_quote' in context['rules']
    assert 'claim_ru' in context['response_schema']['mappings'][0]['role_ru']
    raw = proposal_for(package[0], CASES[0], text, 'role_ru')
    raw['obligation_id'] = context['payload']['atom']['obligation_id']
    mapped = svc.call('reqmap_submit_mapping', dict(session_id=sid, atom_id='REQ-0001-A001',
        context_id=context['context_id'], proposal=raw, request_id='map', expected_revision=1))
    assert mapped.ok, mapped.error
    expected = mapped.data['result']
    code = 'MAPPING_ROLE_UNGROUNDED'
    assert code in json.dumps(expected)
    svc = AgentService(svc.config)
    restored = svc.call('reqmap_get_session', dict(session_id=sid))
    assert restored.ok, restored.error
    assert restored.data['requirements'][0]['atom_results'][0] == expected
    final = svc.call('reqmap_finalize', dict(session_id=sid, request_id='final', expected_revision=2))
    assert final.ok, final.error
    assert svc.call('reqmap_get_result', dict(session_id=sid)).ok
    root, = [p.parent for p in svc.config.output_root.rglob('result.json')]
    assert {p.name for p in root.iterdir() if p.is_file()} == {
        'result.json', 'result.xlsx', 'report.md', 'run.jsonl', 'manifest.json'}
    for name in ('result.json', 'report.md'):
        assert code in (root / name).read_text()
    workbook = load_workbook(root / 'result.xlsx', read_only=True)
    try:
        cells = '\n'.join(str(c) for sheet in workbook for row in sheet.values for c in row if c is not None)
        assert code in cells and 'mappings[0].role_ru' in cells and 'должна' in cells
    finally:
        workbook.close()


def test_previous_engine_session_requires_fresh_analysis(tmp_path):
    from reqmap.agent_service import AgentService
    from reqmap.binding_runtime import require_session_contract
    from reqmap.errors import ReqmapError
    from tests.agent_support import make_agent_config
    from tests.test_agent_session import start

    svc = AgentService(make_agent_config(tmp_path))
    sid = start(svc)
    settings = replace(svc.store.read(sid).seed.settings, binding_engine_version='1.0')
    with pytest.raises(ReqmapError) as error:
        require_session_contract(settings)
    assert error.value.code == 'SESSION_CONTRACT_MISMATCH'


def test_second_mapping_has_same_field_path_in_record_and_atom_diagnostic(package):
    knowledge, catalog = package
    binding = bind_source(replace(requirement(), text=CASES[0][4]))
    atom, = canonical_atoms(binding)
    raw = proposal_for(knowledge, CASES[0], atom.text)
    copied = proposal_for(knowledge, CASES[0], atom.text, 'role_ru')
    raw['mappings'].extend(copied['mappings'])
    raw['supported_aspects'].extend(copied['supported_aspects'])
    raw['obligation_id'] = atom.obligation_id
    result = accept_mapping(atom, retrieve(knowledge, atom.text, (), 8), knowledge, raw,
                            binding_context=reviewed_context(binding, catalog))
    diag, = [d for d in result.binding_decision.diagnostics if d.code == 'MAPPING_ROLE_UNGROUNDED']
    assert diag.field == 'mappings[1].role_ru'
    assert diag.message_ru in result.mappings[1].reason_ru
    assert 'mappings[0].role_ru' not in result.mappings[1].reason_ru


def test_host_delivery_step_is_not_reported_as_lexical_gap(kb):
    from reqmap.mapping import _build_result, mapping_text_gaps, validate_atom_result
    from tests.test_mapping import atom as make_atom, design_steps

    steps = design_steps('sysctl')
    steps[1]['action_ru'] = 'Выполнить kolla-ansible reconfigure'
    host = raw_mapping('host_os_kernel_sysctl', ('E-SYSCTL',), phase='designtime',
        implementation_source='kolla_ansible', mechanism='kolla_ansible', relation='host_os_change', steps=steps)
    delivery = raw_mapping('kolla_ansible', ('E-KOLLA',), phase='designtime',
        implementation_source='kolla_ansible', mechanism='kolla_ansible')
    result = validate_atom_result(_build_result(make_atom('Изменить sysctl'), response(host, delivery)), kb)
    assert result.support_status is SupportStatus.SUPPORTED
    assert mapping_text_gaps(result.mappings[0], kb) == ()

from tests.source_context_support import reviewed_context
