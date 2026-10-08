"""Identity object operations must not stand in for grants, LDAP or host accounts."""
from dataclasses import replace
from pathlib import Path

import pytest

from reqmap.binding_catalog import load_binding_catalog
from reqmap.binding_models import BindingContext
from reqmap.binding_source import bind_source, canonical_atoms
from reqmap.knowledge import load_knowledge
from reqmap.mapping import accept_mapping
from reqmap.models import SupportStatus
from reqmap.proposals import ProposalError
from reqmap.retrieval import retrieve
from tests.factories import requirement
from tests.test_mapping import raw_mapping, response, step


KB = Path('knowledge/epoxy-2025.1')
CATALOG = Path('knowledge/bindings/epoxy-2025.1-api')
# Independent expectations: no case is generated from production annotations.
CASES = [
    ('KEYSTONE-USER-CREATE', 'create', 'user', 'Keystone должна создавать пользователя через API', 'POST /v3/users'),
    ('KEYSTONE-USER-READ', 'read', 'user', 'Keystone должна получать сведения о пользователе через API', 'GET /v3/users/{user_id}'),
    ('KEYSTONE-USER-RENAME', 'rename', 'user', 'Keystone должна переименовывать пользователя через API', 'PATCH /v3/users/{user_id}'),
    ('KEYSTONE-USER-DELETE', 'delete', 'user', 'Keystone должна удалять пользователя через API', 'DELETE /v3/users/{user_id}'),
    ('KEYSTONE-PROJECT-CREATE', 'create', 'project', 'Keystone должна создавать проект через API', 'POST /v3/projects'),
    ('KEYSTONE-PROJECT-READ', 'read', 'project', 'Keystone должна получать сведения о проекте через API', 'GET /v3/projects/{project_id}'),
    ('KEYSTONE-PROJECT-RENAME', 'rename', 'project', 'Keystone должна переименовывать проект через API', 'PATCH /v3/projects/{project_id}'),
    ('KEYSTONE-PROJECT-DELETE', 'delete', 'project', 'Keystone должна удалять проект через API', 'DELETE /v3/projects/{project_id}'),
    ('KEYSTONE-ROLE-CREATE', 'create', 'role', 'Keystone должна создавать роль через API', 'POST /v3/roles'),
    ('KEYSTONE-ROLE-READ', 'read', 'role', 'Keystone должна получать сведения о роли через API', 'GET /v3/roles/{role_id}'),
    ('KEYSTONE-ROLE-RENAME', 'rename', 'role', 'Keystone должна переименовывать роль через API', 'PATCH /v3/roles/{role_id}'),
    ('KEYSTONE-ROLE-DELETE', 'delete', 'role', 'Keystone должна удалять роль через API', 'DELETE /v3/roles/{role_id}'),
]


@pytest.fixture(scope='module')
def package():
    kb = load_knowledge(KB)
    return kb, load_binding_catalog(CATALOG, kb)


def proposal_for(kb, atom, case, *, predicate=True):
    key, _, _, _, operation = case
    claim = kb.evidence['E-' + key].claim_ru
    proposal = response(raw_mapping('keystone', ('E-' + key,), role_ru=claim,
        steps=[step('runtime', api_operation=operation, action_ru=claim)]),
        supported_aspects=(claim,))
    proposal.update(obligation_id=atom.obligation_id,
                    predicate_ids=['P-' + key] if predicate else [])
    return proposal


def submit(package, case, text=None, *, predicate=True):
    kb, catalog = package
    binding = bind_source(replace(requirement(), text=text or case[3]))
    atom, = canonical_atoms(binding)
    return accept_mapping(atom, retrieve(kb, binding.source_text, (), 8), kb,
        proposal_for(kb, atom, case, predicate=predicate),
        binding_context=reviewed_context(binding, catalog))


@pytest.mark.parametrize('case', CASES, ids=lambda c: c[0])
def test_keystone_grammar_preserves_complete_operation(case):
    _, action, obj, text, _ = case
    binding = bind_source(replace(requirement(), text=text))
    obligation, = binding.obligations
    assert (obligation.actor, obligation.action, obligation.object) == ('keystone', action, obj)
    assert obligation.source_quote == text
    assert not binding.unresolved_fragments


@pytest.mark.parametrize('case', CASES, ids=lambda c: c[0])
def test_keystone_specific_operation_is_retrieved_and_supported(package, case):
    key, action, obj, text, operation = case
    kb, catalog = package
    candidate, = [c for c in retrieve(kb, text, (), 8) if c.capability_id == 'CAP-' + key]
    assert candidate.evidence_ids == ('E-' + key,)
    evidence = kb.evidence['E-' + key]
    assert operation in evidence.claim_ru and operation in evidence.locator
    predicate = catalog.predicates['P-' + key]
    assert (predicate.actor, predicate.action, predicate.object) == ('keystone', action, obj)
    assert predicate.evidence_ids == ('E-' + key,)
    assert predicate.review_state == 'reviewed'
    result = submit(package, case)
    assert result.support_status is SupportStatus.SUPPORTED, result.diagnostics
    assert result.supported_aspects == (text,)
    assert result.binding_decision.predicate_ids == ('P-' + key,)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c[0])
@pytest.mark.parametrize('variant', ['time', 'gui', 'failure', 'negation', 'ldap',
                                    'host', 'rights', 'extra', 'release', 'backend'])
def test_identity_api_does_not_prove_additional_obligations(package, case, variant):
    text = case[3]
    changed = {
        'time': text + ' за одну миллисекунду',
        'gui': text.replace('API', 'GUI'),
        'failure': text + ' при отказе узла',
        'negation': text.replace('должна', 'не должна'),
        'ldap': text + ' в Active Directory по LDAPS',
        'host': text + ' на гипервизоре',
        'rights': text + ' независимо от прав доступа',
        'extra': text + ' и назначать пользователю права администратора',
        'release': text + ' в OpenStack 2026.1',
        'backend': text + ' при любом backend-драйвере',
    }[variant]
    result = submit(package, case, changed)
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert result.supported_aspects == ()
    assert result.binding_decision.uncovered


@pytest.mark.parametrize('case', CASES, ids=lambda c: c[0])
def test_identity_operation_requires_selected_reviewed_predicate(package, case):
    assert submit(package, case, predicate=False).support_status is SupportStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize('text,key', [
    ('Keystone должна обеспечивать создание пользователя через REST API', 'KEYSTONE-USER-CREATE'),
    ('Keystone должен удалить проект через API', 'KEYSTONE-PROJECT-DELETE'),
    ('Keystone должна обеспечивать переименование роли через API', 'KEYSTONE-ROLE-RENAME'),
    ('Keystone должна обеспечивать получение сведений о пользователе через API', 'KEYSTONE-USER-READ'),
])
def test_explicit_identity_paraphrases(package, text, key):
    assert submit(package, next(c for c in CASES if c[0] == key), text).support_status is SupportStatus.SUPPORTED


@pytest.mark.parametrize('text', [
    'Создать пользователя через API', 'Система должна создавать пользователя через API',
    'Создание проекта через API', 'Система должна удалять роль через API',
    'Keystone должна назначать роль пользователю в проекте через API',
    'Keystone должна изменять права пользователя через API',
    'Keystone должна изменять пароль пользователя через API',
    'Создание пользователей на гипервизоре',
])
def test_identity_scope_is_never_inferred_from_generic_accounts(package, text):
    binding = bind_source(replace(requirement(), text=text))
    assert binding.obligations[0].parse_state == 'unresolved'
    # Offer relevant-looking evidence directly to isolate source binding from
    # retrieval's earlier rejection of irrelevant user-operation candidates.
    kb, catalog = package
    atom, = canonical_atoms(binding)
    result = accept_mapping(atom, retrieve(kb, CASES[0][3], (), 8), kb,
        proposal_for(kb, atom, CASES[0]), binding_context=reviewed_context(binding, catalog))
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert result.supported_aspects == ()


@pytest.mark.parametrize('text', [
    'Nova должна создавать пользователя через API',
    'Keystone должна создавать ВМ через API',
    'Keystone должна создавать проект через API',
    'Keystone должна создавать роль через API',
])
def test_user_evidence_cannot_prove_wrong_service_or_object(package, text):
    try:
        result = submit(package, CASES[0], text)
    except ProposalError as exc:
        assert exc.kind == 'semantic'
    else:
        assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
        assert result.supported_aspects == ()


def test_identity_mcp_preserves_repeated_occurrences_and_exports(package, tmp_path):
    import json
    from reqmap.agent_service import AgentService
    from tests.agent_support import make_agent_config
    kb, _ = package
    config = replace(make_agent_config(tmp_path), binding_catalog_path=CATALOG.absolute())
    texts = [c[3] for c in CASES] + [CASES[0][3] + ' в Active Directory по LDAPS',
                                   CASES[0][3] + '; ' + CASES[0][3]]
    from tests.source_context_support import with_reviewed_texts
    config = with_reviewed_texts(config, texts)
    service = AgentService(config)
    started = service.call('reqmap_start_session', dict(request_id='start', source=dict(kind='texts', texts=texts)))
    assert started.ok, started.error
    sid, revision = started.data['session_id'], 0
    page = service.call('reqmap_get_session', dict(session_id=sid))
    assert page.ok, page.error
    for i, row in enumerate(page.data['requirements']):
        rid = row['requirement']['requirement_id']
        atoms = service.call('reqmap_submit_atoms', dict(session_id=sid, requirement_id=rid,
            request_id='atoms-' + rid, expected_revision=revision,
            proposal=row['decomposition_context']['canonical_proposal']))
        assert atoms.ok, atoms.error
        revision = atoms.data['revision']
        for raw_atom in atoms.data['atoms']:
            from reqmap.binding_source import decode_atomic_claim
            atom = decode_atomic_claim(raw_atom)
            context = service.call('reqmap_get_atom_context', dict(session_id=sid, atom_id=atom.atom_id))
            assert context.ok, context.error
            mapped = service.call('reqmap_submit_mapping', dict(session_id=sid, atom_id=atom.atom_id,
                context_id=context.data['context_id'], request_id='map-' + atom.atom_id,
                expected_revision=revision, proposal=proposal_for(kb, atom, CASES[i] if i < 12 else CASES[0])))
            assert mapped.ok, mapped.error
            revision = mapped.data['revision']
    final = service.call('reqmap_finalize', dict(session_id=sid, request_id='final', expected_revision=revision, allow_partial=False))
    assert final.ok, final.error
    assert service.call('reqmap_get_result', dict(session_id=sid)).ok
    path, = config.output_root.rglob('result.json')
    result = json.loads(path.read_text())
    assert [r['support_status'] for r in result['requirements']] == ['supported'] * 12 + ['insufficient_evidence', 'supported']
    assert {p.name for p in path.parent.iterdir()} == {'result.json', 'result.xlsx', 'report.md', 'run.jsonl', 'manifest.json'}
    repeated = result['requirements'][-1]['atom_results']
    assert len(repeated) == 2
    assert [r['atom']['source_quote'] for r in repeated] == [CASES[0][3]] * 2
    assert repeated[0]['atom']['atom_id'] != repeated[1]['atom']['atom_id']
    assert repeated[1]['atom']['source_spans'][0]['start'] == len(CASES[0][3]) + 2

from tests.source_context_support import reviewed_context
