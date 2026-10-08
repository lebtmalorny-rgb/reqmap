"""Transactional session journal and receipts; local SQLite plus process locks."""
from contextlib import contextmanager
from dataclasses import replace
import base64
import fcntl
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from typing import Callable
import uuid

from reqmap.agent_input import InputSnapshot
from reqmap.agent_types import (ToolReply, ToolError, SessionSeed, SessionSettings,
    SessionRecord, JournalEvent, MutationCommand, MutationDecision, failure)
from reqmap.config import AnalysisProfile
from reqmap.config_common import parse_input_profile
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes, ensure_secure_directory
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.models import Requirement, SourceCoordinate, to_dict
from reqmap.ids import generated_requirement_id
from reqmap.output_safety import strict_json_object, symlink_component


STATE_VERSION = 1
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value) -> str:
    return canonical_json_bytes(value).decode('utf-8')


def _reply(raw) -> ToolReply:
    if type(raw) is not dict or set(raw) != {'ok','data','error'} or type(raw['ok']) is not bool or type(raw['data']) is not dict:
        raise ValueError('invalid reply')
    error = raw['error']
    if error is not None:
        if type(error) is not dict or set(error) != {'code','message_ru','details'}:
            raise ValueError('invalid error')
        error = ToolError(**error)
    if raw['ok'] != (error is None):
        raise ValueError('inconsistent reply')
    return ToolReply(raw['ok'], raw['data'], error)


def _seed_json(seed: SessionSeed) -> str:
    from reqmap.source_context_codec import encode_source_context_snapshot
    extra = {} if seed.seed_version is None else dict(seed_version=seed.seed_version,
        source_context=encode_source_context_snapshot(seed.source_context) if seed.source_context is not None else seed.source_context_record)
    return _json(dict(**extra, settings=to_dict(seed.settings), clarifications=list(seed.clarifications),
        parent_session_id=seed.parent_session_id, reported_client=seed.reported_client,
        input=dict(source_kind=seed.input_snapshot.source_kind, source_name=seed.input_snapshot.source_name,
                   input_sha256=seed.input_snapshot.input_sha256, content=base64.b64encode(seed.input_snapshot.content).decode('ascii'))))


def _seed(raw: str) -> SessionSeed:
    obj = strict_json_object(raw)
    version = obj.get('seed_version')
    old_fields = {'settings','clarifications','parent_session_id','reported_client','input'}
    if (version is None and set(obj) != old_fields) or (version is not None and
            (type(version) is not int or version != 2 or set(obj) != old_fields | {'seed_version','source_context'})):
        raise ValueError('invalid seed')
    settings = dict(obj['settings'])
    settings['analysis_profile'] = AnalysisProfile(settings['analysis_profile'])
    settings['input_profile'] = parse_input_profile(settings['input_profile']) if settings['input_profile'] is not None else None
    settings = SessionSettings(**settings)
    if (settings.tool_contract_version, settings.workflow_version) not in (('1.0', '1.0'), ('2.0', '2.0'), ('3.0', '3.0')):
        raise ValueError('unsupported session workflow')
    value = obj['input']
    if set(value) != {'source_kind','source_name','input_sha256','content'}:
        raise ValueError('invalid input')
    content = base64.b64decode(value['content'], validate=True)
    if _hash(content) != value['input_sha256']:
        raise ValueError('input hash mismatch')
    kind, name = value['source_kind'], value['source_name']
    if kind == 'texts':
        # Array is wrapped for the existing strict object parser.
        texts = strict_json_object('{"texts":' + content.decode('utf-8') + '}')['texts']
        if type(texts) is not list or any(type(t) is not str or not t.strip() for t in texts):
            raise ValueError('invalid texts')
        requirements = tuple(Requirement(generated_requirement_id(i), None, t, i, SourceCoordinate(name, None, i)) for i, t in enumerate(texts, 1))
    elif kind == 'txt':
        requirements = load_text(content.decode('utf-8'), 'lines', name)
    elif kind == 'xlsx':
        requirements = load_xlsx_bytes(content, settings.input_profile, name)
    else:
        raise ValueError('invalid source kind')
    if not requirements:
        raise ValueError('empty session')
    frozen = None
    frozen_record = obj.get('source_context')
    if version == 2:
        if type(frozen_record) is not dict:
            raise ValueError('missing context snapshot')
        # Historical finalized reads must not execute a different resolver.
        from reqmap.source_context import source_context_resolver_sha256
        if settings.resolver_version == '1.0' and settings.resolver_sha256 == source_context_resolver_sha256():
            from reqmap.source_context_codec import inspect_source_context_record
            frozen = inspect_source_context_record(frozen_record)
            if (frozen.document.content != content or frozen.document.requirements != requirements
                    or frozen.document.source_kind != kind or frozen.document.source_name != name
                    or frozen.document.input_sha256 != value['input_sha256']):
                raise ValueError('seed input differs from context document')
    return SessionSeed(InputSnapshot(kind, name, content, value['input_sha256'], requirements),
                       settings, tuple(obj['clarifications']), obj['parent_session_id'], obj['reported_client'],
                       frozen, frozen_record if frozen is None else None, version)


class SessionStore:
    def __init__(self, root: Path, reply_limit: int | None = None):
        self.reply_limit = reply_limit
        self.root = root.absolute()
        ensure_secure_directory(self.root)
        with self.locked('create'):
            with self._connect(initial=True) as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, STATE_VERSION):
                    raise ReqmapError('SESSION_CORRUPT', 'Версия хранилища сессий несовместима.')
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY, seed_json TEXT NOT NULL, seed_hash TEXT NOT NULL,
                        revision INTEGER NOT NULL, status TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS events (
                        session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_json TEXT NOT NULL,
                        previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
                        PRIMARY KEY(session_id, sequence));
                    CREATE TABLE IF NOT EXISTS publications (
                        session_id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, intent_hash TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS receipts (
                        scope TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                        reply_json TEXT NOT NULL, receipt_hash TEXT NOT NULL,
                        PRIMARY KEY(scope, request_id));
                ''')
                db.execute(f'PRAGMA user_version={STATE_VERSION}')

    @contextmanager
    def _connect(self, initial=False):
        path = self.root / 'sessions.sqlite3'
        for suffix in ('', '-journal', '-wal', '-shm'):
            target = Path(str(path) + suffix)
            if symlink_component(target) or (target.exists() and not target.is_file()):
                raise ReqmapError('SESSION_CORRUPT', 'Небезопасный путь хранилища сессий.')
        db = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            if not initial and db.execute('PRAGMA user_version').fetchone()[0] != STATE_VERSION:
                raise ReqmapError('SESSION_CORRUPT', 'Версия хранилища сессий несовместима.')
            yield db
        except sqlite3.DatabaseError as exc:
            raise ReqmapError('SESSION_CORRUPT', 'Не удалось прочитать хранилище сессий.') from exc
        finally:
            db.close()

    @contextmanager
    def locked(self, session_id: str):
        if session_id != 'create':
            self._session_id(session_id)
        path = self.root / (session_id + '.lock')
        key = str(path)
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.RLock())
        with lock:
            active = getattr(_LOCAL, 'locks', {})
            if key in active:
                yield
                return
            if symlink_component(path):
                raise ReqmapError('SESSION_CORRUPT', 'Небезопасный путь блокировки.')
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ReqmapError('SESSION_CORRUPT', 'Ожидается обычный файл блокировки.')
                fcntl.flock(fd, fcntl.LOCK_EX)
                active[key] = fd
                _LOCAL.locks = active
                yield
            finally:
                active.pop(key, None)
                os.close(fd)

    @staticmethod
    def _session_id(value):
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ReqmapError('SESSION_ID_INVALID', 'Некорректный идентификатор сессии.')

    @staticmethod
    def _request_id(value):
        if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value) is None:
            raise ReqmapError('REQUEST_ID_INVALID', 'Некорректный request_id.')

    def _receipt(self, db, scope, request_id, digest):
        row = db.execute('SELECT payload_hash,reply_json,receipt_hash FROM receipts WHERE scope=? AND request_id=?', (scope,request_id)).fetchone()
        if row is None:
            return None
        if _hash((row[0] + row[1]).encode()) != row[2]:
            raise ReqmapError('SESSION_CORRUPT', 'Повреждена квитанция запроса.')
        if row[0] != digest:
            return failure('REQUEST_ID_REUSED', 'request_id уже использован с другими аргументами.')
        try:
            return _reply(strict_json_object(row[1]))
        except (TypeError, ValueError, KeyError) as exc:
            raise ReqmapError('SESSION_CORRUPT', 'Повреждена квитанция запроса.') from exc

    def _save_receipt(self, db, scope, request_id, digest, reply):
        payload = _json(to_dict(reply))
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)', (scope,request_id,digest,payload,_hash((digest+payload).encode())))

    def _guard_reply(self, reply):
        if self.reply_limit is not None:
            from reqmap.agent_tools import ensure_reply_fits
            ensure_reply_fits(reply,self.reply_limit)
        return reply

    def create(self, request_id: str, arguments: dict[str, object], prepare: Callable[[], SessionSeed]) -> ToolReply:
        self._request_id(request_id)
        digest = _hash(canonical_json_bytes(arguments))
        with self.locked('create'), self._connect() as db:
            previous = self._receipt(db, 'create', request_id, digest)
            if previous is not None:
                return previous
            try:
                seed = prepare()
            except ReqmapError as exc:
                reply = failure(exc.code, exc.message_ru, **exc.details)
                db.execute('BEGIN IMMEDIATE')
                self._save_receipt(db, 'create', request_id, digest, reply)
                db.execute('COMMIT')
                return reply
            sid = str(uuid.uuid4())
            seed_json = _seed_json(seed)
            # Decode immediately too: disk schema and executable input must agree.
            _seed(seed_json)
            reply = ToolReply(True, dict(session_id=sid, revision=0, status='active',
                analysis_profile=seed.settings.analysis_profile.value, requirements_count=len(seed.input_snapshot.requirements)))
            try:
                self._guard_reply(reply)
            except ReqmapError as exc:
                reply = failure(exc.code,exc.message_ru)
                self._save_receipt(db,'create',request_id,digest,reply)
                return reply
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (sid,seed_json,_hash(seed_json.encode()),0,'active'))
                self._save_receipt(db,'create',request_id,digest,reply)
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            return reply

    def read(self, session_id: str) -> SessionRecord:
        self._session_id(session_id)
        with self._connect() as db:
            db.execute('BEGIN')
            return self._read(db, session_id)

    def _read(self, db, sid):
        row = db.execute('SELECT seed_json,seed_hash,revision,status FROM sessions WHERE session_id=?', (sid,)).fetchone()
        if row is None:
            raise ReqmapError('SESSION_NOT_FOUND', 'Сессия не найдена.')
        try:
            if _hash(row[0].encode()) != row[1] or row[3] not in ('active','finalized','failed'):
                raise ValueError('invalid session')
            seed = _seed(row[0])
            events, previous = [], row[1]
            for seq, payload, prev, digest in db.execute('SELECT sequence,event_json,previous_hash,event_hash FROM events WHERE session_id=? ORDER BY sequence', (sid,)):
                if seq != len(events)+1 or prev != previous or digest != _hash((prev+payload).encode()):
                    raise ValueError('journal hash mismatch')
                raw = strict_json_object(payload)
                raw['reply'] = _reply(raw['reply'])
                event = JournalEvent(**raw)
                if event.sequence != seq or type(event.accepted) is not bool or event.accepted != event.reply.ok:
                    raise ValueError('invalid event')
                events.append(event)
                previous = digest
            if sum(e.accepted for e in events) != row[2]:
                raise ValueError('revision mismatch')
            return SessionRecord(sid, row[2], row[3], seed, tuple(events))
        except (ValueError, TypeError, KeyError, ReqmapError) as exc:
            raise ReqmapError('SESSION_CORRUPT', 'Повреждено состояние сессии.') from exc

    def transact(self, command: MutationCommand, mutate: Callable[[SessionRecord], MutationDecision]) -> ToolReply:
        self._session_id(command.session_id)
        self._request_id(command.request_id)
        if type(command.expected_revision) is not int or command.expected_revision < 0:
            raise ReqmapError('REVISION_INVALID', 'expected_revision должна быть неотрицательным целым.')
        digest = _hash(canonical_json_bytes(to_dict(command)))
        with self.locked(command.session_id), self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                previous = self._receipt(db, command.session_id, command.request_id, digest)
                if previous is not None:
                    db.execute('COMMIT')
                    return previous
                record = self._read(db, command.session_id)
                intent = self._publication(db, command.session_id)
                if intent is not None and intent['status'] == 'pending':
                    db.execute('COMMIT')
                    return failure('PUBLICATION_PENDING','Публикация начата; повторите исходный finalize.')
                if record.status != 'active':
                    # The published proposal journal is sealed. Cache new refusals
                    # separately so retries remain durable without changing its hash.
                    error = (failure('REVISION_CONFLICT','Состояние изменилось; прочитайте текущую revision.')
                             if command.expected_revision != record.revision else
                             failure('SESSION_CLOSED','Сессия уже завершена.'))
                    reply = replace(error,data=dict(session_id=record.session_id,revision=record.revision))
                    self._save_receipt(db,record.session_id,command.request_id,digest,reply)
                    db.execute('COMMIT')
                    return reply
                if command.expected_revision != record.revision:
                    decision = MutationDecision(False, failure('REVISION_CONFLICT','Состояние изменилось; прочитайте текущую revision.'),record.status)
                else:
                    try:
                        decision = mutate(record)
                    except ReqmapError as exc:
                        decision = MutationDecision(False, failure(exc.code,exc.message_ru,**exc.details),record.status)
                if decision.accepted != decision.reply.ok or decision.status not in ('active','finalized','failed'):
                    raise ValueError('invalid mutation decision')
                revision = record.revision + int(decision.accepted)
                reply = replace(decision.reply, data={**decision.reply.data,'session_id':record.session_id,'revision':revision})
                try:
                    self._guard_reply(reply)
                except ReqmapError as exc:
                    decision = MutationDecision(False,failure(exc.code,exc.message_ru),record.status)
                    revision = record.revision
                    reply = replace(decision.reply,data=dict(session_id=record.session_id,revision=revision))
                event = JournalEvent(len(record.events)+1, command.request_id,command.operation,command.arguments,decision.accepted,reply)
                self._append(db, record, event)
                db.execute('UPDATE sessions SET revision=?,status=? WHERE session_id=?', (revision,decision.status,record.session_id))
                self._save_receipt(db,record.session_id,command.request_id,digest,reply)
                db.execute('COMMIT')
                return reply
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def _append(self, db, record, event):
        row = db.execute('SELECT event_hash FROM events WHERE session_id=? ORDER BY sequence DESC LIMIT 1', (record.session_id,)).fetchone()
        previous = row[0] if row else db.execute('SELECT seed_hash FROM sessions WHERE session_id=?',(record.session_id,)).fetchone()[0]
        payload = _json(to_dict(event))
        db.execute('INSERT INTO events VALUES(?,?,?,?,?)', (record.session_id,event.sequence,payload,previous,_hash((previous+payload).encode())))

    def command_receipt(self, command):
        self._session_id(command.session_id)
        self._request_id(command.request_id)
        if type(command.expected_revision) is not int or command.expected_revision < 0:
            raise ReqmapError('REVISION_INVALID','expected_revision должна быть неотрицательным целым.')
        with self._connect() as db:
            return self._receipt(db,command.session_id,command.request_id,_hash(canonical_json_bytes(to_dict(command))))

    def _publication(self, db, session_id):
        row = db.execute('SELECT intent_json,intent_hash FROM publications WHERE session_id=?',(session_id,)).fetchone()
        if row is None:
            return None
        try:
            if _hash(row[0].encode()) != row[1]:
                raise ValueError()
            intent = strict_json_object(row[0])
            if set(intent) != {'session_id','request_id','payload_sha256','source_revision','final_revision','proposal_journal_sha256','staging_name','final_name','status','artifact_hashes'}:
                raise ValueError()
            self._session_id(intent['final_name'])
            if intent['session_id'] != session_id or intent['staging_name'] != '.'+intent['final_name']+'.staging' or intent['status'] not in ('pending','committed','failed'):
                raise ValueError()
            return intent
        except (ValueError,TypeError,KeyError,ReqmapError) as exc:
            raise ReqmapError('SESSION_CORRUPT','Повреждено намерение публикации.') from exc

    def publication(self, session_id):
        self._session_id(session_id)
        with self._connect() as db:
            return self._publication(db,session_id)

    def _save_publication(self, db, intent):
        payload = _json(intent)
        db.execute('INSERT OR REPLACE INTO publications VALUES(?,?,?)',(intent['session_id'],payload,_hash(payload.encode())))

    def reserve_publication(self, command, journal_sha256):
        self.command_receipt(command)  # Validate identifiers and revision shape.
        digest = _hash(canonical_json_bytes(to_dict(command)))
        with self.locked(command.session_id), self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._read(db,command.session_id)
            intent = self._publication(db,command.session_id)
            if intent is not None:
                if intent['request_id'] != command.request_id:
                    raise ReqmapError('PUBLICATION_PENDING','Повторите исходный finalize.')
                if intent['payload_sha256'] != digest:
                    raise ReqmapError('REQUEST_ID_REUSED','request_id уже использован с другими аргументами.')
                if intent['proposal_journal_sha256'] != journal_sha256:
                    raise ReqmapError('SESSION_CORRUPT','Журнал изменён после начала публикации.')
            else:
                if record.revision != command.expected_revision:
                    raise ReqmapError('REVISION_CONFLICT','Состояние изменилось; прочитайте текущую revision.')
                if record.status != 'active':
                    raise ReqmapError('SESSION_CLOSED','Сессия уже завершена.')
                name = str(uuid.uuid4())
                intent = dict(session_id=record.session_id,request_id=command.request_id,payload_sha256=digest,
                    source_revision=record.revision,final_revision=record.revision+1,proposal_journal_sha256=journal_sha256,
                    staging_name='.'+name+'.staging',final_name=name,status='pending',artifact_hashes={})
                self._save_publication(db,intent)
            db.execute('COMMIT')
            return intent

    def record_publication_hashes(self, command, hashes):
        with self.locked(command.session_id), self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            intent = self._publication(db,command.session_id)
            if intent is None or intent['status'] != 'pending' or intent['payload_sha256'] != _hash(canonical_json_bytes(to_dict(command))):
                raise ReqmapError('SESSION_CORRUPT','Нет соответствующего намерения публикации.')
            intent['artifact_hashes'] = hashes
            self._save_publication(db,intent)
            db.execute('COMMIT')
            return intent

    def complete_publication(self, command, reply):
        digest = _hash(canonical_json_bytes(to_dict(command)))
        with self.locked(command.session_id), self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self._receipt(db,command.session_id,command.request_id,digest)
            if previous is not None:
                db.execute('COMMIT')
                return previous
            record = self._read(db,command.session_id)
            intent = self._publication(db,command.session_id)
            if intent is None or intent['status'] != 'pending' or intent['payload_sha256'] != digest or record.revision != intent['source_revision']:
                raise ReqmapError('SESSION_CORRUPT','Намерение публикации не совпадает с сессией.')
            revision = record.revision + int(reply.ok)
            reply = replace(reply,data={**reply.data,'session_id':record.session_id,'revision':revision})
            self._append(db,record,JournalEvent(len(record.events)+1,command.request_id,command.operation,command.arguments,reply.ok,reply))
            db.execute('UPDATE sessions SET revision=?,status=? WHERE session_id=?',(revision,'finalized' if reply.ok else 'failed',record.session_id))
            intent['status'] = 'committed' if reply.ok else 'failed'
            self._save_publication(db,intent)
            self._save_receipt(db,record.session_id,command.request_id,digest,reply)
            db.execute('COMMIT')
            return reply
