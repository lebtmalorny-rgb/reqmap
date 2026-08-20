import hashlib
import json
import shutil
from pathlib import Path
from urllib.error import HTTPError

import pytest

from reqmap.knowledge import SourceRecord, load_knowledge
from reqmap.models import EvidenceStrength
from tools.kb.build_snapshot import build_snapshot
from tools.kb import verify_urls as verify_module


SNAPSHOT = Path("knowledge/epoxy-2025.1")
REQUIRED_COMPONENTS = {
    "keystone", "nova", "placement", "neutron", "glance", "cinder", "horizon",
    "heat", "octavia", "barbican", "manila", "swift", "designate", "ironic",
    "magnum", "trove", "watcher", "ceilometer", "aodh", "gnocchi", "cloudkitty",
    "masakari", "kolla_ansible", "host_os_chrony", "host_os_nftables",
    "host_os_networking", "host_os_kernel_sysctl", "host_os_package_management",
    "host_os_storage", "host_os_identity_access",
}
HOST_COMPONENTS = {item for item in REQUIRED_COMPONENTS if item.startswith("host_os_")}
ALLOWED_HOSTS = {"docs.openstack.org", "releases.openstack.org", "opendev.org"}


def test_epoxy_snapshot_has_direct_scope_evidence_for_every_component() -> None:
    kb = load_knowledge(SNAPSHOT)

    assert REQUIRED_COMPONENTS <= set(kb.components)
    evidenced = {
        item.component_id
        for item in kb.evidence.values()
        if item.strength is EvidenceStrength.DIRECT
    }
    assert REQUIRED_COMPONENTS <= evidenced


def test_host_os_evidence_keeps_policy_separate_from_official_automation() -> None:
    kb = load_knowledge(SNAPSHOT)

    assert all(kb.components[item].kind == "host_os_subsystem" for item in HOST_COMPONENTS)
    policy_sources = [source for source in kb.sources.values() if source.provenance == "project_policy"]
    assert len(policy_sources) == 1
    assert policy_sources[0].source_url is None
    for component_id in HOST_COMPONENTS:
        official_direct = [
            evidence
            for evidence in kb.evidence.values()
            if evidence.component_id == component_id
            and evidence.strength is EvidenceStrength.DIRECT
            and evidence.provenance == "official"
        ]
        assert official_direct
        for evidence in official_direct:
            source = kb.sources[evidence.source_id]
            assert {component_id, "kolla_ansible"} <= set(source.component_ids)


def test_official_sources_use_only_allowed_https_hosts() -> None:
    kb = load_knowledge(SNAPSHOT)

    for source in kb.sources.values():
        if source.provenance == "official":
            assert source.source_url is not None
            parsed = verify_module.urlparse(source.source_url)
            assert parsed.scheme == "https"
            assert parsed.hostname in ALLOWED_HOSTS


def test_snapshot_builder_is_reproducible_and_strict_loader_accepts_result(tmp_path: Path) -> None:
    copied = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, copied)

    first = build_snapshot(copied)
    first_bytes = {path.relative_to(copied): path.read_bytes() for path in copied.rglob("*") if path.is_file()}
    second = build_snapshot(copied)
    second_bytes = {path.relative_to(copied): path.read_bytes() for path in copied.rglob("*") if path.is_file()}

    assert first == second
    assert first_bytes == second_bytes
    assert load_knowledge(copied).snapshot_sha256 == first


def test_snapshot_builder_refuses_missing_excerpt(tmp_path: Path) -> None:
    copied = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, copied)
    manifest_path = copied / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing_path = copied / manifest["sources"][0]["local_path"]
    missing_path.unlink()

    with pytest.raises(SystemExit, match="Локальный excerpt отсутствует"):
        build_snapshot(copied)


def _source(*, provenance: str = "official", url: str | None = "https://docs.openstack.org/nova/2025.1/") -> SourceRecord:
    return SourceRecord(
        source_id="SRC-TEST",
        component_ids=("nova",),
        source_url=url,
        retrieved_at="2026-08-20",
        version="2025.1",
        sha256=hashlib.sha256(b"test").hexdigest(),
        local_path="sources/test.md",
        provenance=provenance,
    )


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_verify_url_uses_head_without_get_when_server_accepts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[verify_module.Request] = []

    def fake_urlopen(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        assert timeout == 2.5
        return _Response(200)

    monkeypatch.setattr(verify_module, "urlopen", fake_urlopen)

    result = verify_module.verify_url(_source(), 2.5)

    assert result.status == "passed"
    assert [request.get_method() for request in requests] == ["HEAD"]


def test_verify_url_falls_back_to_ranged_get_only_for_head_405(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[verify_module.Request] = []

    def fake_urlopen(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 405, "not allowed", {}, None)
        return _Response(206)

    monkeypatch.setattr(verify_module, "urlopen", fake_urlopen)

    result = verify_module.verify_url(_source(), 1.0)

    assert result.status == "passed"
    assert [request.get_method() for request in requests] == ["HEAD", "GET"]
    assert requests[1].get_header("Range") == "bytes=0-0"


@pytest.mark.parametrize(
    ("source", "status", "message"),
    [
        (_source(provenance="project_policy", url=None), "skipped", "Локальное проектное правило"),
        (_source(url="http://docs.openstack.org/nova/2025.1/"), "failed", "разрешённому"),
        (_source(url="https://example.com/nova"), "failed", "разрешённому"),
    ],
)
def test_verify_url_skips_policy_and_rejects_non_official_hosts(
    source: SourceRecord, status: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify_module, "urlopen", lambda *args, **kwargs: pytest.fail("network called"))

    result = verify_module.verify_url(source, 1.0)

    assert result.status == status
    assert message in result.message_ru
