"""Rebuild a reviewed legacy binding manifest without changing annotations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile

from reqmap.agent_input import read_regular_bytes
from reqmap.binding_catalog import load_binding_catalog
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.knowledge import load_knowledge
from reqmap.output_safety import strict_json_object, symlink_component


def build_binding_catalog(path: Path, knowledge_path: Path) -> str:
    """Validate a staged catalog before atomically replacing its manifest."""
    path = path.absolute()
    if symlink_component(path) or not path.is_dir():
        raise ReqmapError("BINDING_IO", "Каталог отсутствует или содержит symlink.")
    knowledge = load_knowledge(knowledge_path)
    manifest_path = path / "binding-manifest.json"
    predicate_path = path / "predicates.jsonl"
    original = read_regular_bytes(manifest_path, 1024 * 1024)
    predicates = read_regular_bytes(predicate_path, 25 * 1024 * 1024)
    try:
        manifest = strict_json_object(original.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ReqmapError("BINDING_SCHEMA", "Требуется строгий JSON manifest.") from exc
    manifest["knowledge_sha256"] = knowledge.snapshot_sha256
    manifest["files"] = [{"path": "predicates.jsonl", "size": len(predicates),
                          "sha256": hashlib.sha256(predicates).hexdigest()}]
    with tempfile.TemporaryDirectory(prefix=".binding-", dir=path.parent) as temporary:
        staged = Path(temporary)
        (staged / "predicates.jsonl").write_bytes(predicates)
        staged_manifest = staged / "binding-manifest.json"
        staged_manifest.write_bytes(canonical_json_bytes(manifest))
        catalog = load_binding_catalog(staged, knowledge)
        if (read_regular_bytes(manifest_path, 1024 * 1024) != original
                or read_regular_bytes(predicate_path, 25 * 1024 * 1024) != predicates):
            raise ReqmapError("BINDING_INTEGRITY", "Каталог изменился во время сборки.")
        os.replace(staged_manifest, manifest_path)
    return catalog.catalog_sha256


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        print("Использование: build_binding_catalog.py КАТАЛОГ КАТАЛОГ_KB", file=sys.stderr)
        return 2
    try:
        print(build_binding_catalog(Path(arguments[0]), Path(arguments[1])))
    except (ReqmapError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
