"""A complete API label and exact operation must agree before evidence is usable."""
from dataclasses import replace
from pathlib import Path

import pytest

from reqmap.binding_catalog import load_binding_catalog
from reqmap.binding_source import bind_source, canonical_atoms, atom_selection_proposal, validate_atom_selection
from reqmap.knowledge import load_knowledge
from reqmap.mapping import accept_mapping
from reqmap.models import SupportStatus
from reqmap.retrieval import retrieve
from tests.factories import requirement
from tests.source_context_support import reviewed_context
from tests.test_mapping import raw_mapping, response, step


# Independent, literal operation expectations. No source workbook or IDs are shipped.
CASES = [
    ('CINDER-VOLUME-CREATE', 'Создание тома: вызов POST /v3/{project_id}/volumes; ', 'cinder', 'create', 'volume', 'POST /v3/{project_id}/volumes'),
    ('CINDER-VOLUME-LIST-DETAIL', 'Список доступных томов с деталями: вызов GET /v3/{project_id}/volumes/detail; ', 'cinder', 'list_detail', 'volume', 'GET /v3/{project_id}/volumes/detail'),
    ('CINDER-VOLUME-LIST', 'Список доступных томов: вызов GET /v3/{project_id}/volumes;', 'cinder', 'list', 'volume', 'GET /v3/{project_id}/volumes'),
    ('CINDER-VOLUME-READ', 'Показ деталей тома: вызов GET /v3/{project_id}/volumes/{volume_id};', 'cinder', 'read', 'volume', 'GET /v3/{project_id}/volumes/{volume_id}'),
    ('CINDER-VOLUME-DELETE', 'Удаление тома: вызов DELETE /v3/{project_id}/volumes/{volume_id};', 'cinder', 'delete', 'volume', 'DELETE /v3/{project_id}/volumes/{volume_id}'),
    ('KEYSTONE-PROJECT-CREATE', 'Создание проекта: POST /v3/projects;', 'keystone', 'create', 'project', 'POST /v3/projects'),
    ('KEYSTONE-PROJECT-DELETE', 'Удаление проекта: DELETE /v3/projects/{project_id};', 'keystone', 'delete', 'project', 'DELETE /v3/projects/{project_id}'),
    ('KEYSTONE-ROLE-CREATE', 'Создание роли: POST /v3/roles;', 'keystone', 'create', 'role', 'POST /v3/roles'),
    ('KEYSTONE-ROLE-DELETE', 'Удаление роли: DELETE /v3/roles/{role_id};', 'keystone', 'delete', 'role', 'DELETE /v3/roles/{role_id}'),
    ('KEYSTONE-PROJECT-USER-ROLE-ASSIGN', 'Назначение роли пользователю проекта: PUT /v3/projects/{project_id}/users/{user_id}/roles/{role_id};', 'keystone', 'assign', 'project_user_role', 'PUT /v3/projects/{project_id}/users/{user_id}/roles/{role_id}'),
    ('KEYSTONE-DOMAIN-USER-ROLE-ASSIGN', 'Назначение роли пользователю домена: PUT /v3/domains/{domain_id}/users/{user_id}/roles/{role_id};', 'keystone', 'assign', 'domain_user_role', 'PUT /v3/domains/{domain_id}/users/{user_id}/roles/{role_id}'),
    ('KEYSTONE-USER-DELETE', 'Удаление пользователя: DELETE /v3/users/{user_id};', 'keystone', 'delete', 'user', 'DELETE /v3/users/{user_id}'),
]


@pytest.mark.parametrize('case', CASES, ids=lambda c: c[0])
def test_complete_api_row_retains_all_source_characters(case):
    _, text, actor, action, obj, _ = case
    binding = bind_source(replace(requirement(), text=text))
    obligation, = binding.obligations
    assert (obligation.parse_state, obligation.actor, obligation.action, obligation.object,
            obligation.interface, obligation.direction) == ('bound', actor, action, obj, 'api', 'capability')
    assert obligation.source_quote == text
    assert [(s.start, s.end, s.quote) for s in obligation.source_spans] == [(0, len(text), text)]
    assert ''.join(f.span.quote for f in binding.fragments) == text
    assert not binding.unresolved_fragments
    assert validate_atom_selection(binding, atom_selection_proposal(binding)) == canonical_atoms(binding)


@pytest.mark.parametrize('text', [
    'Создать пользователя: PATCH /v3/users/{user_id};',
    'Модификация данных пользователя: POST /v3/users;',
    'Создание тома: вызов DELETE /v3/{project_id}/volumes;',
    'Список доступных томов с деталями: вызов GET /v3/{project_id}/volumes;',
    'Список доступных томов: вызов GET /v3/{project_id}/volumes/detail;',
    'Назначение роли пользователю проекта: PUT /v3/domains/{domain_id}/users/{user_id}/roles/{role_id};',
    'Назначение роли пользователю домена: PUT /v3/projects/{project_id}/users/{user_id}/roles/{role_id};',
    'Удаление пользователя: DELETE /v3/groups/{user_id};',
    'Создание проекта: POST /v3/Projects;',
    'Создание тома: вызов /v3/{project_id}/volumes;',
    'Создание тома: вызов POST /v3/{project_id}/volumes?force=true;',
    'Создание тома: вызов POST /v3/{project_id}/volumes через GUI;',
    'Создание тома: вызов POST /v3/{project_id}/volumes за одну миллисекунду;',
    'Не разрешено создание тома: вызов POST /v3/{project_id}/volumes;',
    'Создание тома: вызов POST /v3/{project_id}/volumes; и назначение прав',
    'Создание проекта: POST /v3/projects; в любом регионе',
])
def test_inconsistent_or_extended_api_row_is_not_partially_accepted(text):
    binding = bind_source(replace(requirement(), text=text))
    obligation, = binding.obligations
    assert obligation.parse_state == 'unresolved'
    assert obligation.source_quote == text
    assert binding.unresolved_fragments


@pytest.fixture(scope='module')
def package():
    kb = load_knowledge(Path('knowledge/epoxy-2025.1'))
    return kb, load_binding_catalog(Path('knowledge/bindings/epoxy-2025.1-api'), kb)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c[0])
def test_api_row_is_supported_only_with_matching_reviewed_evidence(package, case):
    key, text, actor, _, _, operation = case
    kb, catalog = package
    binding = bind_source(replace(requirement(), text=text))
    atom, = canonical_atoms(binding)
    claim = kb.evidence['E-' + key].claim_ru
    proposal = response(raw_mapping(actor, ('E-' + key,), role_ru=claim,
        steps=[step('runtime', api_operation=operation, action_ru=claim)]), supported_aspects=(claim,))
    proposal.update(obligation_id=atom.obligation_id, predicate_ids=['P-' + key])
    result = accept_mapping(atom, retrieve(kb, text, (), 8), kb, proposal,
                            binding_context=reviewed_context(binding, catalog))
    assert result.support_status is SupportStatus.SUPPORTED, result.diagnostics
    assert result.supported_aspects == (text,)
    assert result.binding_decision.predicate_ids == ('P-' + key,)


@pytest.mark.parametrize('first,second', [(1, 2), (9, 10)])
def test_similar_api_scopes_cannot_substitute_for_each_other(package, first, second):
    kb, catalog = package
    _, text, actor, _, _, _ = CASES[first]
    key, other_text, _, _, _, operation = CASES[second]
    binding = bind_source(replace(requirement(), text=text))
    atom, = canonical_atoms(binding)
    claim = kb.evidence['E-' + key].claim_ru
    proposal = response(raw_mapping(actor, ('E-' + key,), role_ru=claim,
        steps=[step('runtime', api_operation=operation, action_ru=claim)]), supported_aspects=(claim,))
    proposal.update(obligation_id=atom.obligation_id, predicate_ids=['P-' + key])
    result = accept_mapping(atom, retrieve(kb, other_text, (), 8), kb, proposal,
                            binding_context=reviewed_context(binding, catalog))
    assert result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert result.supported_aspects == ()
