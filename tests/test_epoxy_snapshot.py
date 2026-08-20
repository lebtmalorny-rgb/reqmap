import hashlib
import json
import shutil
from pathlib import Path
from email.message import Message
from urllib.error import HTTPError

import pytest

from reqmap.knowledge import SourceRecord, load_knowledge
from reqmap.models import EvidencePolarity, EvidenceStrength
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

    assert len(REQUIRED_COMPONENTS) == 30
    assert set(kb.components) == REQUIRED_COMPONENTS
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


def test_chrony_official_evidence_is_a_negative_specific_support_boundary() -> None:
    kb = load_knowledge(SNAPSHOT)

    official = [
        evidence
        for evidence in kb.evidence.values()
        if evidence.component_id == "host_os_chrony" and evidence.provenance == "official"
    ]
    policy = [
        evidence
        for evidence in kb.evidence.values()
        if evidence.component_id == "host_os_chrony" and evidence.provenance == "project_policy"
    ]

    assert official
    assert all(evidence.polarity is EvidencePolarity.NEGATIVE for evidence in official)
    assert policy and all(evidence.polarity is EvidencePolarity.POSITIVE for evidence in policy)
    capability = kb.capabilities["CAP-HOST-CHRONY-AUTOMATION"]
    assert capability.name_ru == "Граница Chrony-specific автоматизации хоста"
    assert capability.terms == ("chrony", "chrony-specific role", "граница автоматизации chrony")


def test_networking_capability_is_limited_to_etc_hosts_and_sysctl_has_own_source() -> None:
    kb = load_knowledge(SNAPSHOT)

    capability = kb.capabilities["CAP-HOST-NETWORKING-AUTOMATION"]
    assert capability.name_ru == "Автоматизация записей /etc/hosts"
    assert capability.terms == ("/etc/hosts", "hostname resolution", "host entries", "имена хостов")
    networking = kb.evidence["E-HOST-NETWORKING-OFFICIAL-001"]
    assert "/etc/hosts" in networking.claim_ru
    assert "network interface" not in networking.claim_ru.lower()
    bootstrap = kb.sources["SRC-KOLLA-BOOTSTRAP-2025.1"]
    assert "host_os_kernel_sysctl" not in bootstrap.component_ids
    sysctl = kb.sources["SRC-KOLLA-SYSCTL-ROLE-2025.1"]
    assert {"host_os_kernel_sysctl", "kolla_ansible"} <= set(sysctl.component_ids)


def test_host_policy_excerpt_points_to_host_os_design_section() -> None:
    excerpt = (SNAPSHOT / "sources" / "SRC-POLICY-HOST-OS-KOLLA.md").read_text(encoding="utf-8")

    assert "разделы 1, 8.3 и 13" in excerpt
    assert "разделы 1, 8.4 и 13" not in excerpt


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


@pytest.mark.parametrize("invalid_constant", ["NaN", "Infinity", "-Infinity"])
def test_snapshot_builder_rejects_non_finite_manifest_without_modifying_it(
    tmp_path: Path, invalid_constant: str
) -> None:
    copied = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, copied)
    manifest_path = copied / "source-manifest.json"
    original = manifest_path.read_text(encoding="utf-8")
    corrupted = original.replace('"version": "2025.1"', f'"version": {invalid_constant}', 1)
    manifest_path.write_text(corrupted, encoding="utf-8")
    before = manifest_path.read_bytes()

    with pytest.raises(SystemExit, match="Не удалось прочитать JSON"):
        build_snapshot(copied)

    assert manifest_path.read_bytes() == before


def test_snapshot_builder_rejects_duplicate_manifest_keys_without_modifying_it(tmp_path: Path) -> None:
    copied = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, copied)
    manifest_path = copied / "source-manifest.json"
    original = manifest_path.read_text(encoding="utf-8")
    corrupted = original.replace('"sources": [', '"sources": [],\n  "sources": [', 1)
    manifest_path.write_text(corrupted, encoding="utf-8")
    before = manifest_path.read_bytes()

    with pytest.raises(SystemExit, match="Не удалось прочитать JSON"):
        build_snapshot(copied)

    assert manifest_path.read_bytes() == before


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


def _patch_transport(monkeypatch: pytest.MonkeyPatch, implementation: object) -> None:
    target = "_open_no_redirect" if hasattr(verify_module, "_open_no_redirect") else "urlopen"
    monkeypatch.setattr(verify_module, target, implementation)


def test_verify_url_uses_head_without_get_when_server_accepts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[verify_module.Request] = []

    def fake_urlopen(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        assert timeout == 2.5
        return _Response(200)

    _patch_transport(monkeypatch, fake_urlopen)

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

    _patch_transport(monkeypatch, fake_urlopen)

    result = verify_module.verify_url(_source(), 1.0)

    assert result.status == "passed"
    assert [request.get_method() for request in requests] == ["HEAD", "GET"]
    assert requests[1].get_header("Range") == "bytes=0-0"


def _redirect(request: verify_module.Request, location: str) -> HTTPError:
    headers = Message()
    headers["Location"] = location
    return HTTPError(request.full_url, 302, "found", headers, None)


def test_verify_url_follows_same_allowlisted_host_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[verify_module.Request] = []

    def fake_open(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        if len(requests) == 1:
            raise _redirect(request, "/nova/2025.1/index.html")
        return _Response(200)

    _patch_transport(monkeypatch, fake_open)

    result = verify_module.verify_url(_source(), 1.0)

    assert result.status == "passed"
    assert [request.full_url for request in requests] == [
        "https://docs.openstack.org/nova/2025.1/",
        "https://docs.openstack.org/nova/2025.1/index.html",
    ]


def test_verify_url_rejects_head_redirect_before_disallowed_host_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[verify_module.Request] = []

    def fake_open(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        raise _redirect(request, "https://example.com/capture")

    _patch_transport(monkeypatch, fake_open)

    result = verify_module.verify_url(_source(), 1.0)

    assert result.status == "failed"
    assert "redirect" in result.message_ru.lower()
    assert [request.full_url for request in requests] == ["https://docs.openstack.org/nova/2025.1/"]


def test_verify_url_get_fallback_follows_same_allowlisted_host_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[verify_module.Request] = []

    def fake_open(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 405, "not allowed", {}, None)
        if len(requests) == 2:
            raise _redirect(request, "/nova/2025.1/index.html")
        return _Response(206)

    _patch_transport(monkeypatch, fake_open)

    result = verify_module.verify_url(_source(), 1.0)

    assert result.status == "passed"
    assert [(request.get_method(), request.full_url) for request in requests] == [
        ("HEAD", "https://docs.openstack.org/nova/2025.1/"),
        ("GET", "https://docs.openstack.org/nova/2025.1/"),
        ("GET", "https://docs.openstack.org/nova/2025.1/index.html"),
    ]
    assert requests[2].get_header("Range") == "bytes=0-0"


def test_verify_url_rejects_get_fallback_redirect_before_disallowed_host_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[verify_module.Request] = []

    def fake_open(request: verify_module.Request, timeout: float) -> _Response:
        requests.append(request)
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 405, "not allowed", {}, None)
        raise _redirect(request, "https://example.com/capture")

    _patch_transport(monkeypatch, fake_open)

    result = verify_module.verify_url(_source(), 1.0)

    assert result.status == "failed"
    assert "redirect" in result.message_ru.lower()
    assert [(request.get_method(), request.full_url) for request in requests] == [
        ("HEAD", "https://docs.openstack.org/nova/2025.1/"),
        ("GET", "https://docs.openstack.org/nova/2025.1/"),
    ]


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
    _patch_transport(monkeypatch, lambda *args, **kwargs: pytest.fail("network called"))

    result = verify_module.verify_url(source, 1.0)

    assert result.status == status
    assert message in result.message_ru
