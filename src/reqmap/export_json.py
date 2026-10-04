"""Каноническая JSON-сериализация и безопасная атомарная запись."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit

from reqmap.aggregation import aggregate_groups
from reqmap.analysis_origin import origin_metadata
from reqmap.models import AnalysisState, Evidence, RunResult, to_dict


_SECRET_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "base_url",
        "credential",
        "endpoint_url",
        "password",
        "secret",
        "token",
    }
)


def write_canonical_json(run: RunResult, path: Path) -> str:
    """Записать единственный канонический result JSON и вернуть его SHA-256."""
    validate_run_result(run)
    payload = canonical_json_bytes(to_dict(run))
    atomic_write_bytes(path, payload)
    return hashlib.sha256(payload).hexdigest()


def validate_run_result(run: RunResult) -> None:
    """Повторно доказать структурную каноничность RunResult перед экспортом."""
    if type(run) is not RunResult:
        raise ValueError("write_canonical_json ожидает canonical RunResult.")
    for label, value in (
        ("run_id", run.run_id),
        ("schema_version", run.schema_version),
        ("run_status", run.run_status),
    ):
        if type(value) is not str or not value:
            raise ValueError(f"{label} RunResult должен быть непустой строкой.")
    if run.run_status not in {"SUCCESS", "PARTIAL", "FAILED"}:
        raise ValueError("run_status не входит в канонический набор.")
    if type(run.requirements) is not tuple:
        raise ValueError("requirements RunResult должен быть tuple.")
    if type(run.groups) is not tuple:
        raise ValueError("groups RunResult должен быть tuple.")
    if type(run.evidence) is not tuple or any(
        type(item) is not Evidence for item in run.evidence
    ):
        raise ValueError("evidence RunResult должен быть tuple Evidence.")
    if type(run.diagnostics) is not tuple or any(
        type(item) is not str or not item.strip() for item in run.diagnostics
    ):
        raise ValueError("diagnostics RunResult должен быть tuple строк.")

    requirement_ids = tuple(
        item.requirement.requirement_id for item in run.requirements
    )
    if len(requirement_ids) != len(set(requirement_ids)):
        raise ValueError("RunResult содержит повторяющиеся requirement_id.")
    if tuple(item.requirement.ordinal for item in run.requirements) != tuple(
        range(1, len(run.requirements) + 1)
    ):
        raise ValueError("RunResult нарушает исходный порядок requirements.")
    expected_groups = aggregate_groups(run.requirements)
    if run.groups != expected_groups:
        raise ValueError("GroupResult не воспроизводится из requirements.")

    completed = sum(
        item.analysis_state is AnalysisState.COMPLETED
        for item in run.requirements
    )
    if not run.requirements or completed == 0:
        expected_run_status = "FAILED"
    elif completed == len(run.requirements):
        expected_run_status = "SUCCESS"
    else:
        expected_run_status = "PARTIAL"
    if run.run_status != expected_run_status:
        raise ValueError("run_status не согласован с analysis_state требований.")

    evidence_ids = tuple(item.evidence_id for item in run.evidence)
    if any(type(item) is not str or not item for item in evidence_ids):
        raise ValueError("evidence_id должен быть непустой строкой.")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("RunResult содержит повторяющиеся evidence_id.")
    cited_ids = {
        evidence_id
        for result in run.requirements
        for mapping in result.mappings
        for evidence_id in mapping.evidence_ids
    }
    if evidence_ids != tuple(sorted(cited_ids)):
        raise ValueError("RunResult.evidence не совпадает с cited evidence mappings.")

    if not isinstance(run.metadata, MappingABC):
        raise ValueError("RunResult.metadata должен быть mapping.")
    origin_metadata(run.metadata)
    _validate_metadata_keys(run.metadata)
    _validate_endpoint_origin(run.metadata.get("endpoint_origin"))


def _validate_metadata_keys(value: MappingABC[object, object]) -> None:
    for key, item in value.items():
        if type(key) is not str or not key:
            raise ValueError("metadata keys должны быть непустыми строками.")
        normalized = key.casefold().replace("-", "_")
        if normalized in _SECRET_METADATA_KEYS:
            raise ValueError(f"metadata содержит secret-bearing key: {key}")
        _validate_metadata_value(item)


def _validate_metadata_value(value: object) -> None:
    if isinstance(value, MappingABC):
        _validate_metadata_keys(value)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_metadata_value(item)


def _validate_endpoint_origin(value: object) -> None:
    if value is None:
        return
    if type(value) is not str or not value:
        raise ValueError("metadata.endpoint_origin должен быть строкой или null.")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("metadata.endpoint_origin имеет недопустимый URL.") from exc
    if (
        parts.scheme not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("metadata.endpoint_origin обязан содержать только origin.")
    if ":" in hostname:
        hostname = f"[{hostname}]"
    suffix = f":{port}" if port is not None else ""
    canonical = urlunsplit((parts.scheme, f"{hostname}{suffix}", "", "", ""))
    if value.rstrip("/") != canonical:
        raise ValueError("metadata.endpoint_origin имеет неканоническую форму.")


def canonical_json_bytes(value: object) -> bytes:
    """Сериализовать JSON детерминированно с завершающим переводом строки."""
    return (
        json.dumps(
            to_dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Атомарно заменить обычный файл, никогда не следуя symlink."""
    if not isinstance(path, Path):
        raise ValueError("Выходной path должен быть Path.")
    if type(payload) is not bytes:
        raise ValueError("Атомарная запись принимает только bytes.")
    symlink = symlink_component(path)
    if symlink is not None:
        raise ValueError(
            f"Выходной путь не может проходить через symlink: {symlink.name}"
        )
    ensure_secure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError(f"Выходной файл не может быть symlink: {path.name}")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def ensure_secure_directory(path: Path) -> None:
    """Создать каталог с mode 0700 после проверки всех компонентов пути."""
    symlink = symlink_component(path)
    if symlink is not None:
        raise ValueError(f"Каталог не может проходить через symlink: {symlink.name}")
    existed = path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"Ожидался обычный каталог: {path.name}")
    if not existed:
        os.chmod(path, 0o700)


def symlink_component(path: Path) -> Path | None:
    """Вернуть первый существующий symlink в абсолютном пути без resolve."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            return current
    return None
