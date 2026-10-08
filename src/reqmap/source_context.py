"""Capture and validate exact source input before deriving any context."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from reqmap.config import AnalysisProfile, InputProfile, KnowledgeTrustConfig
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.ids import generated_requirement_id
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.models import Requirement, SourceCoordinate, to_dict
from reqmap.source_context_models import (
    MAX_CONTEXT_BYTES, ContextTrust, LoadedSourceContext, SourceDocumentSnapshot,
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
