"""Capture and validate exact source input before deriving any context."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from collections import deque

from reqmap.config import AnalysisProfile, InputProfile, KnowledgeTrustConfig
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.ids import generated_requirement_id
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.models import Requirement, SourceCoordinate, to_dict
from reqmap.source_context_models import (
    MAX_CONTEXT_BYTES, ContextTrust, LoadedSourceContext, SourceDocumentSnapshot,
    SOURCE_CONTEXT_RESOLVER_VERSION, SourceContextDecision, SourceContextIssue,
    SourceRowRef, EffectiveObligation, ConstraintOrigins,
    SourceContextSnapshot,
)


def _source_rows(requirements: tuple[Requirement, ...]) -> tuple[Requirement, ...]:
    return tuple(replace(row, parent_id=None, group_ids=()) for row in requirements)


def _rows_digest(requirements: tuple[Requirement, ...]) -> str:
    rows = []
    for row in _source_rows(requirements):
        raw = to_dict(row)
        del raw["parent_id"], raw["group_ids"]
        rows.append(raw)
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def capture_source_document(*, content: bytes, source_kind: str, source_name: str,
                            requirements: tuple[Requirement, ...],
                            input_profile: InputProfile | None,
                            text_mode: str | None = None) -> SourceDocumentSnapshot:
    """Reimport bytes; only derived grouping may differ from the caller's rows."""
    try:
        if (type(content) is not bytes or not content or type(source_name) is not str
                or not source_name.strip() or type(requirements) is not tuple
                or any(type(row) is not Requirement for row in requirements)):
            raise ValueError("invalid snapshot shape")
        if source_kind == "xlsx":
            if text_mode is not None or (input_profile is not None and type(input_profile) is not InputProfile):
                raise ValueError("invalid XLSX settings")
            imported = load_xlsx_bytes(content, input_profile, source_name)
        elif source_kind == "txt":
            if input_profile is not None or text_mode not in ("single", "lines"):
                raise ValueError("invalid TXT settings")
            imported = load_text(content.decode("utf-8"), text_mode, source_name)
        elif source_kind == "texts":
            if input_profile is not None or text_mode is not None:
                raise ValueError("invalid inline settings")
            values = json.loads(content)
            if (type(values) is not list or not values
                    or any(type(v) is not str or not v.strip() for v in values)
                    or canonical_json_bytes(values) != content):
                raise ValueError("inline texts must use canonical JSON bytes")
            imported = tuple(Requirement(generated_requirement_id(i), None, value, i,
                                         SourceCoordinate(source_name, None, i))
                             for i, value in enumerate(values, 1))
        else:
            raise ValueError("unknown source kind")
        if not imported or _source_rows(requirements) != imported:
            raise ValueError("rows differ from source import")
    except (ValueError, TypeError, KeyError, AttributeError, ReqmapError) as exc:
        raise ReqmapError("SOURCE_CONTEXT_INPUT_MISMATCH",
                          "Исходные строки или профиль не соответствуют сохранённым bytes.") from exc
    return SourceDocumentSnapshot(
        content, source_kind, source_name, text_mode, input_profile,
        hashlib.sha256(content).hexdigest(),
        hashlib.sha256(canonical_json_bytes(input_profile)).hexdigest() if input_profile is not None else None,
        _rows_digest(imported), imported)


def verify_source_context_signature(map_bytes: bytes, signature_bytes: bytes,
                                    trust: KnowledgeTrustConfig) -> ContextTrust:
    """Freeze current trust and signature; verify the exact map bytes."""
    from reqmap.agent_input import read_regular_bytes
    from reqmap.snapshot_trust import _require_ed25519_signature
    try:
        if (type(trust) is not KnowledgeTrustConfig or type(signature_bytes) is not bytes
                or len(signature_bytes) > 1024*1024 or type(map_bytes) is not bytes
                or len(map_bytes) > MAX_CONTEXT_BYTES):
            raise ValueError("invalid signature inputs")
        signers = read_regular_bytes(trust.allowed_signers_path, 1024*1024)
        _require_ed25519_signature(signature_bytes)
        with tempfile.TemporaryDirectory(prefix="reqmap-context-verify-") as temporary:
            directory = Path(temporary).resolve()
            signer_file, signature_file = directory/"allowed_signers", directory/"signature"
            signer_file.write_bytes(signers)
            signature_file.write_bytes(signature_bytes)
            # Signature envelope already restricts the key to Ed25519; OpenSSH
            # verifies that exact key against the configured identity/namespace.
            result = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(signer_file),
                 "-I", trust.signer_identity, "-n", "reqmap-source-context", "-s", str(signature_file)],
                input=map_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, check=False, shell=False)
        if result.returncode != 0:
            raise ValueError("signature rejected")
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, ReqmapError) as exc:
        raise ReqmapError("SOURCE_CONTEXT_UNTRUSTED", "Подпись карты контекста не подтверждена.") from exc
    return ContextTrust("deep", "reqmap-source-context", trust.signer_identity,
                        hashlib.sha256(signature_bytes).hexdigest(), hashlib.sha256(signers).hexdigest())


def load_source_context_map(path: Path | None, document: SourceDocumentSnapshot, *,
                            profile: AnalysisProfile, trust: KnowledgeTrustConfig | None) -> LoadedSourceContext:
    from reqmap.agent_input import read_regular_bytes
    from reqmap.source_context_codec import decode_source_context_map
    context_trust = ContextTrust(profile.value, None, None, None, None)
    if path is None:
        return LoadedSourceContext(None, None, None, None, context_trust)
    try:
        raw = read_regular_bytes(path, MAX_CONTEXT_BYTES)
    except ReqmapError as exc:
        raise ReqmapError("SOURCE_CONTEXT_INVALID", "Карта отсутствует, небезопасна или изменена при чтении.") from exc
    mapping = decode_source_context_map(raw, document)
    signature = None
    if profile is AnalysisProfile.DEEP:
        try:
            signature = read_regular_bytes(Path(str(path)+".sig"), 1024*1024)
        except ReqmapError as exc:
            raise ReqmapError("SOURCE_CONTEXT_UNTRUSTED", "Не удалось прочитать подпись карты.") from exc
        context_trust = verify_source_context_signature(raw, signature, trust)
    return LoadedSourceContext(raw, signature, hashlib.sha256(raw).hexdigest(), mapping, context_trust)


def source_context_output_diagnostics(path: Path | None, output_roots: tuple[Path, ...],
                                      trust: KnowledgeTrustConfig | None) -> tuple[str, ...]:
    protected = ([] if path is None else [path, Path(str(path)+".sig")])
    if trust is not None:
        protected.append(trust.allowed_signers_path)
    for item in protected:
        source = item.resolve(strict=False)
        for root in output_roots:
            output = root.resolve(strict=False)
            if source.is_relative_to(output) or output.is_relative_to(source):
                return ("SOURCE_CONTEXT_INVALID: каталог записи пересекается с картой, подписью или trust.",)
    return ()


def source_context_resolver_sha256() -> str:
    """Exact dependency manifest; changing implementation invalidates active runs."""
    root = Path(__file__).parent
    names = sorted(("source_context.py", "source_context_models.py", "source_context_codec.py",
                    "binding_models.py", "binding_engine.py", "binding_runtime.py"))
    manifest = [{"name": name, "sha256": hashlib.sha256((root/name).read_bytes()).hexdigest()}
                for name in names]
    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()


def _applicable(entry):
    return entry is not None and entry.review_state == "reviewed" and entry.disposition == "linked"


def _check_context_graph(mapping):
    graph = {} if mapping is None else {
        entry.target.requirement_id: {link.origin.row.requirement_id for link in entry.links}
        for entry in mapping.rows if _applicable(entry)}
    indegrees = {key: 0 for key in graph}
    for origins in graph.values():
        for origin in origins:
            indegrees[origin] = indegrees.get(origin, 0) + 1
    ready = deque(key for key, count in indegrees.items() if count == 0)
    visited = 0
    while ready:
        key = ready.popleft()
        visited += 1
        for origin in graph.get(key, ()):
            indegrees[origin] -= 1
            if indegrees[origin] == 0:
                ready.append(origin)
    if visited != len(indegrees):
        raise ReqmapError("SOURCE_CONTEXT_CYCLE", "Применимые связи контекста содержат self-link или цикл.")


def _effective_obligation(own, links):
    interface = own.interface
    interface_refs = tuple(link.origin for link in links if link.field == "interface")
    values = {link.value for link in links if link.field == "interface"}
    if own.interface not in (None, "unspecified"):
        values.add(own.interface)
    issues = []
    if len(values) > 1:
        issues.append(SourceContextIssue("SOURCE_CONTEXT_CONFLICT", "Интерфейсы исходной строки и контекста противоречат друг другу.", "interface", interface_refs))
    elif values:
        interface = next(iter(values))
    constraints = list(own.constraints)
    refs = {}
    for link in links:
        if link.field != "constraint":
            continue
        constraint = link.value
        refs.setdefault(constraint.semantic_key, []).append(link.origin)
        if any(c.name == constraint.name and c.semantic_key != constraint.semantic_key for c in constraints):
            issues.append(SourceContextIssue("SOURCE_CONTEXT_CONFLICT", "Значения условия исходной строки и контекста противоречат друг другу.", constraint.name,
                                             tuple(l.origin for l in links if l.field == "constraint" and l.value.name == constraint.name)))
        if all(c.semantic_key != constraint.semantic_key for c in constraints):
            constraints.append(constraint)
    effective = replace(own, interface=interface, constraints=tuple(constraints))
    return EffectiveObligation(effective, interface_refs,
                               tuple(ConstraintOrigins(key, tuple(origins)) for key, origins in refs.items())), issues


def resolve_source_context(document: SourceDocumentSnapshot,
                           loaded: LoadedSourceContext) -> tuple[SourceContextDecision, ...]:
    from reqmap.binding_source import bind_source
    from reqmap.source_context_codec import decode_source_context_map, encode_source_context_map
    recaptured = capture_source_document(content=document.content, source_kind=document.source_kind,
        source_name=document.source_name, text_mode=document.text_mode, input_profile=document.input_profile,
        requirements=document.requirements)
    if recaptured != document:
        raise ReqmapError("SOURCE_CONTEXT_INPUT_MISMATCH", "Snapshot входа изменён.")
    mapping = None if loaded.map_bytes is None else decode_source_context_map(loaded.map_bytes, document)
    digest = None if loaded.map_bytes is None else hashlib.sha256(loaded.map_bytes).hexdigest()
    if mapping != loaded.mapping or digest != loaded.map_sha256:
        raise ReqmapError("SOURCE_CONTEXT_CHANGED", "Нормализованная карта не соответствует сохранённым bytes.")
    _check_context_graph(mapping)
    entries = {} if mapping is None else {entry.target.requirement_id: entry for entry in mapping.rows}
    wire_entries = {} if mapping is None else {entry["target"]["requirement_id"]: entry for entry in encode_source_context_map(mapping)["rows"]}
    resolver_digest = source_context_resolver_sha256()
    decisions = []
    for requirement in document.requirements:
        own = bind_source(requirement)
        target = SourceRowRef(requirement.requirement_id, requirement.coordinate, own.source_sha256)
        entry = entries.get(requirement.requirement_id)
        state = "unreviewed" if entry is None or entry.review_state == "draft" else entry.disposition
        links = entry.links if _applicable(entry) else ()
        diagnostics, effective = [], []
        if state in ("unreviewed", "unresolved"):
            diagnostics.append(SourceContextIssue("SOURCE_CONTEXT_"+state.upper(),
                "Независимость или полный внешний контекст исходной строки не проверены.", None, ()))
        for obligation in own.obligations:
            resolved, issues = _effective_obligation(obligation, links)
            effective.append(resolved)
            diagnostics.extend(issue for issue in issues if issue not in diagnostics)
        if any(issue.code == "SOURCE_CONTEXT_CONFLICT" for issue in diagnostics):
            state = "conflict"
        entry_digest = None if entry is None else hashlib.sha256(canonical_json_bytes(wire_entries[requirement.requirement_id])).hexdigest()
        decision = SourceContextDecision(target, state, digest, entry_digest, "",
            SOURCE_CONTEXT_RESOLVER_VERSION, resolver_digest, tuple(links), tuple(effective), tuple(diagnostics))
        identity = {"input_sha256": document.input_sha256, "input_profile_sha256": document.input_profile_sha256,
                    "requirements_sha256": document.requirements_sha256, "source_kind": document.source_kind,
                    "source_name": document.source_name, "text_mode": document.text_mode,
                    "decision": {k: v for k, v in to_dict(decision).items() if k != "context_id"}}
        decisions.append(replace(decision, context_id=hashlib.sha256(canonical_json_bytes(identity)).hexdigest()))
    return tuple(decisions)


def freeze_source_context(document: SourceDocumentSnapshot,
                          loaded: LoadedSourceContext) -> SourceContextSnapshot:
    return SourceContextSnapshot(document, loaded, resolve_source_context(document, loaded))
