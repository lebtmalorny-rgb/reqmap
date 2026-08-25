"""Fail-closed integrity and OpenSSH trust verification for v2 snapshots."""

from __future__ import annotations

from collections.abc import Mapping
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from typing import Any, NoReturn

from reqmap.errors import ReqmapError
from reqmap.export_json import atomic_write_bytes, canonical_json_bytes


_MANIFEST_NAME = "snapshot-manifest.json"
_SIGNATURE_NAME = "snapshot-manifest.sig"
_EXCLUDED_ROOT_FILES = {_MANIFEST_NAME, _SIGNATURE_NAME}
_GOVERNED_SUFFIXES = {".json", ".jsonl", ".md"}
_MANIFEST_FIELDS = {
    "manifest_schema_version",
    "knowledge_schema_version",
    "snapshot_id",
    "key_id",
    "files",
}
_FILE_FIELDS = {"path", "size", "sha256"}
_MANIFEST_SCHEMA_VERSION = "1.0"
_KNOWLEDGE_SCHEMA_VERSION = 2
_SNAPSHOT_ID = "epoxy-2025.1-deep-001"
_KEY_ID = "reqmap-maintenance-2026"
_SIGNER_IDENTITY = "reqmap-snapshot"
_SIGNATURE_NAMESPACE = "reqmap-snapshot"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SnapshotTrustError(ReqmapError):
    """A snapshot cannot be proven complete, intact, and trusted."""


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SnapshotTrust:
    snapshot_id: str
    key_id: str
    signer_identity: str
    manifest_sha256: str
    files: tuple[SnapshotFile, ...]


def build_snapshot_manifest(root: Path) -> dict[str, object]:
    """Build the canonical sorted manifest object for governed snapshot files."""
    files = _snapshot_files(root, "SNAPSHOT_INTEGRITY_FAILED")
    return {
        "manifest_schema_version": _MANIFEST_SCHEMA_VERSION,
        "knowledge_schema_version": _KNOWLEDGE_SCHEMA_VERSION,
        "snapshot_id": _SNAPSHOT_ID,
        "key_id": _KEY_ID,
        "files": [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in files
        ],
    }


def write_snapshot_manifest(root: Path) -> dict[str, object]:
    """Validate a schema-v2 snapshot and atomically write its canonical manifest."""
    normalized_root = _validated_root(root, "SNAPSHOT_INTEGRITY_FAILED")
    manifest_path = normalized_root / _MANIFEST_NAME
    _validate_replace_target(
        manifest_path,
        "SNAPSHOT_MANIFEST_INVALID",
        "Существующий manifest snapshot небезопасен.",
    )
    prior_manifest = _optional_regular_file_bytes(manifest_path)

    # Avoid a module-level cycle: the maintenance loader imports this trust module.
    from reqmap.knowledge_v2 import load_knowledge_v2_for_maintenance

    manifest = build_snapshot_manifest(normalized_root)
    manifest_bytes = canonical_json_bytes(manifest)
    load_knowledge_v2_for_maintenance(normalized_root)
    if canonical_json_bytes(build_snapshot_manifest(normalized_root)) != manifest_bytes:
        _fail_without_context(
            "SNAPSHOT_INTEGRITY_FAILED",
            "Governed файлы snapshot изменились во время validation.",
        )

    try:
        atomic_write_bytes(manifest_path, manifest_bytes)
        published_bytes = _read_regular_file(
            manifest_path,
            "SNAPSHOT_MANIFEST_INVALID",
            "Опубликованный manifest snapshot отсутствует или небезопасен.",
        )
        if published_bytes != manifest_bytes:
            _fail_without_context(
                "SNAPSHOT_MANIFEST_INVALID",
                "Опубликованные bytes manifest snapshot изменились.",
            )
        published_trust = _parse_manifest_bytes(published_bytes)
        verify_snapshot_integrity(normalized_root, published_trust)
    except BaseException as exc:
        _restore_manifest(manifest_path, prior_manifest, manifest_bytes)
        if isinstance(exc, SnapshotTrustError):
            raise
        _fail_without_context(
            "SNAPSHOT_MANIFEST_INVALID",
            "Не удалось безопасно записать manifest snapshot.",
        )
    return manifest


def sign_snapshot_manifest(root: Path, private_key: Path) -> Path:
    """Sign an approved current canonical manifest with an external Ed25519 key."""
    normalized_root = _validated_root(root, "SNAPSHOT_INTEGRITY_FAILED")
    _validate_private_key(normalized_root, private_key)
    manifest_path = normalized_root / _MANIFEST_NAME
    signature_path = normalized_root / _SIGNATURE_NAME
    _validate_replace_target(
        signature_path,
        "SNAPSHOT_TRUST_BOUNDARY",
        "Существующий target подписи snapshot небезопасен.",
    )

    # Avoid a module-level cycle: the maintenance loader imports this trust module.
    from reqmap.knowledge_v2 import load_knowledge_v2_for_maintenance

    private_key_bytes = _read_private_key_bytes(private_key)
    manifest_bytes = _read_regular_file(
        manifest_path,
        "SNAPSHOT_MANIFEST_INVALID",
        "Manifest snapshot отсутствует или небезопасен.",
    )
    trust = _parse_manifest_bytes(manifest_bytes)
    verify_snapshot_integrity(normalized_root, trust)
    knowledge = load_knowledge_v2_for_maintenance(normalized_root)
    if knowledge.snapshot_status != "approved":
        _fail_without_context("SNAPSHOT_NOT_APPROVED", "Deep snapshot не утверждён.")

    workspace, signing_key, signing_manifest, generated_path = _signing_workspace(
        normalized_root,
        private_key_bytes,
        manifest_bytes,
    )
    try:
        try:
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    str(signing_key),
                    "-n",
                    _SIGNATURE_NAMESPACE,
                    str(signing_manifest),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
                shell=False,
            )
        except FileNotFoundError:
            _fail_without_context(
                "SNAPSHOT_SIGNER_MISSING",
                "Системный signer подписи snapshot недоступен.",
            )
        except subprocess.TimeoutExpired:
            _fail_without_context(
                "SNAPSHOT_SIGNATURE_TIMEOUT",
                "Создание подписи snapshot превысило безопасный timeout.",
            )
        except OSError:
            _fail_without_context(
                "SNAPSHOT_SIGNING_FAILED",
                "Не удалось создать подпись snapshot.",
            )
        if completed.returncode != 0:
            _fail_without_context(
                "SNAPSHOT_SIGNING_FAILED",
                "Не удалось создать подпись snapshot.",
            )

        signature_bytes = _read_generated_signature(generated_path)
        _require_ed25519_signature(signature_bytes)
        if _read_regular_file(
            manifest_path,
            "SNAPSHOT_MANIFEST_INVALID",
            "Manifest snapshot изменён во время подписания.",
        ) != manifest_bytes:
            _fail_without_context(
                "SNAPSHOT_MANIFEST_INVALID",
                "Manifest snapshot изменён во время подписания.",
            )
        verify_snapshot_integrity(normalized_root, trust)
        _validate_replace_target(
            signature_path,
            "SNAPSHOT_TRUST_BOUNDARY",
            "Target подписи snapshot изменён и небезопасен.",
        )
        _remove_temp_file(signing_key)
        _remove_temp_file(signing_manifest)
        try:
            os.replace(generated_path, signature_path)
        except OSError:
            _fail_without_context(
                "SNAPSHOT_SIGNING_FAILED",
                "Не удалось безопасно установить подпись snapshot.",
            )
        return signature_path
    finally:
        _cleanup_signing_workspace(
            workspace,
            (signing_key, signing_manifest, generated_path),
        )


def parse_snapshot_manifest(path: Path) -> SnapshotTrust:
    """Read and strictly parse one canonical snapshot manifest."""
    payload = _read_regular_file(
        path,
        "SNAPSHOT_MANIFEST_INVALID",
        "Manifest snapshot отсутствует или небезопасен.",
    )
    return _parse_manifest_bytes(payload)


def verify_snapshot_integrity(root: Path, trust: SnapshotTrust) -> None:
    """Verify that the governed filesystem is exactly the signed file set."""
    if type(trust) is not SnapshotTrust:
        _fail("SNAPSHOT_MANIFEST_INVALID", "Manifest snapshot имеет неверный тип.")
    actual = _snapshot_files(root, "SNAPSHOT_INTEGRITY_FAILED")
    if actual != trust.files:
        _fail(
            "SNAPSHOT_INTEGRITY_FAILED",
            "Состав, размер или SHA-256 файлов snapshot не совпадает с manifest.",
        )


def verify_snapshot_signature(root: Path, allowed_signers_path: Path) -> None:
    """Verify the detached manifest signature with fixed OpenSSH policy."""
    normalized_root = _validated_root(root, "SNAPSHOT_INTEGRITY_FAILED")
    _validate_allowed_signers(normalized_root, allowed_signers_path)
    manifest_bytes = _read_regular_file(
        normalized_root / _MANIFEST_NAME,
        "SNAPSHOT_MANIFEST_INVALID",
        "Manifest snapshot отсутствует или небезопасен.",
    )
    _verify_signature_bytes(normalized_root, allowed_signers_path, manifest_bytes)


def verify_snapshot(root: Path, allowed_signers_path: Path) -> SnapshotTrust:
    """Verify canonical manifest, complete file integrity, and Ed25519 trust."""
    normalized_root = _validated_root(root, "SNAPSHOT_INTEGRITY_FAILED")
    _validate_allowed_signers(normalized_root, allowed_signers_path)
    manifest_bytes = _read_regular_file(
        normalized_root / _MANIFEST_NAME,
        "SNAPSHOT_MANIFEST_INVALID",
        "Manifest snapshot отсутствует или небезопасен.",
    )
    trust = _parse_manifest_bytes(manifest_bytes)
    verify_snapshot_integrity(normalized_root, trust)
    _verify_signature_bytes(normalized_root, allowed_signers_path, manifest_bytes)
    return trust


def read_verified_snapshot_file(
    root: Path, trust: SnapshotTrust, relative_path: str
) -> bytes:
    """Read one signed file and recheck the exact bytes against verified metadata."""
    normalized_root = _validated_root(root, "SNAPSHOT_INTEGRITY_FAILED")
    if type(trust) is not SnapshotTrust:
        _fail("SNAPSHOT_INTEGRITY_FAILED", "Snapshot trust имеет неверный тип.")
    relative = _safe_requested_path(relative_path)
    matches = tuple(item for item in trust.files if item.path == relative)
    if len(matches) != 1:
        _fail(
            "SNAPSHOT_INTEGRITY_FAILED",
            "Запрошенный файл отсутствует в подтверждённом manifest snapshot.",
        )
    payload = _read_regular_file(
        normalized_root / PurePosixPath(relative),
        "SNAPSHOT_INTEGRITY_FAILED",
        "Подтверждённый файл snapshot отсутствует или небезопасен.",
    )
    signed = matches[0]
    if len(payload) != signed.size or hashlib.sha256(payload).hexdigest() != signed.sha256:
        _fail(
            "SNAPSHOT_INTEGRITY_FAILED",
            "Bytes файла snapshot не совпадают с подтверждённым manifest.",
        )
    return payload


def _parse_manifest_bytes(payload: bytes) -> SnapshotTrust:
    try:
        raw = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        _fail(
            "SNAPSHOT_MANIFEST_INVALID",
            "Manifest snapshot не является строгим JSON.",
            exc,
        )
    if type(raw) is not dict:
        _fail("SNAPSHOT_MANIFEST_INVALID", "Manifest snapshot должен быть JSON object.")
    if canonical_json_bytes(raw) != payload:
        _fail("SNAPSHOT_MANIFEST_INVALID", "Manifest snapshot имеет неканонические bytes.")
    _require_exact_fields(raw, _MANIFEST_FIELDS, "manifest")
    if raw["manifest_schema_version"] != _MANIFEST_SCHEMA_VERSION:
        _fail("SNAPSHOT_MANIFEST_INVALID", "Версия schema manifest не поддерживается.")
    if (
        type(raw["knowledge_schema_version"]) is not int
        or raw["knowledge_schema_version"] != _KNOWLEDGE_SCHEMA_VERSION
    ):
        _fail("SNAPSHOT_MANIFEST_INVALID", "Snapshot должен использовать knowledge schema v2.")
    snapshot_id = _required_identifier(raw["snapshot_id"], "snapshot_id")
    key_id = _required_identifier(raw["key_id"], "key_id")
    if key_id != _KEY_ID:
        _fail("SNAPSHOT_MANIFEST_INVALID", "ID ключа manifest не разрешён.")
    raw_files = raw["files"]
    if type(raw_files) is not list:
        _fail("SNAPSHOT_MANIFEST_INVALID", "manifest.files должен быть list.")
    files: list[SnapshotFile] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_files):
        if type(item) is not dict:
            _fail("SNAPSHOT_MANIFEST_INVALID", f"manifest.files[{index}] должен быть object.")
        _require_exact_fields(item, _FILE_FIELDS, f"manifest.files[{index}]")
        relative = _safe_manifest_path(item["path"])
        if relative in seen:
            _fail("SNAPSHOT_MANIFEST_INVALID", "Manifest содержит дублирующийся path.")
        seen.add(relative)
        size = item["size"]
        if type(size) is not int or size < 0:
            _fail("SNAPSHOT_MANIFEST_INVALID", "Размер файла в manifest некорректен.")
        digest = item["sha256"]
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            _fail("SNAPSHOT_MANIFEST_INVALID", "SHA-256 файла в manifest некорректен.")
        files.append(SnapshotFile(relative, size, digest))
    if [item.path for item in files] != sorted(item.path for item in files):
        _fail("SNAPSHOT_MANIFEST_INVALID", "Файлы manifest должны быть отсортированы по path.")
    return SnapshotTrust(
        snapshot_id=snapshot_id,
        key_id=key_id,
        signer_identity=_SIGNER_IDENTITY,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        files=tuple(files),
    )


def _snapshot_files(root: Path, error_code: str) -> tuple[SnapshotFile, ...]:
    normalized_root = _validated_root(root, error_code)
    candidates: list[tuple[str, Path]] = []
    try:
        for directory, directory_names, file_names in os.walk(
            normalized_root,
            topdown=True,
            onerror=lambda exc: _fail(
                error_code,
                "Не удалось безопасно перечислить файлы snapshot.",
                exc,
            ),
            followlinks=False,
        ):
            base = Path(directory)
            for name in directory_names:
                candidate = base / name
                if not stat.S_ISDIR(candidate.lstat().st_mode):
                    _fail(error_code, "Snapshot должен содержать только обычные каталоги.")
            for name in file_names:
                candidate = base / name
                relative_path = candidate.relative_to(normalized_root)
                relative = relative_path.as_posix()
                if not stat.S_ISREG(candidate.lstat().st_mode):
                    _fail(error_code, "Snapshot должен содержать только обычные файлы.")
                if any(part.startswith(".") for part in relative_path.parts):
                    _fail(error_code, "Snapshot содержит скрытый временный файл.")
                if relative in _EXCLUDED_ROOT_FILES:
                    continue
                if _is_governed(relative_path):
                    candidates.append((relative, candidate))
    except OSError as exc:
        _fail(error_code, "Не удалось безопасно перечислить файлы snapshot.", exc)
    candidates.sort(key=lambda item: item[0])
    if len({relative for relative, _ in candidates}) != len(candidates):
        _fail(error_code, "Snapshot содержит дублирующиеся normalized paths.")
    files = []
    for relative, candidate in candidates:
        payload = _read_regular_file(
            candidate,
            error_code,
            "Файл snapshot отсутствует или небезопасен.",
        )
        files.append(SnapshotFile(relative, len(payload), hashlib.sha256(payload).hexdigest()))
    return tuple(files)


def _is_governed(relative: Path) -> bool:
    return relative.suffix.lower() in _GOVERNED_SUFFIXES or (
        bool(relative.parts) and relative.parts[0] == "indexes"
    )


def _validated_root(root: Path, error_code: str) -> Path:
    if not isinstance(root, Path):
        _fail(error_code, "Snapshot root должен быть Path.")
    try:
        root_stat = root.lstat()
    except OSError as exc:
        _fail(error_code, "Каталог snapshot отсутствует или недоступен.", exc)
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        _fail(error_code, "Snapshot root должен быть обычным каталогом, не symlink.")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        _fail(error_code, "Каталог snapshot невозможно безопасно разрешить.", exc)


def _validate_allowed_signers(root: Path, allowed_signers_path: Path) -> None:
    if not isinstance(allowed_signers_path, Path):
        _fail("SNAPSHOT_TRUST_BOUNDARY", "allowed_signers должен быть Path вне snapshot.")
    try:
        resolved_signers = allowed_signers_path.resolve(strict=False)
        resolved_signers.relative_to(root)
    except ValueError:
        pass
    else:
        _fail(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Файл allowed_signers должен находиться вне snapshot.",
        )
    try:
        signer_stat = allowed_signers_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        _fail("SNAPSHOT_TRUST_BOUNDARY", "Файл allowed_signers небезопасен.", exc)
    if stat.S_ISLNK(signer_stat.st_mode) or not stat.S_ISREG(signer_stat.st_mode):
        _fail(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Файл allowed_signers должен быть обычным файлом вне snapshot, не symlink.",
        )


def _validate_private_key(root: Path, private_key: Path) -> None:
    if not isinstance(private_key, Path):
        _fail_without_context(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Private key должен быть Path вне snapshot.",
        )
    try:
        key_stat = private_key.lstat()
    except OSError:
        _fail_without_context(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Private key отсутствует или небезопасен.",
        )
    if stat.S_ISLNK(key_stat.st_mode) or not stat.S_ISREG(key_stat.st_mode):
        _fail_without_context(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Private key должен быть обычным файлом вне snapshot, не symlink.",
        )
    try:
        private_key.resolve(strict=True).relative_to(root)
    except ValueError:
        return
    except OSError:
        _fail_without_context(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Private key отсутствует или небезопасен.",
        )
    _fail_without_context(
        "SNAPSHOT_TRUST_BOUNDARY",
        "Private key должен находиться вне snapshot.",
    )


def _validate_replace_target(path: Path, code: str, message: str) -> None:
    try:
        target_stat = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        _fail_without_context(code, message)
    if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode):
        _fail_without_context(code, message)


def _optional_regular_file_bytes(path: Path) -> bytes | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        _fail_without_context(
            "SNAPSHOT_MANIFEST_INVALID",
            "Существующий manifest snapshot небезопасен.",
        )
    return _read_regular_file(
        path,
        "SNAPSHOT_MANIFEST_INVALID",
        "Существующий manifest snapshot небезопасен.",
    )


def _restore_manifest(path: Path, prior: bytes | None, published: bytes) -> None:
    if prior is not None:
        try:
            atomic_write_bytes(path, prior)
        except (OSError, ValueError):
            _fail_without_context(
                "SNAPSHOT_MANIFEST_INVALID",
                "Не удалось атомарно восстановить prior manifest snapshot.",
            )
        return
    try:
        current = _optional_regular_file_bytes(path)
        if current is None:
            return
        if current != published:
            _fail_without_context(
                "SNAPSHOT_MANIFEST_INVALID",
                "Отсутствующий prior manifest невозможно безопасно восстановить.",
            )
        path.unlink()
    except SnapshotTrustError:
        raise
    except OSError:
        _fail_without_context(
            "SNAPSHOT_MANIFEST_INVALID",
            "Не удалось удалить невалидный опубликованный manifest snapshot.",
        )


def _read_private_key_bytes(path: Path) -> bytes:
    try:
        return _read_regular_file(
            path,
            "SNAPSHOT_TRUST_BOUNDARY",
            "Private key отсутствует или небезопасен.",
        )
    except SnapshotTrustError:
        _fail_without_context(
            "SNAPSHOT_TRUST_BOUNDARY",
            "Private key отсутствует или небезопасен.",
        )


def _signing_workspace(
    root: Path, private_key: bytes, manifest: bytes
) -> tuple[Path, Path, Path, Path]:
    workspace: Path | None = None
    try:
        workspace = Path(
            tempfile.mkdtemp(prefix=f".{root.name}.reqmap-sign-", dir=root.parent)
        )
        os.chmod(workspace, 0o700)
        signing_key = workspace / "private-key"
        signing_manifest = workspace / _MANIFEST_NAME
        atomic_write_bytes(signing_key, private_key)
        atomic_write_bytes(signing_manifest, manifest)
        generated = signing_manifest.with_suffix(signing_manifest.suffix + ".sig")
        return workspace, signing_key, signing_manifest, generated
    except (OSError, ValueError):
        if workspace is not None:
            _cleanup_signing_workspace(workspace, ())
        _fail_without_context(
            "SNAPSHOT_SIGNING_FAILED",
            "Не удалось создать безопасный signing workspace.",
        )


def _read_generated_signature(path: Path) -> bytes:
    try:
        return _read_regular_file(
            path,
            "SNAPSHOT_SIGNING_FAILED",
            "Signer не создал безопасную подпись snapshot.",
        )
    except SnapshotTrustError:
        _fail_without_context(
            "SNAPSHOT_SIGNING_FAILED",
            "Signer не создал безопасную подпись snapshot.",
        )


def _remove_temp_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        _fail_without_context(
            "SNAPSHOT_SIGNING_FAILED",
            "Не удалось очистить signing workspace.",
        )


def _cleanup_signing_workspace(workspace: Path, paths: tuple[Path, ...]) -> None:
    candidates = list(paths)
    try:
        candidates.extend(workspace.iterdir())
    except OSError:
        pass
    for path in dict.fromkeys(candidates):
        try:
            mode = path.lstat().st_mode
            if not stat.S_ISDIR(mode):
                path.unlink()
        except OSError:
            pass
    try:
        workspace.rmdir()
    except OSError:
        pass


def _verify_signature_bytes(
    root: Path, allowed_signers_path: Path, manifest_bytes: bytes
) -> None:
    _require_ed25519_signer(allowed_signers_path)
    signature_bytes = _read_regular_file(
        root / _SIGNATURE_NAME,
        "SNAPSHOT_UNTRUSTED",
        "Подпись snapshot отсутствует или небезопасна.",
    )
    _require_ed25519_signature(signature_bytes)
    try:
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed_signers_path),
                "-I",
                _SIGNER_IDENTITY,
                "-n",
                _SIGNATURE_NAMESPACE,
                "-s",
                str(root / _SIGNATURE_NAME),
            ],
            input=manifest_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        _fail_without_context(
            "SNAPSHOT_VERIFIER_MISSING",
            "Системный verifier подписи snapshot недоступен.",
        )
    except subprocess.TimeoutExpired:
        _fail_without_context(
            "SNAPSHOT_SIGNATURE_TIMEOUT",
            "Проверка подписи snapshot превысила безопасный timeout.",
        )
    except OSError:
        _fail_without_context("SNAPSHOT_UNTRUSTED", "Не удалось проверить доверие к snapshot.")
    if completed.returncode != 0:
        _fail("SNAPSHOT_UNTRUSTED", "Подпись snapshot не подтверждена.")


def _require_ed25519_signature(payload: bytes) -> None:
    try:
        lines = payload.decode("ascii").splitlines()
        if (
            len(lines) < 3
            or lines[0] != "-----BEGIN SSH SIGNATURE-----"
            or lines[-1] != "-----END SSH SIGNATURE-----"
        ):
            raise ValueError("invalid SSH signature armor")
        envelope = base64.b64decode("".join(lines[1:-1]), validate=True)
        if not envelope.startswith(b"SSHSIG"):
            raise ValueError("invalid SSH signature magic")
        offset = len(b"SSHSIG")
        if envelope[offset : offset + 4] != b"\x00\x00\x00\x01":
            raise ValueError("unsupported SSH signature version")
        public_key, _ = _ssh_string(envelope, offset + 4)
        key_type, _ = _ssh_string(public_key, 0)
    except (UnicodeDecodeError, ValueError, binascii.Error):
        _fail_without_context("SNAPSHOT_UNTRUSTED", "Формат подписи snapshot не разрешён.")
    if key_type != b"ssh-ed25519":
        _fail("SNAPSHOT_UNTRUSTED", "Подпись snapshot должна использовать Ed25519.")


def _ssh_string(payload: bytes, offset: int) -> tuple[bytes, int]:
    if offset < 0 or offset + 4 > len(payload):
        raise ValueError("truncated SSH string")
    size = int.from_bytes(payload[offset : offset + 4], "big")
    start = offset + 4
    end = start + size
    if end > len(payload):
        raise ValueError("truncated SSH string payload")
    return payload[start:end], end


def _require_ed25519_signer(allowed_signers_path: Path) -> None:
    payload = _read_regular_file(
        allowed_signers_path,
        "SNAPSHOT_UNTRUSTED",
        "Файл доверенных подписантов недоступен.",
    )
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError:
        _fail_without_context("SNAPSHOT_UNTRUSTED", "Файл доверенных подписантов некорректен.")
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 3 and _SIGNER_IDENTITY in fields[0].split(","):
            key_type = next(
                (
                    field
                    for field in fields[1:]
                    if field.startswith(("ssh-", "sk-", "ecdsa-", "rsa-"))
                ),
                None,
            )
            if key_type == "ssh-ed25519":
                return
    _fail("SNAPSHOT_UNTRUSTED", "Для snapshot не настроен доверенный Ed25519 signer.")


def _read_regular_file(path: Path, code: str, message: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        _fail(code, message, exc)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(code, message)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev != before.st_dev or opened.st_ino != before.st_ino
            ):
                _fail(code, message)
            payload = stream.read()
            after = os.fstat(stream.fileno())
    except SnapshotTrustError:
        raise
    except OSError as exc:
        _fail(code, message, exc)
    if (
        opened.st_dev != after.st_dev
        or opened.st_ino != after.st_ino
        or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
        or len(payload) != after.st_size
    ):
        _fail(code, message)
    return payload


def _safe_manifest_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value == "."
        or "\\" in value
        or "\x00" in value
    ):
        _fail("SNAPSHOT_MANIFEST_INVALID", "Path файла в manifest некорректен.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail("SNAPSHOT_MANIFEST_INVALID", "Path файла выходит за пределы snapshot.")
    normalized = path.as_posix()
    if normalized != value or normalized in _EXCLUDED_ROOT_FILES:
        _fail("SNAPSHOT_MANIFEST_INVALID", "Path файла в manifest неканонический.")
    return normalized


def _safe_requested_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value == "."
        or "\\" in value
        or "\x00" in value
    ):
        _fail("SNAPSHOT_INTEGRITY_FAILED", "Запрошенный path snapshot некорректен.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail("SNAPSHOT_INTEGRITY_FAILED", "Запрошенный path выходит за snapshot.")
    normalized = path.as_posix()
    if normalized != value or normalized in _EXCLUDED_ROOT_FILES:
        _fail("SNAPSHOT_INTEGRITY_FAILED", "Запрошенный path snapshot неканонический.")
    return normalized


def _required_identifier(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        _fail("SNAPSHOT_MANIFEST_INVALID", f"Поле {field} manifest некорректно.")
    return value


def _require_exact_fields(
    value: Mapping[str, Any], allowed: set[str], location: str
) -> None:
    if set(value) != allowed:
        _fail("SNAPSHOT_MANIFEST_INVALID", f"Поля {location} не соответствуют schema.")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _fail(
    code: str, message_ru: str, cause: BaseException | None = None
) -> NoReturn:
    error = SnapshotTrustError(code, message_ru)
    if cause is None:
        raise error
    raise error from cause


def _fail_without_context(code: str, message_ru: str) -> NoReturn:
    raise SnapshotTrustError(code, message_ru) from None
