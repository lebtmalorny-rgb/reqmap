"""Воспроизводимая сборка локального snapshot базы знаний."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from reqmap.knowledge import load_knowledge, snapshot_digest, validate_knowledge


SNAPSHOT_FILES = (
    "components.json",
    "capabilities.jsonl",
    "evidence.jsonl",
    "synonyms.json",
    "source-manifest.json",
)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Не удалось прочитать JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Ожидался JSON-объект: {path}")
    return value


def _atomic_json_write(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def populate_source_hashes(manifest_path: Path, root: Path) -> None:
    """Атомарно записать SHA-256 существующих локальных excerpt-файлов."""
    manifest = _read_json_object(manifest_path)
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise SystemExit("source-manifest.json.sources должен быть списком")
    normalized: list[object] = []
    for index, raw_source in enumerate(sources):
        if not isinstance(raw_source, dict):
            raise SystemExit(f"Некорректная запись источника: sources[{index}]")
        local_path = raw_source.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            raise SystemExit(f"Не задан local_path: sources[{index}]")
        candidate = Path(local_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SystemExit(f"local_path выходит за пределы snapshot: {local_path}")
        excerpt_path = root / candidate
        if not excerpt_path.is_file():
            raise SystemExit(f"Локальный excerpt отсутствует: {local_path}")
        try:
            excerpt_bytes = excerpt_path.read_bytes()
        except OSError as exc:
            raise SystemExit(f"Не удалось прочитать локальный excerpt: {local_path}") from exc
        source = dict(raw_source)
        source["sha256"] = hashlib.sha256(excerpt_bytes).hexdigest()
        normalized.append(source)
    manifest["sources"] = normalized
    _atomic_json_write(manifest_path, manifest)


def update_metadata_hash(metadata_path: Path, digest: str) -> None:
    metadata = _read_json_object(metadata_path)
    metadata["snapshot_sha256"] = digest
    _atomic_json_write(metadata_path, metadata)


def build_snapshot(root: Path) -> str:
    """Проверить snapshot, обновить source hashes и вернуть aggregate SHA-256."""
    root = root.resolve()
    populate_source_hashes(root / "source-manifest.json", root)
    kb = load_knowledge(root, verify_snapshot_hash=False)
    issues = validate_knowledge(kb)
    if issues:
        raise SystemExit("\n".join(f"{item.code}: {item.message_ru}" for item in issues))
    digest = snapshot_digest(root, names=SNAPSHOT_FILES)
    update_metadata_hash(root / "metadata.json", digest)
    return digest


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("Использование: build_snapshot.py КАТАЛОГ_SNAPSHOT", file=sys.stderr)
        return 2
    print(build_snapshot(Path(arguments[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
