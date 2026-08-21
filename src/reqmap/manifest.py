"""Воспроизводимый manifest и безопасный append-only JSONL-журнал."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from reqmap.export_json import (
    atomic_write_bytes,
    canonical_json_bytes,
    ensure_secure_directory,
    symlink_component,
)
from reqmap.models import RunResult, to_dict


_RESERVED_LOG_FIELDS = frozenset({"event", "level", "message_ru"})
_ARTIFACT_NAMES = frozenset(
    {"result.json", "result.xlsx", "report.md", "run.jsonl"}
)
_MODEL_STAGES = ("decomposition", "mapping")


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
    atomic_write_json(path, manifest)


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

    def write(
        self,
        event: str,
        level: str,
        message_ru: str,
        **safe_fields: object,
    ) -> None:
        """Добавить одну завершённую JSON object строку без раскрытия secret values."""
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
        _append_line(self._path, canonical_json_bytes(redacted))


def _append_line(path: Path, payload: bytes) -> None:
    symlink = symlink_component(path)
    if symlink is not None:
        raise ValueError(
            f"Путь журнала не может проходить через symlink: {symlink.name}"
        )
    ensure_secure_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Не удалось дописать JSONL record.")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
