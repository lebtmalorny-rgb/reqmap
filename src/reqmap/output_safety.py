"""Shared request, output-directory, and secure checkpoint primitives."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import cast

from reqmap.models import AnalysisRequest, Requirement, to_dict


FINAL_ARTIFACTS = ("result.json", "result.xlsx", "report.md")
DIAGNOSTIC_ARTIFACTS = ("run.jsonl", "manifest.json")
PUBLISHED_ARTIFACTS = (*FINAL_ARTIFACTS, *DIAGNOSTIC_ARTIFACTS)
OUTPUT_NOT_CLEAN_CODES = frozenset(
    {"OUTPUT_ARTIFACT_INVALID", "OUTPUT_CLEANUP_FAILED"}
)
_SAFE_REQUIREMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_analysis_request(request: AnalysisRequest) -> tuple[str, ...]:
    """Validate canonical input and create only a safe output directory."""
    if type(request) is not AnalysisRequest:
        return ("REQUEST_INVALID: ожидается canonical AnalysisRequest.",)
    diagnostics: list[str] = []
    if type(request.requirements) is not tuple or any(
        type(item) is not Requirement for item in request.requirements
    ):
        diagnostics.append("REQUEST_INVALID: requirements должен быть tuple Requirement.")
    else:
        identifiers = tuple(item.requirement_id for item in request.requirements)
        if len(identifiers) != len(set(identifiers)):
            diagnostics.append("REQUEST_INVALID: requirement_id должны быть уникальными.")
        if any(
            type(identifier) is not str
            or _SAFE_REQUIREMENT_ID.fullmatch(identifier) is None
            for identifier in identifiers
        ):
            diagnostics.append("REQUEST_INVALID: небезопасный requirement_id.")
        expected_ordinals = tuple(range(1, len(request.requirements) + 1))
        if tuple(item.ordinal for item in request.requirements) != expected_ordinals:
            diagnostics.append("REQUEST_INVALID: нарушен исходный порядок требований.")
    if type(request.input_sha256) is not str or _SHA256.fullmatch(
        request.input_sha256
    ) is None:
        diagnostics.append("REQUEST_INVALID: input_sha256 должен быть SHA-256.")
    if not isinstance(request.output_dir, Path):
        diagnostics.append("REQUEST_INVALID: output_dir должен быть Path.")
        return tuple(diagnostics)
    if request.source_path is not None and not isinstance(request.source_path, Path):
        diagnostics.append("REQUEST_INVALID: source_path должен быть Path или null.")
    if diagnostics:
        return tuple(diagnostics)

    output = request.output_dir
    symlink = symlink_component(output)
    if symlink is not None:
        return (
            f"OUTPUT_SYMLINK: компонент {symlink.name} в output path является symlink.",
        )
    if output.exists() and not output.is_dir():
        return ("OUTPUT_INVALID: output_dir не является каталогом.",)
    if request.source_path is not None:
        source = request.source_path
        if not source.is_file() or not os.access(source, os.R_OK):
            diagnostics.append("INPUT_UNREADABLE: входной файл недоступен для чтения.")
        collisions = tuple(
            name
            for name in PUBLISHED_ARTIFACTS
            if same_path(source, output / name)
        )
        if request.input_kind == "xlsx" and collisions:
            diagnostics.append(
                "OUTPUT_INPUT_COLLISION: выходной артефакт совпадает с исходным "
                f"XLSX: {', '.join(collisions)}."
            )

    for name in (*PUBLISHED_ARTIFACTS, ".work"):
        candidate = output / name
        if candidate.is_symlink():
            diagnostics.append(f"OUTPUT_SYMLINK: {name} не может быть symlink.")
    if diagnostics:
        return tuple(diagnostics)

    parent = nearest_existing_parent(output)
    if not os.access(parent, os.W_OK | os.X_OK):
        return ("OUTPUT_UNWRITABLE: родитель output_dir недоступен для записи.",)
    try:
        ensure_secure_directory(output)
    except OSError:
        return ("OUTPUT_UNWRITABLE: не удалось создать output_dir.",)
    return ()


def clear_published_artifacts(output: Path) -> tuple[str, ...]:
    """Не допустить смешения артефактов предыдущего и текущего запусков."""
    paths = tuple(output / name for name in PUBLISHED_ARTIFACTS)
    invalid = tuple(
        path.name for path in paths if path.exists() and not path.is_file()
    )
    if invalid:
        return (
            "OUTPUT_ARTIFACT_INVALID: вместо обычного файла обнаружено: "
            f"{', '.join(invalid)}.",
        )
    try:
        for path in paths:
            path.unlink(missing_ok=True)
    except OSError:
        return (
            "OUTPUT_CLEANUP_FAILED: не удалось удалить артефакты предыдущего запуска.",
        )
    return ()


def ensure_secure_directory(path: Path) -> None:
    symlink = symlink_component(path)
    if symlink is not None:
        raise OSError(f"Каталог не может проходить через symlink: {symlink.name}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise OSError(f"Ожидался обычный каталог: {path.name}")
    os.chmod(path, 0o700)


def atomic_write(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"Выходной файл не может быть symlink: {path.name}")
    ensure_secure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def canonical_bytes(value: object) -> bytes:
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


def strict_json_object(source: str) -> dict[str, object]:
    value = json.loads(source, object_pairs_hook=_no_duplicate_keys)
    if type(value) is not dict:
        raise ValueError("Ожидался JSON object")
    return cast(dict[str, object], value)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющийся JSON key: {key}")
        result[key] = value
    return result


def nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            return candidate
        candidate = candidate.parent
    return candidate


def symlink_component(path: Path) -> Path | None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            return current
    return None


def same_path(first: Path, second: Path) -> bool:
    try:
        return first.resolve(strict=False) == second.resolve(strict=False)
    except OSError:
        return first.absolute() == second.absolute()
