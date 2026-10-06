"""Reconstruct derived domain state by revalidating accepted proposals."""
from reqmap.binding_runtime import require_session_contract
from reqmap.agent_knowledge import VerifiedKnowledge
from reqmap.agent_types import SessionRecord, SessionView
from reqmap.decomposition import accept_decomposition
from reqmap.errors import ReqmapError
from reqmap.proposals import ProposalError


def replay_session(record: SessionRecord, knowledge: VerifiedKnowledge) -> SessionView:
    if record.seed.settings.knowledge_sha256 != knowledge.knowledge_sha256:
        raise ReqmapError('KNOWLEDGE_CHANGED', 'Snapshot изменился; требуется новая сессия.')
    require_session_contract(record.seed.settings, knowledge)
    requirements = record.seed.input_snapshot.requirements
    by_id = {r.requirement_id:r for r in requirements}
    atoms, mappings = {}, {}
    try:
        for event in record.events:
            if not event.accepted:
                continue
            args = event.arguments
            if event.operation == 'reqmap_submit_atoms':
                rid = args['requirement_id']
                for atom in atoms.get(rid, ()):
                    mappings.pop(atom.atom_id, None)
                atoms[rid] = accept_decomposition(by_id[rid], args['proposal'])
            elif event.operation == 'reqmap_submit_mapping':
                from reqmap.agent_context import accept_context_mapping
                view = SessionView(record,requirements,atoms,mappings)
                mappings[args['atom_id']] = accept_context_mapping(view,knowledge,args)
            elif event.operation != 'reqmap_finalize':
                raise ValueError('Unknown accepted event')
    except (KeyError, TypeError, ValueError, ProposalError) as exc:
        raise ReqmapError('SESSION_CORRUPT', 'Сохранённые предложения не проходят повторную проверку.') from exc
    return SessionView(record, requirements, atoms, mappings)
