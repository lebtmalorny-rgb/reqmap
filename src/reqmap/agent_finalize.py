"""Recoverable publication of a deterministic external-agent result."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from reqmap import __version__
from reqmap.agent_config import AgentConfig
from reqmap.agent_input import read_regular_bytes
from reqmap.agent_knowledge import VerifiedKnowledge
from reqmap.agent_store import SessionStore
from reqmap.agent_types import SessionView, MutationCommand, MutationDecision, ToolReply, failure
from reqmap.aggregation import aggregate_requirement, aggregate_groups
from reqmap.analysis_origin import build_analysis_origin, proposal_journal_sha256
from reqmap.config import AnalysisProfile
from reqmap.deep_aggregation import aggregate_deep_requirement, aggregate_deep_groups, deep_run_status
from reqmap.deep_mapping import DeepMappingOutcome
from reqmap.deep_models import DeepAtomResult, DeepRequirementResult, DeepRunResult, LifecyclePhase
from reqmap.deep_pipeline import cited_deep_evidence
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes, ensure_secure_directory
from reqmap.manifest import RunLogger
from reqmap.models import AnalysisState, AtomResult, RequirementResult, RunResult, to_dict
from reqmap.output_safety import strict_json_object, symlink_component
from reqmap.pipeline import collect_cited_evidence, run_status
from reqmap.procedure import instantiate_procedure_graphs
from reqmap.prompts import PROMPT_DECOMPOSITION_VERSION, PROMPT_MAPPING_VERSION, PROMPT_DEEP_MAPPING_VERSION
from reqmap.publication import ARTIFACTS, publish_artifacts
from reqmap.crosscheck import crosscheck
from reqmap.crosscheck_deep import crosscheck_deep


_SKIPPED = ('AGENT_UNPROCESSED: агент не завершил анализ.',)


def build_agent_run(view: SessionView, knowledge: VerifiedKnowledge) -> RunResult | DeepRunResult:
    record, kb = view.record, knowledge.kb
    deep = record.seed.settings.analysis_profile is AnalysisProfile.DEEP
    rows, records, graphs = [], [], []
    for req in view.requirements:
        atoms = view.atoms_by_requirement.get(req.requirement_id, ())
        if not atoms:
            rows.append(DeepRequirementResult(req,AnalysisState.SKIPPED,None,(),(),(),_SKIPPED) if deep else
                        RequirementResult(req,AnalysisState.SKIPPED,None,(),(),_SKIPPED))
            continue
        if deep:
            outcomes = tuple(view.mappings_by_atom.get(a.atom_id,DeepMappingOutcome(DeepAtomResult(a,AnalysisState.SKIPPED,None,(),diagnostics=_SKIPPED),(),())) for a in atoms)
            mapping_records = tuple(r for o in outcomes for r in o.responsibility_records)
            templates = tuple(sorted({t for o in outcomes for t in o.procedure_template_ids}))
            procedure = instantiate_procedure_graphs(req.requirement_id,templates,mapping_records,kb)
            row, linked = aggregate_deep_requirement(req,outcomes,procedure)
            rows.append(row); records.extend(linked); graphs.extend(procedure.graphs)
        else:
            outcomes = tuple(view.mappings_by_atom.get(a.atom_id,AtomResult(a,AnalysisState.SKIPPED,None,(),diagnostics=_SKIPPED)) for a in atoms)
            rows.append(aggregate_requirement(req,outcomes))
    rows, records, graphs = tuple(rows), tuple(records), tuple(graphs)
    metadata = dict(reqmap_version=__version__, model='external-agent',seed=None,top_k=record.seed.settings.top_k,
        input_sha256=record.seed.input_snapshot.input_sha256,
        analysis_origin=build_analysis_origin(record,record.revision+int(record.status == 'active')),
        prompt_versions=dict(decomposition=PROMPT_DECOMPOSITION_VERSION))
    diagnostics = tuple(dict.fromkeys(d for row in rows for d in row.diagnostics))
    run_id = 'run-'+record.session_id
    if not deep:
        metadata.update(knowledge_sha256=knowledge.knowledge_sha256)
        metadata['prompt_versions']['mapping'] = PROMPT_MAPPING_VERSION
        return RunResult(run_id,'1.0',run_status(rows,preflight_ok=True),rows,aggregate_groups(rows),collect_cited_evidence(rows,kb),metadata,diagnostics)
    metadata.update(analysis_profile='deep',snapshot_id=kb.snapshot_id,manifest_sha256=kb.trust.manifest_sha256,
        key_id=kb.trust.key_id,signer_identity=kb.trust.signer_identity,retry_counts=dict(decomposition=0,deep_mapping=0),
        release_profile=dict(source_release=kb.base_release,target_release='2026.1' if any(r.lifecycle_phase is LifecyclePhase.UPGRADE and r.version_scope.target_release == '2026.1' for r in records) else '2025.1',kolla_ansible_release=kb.kolla_ansible_release,host_profile=kb.host_profile))
    metadata['prompt_versions']['deep_mapping'] = PROMPT_DEEP_MAPPING_VERSION
    return DeepRunResult(run_id,'2.0',deep_run_status(rows,True),rows,aggregate_deep_groups(rows,records),records,graphs,cited_deep_evidence(records,graphs,kb),metadata,diagnostics)


def _hashes(directory):
    # Generated reports can exceed the source/frame limit; reads remain bounded.
    return {name:hashlib.sha256(read_regular_bytes(directory/name,512*1024*1024)).hexdigest() for name in ARTIFACTS}


def verify_publication(directory, intent, run=None):
    try:
        hashes = _hashes(directory)
        if hashes != intent['artifact_hashes']:
            raise ValueError('artifact hash mismatch')
        manifest = strict_json_object(read_regular_bytes(directory/'manifest.json',2*1024*1024).decode('utf-8'))
        if manifest['artifact_hashes'] != {k:v for k,v in hashes.items() if k != 'manifest.json'} or manifest['analysis_origin']['proposal_journal_sha256'] != intent['proposal_journal_sha256'] or manifest['analysis_origin']['revision'] != intent['final_revision']:
            raise ValueError('manifest mismatch')
        if run is not None and (crosscheck_deep if isinstance(run,DeepRunResult) else crosscheck)(run,directory/'result.json',directory/'result.xlsx',directory/'report.md'):
            raise ValueError('crosscheck failed')
        return manifest
    except (OSError, ValueError, KeyError, ReqmapError) as exc:
        raise ReqmapError('ARTIFACTS_CHANGED','Артефакты отсутствуют или изменены; проверенный отчёт недоступен.') from exc


def _reset_staging(staging):
    ensure_secure_directory(staging)
    for path in staging.iterdir():
        if path.name not in ARTIFACTS or symlink_component(path) or not path.is_file():
            raise ValueError('unsafe staging content')
        path.unlink()


def finalize_session(store: SessionStore, config: AgentConfig, command: MutationCommand) -> ToolReply:
    from reqmap.agent_service import AgentService
    with store.locked(command.session_id):
        previous = store.command_receipt(command)
        if previous is not None:
            return previous
        record = store.read(command.session_id)
        pending = store.publication(command.session_id)
        if pending is not None:
            if pending['request_id'] != command.request_id:
                return failure('PUBLICATION_PENDING','Повторите исходный finalize.')
            if pending['payload_sha256'] != hashlib.sha256(canonical_json_bytes(to_dict(command))).hexdigest():
                return failure('REQUEST_ID_REUSED','request_id уже использован с другими аргументами.')
        if record.revision != command.expected_revision or record.status != 'active':
            return store.transact(command,lambda r: MutationDecision(False,failure('SESSION_CLOSED','Сессия завершена.'),r.status))
        view, knowledge = AgentService(config).verified_view(record)
        run = build_agent_run(view,knowledge)
        if run.run_status != 'SUCCESS' and not command.arguments.get('allow_partial',False):
            return store.transact(command,lambda r: MutationDecision(False,failure('ANALYSIS_INCOMPLETE','Анализ неполон; продолжите работу или явно разрешите частичный экспорт.',run_status=run.run_status),r.status))
        from reqmap.agent_tools import ensure_reply_fits
        prospective = ToolReply(True,dict(status='finalized',run_status=run.run_status,requirements_count=len(run.requirements),
            session_id=record.session_id,revision=record.revision+1,
            artifacts={name:str(config.output_root/('0'*36)/name) for name in ARTIFACTS}))
        try:
            ensure_reply_fits(prospective,config.limits.max_response_bytes)
        except ReqmapError as exc:
            return store.transact(command,lambda r: MutationDecision(False,failure(exc.code,exc.message_ru),r.status))
        intent = store.reserve_publication(command,proposal_journal_sha256(record))
        staging, final = config.output_root/intent['staging_name'], config.output_root/intent['final_name']
        try:
            ensure_secure_directory(config.output_root)
            if final.exists() or final.is_symlink():
                verify_publication(final,intent,run)
            else:
                _reset_staging(staging)
                log = RunLogger(staging/'run.jsonl')
                for event in record.events:
                    log.write('agent_proposal','info','Попытка предложения внешнего агента.',
                        sequence=event.sequence,operation=event.operation,accepted=event.accepted,
                        request_id=event.request_id,proposal_sha256=hashlib.sha256(canonical_json_bytes(event.arguments)).hexdigest(),
                        error_code=event.reply.error.code if event.reply.error else None)
                issues = publish_artifacts(run,staging)
                if issues:
                    raise ValueError('crosscheck failed')
                intent = store.record_publication_hashes(command,_hashes(staging))
                verify_publication(staging,intent,run)
                os.rename(staging,final)
                fd = os.open(config.output_root,os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            reply = ToolReply(True,dict(status='finalized',run_status=run.run_status,requirements_count=len(run.requirements),
                artifacts={name:str(final/name) for name in ARTIFACTS}))
        except (OSError, ValueError, ReqmapError) as exc:
            reply = failure('PUBLICATION_FAILED','Публикация не завершена; доступны только диагностика и журнал сессии.',reason_code=exc.code if isinstance(exc,ReqmapError) else 'EXPORT_FAILED')
        return store.complete_publication(command,reply)


def get_result(store, config, args, page_size):
    from reqmap.agent_service import page_offset, page_cursor
    with store.locked(args['session_id']):
        record = store.read(args['session_id'])
        intent = store.publication(record.session_id)
        data = dict(session_id=record.session_id,revision=record.revision,status=record.status)
        if intent is None or intent['status'] != 'committed':
            data['publication_status'] = intent['status'] if intent else 'not_started'
            return ToolReply(True,data)
        directory = config.output_root/intent['final_name']
        manifest = verify_publication(directory,intent)
        payload = strict_json_object(read_regular_bytes(directory/'result.json',512*1024*1024).decode('utf-8'))
        rows = payload['requirements']
        offset = page_offset(args.get('cursor'),record.session_id,record.revision,'result')
        if offset > len(rows):
            raise ReqmapError('CURSOR_INVALID','Cursor вне диапазона.')
        page = rows[offset:offset+page_size]
        data.update(run_status=manifest['run_status'],requirements_count=len(rows),requirements=page,
            artifacts={name:str(directory/name) for name in ARTIFACTS},
            next_cursor=page_cursor(record.session_id,record.revision,'result',offset+len(page)) if offset+len(page)<len(rows) else None)
        return ToolReply(True,data)
