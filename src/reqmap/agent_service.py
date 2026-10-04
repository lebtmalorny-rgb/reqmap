"""Model-free application service over the durable session store."""
import base64
import hashlib
import re
from reqmap.agent_config import AgentConfig
from reqmap.agent_input import import_agent_input
from reqmap.agent_knowledge import load_agent_knowledge
from reqmap.agent_replay import replay_session
from reqmap.agent_store import SessionStore
from reqmap.agent_types import (ToolReply, SessionSeed, SessionSettings,
    MutationCommand, MutationDecision, failure)
from reqmap.decomposition import prepare_decomposition, accept_decomposition
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.models import to_dict
from reqmap.output_safety import strict_json_object
from reqmap.proposals import ProposalError


TOOL_FIELDS = {
    'reqmap_finalize': ({'session_id','request_id','expected_revision'}, {'allow_partial'}),
    'reqmap_get_result': ({'session_id'}, {'cursor','page_size'}),
    'reqmap_start_session': ({'request_id','source'}, {'clarifications','parent_session_id','reported_client'}),
    'reqmap_get_session': ({'session_id'}, {'cursor','page_size','requirement_id'}),
    'reqmap_get_atom_context': ({'session_id','atom_id'}, set()),
    'reqmap_search_knowledge': ({'session_id','query'}, {'cursor','page_size'}),
    'reqmap_get_evidence': ({'session_id','evidence_id'}, {'cursor'}),
    'reqmap_submit_mapping': ({'session_id','atom_id','context_id','proposal','request_id','expected_revision'}, {'reported_model'}),
    'reqmap_submit_atoms': ({'session_id','requirement_id','proposal','request_id','expected_revision'}, {'reported_model'}),
}


def page_offset(cursor, session_id, revision, query):
    if cursor is None:
        return 0
    try:
        if type(cursor) is not str or len(cursor) > 2048:
            raise ValueError()
        raw = strict_json_object(base64.b64decode(cursor, altchars=b'-_', validate=True).decode('utf-8'))
        if set(raw) != {'session_id','revision','query','offset'} or raw['session_id'] != session_id or raw['query'] != query or type(raw['offset']) is not int or raw['offset'] < 0:
            raise ValueError()
        if raw['revision'] != revision:
            raise ReqmapError('CURSOR_STALE','Сессия изменена; запросите первую страницу.')
        return raw['offset']
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ReqmapError('CURSOR_INVALID','Некорректный cursor.') from exc


def page_cursor(session_id, revision, query, offset):
    return base64.urlsafe_b64encode(canonical_json_bytes(dict(session_id=session_id,revision=revision,query=query,offset=offset))).decode('ascii')


def validate_reported_model(value):
    if value is None:
        return
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', value) is None or '://' in value or value.lower().startswith(('sk-', 'bearer', 'token')):
        raise ReqmapError('TOOL_ARGUMENTS','reported_model должен быть безопасным идентификатором, не URL или ключом.')


class AgentService:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.store = SessionStore(config.session_root,config.limits.max_response_bytes)

    def call(self, name: str, arguments: dict[str, object]) -> ToolReply:
        from reqmap.agent_tools import ensure_reply_fits
        args = dict(arguments) if type(arguments) is dict else arguments
        while True:
            reply = self._call(name,args)
            try:
                return ensure_reply_fits(reply,self.config.limits.max_response_bytes)
            except ReqmapError as exc:
                if reply.ok and name in ('reqmap_get_session','reqmap_search_knowledge','reqmap_get_result'):
                    size = args.get('page_size',self.config.limits.max_page_size)
                    if type(size) is int and size > 1:
                        args['page_size'] = max(1,size//2)
                        continue
                return failure(exc.code,exc.message_ru)

    def _call(self, name: str, arguments: dict[str, object]) -> ToolReply:
        try:
            if type(name) is not str or name not in TOOL_FIELDS:
                return failure('TOOL_UNKNOWN','Неизвестный инструмент reqmap.')
            required, optional = TOOL_FIELDS[name]
            if type(arguments) is not dict or not required.issubset(arguments) or set(arguments) - required - optional:
                raise ReqmapError('TOOL_ARGUMENTS','Недостающие или неизвестные аргументы инструмента.')
            from reqmap.agent_tools import validate_arguments
            validate_arguments(name,arguments)
            if 'reported_model' in arguments:
                validate_reported_model(arguments['reported_model'])
            if name == 'reqmap_start_session':
                return self._start(arguments)
            if name == 'reqmap_get_session':
                return self._get_session(arguments)
            if name in ('reqmap_get_atom_context','reqmap_search_knowledge','reqmap_get_evidence'):
                return self._knowledge_tool(name, arguments)
            if name == 'reqmap_get_result':
                from reqmap.agent_finalize import get_result
                return get_result(self.store,self.config,arguments,self._page_size(arguments))
            if name == 'reqmap_finalize' and type(arguments.get('allow_partial',False)) is not bool:
                raise ReqmapError('TOOL_ARGUMENTS','allow_partial должен быть bool.')
            command = MutationCommand(arguments['session_id'], arguments['request_id'], arguments['expected_revision'], name, arguments)
            if name == 'reqmap_finalize':
                from reqmap.agent_finalize import finalize_session
                return finalize_session(self.store,self.config,command)
            return self.store.transact(command, lambda record:self._submit_atoms(record, arguments) if name == 'reqmap_submit_atoms' else self._submit_mapping(record, arguments))
        except ReqmapError as exc:
            return failure(exc.code, exc.message_ru, **exc.details)

    def _start(self, args):
        def prepare():
            clarifications = args.get('clarifications', [])
            client = args.get('reported_client', 'unknown')
            if type(clarifications) is not list or any(type(x) is not str or not x.strip() for x in clarifications) or client not in ('codex','opencode','unknown'):
                raise ReqmapError('TOOL_ARGUMENTS','Некорректные уточнения или reported_client.')
            parent = args.get('parent_session_id')
            if parent is not None:
                self.store.read(parent)
            snapshot = import_agent_input(args['source'], self.config)
            knowledge = load_agent_knowledge(self.config)
            settings = SessionSettings(self.config.analysis_profile,self.config.top_k,self.config.input_profile,
                                       knowledge.knowledge_sha256,knowledge.snapshot_id,'1.0','1.0')
            return SessionSeed(snapshot,settings,tuple(clarifications),parent,client)
        return self.store.create(args['request_id'], args, prepare)

    def verified_view(self, record):
        settings = record.seed.settings
        if (settings.analysis_profile, settings.top_k, settings.input_profile) != (self.config.analysis_profile,self.config.top_k,self.config.input_profile):
            raise ReqmapError('CONFIG_CHANGED','Профиль/параметры изменены; требуется новая сессия.')
        try:
            knowledge = load_agent_knowledge(self.config)
        except ReqmapError as exc:
            raise ReqmapError('KNOWLEDGE_CHANGED','База знаний больше не проходит проверку.') from exc
        return replay_session(record, knowledge), knowledge

    def _page_size(self, args):
        size = args.get('page_size', self.config.limits.max_page_size)
        if type(size) is not int or not 1 <= size <= self.config.limits.max_page_size:
            raise ReqmapError('TOOL_ARGUMENTS','page_size вне допустимого диапазона.')
        return size

    def _get_session(self, args):
        size = self._page_size(args)
        record = self.store.read(args['session_id'])
        view, _ = self.verified_view(record)
        rid = args.get('requirement_id')
        if rid is not None and (type(rid) is not str or rid not in {r.requirement_id for r in view.requirements}):
            raise ReqmapError('REQUIREMENT_NOT_FOUND','Требование не найдено.')
        query = 'requirements:' + (rid or '*')
        offset = page_offset(args.get('cursor'), record.session_id, record.revision, query)
        requirements = tuple(r for r in view.requirements if rid is None or r.requirement_id == rid)
        if offset > len(requirements):
            raise ReqmapError('CURSOR_INVALID','Cursor вне диапазона.')
        by_id = {r.requirement_id:r.text for r in view.requirements}
        rows = []
        for req in requirements[offset:offset+size]:
            atoms = view.atoms_by_requirement.get(req.requirement_id, ())
            rows.append(dict(requirement=to_dict(req),atoms=to_dict(atoms),
                atom_results=[to_dict(view.mappings_by_atom[a.atom_id]) for a in atoms if a.atom_id in view.mappings_by_atom],
                pending_atom_ids=[a.atom_id for a in atoms if a.atom_id not in view.mappings_by_atom],
                decomposition_context=prepare_decomposition(req,by_id.get(req.parent_id))))
        pending = sum(not view.atoms_by_requirement.get(r.requirement_id) or any(a.atom_id not in view.mappings_by_atom for a in view.atoms_by_requirement[r.requirement_id]) for r in view.requirements)
        return ToolReply(True, dict(session_id=record.session_id,revision=record.revision,status=record.status,
            analysis_profile=record.seed.settings.analysis_profile.value, requirements_count=len(view.requirements),
            pending_requirements_count=pending, clarifications=list(record.seed.clarifications), requirements=rows,
            next_cursor=page_cursor(record.session_id,record.revision,query,offset+len(rows)) if offset+len(rows)<len(requirements) else None))

    def _submit_atoms(self, record, args):
        view, _ = self.verified_view(record)
        req = next((r for r in view.requirements if r.requirement_id == args['requirement_id']), None)
        if req is None:
            raise ReqmapError('REQUIREMENT_NOT_FOUND','Требование не найдено.')
        proposal = args['proposal']
        if type(proposal) is dict and type(proposal.get('atoms')) is list and len(proposal['atoms']) > self.config.limits.max_atoms_per_requirement:
            raise ReqmapError('ATOM_LIMIT','Превышено число атомарных требований в одной строке.')
        try:
            atoms = accept_decomposition(req,proposal)
        except ProposalError as exc:
            raise ReqmapError('PROPOSAL_INVALID','Предложение атомов отклонено.', {'violations':list(exc.violations)}) from exc
        return MutationDecision(True,ToolReply(True,dict(atoms=to_dict(atoms))), 'active')

    def _knowledge_tool(self, name, args):
        from reqmap.agent_context import get_atom_context, search_knowledge, get_evidence
        view, knowledge = self.verified_view(self.store.read(args['session_id']))
        if name == 'reqmap_get_atom_context':
            data = get_atom_context(view,args['atom_id'],knowledge)
        elif name == 'reqmap_search_knowledge':
            data = search_knowledge(view,args['query'],knowledge,args.get('cursor'),self._page_size(args))
        else:
            data = get_evidence(view,args['evidence_id'],knowledge,args.get('cursor'))
        if len(canonical_json_bytes(data)) > self.config.limits.max_response_bytes:
            raise ReqmapError('RESPONSE_TOO_LARGE','Ответ превышает лимит; содержимое не обрезано.')
        return ToolReply(True,data)

    def _submit_mapping(self, record, args):
        from reqmap.agent_context import accept_context_mapping
        view, knowledge = self.verified_view(record)
        try:
            outcome = accept_context_mapping(view,knowledge,args)
        except ProposalError as exc:
            raise ReqmapError('PROPOSAL_INVALID','Сопоставление отклонено.',{'violations':list(exc.violations)}) from exc
        return MutationDecision(True,ToolReply(True,dict(result=to_dict(outcome))), 'active')
