import json
import shutil
from pathlib import Path

import pytest

from reqmap import knowledge as knowledge_module
from reqmap.knowledge import KnowledgeError, load_knowledge, validate_knowledge


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    """Give each corruption case an isolated complete knowledge snapshot."""
    source = Path(__file__).parent / "fixtures" / "kb_minimal"
    destination = tmp_path / "kb"
    shutil.copytree(source, destination)
    return destination


def _replace_json_field(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _replace_manifest_source_field(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sources"][0][field] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _replace_evidence_field(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    data[field] = value
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def test_load_knowledge_returns_valid_immutable_evidence_graph(kb_path: Path) -> None:
    """Dropping provenance, references, or read-only mappings would invalidate runtime input."""
    kb = load_knowledge(kb_path)

    assert validate_knowledge(kb) == ()
    assert kb.release == "2025.1"
    assert kb.components["nova"].display_name == "Nova"
    assert kb.evidence["E-NOVA-API-001"].source_url == "https://docs.openstack.org/api-ref/compute/"
    assert kb.synonyms["сервер"] == ("виртуальная машина", "instance")
    with pytest.raises(TypeError):
        kb.components["other"] = kb.components["nova"]  # type: ignore[index]


def test_load_knowledge_rejects_wrong_release(kb_path: Path) -> None:
    """Acceptance of another OpenStack cycle would mix incompatible evidence."""
    _replace_json_field(kb_path / "metadata.json", "openstack_release", "2024.2")

    with pytest.raises(KnowledgeError, match="ожидается Epoxy 2025.1"):
        load_knowledge(kb_path, verify_snapshot_hash=False)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("component_id", "unknown", "неизвестный компонент"),
        ("capability_id", "CAP-UNKNOWN", "неизвестную capability"),
        ("source_id", "SRC-UNKNOWN", "неизвестный источник"),
    ],
)
def test_load_knowledge_rejects_unknown_evidence_reference(
    kb_path: Path, field: str, value: str, message: str
) -> None:
    """A dangling evidence reference must not become a plausible mapping candidate."""
    _replace_evidence_field(kb_path / "evidence.jsonl", field, value)

    with pytest.raises(KnowledgeError, match=message):
        load_knowledge(kb_path, verify_snapshot_hash=False)


def test_load_knowledge_rejects_duplicate_component_id(kb_path: Path) -> None:
    """Keeping the last duplicate record would make the resulting evidence non-auditable."""
    path = kb_path / "components.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["components"].append(data["components"][0].copy())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(KnowledgeError, match="дублирующийся ID"):
        load_knowledge(kb_path, verify_snapshot_hash=False)


def test_load_knowledge_rejects_empty_evidence_claim(kb_path: Path) -> None:
    """An empty claim cannot substantiate a support conclusion."""
    _replace_evidence_field(kb_path / "evidence.jsonl", "claim_ru", "")

    with pytest.raises(KnowledgeError, match="пустое утверждение"):
        load_knowledge(kb_path, verify_snapshot_hash=False)


def test_load_knowledge_rejects_whitespace_only_evidence_claim(kb_path: Path) -> None:
    """Whitespace is not an auditable evidence claim and must not be normalized into one."""
    _replace_evidence_field(kb_path / "evidence.jsonl", "claim_ru", " \t\n")

    with pytest.raises(KnowledgeError, match="пустое утверждение"):
        load_knowledge(kb_path, verify_snapshot_hash=False)


def test_load_knowledge_rejects_missing_local_source(kb_path: Path) -> None:
    """A manifest without local bytes breaks offline evidence auditability."""
    _replace_manifest_source_field(kb_path / "source-manifest.json", "local_path", "sources/missing.md")

    with pytest.raises(KnowledgeError, match="(?i)локальный источник отсутствует"):
        load_knowledge(kb_path, verify_snapshot_hash=False)


def test_load_knowledge_rejects_mismatched_local_source_hash(kb_path: Path) -> None:
    """Changed local evidence must be noticed even during snapshot build mode."""
    (kb_path / "sources" / "nova-api.md").write_text("подменено\n", encoding="utf-8")

    with pytest.raises(KnowledgeError, match="SHA-256 локального источника"):
        load_knowledge(kb_path, verify_snapshot_hash=False)


def test_load_knowledge_wraps_invalid_source_path_as_domain_error(kb_path: Path) -> None:
    """An embedded NUL must not leak a raw Path resolution exception to callers."""
    _replace_manifest_source_field(kb_path / "source-manifest.json", "local_path", "sources/\x00bad.md")

    with pytest.raises(KnowledgeError) as error:
        load_knowledge(kb_path, verify_snapshot_hash=False)

    assert error.value.code == "SOURCE_PATH"


def test_load_knowledge_wraps_source_read_oserror_as_domain_error(
    kb_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem read failure must be a diagnostics result, never a leaked OSError."""
    original_read_bytes = Path.read_bytes

    def fail_source_read(path: Path) -> bytes:
        if path.name == "nova-api.md":
            raise OSError("synthetic source read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(knowledge_module.Path, "read_bytes", fail_source_read)

    with pytest.raises(KnowledgeError) as error:
        load_knowledge(kb_path, verify_snapshot_hash=False)

    assert error.value.code == "SOURCE_FILE_READ"


def test_load_knowledge_rejects_snapshot_hash_at_runtime(kb_path: Path) -> None:
    """Runtime must reject a metadata digest that no longer represents the snapshot."""
    _replace_json_field(kb_path / "metadata.json", "snapshot_sha256", "0" * 64)

    with pytest.raises(KnowledgeError, match="SHA-256 snapshot"):
        load_knowledge(kb_path)


def test_build_mode_skips_only_aggregate_snapshot_hash(kb_path: Path) -> None:
    """Build mode may repair metadata but must still load the checked local records."""
    _replace_json_field(kb_path / "metadata.json", "snapshot_sha256", "0" * 64)

    assert load_knowledge(kb_path, verify_snapshot_hash=False).release == "2025.1"


def test_official_source_requires_url_and_policy_allows_null_url(kb_path: Path) -> None:
    """Official provenance needs an origin while local approved policy remains offline-only."""
    _replace_manifest_source_field(kb_path / "source-manifest.json", "source_url", None)

    with pytest.raises(KnowledgeError, match="official.*source_url"):
        load_knowledge(kb_path, verify_snapshot_hash=False)

    _replace_manifest_source_field(kb_path / "source-manifest.json", "provenance", "project_policy")
    assert load_knowledge(kb_path, verify_snapshot_hash=False).sources["SRC-NOVA-API"].source_url is None


def test_project_policy_rejects_official_url(kb_path: Path) -> None:
    """A policy source with an upstream URL has ambiguous provenance and must fail closed."""
    _replace_manifest_source_field(kb_path / "source-manifest.json", "provenance", "project_policy")

    with pytest.raises(KnowledgeError) as error:
        load_knowledge(kb_path, verify_snapshot_hash=False)

    assert error.value.code == "SOURCE_URL"


def test_evidence_component_must_belong_to_its_source(kb_path: Path) -> None:
    """Evidence cannot use a source excerpt that was recorded for another component."""
    components_path = kb_path / "components.json"
    components = json.loads(components_path.read_text(encoding="utf-8"))
    components["components"].append(
        {
            "id": "glance",
            "display_name": "Glance",
            "kind": "openstack_service",
            "release": "2025.1",
        }
    )
    components_path.write_text(
        json.dumps(components, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _replace_manifest_source_field(kb_path / "source-manifest.json", "component_ids", ["glance"])

    with pytest.raises(KnowledgeError) as error:
        load_knowledge(kb_path, verify_snapshot_hash=False)

    assert error.value.code == "EVIDENCE_SOURCE_COMPONENT_MISMATCH"


@pytest.mark.parametrize(
    ("path_name", "payload", "message"),
    [
        ("metadata.json", '{"openstack_release":"2025.1","snapshot_sha256":"' + "0" * 64 + '","extra":true}', "Неизвестные поля"),
        ("components.json", '{"components":[],"extra":true}', "Неизвестные поля"),
        ("capabilities.jsonl", '{"id":"CAP-NOVA-SERVER-API","component_id":"nova","name_ru":"API","terms":["api"],"extra":true}\n', "Неизвестные поля"),
        ("source-manifest.json", '{"sources":[],"extra":true}', "Неизвестные поля"),
        ("evidence.jsonl", '{"id":"E-NOVA-API-001","component_id":"nova","capability_id":"CAP-NOVA-SERVER-API","polarity":"positive","strength":"direct","claim_ru":"claim","source_id":"SRC-NOVA-API","locator":"section","version_constraint":"2025.1","extra":true}\n', "Неизвестные поля"),
    ],
)
def test_load_knowledge_rejects_unknown_schema_fields(
    kb_path: Path, path_name: str, payload: str, message: str
) -> None:
    """Typos and unreviewed schema extensions must not silently alter a runtime snapshot."""
    (kb_path / path_name).write_text(payload, encoding="utf-8")

    with pytest.raises(KnowledgeError, match=message):
        load_knowledge(kb_path, verify_snapshot_hash=False)


@pytest.mark.parametrize(
    ("path_name", "payload", "code"),
    [
        ("metadata.json", '{"openstack_release":"2025.1","openstack_release":"2025.1","snapshot_sha256":"' + "0" * 64 + '"}', "KNOWLEDGE_JSON"),
        ("evidence.jsonl", '{"id":"E-NOVA-API-001","id":"E-NOVA-API-001"}\n', "KNOWLEDGE_JSONL"),
    ],
)
def test_load_knowledge_rejects_duplicate_json_object_keys(
    kb_path: Path, path_name: str, payload: str, code: str
) -> None:
    """JSON duplicate keys would otherwise overwrite auditable data before validation."""
    (kb_path / path_name).write_text(payload, encoding="utf-8")

    with pytest.raises(KnowledgeError) as error:
        load_knowledge(kb_path, verify_snapshot_hash=False)

    assert error.value.code == code
