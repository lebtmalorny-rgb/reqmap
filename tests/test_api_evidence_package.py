"""Operation-specific production evidence must prove only its complete source scope."""
from dataclasses import replace
from pathlib import Path

import pytest

from reqmap.binding_catalog import load_binding_catalog
from reqmap.binding_models import BindingContext
from reqmap.binding_source import bind_source, canonical_atoms
from reqmap.knowledge import load_knowledge
from reqmap.mapping import accept_mapping
from reqmap.models import SupportStatus
from reqmap.retrieval import retrieve
from tests.factories import requirement
from tests.test_mapping import raw_mapping, response, step


KB = Path('knowledge/epoxy-2025.1')
CATALOG = Path('knowledge/bindings/epoxy-2025.1-api')
# Literal expectations, not generated from production annotations.
CASES = [
    ('NOVA-VM-CREATE', 'nova', 'create', 'vm', 'Nova должна создавать ВМ через API', 'POST /servers'),
    ('NOVA-VM-READ', 'nova', 'read', 'vm', 'Nova должна получать сведения о ВМ через API', 'GET /servers/{server_id}'),
    ('NOVA-VM-RENAME', 'nova', 'rename', 'vm', 'Nova должна переименовывать ВМ через API', 'PUT /servers/{server_id}'),
    ('NOVA-VM-DELETE', 'nova', 'delete', 'vm', 'Nova должна удалять ВМ через API', 'DELETE /servers/{server_id}'),
    ('NEUTRON-NETWORK-CREATE', 'neutron', 'create', 'network', 'Neutron должна создавать сеть через API', 'POST /v2.0/networks'),
    ('NEUTRON-NETWORK-READ', 'neutron', 'read', 'network', 'Neutron должна получать сведения о сети через API', 'GET /v2.0/networks/{network_id}'),
    ('NEUTRON-NETWORK-RENAME', 'neutron', 'rename', 'network', 'Neutron должна переименовывать сеть через API', 'PUT /v2.0/networks/{network_id}'),
    ('NEUTRON-NETWORK-DELETE', 'neutron', 'delete', 'network', 'Neutron должна удалять сеть через API', 'DELETE /v2.0/networks/{network_id}'),
    ('NEUTRON-PORT-CREATE', 'neutron', 'create', 'port', 'Neutron должна создавать сетевой порт через API', 'POST /v2.0/ports'),
    ('NEUTRON-PORT-READ', 'neutron', 'read', 'port', 'Neutron должна получать сведения о сетевом порте через API', 'GET /v2.0/ports/{port_id}'),
    ('NEUTRON-PORT-RENAME', 'neutron', 'rename', 'port', 'Neutron должна переименовывать сетевой порт через API', 'PUT /v2.0/ports/{port_id}'),
    ('NEUTRON-PORT-DELETE', 'neutron', 'delete', 'port', 'Neutron должна удалять сетевой порт через API', 'DELETE /v2.0/ports/{port_id}'),
    ('CINDER-VOLUME-CREATE', 'cinder', 'create', 'volume', 'Cinder должна создавать блочный том через API', 'POST /v3/{project_id}/volumes'),
    ('CINDER-VOLUME-READ', 'cinder', 'read', 'volume', 'Cinder должна получать сведения о блочном томе через API', 'GET /v3/{project_id}/volumes/{volume_id}'),
    ('CINDER-VOLUME-RENAME', 'cinder', 'rename', 'volume', 'Cinder должна переименовывать блочный том через API', 'PUT /v3/{project_id}/volumes/{volume_id}'),
    ('CINDER-VOLUME-DELETE', 'cinder', 'delete', 'volume', 'Cinder должна удалять блочный том через API', 'DELETE /v3/{project_id}/volumes/{volume_id}'),
]


@pytest.fixture(scope='module')
def package():
    kb = load_knowledge(KB)
    assert CATALOG.is_dir(), 'reviewed production binding catalog is missing'
    return kb, load_binding_catalog(CATALOG, kb)


def submit(package, case, text=None, *, predicate=True, evidence_key=None):
    key, actor, action, obj, canonical, operation = case
    kb, catalog = package
    binding = bind_source(replace(requirement(), text=text or canonical))
    atom = canonical_atoms(binding)[0]
    evidence_id = 'E-' + (evidence_key or key)
    claim = kb.evidence[evidence_id].claim_ru
    proposal = response(raw_mapping(actor, (evidence_id,), role_ru=claim,
        steps=[step('runtime', api_operation=operation, action_ru=claim)]), supported_aspects=(claim,))
    proposal.update(obligation_id=atom.obligation_id, predicate_ids=['P-' + key] if predicate else [])
    candidates = retrieve(kb, binding.source_text, (), 8)
    return accept_mapping(atom, candidates, kb, proposal, binding_context=reviewed_context(binding, catalog))


def test_api_package_mcp_finalizes_positive_and_contrast_rows(tmp_path, package):
    import json
    from reqmap.agent_service import AgentService
    from tests.agent_support import make_agent_config
    config = replace(make_agent_config(tmp_path), binding_catalog_path=CATALOG.absolute())
    texts = [c[4] for c in CASES] + [c[4] + ' за одну миллисекунду' for c in CASES]
    from tests.source_context_support import with_reviewed_texts
    config = with_reviewed_texts(config, texts)
    service = AgentService(config)
    started = service.call('reqmap_start_session', dict(request_id='api-start', source=dict(kind='texts', texts=texts)))
    assert started.ok, started.error
    sid, revision = started.data['session_id'], 0
    page = service.call('reqmap_get_session', dict(session_id=sid))
    assert page.ok, page.error
    for i, row in enumerate(page.data['requirements']):
        key, actor, action, obj, text, operation = CASES[i % len(CASES)]
        rid = row['requirement']['requirement_id']
        atoms = service.call('reqmap_submit_atoms', dict(session_id=sid, requirement_id=rid,
            request_id='atoms-'+rid, expected_revision=revision, proposal=row['decomposition_context']['canonical_proposal']))
        assert atoms.ok, atoms.error
        revision = atoms.data['revision']
        atom = atoms.data['atoms'][0]
        context = service.call('reqmap_get_atom_context', dict(session_id=sid, atom_id=atom['atom_id']))
        assert context.ok, context.error
        claim = package[0].evidence['E-'+key].claim_ru
        proposal = response(raw_mapping(actor, ('E-'+key,), role_ru=claim,
                            steps=[step('runtime', api_operation=operation, action_ru=claim)]), supported_aspects=(claim,))
        proposal.update(obligation_id=atom['obligation_id'], predicate_ids=['P-'+key])
        mapped = service.call('reqmap_submit_mapping', dict(session_id=sid, atom_id=atom['atom_id'],
            context_id=context.data['context_id'], request_id='map-'+rid, expected_revision=revision, proposal=proposal))
        assert mapped.ok, mapped.error
        revision = mapped.data['revision']
    final = service.call('reqmap_finalize', dict(session_id=sid, request_id='final', expected_revision=revision))
    assert final.ok, final.error
    files = list(config.output_root.rglob('result.json'))
    assert len(files) == 1
    result = json.loads(files[0].read_text())
    assert [r['support_status'] for r in result['requirements']] == ['supported'] * 16 + ['insufficient_evidence'] * 16
    assert {p.name for p in files[0].parent.iterdir() if p.is_file()} == {'result.json','result.xlsx','report.md','run.jsonl','manifest.json'}


@pytest.mark.parametrize('case', CASES, ids=lambda c:c[0])
def test_api_grammar_covers_declared_operation(case):
    key, actor, action, obj, text, operation = case
    binding = bind_source(replace(requirement(), text=text))
    obligation, = binding.obligations
    assert (obligation.actor, obligation.action, obligation.object) == (actor, action, obj)
    assert not binding.unresolved_fragments


@pytest.mark.parametrize('case', CASES, ids=lambda c:c[0])
def test_api_package_binds_and_retrieves_specific_operation(package, case):
    key, actor, action, obj, text, operation = case
    kb, _ = package
    candidate, = [c for c in retrieve(kb, text, (), 8) if c.capability_id == 'CAP-' + key]
    assert candidate.evidence_ids == ('E-' + key,)
    assert operation in kb.evidence['E-' + key].claim_ru
    result = submit(package, case)
    assert result.support_status is SupportStatus.SUPPORTED, result.diagnostics
    assert result.supported_aspects == (text,)
    assert result.binding_decision.predicate_ids == ('P-' + key,)


@pytest.mark.parametrize('case', CASES, ids=lambda c:c[0])
@pytest.mark.parametrize('variant', ['time', 'failure', 'gui', 'negative', 'extra', 'release', 'quota'])
def test_api_package_cannot_prove_uncovered_variant(package, case, variant):
    text = case[4]
    changed = {
        'time': text + ' за одну миллисекунду',
        'failure': text + ' при отказе узла',
        'gui': text.replace('API', 'GUI'),
        'negative': text.replace('должна', 'не должна'),
        'extra': text + ' и отправлять SMS',
        'release': text + ' в OpenStack 2026.1',
        'quota': text + ' независимо от квот и прав доступа',
    }[variant]
    result = submit(package, case, changed)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert result.supported_aspects == ()
    assert result.binding_decision.uncovered


@pytest.mark.parametrize('case', CASES, ids=lambda c:c[0])
def test_api_package_requires_selected_predicate(package, case):
    assert submit(package, case, predicate=False).support_status is SupportStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize('text,key', [
    ('Создать сетевой порт через API', 'NEUTRON-PORT-CREATE'),
    ('Создание сетевого порта через API', 'NEUTRON-PORT-CREATE'),
    ('Система должна создавать сеть через API', 'NEUTRON-NETWORK-CREATE'),
    ('Система должна получать сведения о виртуальной машине через API', 'NOVA-VM-READ'),
    ('Переименовать блочный том через API', 'CINDER-VOLUME-RENAME'),
])
def test_known_resource_without_service_name_uses_same_proof(package, text, key):
    case = next(c for c in CASES if c[0] == key)
    assert submit(package, case, text).support_status is SupportStatus.SUPPORTED


@pytest.mark.parametrize('text', [
    'AD/LDAPS', 'LACP на хостах', 'Система должна изменять ВМ через API',
    'Система должна изменять размер блочного тома через API',
    'Cinder должна создавать ВМ через API',
])
def test_uncovered_domains_and_object_substitution_do_not_gain_support(package, text):
    from reqmap.proposals import ProposalError
    try:
        result = submit(package, CASES[0], text)
    except ProposalError as exc:
        # An irrelevant component/evidence can be rejected even before the binding gate.
        assert exc.kind == 'semantic'
    else:
        assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE


def test_port_retrieval_works_without_neutron_and_wrong_domains_have_no_proof(package):
    kb, _ = package
    candidates = retrieve(kb, 'Создать сетевой порт через API', (), 8)
    assert candidates[0].component_id == 'neutron'
    assert candidates[0].evidence_ids == ('E-NEUTRON-PORT-CREATE',)
    for text in ('Интеграция AD/LDAPS', 'LACP на хостах'):
        assert not any(e.startswith(('E-NOVA-VM-', 'E-NEUTRON-NETWORK-', 'E-NEUTRON-PORT-', 'E-CINDER-VOLUME-'))
                       for c in retrieve(kb, text, (), 8) for e in c.evidence_ids)


def test_catalog_maintenance_rebuild_is_deterministic_and_never_marks_reviewed(tmp_path, package):
    import importlib.util
    import json
    import shutil
    from reqmap.errors import ReqmapError
    assert importlib.util.find_spec('tools.kb.build_binding_catalog'), 'catalog maintenance builder is missing'
    from tools.kb.build_binding_catalog import build_binding_catalog
    copied = tmp_path / 'catalog'
    shutil.copytree(CATALOG, copied)
    first = build_binding_catalog(copied, KB)
    data = (copied/'binding-manifest.json').read_bytes()
    assert build_binding_catalog(copied, KB) == first
    assert (copied/'binding-manifest.json').read_bytes() == data
    rows = [json.loads(line) for line in (copied/'predicates.jsonl').read_text().splitlines()]
    rows[0]['review_state'] = 'needs_review'
    (copied/'predicates.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    with pytest.raises(ReqmapError):
        build_binding_catalog(copied, KB)
    assert (copied/'binding-manifest.json').read_bytes() == data
    assert json.loads((copied/'predicates.jsonl').read_text().splitlines()[0])['review_state'] == 'needs_review'

from tests.source_context_support import reviewed_context
