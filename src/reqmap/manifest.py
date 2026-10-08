"""Воспроизводимый manifest и безопасный append-only JSONL-журнал."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
import hashlib
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit, urlunsplit

from reqmap.analysis_origin import origin_metadata
from reqmap.export_json import (
    atomic_write_bytes,
    canonical_json_bytes,
    ensure_secure_directory,
    symlink_component,
)
from reqmap.export_deep_json import _deep_metadata_payload, validate_deep_run_result
from reqmap.deep_models import DeepRunResult
from reqmap.models import RunResult, to_dict


_RESERVED_LOG_FIELDS = frozenset({"event", "level", "message_ru"})
_ARTIFACT_NAMES = frozenset(
    {"result.json", "result.xlsx", "report.md", "run.jsonl"}
)
_MODEL_STAGES = ("decomposition", "mapping")
_DEEP_ARTIFACT_NAMES = frozenset(
    {"result.json", "result.xlsx", "report.md", "run.jsonl"}
)
_SHA256_LENGTH = 64
_UNSAFE_DIAGNOSTIC_FRAGMENTS = (
    "://",
    "/users/",
    "/home/",
    "\\users\\",
    "allowed_signers_path",
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "credential",
    "local_path",
    "output_path",
    "password",
    "private_key",
    "raw_model",
    "source_url",
    "token=",
)
_PATH_KEY_VALUE = re.compile(r"\b[A-Za-z0-9_-]*path\s*[:=]", re.IGNORECASE)
_SAFE_MODELS_ENDPOINT_TOKEN = re.compile(
    r'''(?<![A-Za-z0-9_./:?=#&%+-])/models'''
    r'''(?=$|[\s,;!?)\]"']|\.(?:$|\s))'''
)


class UnsafeLogError(ValueError):
    """The JSONL pathname no longer names the verified opened log inode."""


def write_manifest(
    run: RunResult,
    artifact_hashes: MappingABC[str, str],
    path: Path,
) -> None:
    """Записать allowlisted manifest без рекурсивного hash самого manifest."""
    if type(run) is not RunResult:
        raise ValueError("write_manifest ожидает canonical RunResult.")
    if not isinstance(run.metadata, MappingABC):
        raise ValueError("RunResult.metadata должен быть mapping.")
    if not isinstance(artifact_hashes, MappingABC):
        raise ValueError("artifact_hashes должен быть mapping строк.")
    safe_hashes: dict[str, str] = {}
    for name, digest in artifact_hashes.items():
        if type(name) is not str or not name or type(digest) is not str or not digest:
            raise ValueError("artifact_hashes должен содержать непустые строки.")
        if name == "manifest.json":
            continue
        if name not in _ARTIFACT_NAMES:
            raise ValueError(f"Неизвестный artifact hash: {name}")
        safe_hashes[name] = digest

    metadata = run.metadata
    manifest = {
        "schema_version": run.schema_version,
        "run_id": run.run_id,
        "run_status": run.run_status,
        "reqmap_version": metadata.get("reqmap_version"),
        "knowledge_version": metadata.get("knowledge_version"),
        "input_sha256": metadata.get("input_sha256"),
        "knowledge_sha256": metadata.get("knowledge_sha256"),
        "model": metadata.get("model"),
        "endpoint_origin": _safe_origin(metadata.get("endpoint_origin")),
        "seed": metadata.get("seed"),
        "prompt_versions": _safe_stage_mapping(
            metadata.get("prompt_versions"),
            value_type=str,
        ),
        "started_at": metadata.get("started_at"),
        "finished_at": metadata.get("finished_at"),
        "retry_counts": _safe_stage_mapping(
            metadata.get("retry_counts"),
            value_type=int,
        ),
        "artifact_hashes": dict(sorted(safe_hashes.items())),
    }
    if "binding_contract" in metadata:
        from reqmap.binding_export import contract_payload
        manifest["binding_contract"] = contract_payload(metadata["binding_contract"])
    if "source_context" in metadata:
        from reqmap.binding_export import source_context_summary
        manifest["source_context"] = source_context_summary(run)
    origin = origin_metadata(metadata)
    if origin is not None:
        manifest["analysis_origin"] = origin
        manifest.pop("endpoint_origin", None)
    atomic_write_json(path, manifest)


def write_deep_manifest(
    run: DeepRunResult,
    artifact_hashes: MappingABC[str, str],
    path: Path,
) -> None:
    """Publish a strict allowlisted schema-v2 run manifest."""
    validate_deep_run_result(run)
    manifest = _deep_manifest_payload(run, artifact_hashes)
    atomic_write_json(path, manifest)


def write_deep_preflight_artifacts(run: DeepRunResult, output_dir: Path) -> None:
    """Publish only the safe diagnostic pair for a failed deep preflight."""
    if not isinstance(output_dir, Path):
        raise ValueError("output_dir must be Path")
    log_payload, manifest_payload = deep_preflight_artifact_bytes(run)
    atomic_write_bytes(output_dir / "run.jsonl", log_payload)
    atomic_write_bytes(output_dir / "manifest.json", manifest_payload)


def deep_preflight_artifact_bytes(run: DeepRunResult) -> tuple[bytes, bytes]:
    """Return the one exact canonical diagnostic pair allowed for ``run``."""
    validate_deep_run_result(run)
    if run.run_status != "FAILED":
        raise ValueError("deep preflight artifacts require a FAILED run")
    if run.responsibility_records or run.procedure_graphs or run.evidence:
        raise ValueError("deep preflight run cannot contain subject payload")
    if any(
        item.analysis_state.value != "skipped"
        or item.support_status is not None
        or item.atom_results
        or item.responsibility_ids
        or item.procedure_graph_ids
        for item in run.requirements
    ):
        raise ValueError("deep preflight requirements must be skipped without payload")
    run_diagnostics = _safe_diagnostics(run.diagnostics)
    requirement_diagnostics = tuple(
        _safe_diagnostics(item.diagnostics) for item in run.requirements
    )
    record = {
        "event": "preflight_failed",
        "level": "error",
        "message_ru": "Preflight не пройден; предметный анализ не запускался.",
        "run_id": run.run_id,
        "run_status": run.run_status,
        "diagnostics": run_diagnostics,
        "requirements": [
            {
                "requirement_id": item.requirement.requirement_id,
                "analysis_state": item.analysis_state.value,
                "support_status": None,
                "diagnostics": diagnostics,
            }
            for item, diagnostics in zip(
                run.requirements, requirement_diagnostics, strict=True
            )
        ],
    }
    log_payload = canonical_json_bytes(record)
    log_hash = hashlib.sha256(log_payload).hexdigest()
    manifest = _deep_manifest_payload(run, {"run.jsonl": log_hash})
    return log_payload, canonical_json_bytes(manifest)


def _safe_diagnostics(value: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for item in value:
        if type(item) is not str or not item.strip() or any(
            character in item for character in "\r\n\x00"
        ):
            raise ValueError("diagnostic must be safe non-empty single-line text")
        value_for_checks = _SAFE_MODELS_ENDPOINT_TOKEN.sub("", item)
        normalized = value_for_checks.casefold()
        if (
            "/" in value_for_checks
            or "\\" in value_for_checks
            or _PATH_KEY_VALUE.search(value_for_checks) is not None
            or any(
                fragment in normalized
                for fragment in _UNSAFE_DIAGNOSTIC_FRAGMENTS
            )
        ):
            raise ValueError("diagnostic contains secret, URL, or local-path data")
        result.append(item)
    return result


def _deep_manifest_payload(
    run: DeepRunResult,
    artifact_hashes: MappingABC[str, str],
) -> dict[str, object]:
    if not isinstance(artifact_hashes, MappingABC):
        raise ValueError("artifact_hashes must be a mapping")
    safe_hashes: dict[str, str] = {}
    for name, digest in artifact_hashes.items():
        if name == "manifest.json":
            continue
        if type(name) is not str or name not in _DEEP_ARTIFACT_NAMES:
            raise ValueError(f"unknown deep artifact hash: {name}")
        if (
            type(digest) is not str
            or len(digest) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"artifact hash for {name} must be lowercase SHA-256")
        safe_hashes[name] = digest
    metadata = _deep_metadata_payload(run)
    from reqmap.binding_export import source_context_summary
    return {
        **({"source_context": source_context_summary(run)} if "source_context" in metadata else {}),
        "schema_version": run.schema_version,
        "run_id": run.run_id,
        "run_status": run.run_status,
        "reqmap_version": metadata["reqmap_version"],
        **({"analysis_origin": metadata["analysis_origin"]} if "analysis_origin" in metadata else {}),
        "analysis_profile": metadata["analysis_profile"],
        "model": metadata["model"],
        "seed": metadata["seed"],
        "top_k": metadata["top_k"],
        "input_sha256": metadata["input_sha256"],
        "snapshot_id": metadata["snapshot_id"],
        "knowledge_trust": metadata["knowledge_trust"],
        **({"binding_contract": metadata["binding_contract"]} if "binding_contract" in metadata else {}),
        "prompt_versions": metadata["prompt_versions"],
        "release_profile": metadata["release_profile"],
        "retry_counts": metadata["retry_counts"],
        "artifact_hashes": dict(sorted(safe_hashes.items())),
    }


def atomic_write_json(
    path: Path,
    payload: MappingABC[str, object],
) -> None:
    """Записать JSON object тем же canonical и atomic способом, что result."""
    if not isinstance(payload, MappingABC):
        raise ValueError("atomic_write_json ожидает mapping.")
    atomic_write_bytes(path, canonical_json_bytes(payload))


class RunLogger:
    """Однопроцессный JSONL logger с рекурсивной redaction перед записью."""

    def __init__(
        self,
        path: Path,
        redacted_values: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(path, Path):
            raise ValueError("Путь журнала должен быть Path.")
        if type(redacted_values) is not tuple or any(
            type(value) is not str for value in redacted_values
        ):
            raise ValueError("redacted_values должен быть tuple строк.")
        self._path = path
        self._redacted_values = tuple(
            sorted(
                {value for value in redacted_values if value},
                key=len,
                reverse=True,
            )
        )
        self._verified_prefix: tuple[int, str] | None = None

    def write(
        self,
        event: str,
        level: str,
        message_ru: str,
        **safe_fields: object,
    ) -> str:
        """Append one JSON object and return the verified opened log digest."""
        for label, value in (
            ("event", event),
            ("level", level),
            ("message_ru", message_ru),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{label} должен быть непустой built-in строкой.")
        collisions = sorted(_RESERVED_LOG_FIELDS.intersection(safe_fields))
        if collisions:
            raise ValueError(
                f"safe_fields не может заменять поля: {', '.join(collisions)}"
            )
        record: dict[str, object] = {
            "event": event,
            "level": level,
            "message_ru": message_ru,
            **safe_fields,
        }
        redacted = _redact(to_dict(record), self._redacted_values)
        self._verified_prefix = _append_line(
            self._path,
            canonical_json_bytes(redacted),
            self._verified_prefix,
        )
        return self._verified_prefix[1]


def _append_line(
    path: Path,
    payload: bytes,
    verified_prefix: tuple[int, str] | None,
) -> tuple[int, str]:
    symlink = symlink_component(path)
    if symlink is not None:
        raise UnsafeLogError(
            f"Путь журнала не может проходить через symlink: {symlink.name}"
        )
    ensure_secure_directory(path.parent)
    try:
        prior = os.lstat(path)
    except FileNotFoundError:
        prior = None
    if prior is not None and not stat.S_ISREG(prior.st_mode):
        raise UnsafeLogError("Путь журнала должен быть обычным файлом.")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise UnsafeLogError("Путь журнала должен быть обычным файлом.")
        published = _opened_log_path_state(path)
        opened_identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(published.st_mode)
            or (published.st_dev, published.st_ino) != opened_identity
            or (
                prior is not None
                and (prior.st_dev, prior.st_ino) != opened_identity
            )
        ):
            raise UnsafeLogError("Путь журнала был заменён во время открытия.")
        os.fchmod(descriptor, 0o600)
        before_write = os.fstat(descriptor)
        published_before_write = _opened_log_path_state(path)
        prefix_state = _log_state(before_write)
        if (
            not stat.S_ISREG(before_write.st_mode)
            or not stat.S_ISREG(published_before_write.st_mode)
            or _log_state(published_before_write) != prefix_state
        ):
            raise UnsafeLogError("Журнал изменился перед безопасной записью.")
        prefix_digest, expected_digest = _prefix_and_expected_digests(
            descriptor,
            before_write.st_size,
            payload,
        )
        prefix_hashed = os.fstat(descriptor)
        published_prefix_hashed = _opened_log_path_state(path)
        if (
            not stat.S_ISREG(prefix_hashed.st_mode)
            or not stat.S_ISREG(published_prefix_hashed.st_mode)
            or _log_state(prefix_hashed) != prefix_state
            or _log_state(published_prefix_hashed) != prefix_state
        ):
            raise UnsafeLogError("Журнал изменился при проверке исходного prefix.")
        if verified_prefix is not None and verified_prefix != (
            before_write.st_size,
            prefix_digest,
        ):
            raise UnsafeLogError("Проверенный prefix журнала был изменён.")
        expected_size = before_write.st_size + len(payload)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Не удалось дописать JSONL record.")
            view = view[written:]
        os.fsync(descriptor)
        after_write = os.fstat(descriptor)
        published = _opened_log_path_state(path)
        if (
            not stat.S_ISREG(after_write.st_mode)
            or not stat.S_ISREG(published.st_mode)
            or (after_write.st_dev, after_write.st_ino) != opened_identity
            or (published.st_dev, published.st_ino) != opened_identity
            or after_write.st_size != expected_size
            or published.st_size != expected_size
        ):
            raise UnsafeLogError("Путь или размер журнала изменился во время записи.")
        if _descriptor_region(
            descriptor,
            before_write.st_size,
            len(payload),
        ) != payload:
            raise UnsafeLogError("Добавленная запись журнала была изменена.")
        digest = _sha256_descriptor(descriptor, expected_size)
        if digest != expected_digest:
            raise UnsafeLogError("Содержимое журнала не совпало с проверенным prefix.")
        after_hash = os.fstat(descriptor)
        published_after_hash = _opened_log_path_state(path)
        stable_state = _log_state(after_write)
        if (
            not stat.S_ISREG(after_hash.st_mode)
            or not stat.S_ISREG(published_after_hash.st_mode)
            or _log_state(after_hash) != stable_state
            or _log_state(published_after_hash) != stable_state
        ):
            raise UnsafeLogError("Журнал изменился во время вычисления SHA-256.")
        return expected_size, digest
    finally:
        os.close(descriptor)


def _prefix_and_expected_digests(
    descriptor: int,
    prefix_size: int,
    payload: bytes,
) -> tuple[str, str]:
    prefix_hasher = hashlib.sha256()
    offset = 0
    while offset < prefix_size:
        chunk = os.pread(
            descriptor,
            min(1024 * 1024, prefix_size - offset),
            offset,
        )
        if not chunk:
            raise UnsafeLogError("Prefix журнала укоротился во время проверки.")
        prefix_hasher.update(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, prefix_size):
        raise UnsafeLogError("Prefix журнала вырос во время проверки.")
    expected_hasher = prefix_hasher.copy()
    expected_hasher.update(payload)
    return prefix_hasher.hexdigest(), expected_hasher.hexdigest()


def _descriptor_region(descriptor: int, offset: int, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = os.pread(
            descriptor,
            min(1024 * 1024, length - len(result)),
            offset + len(result),
        )
        if not chunk:
            raise UnsafeLogError("Добавленная запись журнала укоротилась.")
        result.extend(chunk)
    return bytes(result)


def _sha256_descriptor(descriptor: int, expected_size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < expected_size:
        chunk = os.pread(
            descriptor,
            min(1024 * 1024, expected_size - offset),
            offset,
        )
        if not chunk:
            raise UnsafeLogError("Журнал укоротился во время вычисления SHA-256.")
        digest.update(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, expected_size):
        raise UnsafeLogError("Журнал вырос во время вычисления SHA-256.")
    return digest.hexdigest()


def _log_state(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _opened_log_path_state(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise UnsafeLogError("Путь журнала исчез во время безопасной записи.") from exc


def _redact(value: object, redacted_values: tuple[str, ...]) -> object:
    if isinstance(value, str):
        result = value
        for secret in redacted_values:
            result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, list):
        return [_redact(item, redacted_values) for item in value]
    if isinstance(value, dict):
        return {
            str(_redact(str(key), redacted_values)): _redact(
                item,
                redacted_values,
            )
            for key, item in value.items()
        }
    return value


def _safe_origin(value: object) -> str | None:
    if type(value) is not str or not value:
        return None
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not hostname:
        return None
    if ":" in hostname:
        hostname = f"[{hostname}]"
    suffix = f":{port}" if port is not None else ""
    return urlunsplit((parts.scheme, f"{hostname}{suffix}", "", "", ""))


def _safe_stage_mapping(
    value: object,
    *,
    value_type: type,
) -> dict[str, object]:
    if not isinstance(value, MappingABC):
        return {}
    result: dict[str, object] = {}
    for stage in _MODEL_STAGES:
        item = value.get(stage)
        if type(item) is value_type:
            result[stage] = item
    return result
