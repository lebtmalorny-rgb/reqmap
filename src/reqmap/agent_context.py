"""Deterministic candidate contexts and read-only knowledge discovery."""
import hashlib
from reqmap.agent_input import read_regular_bytes
from reqmap.agent_knowledge import VerifiedKnowledge
from reqmap.agent_types import SessionView
from reqmap.config import AnalysisProfile
from reqmap.deep_mapping import prepare_deep_mapping, accept_deep_mapping
from reqmap.deep_retrieval import retrieve_deep
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.mapping import prepare_mapping, accept_mapping
from reqmap.models import to_dict
from reqmap.prompts import (MAPPING_PROMPT, DEEP_MAPPING_PROMPT,
    PROMPT_MAPPING_VERSION, PROMPT_DEEP_MAPPING_VERSION)
from reqmap.retrieval import retrieve


def atom_candidates(view, atom_id, knowledge):
    atom = next((a for atoms in view.atoms_by_requirement.values() for a in atoms if a.atom_id == atom_id), None)
    if atom is None:
        raise ReqmapError('ATOM_NOT_FOUND','Атом не найден в текущей версии сессии.')
    req = next(r for r in view.requirements if r.requirement_id == atom.requirement_id)
    deep = view.record.seed.settings.analysis_profile is AnalysisProfile.DEEP
    retrieval = (retrieve_deep if deep else retrieve)(knowledge.kb,atom.text,req.source_hints,view.record.seed.settings.top_k)
    return atom, req, retrieval


def get_atom_context(view: SessionView, atom_id: str, knowledge: VerifiedKnowledge) -> dict[str, object]:
    atom, req, candidates = atom_candidates(view,atom_id,knowledge)
    deep = view.record.seed.settings.analysis_profile is AnalysisProfile.DEEP
    payload = (prepare_deep_mapping if deep else prepare_mapping)(atom,candidates,knowledge.kb)
    signature = dict(session_id=view.record.session_id,atom=to_dict(atom),hints=to_dict(req.source_hints),
        settings=to_dict(view.record.seed.settings),prompt_version=PROMPT_DEEP_MAPPING_VERSION if deep else PROMPT_MAPPING_VERSION)
    return dict(session_id=view.record.session_id,revision=view.record.revision,
        context_id=hashlib.sha256(canonical_json_bytes(signature)).hexdigest(),
        rules=DEEP_MAPPING_PROMPT if deep else MAPPING_PROMPT,response_schema=payload['response_schema'],payload=payload)


def accept_context_mapping(view: SessionView, knowledge: VerifiedKnowledge, arguments: dict[str, object]):
    context = get_atom_context(view,arguments['atom_id'],knowledge)
    if arguments['context_id'] != context['context_id']:
        raise ReqmapError('CONTEXT_MISMATCH','Контекст не соответствует текущему атому и snapshot.')
    atom, req, retrieval = atom_candidates(view,arguments['atom_id'],knowledge)
    accept = accept_deep_mapping if view.record.seed.settings.analysis_profile is AnalysisProfile.DEEP else accept_mapping
    return accept(atom,retrieval,knowledge.kb,arguments['proposal'])


def search_knowledge(view: SessionView, query: str, knowledge: VerifiedKnowledge, cursor: str | None, page_size: int) -> dict[str, object]:
    from reqmap.agent_service import page_offset, page_cursor
    if type(query) is not str or not query.strip() or len(query) > 2000:
        raise ReqmapError('TOOL_ARGUMENTS','Запрос поиска должен содержать от 1 до 2000 символов.')
    key = 'search:' + hashlib.sha256(query.encode()).hexdigest()
    record = view.record
    offset = page_offset(cursor,record.session_id,record.revision,key)
    if record.seed.settings.analysis_profile is AnalysisProfile.DEEP:
        retrieval = retrieve_deep(knowledge.kb,query,(),max(1,len(knowledge.kb.capabilities)+len(knowledge.kb.actions)))
        candidates = retrieval.normalized_candidates
    else:
        candidates = retrieve(knowledge.kb,query,(),max(1,len(knowledge.kb.capabilities)))
    if offset > len(candidates):
        raise ReqmapError('CURSOR_INVALID','Cursor вне диапазона.')
    items = candidates[offset:offset+page_size]
    return dict(session_id=record.session_id,revision=record.revision,authoritative_for_mapping=False,
        candidates=to_dict(items),total=len(candidates),
        next_cursor=page_cursor(record.session_id,record.revision,key,offset+len(items)) if offset+len(items)<len(candidates) else None)


def get_evidence(view: SessionView, evidence_id: str, knowledge: VerifiedKnowledge, cursor: str | None) -> dict[str, object]:
    from reqmap.agent_service import page_offset, page_cursor
    if type(evidence_id) is not str or evidence_id not in knowledge.kb.evidence:
        raise ReqmapError('EVIDENCE_NOT_FOUND','Доказательство не найдено в snapshot.')
    evidence = knowledge.kb.evidence[evidence_id]
    record = view.record
    key = 'evidence:' + evidence_id
    offset = page_offset(cursor,record.session_id,record.revision,key)
    raw = to_dict(evidence)
    source = knowledge.kb.sources[evidence.source_id]
    if record.seed.settings.analysis_profile is AnalysisProfile.DEEP:
        excerpt = raw.pop('local_excerpt')
    else:
        path = knowledge.kb.root / evidence.local_path
        if not path.is_relative_to(knowledge.kb.root) or '..' in path.parts:
            raise ReqmapError('KNOWLEDGE_CHANGED','Некорректный путь источника.')
        content = read_regular_bytes(path, 25*1024*1024)
        if hashlib.sha256(content).hexdigest() != evidence.source_sha256:
            raise ReqmapError('KNOWLEDGE_CHANGED','Источник изменился.')
        excerpt = content.decode('utf-8')
    if offset > len(excerpt):
        raise ReqmapError('CURSOR_INVALID','Cursor вне диапазона.')
    # 4096 Unicode characters occupy at most 16 KiB UTF-8.
    fragment = excerpt[offset:offset+4096]
    return dict(session_id=record.session_id,revision=record.revision,evidence=raw,source=to_dict(source),
        excerpt=fragment,excerpt_offset=offset,
        next_cursor=page_cursor(record.session_id,record.revision,key,offset+len(fragment)) if offset+len(fragment)<len(excerpt) else None)
