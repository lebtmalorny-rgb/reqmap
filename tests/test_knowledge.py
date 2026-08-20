import json
import shutil
from pathlib import Path

import pytest

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
