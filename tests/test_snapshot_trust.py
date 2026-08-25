"""Fail-closed trust checks for signed schema-v2 knowledge snapshots."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from reqmap.export_json import canonical_json_bytes
from reqmap import snapshot_trust as snapshot_trust_module
from reqmap.snapshot_trust import (
    SnapshotFile,
    SnapshotTrust,
    SnapshotTrustError,
    build_snapshot_manifest,
    parse_snapshot_manifest,
    read_verified_snapshot_file,
    verify_snapshot,
    verify_snapshot_integrity,
    verify_snapshot_signature,
)
from tests.deep_factories import signed_v2_snapshot


def _write_manifest(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "snapshot-manifest.json").read_text(encoding="utf-8"))


def test_verify_snapshot_accepts_ephemeral_ed25519_signature(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)

    trust = verify_snapshot(root, allowed_signers)

    assert trust.signer_identity == "reqmap-snapshot"
    assert trust.snapshot_id == "epoxy-2025.1-deep-001"
    assert trust.key_id == "reqmap-maintenance-2026"
    assert len(trust.manifest_sha256) == 64
    assert tuple(item.path for item in trust.files) == tuple(
        sorted(item.path for item in trust.files)
    )


def test_read_verified_snapshot_file_returns_exact_signed_bytes(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    trust = verify_snapshot(root, allowed_signers)

    payload = read_verified_snapshot_file(root, trust, "components.json")

    assert payload == (root / "components.json").read_bytes()


@pytest.mark.parametrize(
    "relative_path",
    ["missing.json", "../components.json", "/components.json", "a\\b.json", "."],
)
def test_read_verified_snapshot_file_rejects_unlisted_or_unsafe_path(
    tmp_path: Path, relative_path: str
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    trust = verify_snapshot(root, allowed_signers)

    with pytest.raises(SnapshotTrustError) as error:
        read_verified_snapshot_file(root, trust, relative_path)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_read_verified_snapshot_file_rejects_swap_after_verification(
    tmp_path: Path,
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    trust = verify_snapshot(root, allowed_signers)
    components = root / "components.json"
    components.write_bytes(b"X" * components.stat().st_size)

    with pytest.raises(SnapshotTrustError) as error:
        read_verified_snapshot_file(root, trust, "components.json")

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_snapshot_results_are_immutable() -> None:
    snapshot_file = SnapshotFile("components.json", 3, "a" * 64)
    trust = SnapshotTrust("snapshot", "key", "reqmap-snapshot", "b" * 64, (snapshot_file,))

    with pytest.raises(FrozenInstanceError):
        snapshot_file.size = 4  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        trust.snapshot_id = "changed"  # type: ignore[misc]


def test_verify_snapshot_rejects_tampered_file(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    (root / "components.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_verify_snapshot_rejects_signer_file_inside_snapshot(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)

    with pytest.raises(SnapshotTrustError, match="вне snapshot") as error:
        verify_snapshot(root, root / "allowed_signers")

    assert error.value.code == "SNAPSHOT_TRUST_BOUNDARY"


@pytest.mark.parametrize(
    ("signer_identity", "signature_namespace"),
    [("unexpected", "reqmap-snapshot"), ("reqmap-snapshot", "unexpected")],
)
def test_verify_snapshot_rejects_wrong_identity_or_namespace(
    tmp_path: Path, signer_identity: str, signature_namespace: str
) -> None:
    root, allowed_signers = signed_v2_snapshot(
        tmp_path,
        signer_identity=signer_identity,
        signature_namespace=signature_namespace,
    )

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_UNTRUSTED"


def test_verify_snapshot_rejects_signature_mismatch(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    signature = root / "snapshot-manifest.sig"
    signature.write_bytes(signature.read_bytes().replace(b"A", b"B", 1))

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_UNTRUSTED"


def test_build_manifest_uses_one_sorted_governed_file_set(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    (root / "nested").mkdir(parents=True)
    (root / "indexes").mkdir()
    (root / "z.jsonl").write_bytes(b"{}\n")
    (root / "nested" / "a.md").write_bytes(b"A")
    (root / "indexes" / "vectors.bin").write_bytes(b"IDX")
    (root / "metadata.json").write_bytes(canonical_json_bytes({"snapshot_id": "fixture-snapshot"}))
    (root / "ignored.txt").write_bytes(b"ignored")
    (root / "snapshot-manifest.json").write_bytes(b"ignored")
    (root / "snapshot-manifest.sig").write_bytes(b"ignored")

    manifest = build_snapshot_manifest(root)

    assert manifest == {
        "manifest_schema_version": "1.0",
        "knowledge_schema_version": 2,
        "snapshot_id": "fixture-snapshot",
        "key_id": "reqmap-maintenance-2026",
        "files": [
            {
                "path": "indexes/vectors.bin",
                "size": 3,
                "sha256": hashlib.sha256(b"IDX").hexdigest(),
            },
            {
                "path": "metadata.json",
                "size": len(canonical_json_bytes({"snapshot_id": "fixture-snapshot"})),
                "sha256": hashlib.sha256(canonical_json_bytes({"snapshot_id": "fixture-snapshot"})).hexdigest(),
            },
            {
                "path": "nested/a.md",
                "size": 1,
                "sha256": hashlib.sha256(b"A").hexdigest(),
            },
            {
                "path": "z.jsonl",
                "size": 3,
                "sha256": hashlib.sha256(b"{}\n").hexdigest(),
            },
        ],
    }


def test_build_manifest_uses_the_canonical_metadata_snapshot_id(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    metadata = root / "metadata.json"
    metadata.write_bytes(canonical_json_bytes({"snapshot_id": "draft-first"}))

    first = build_snapshot_manifest(root)
    metadata.write_bytes(canonical_json_bytes({"snapshot_id": "draft-second"}))
    second = build_snapshot_manifest(root)

    assert first["snapshot_id"] == "draft-first"
    assert second["snapshot_id"] == "draft-second"


@pytest.mark.parametrize(
    "payload",
    [b'{"snapshot_id":"one","snapshot_id":"two"}\n', b'{"snapshot_id":true}\n'],
)
def test_build_manifest_rejects_duplicate_or_invalid_metadata_id(
    tmp_path: Path, payload: bytes
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "metadata.json").write_bytes(payload)

    with pytest.raises(SnapshotTrustError):
        build_snapshot_manifest(root)


def test_build_manifest_rejects_snapshot_walk_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()

    def failing_walk(path, *, topdown, followlinks, onerror=None):
        if onerror is not None:
            onerror(PermissionError("hidden governed subtree"))
        return iter(())

    monkeypatch.setattr(snapshot_trust_module.os, "walk", failing_walk)

    with pytest.raises(SnapshotTrustError) as error:
        build_snapshot_manifest(root)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {**value, "unexpected": True},
        lambda value: {**value, "manifest_schema_version": "2.0"},
        lambda value: {**value, "knowledge_schema_version": 1},
        lambda value: {**value, "snapshot_id": ""},
        lambda value: {**value, "key_id": ""},
        lambda value: {**value, "key_id": "unexpected-maintenance-key"},
        lambda value: {**value, "files": "components.json"},
        lambda value: {
            **value,
            "files": [{**value["files"][0], "unexpected": True}],
        },
        lambda value: {
            **value,
            "files": [{**value["files"][0], "sha256": "A" * 64}],
        },
        lambda value: {
            **value,
            "files": [{**value["files"][0], "size": True}],
        },
    ],
)
def test_parse_manifest_rejects_unknown_or_malformed_fields(
    tmp_path: Path, mutation
) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    manifest_path = root / "snapshot-manifest.json"
    _write_manifest(manifest_path, mutation(_manifest(root)))

    with pytest.raises(SnapshotTrustError) as error:
        parse_snapshot_manifest(manifest_path)

    assert error.value.code == "SNAPSHOT_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "unsafe_path",
    ["/absolute.json", "../outside.json", "a/../b.json", "a\\b.json", "."],
)
def test_parse_manifest_rejects_unsafe_paths(tmp_path: Path, unsafe_path: str) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    manifest = _manifest(root)
    manifest["files"][0]["path"] = unsafe_path
    manifest_path = root / "snapshot-manifest.json"
    _write_manifest(manifest_path, manifest)

    with pytest.raises(SnapshotTrustError) as error:
        parse_snapshot_manifest(manifest_path)

    assert error.value.code == "SNAPSHOT_MANIFEST_INVALID"


def test_parse_manifest_rejects_duplicate_paths(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    manifest = _manifest(root)
    manifest["files"].append(dict(manifest["files"][0]))
    manifest_path = root / "snapshot-manifest.json"
    _write_manifest(manifest_path, manifest)

    with pytest.raises(SnapshotTrustError) as error:
        parse_snapshot_manifest(manifest_path)

    assert error.value.code == "SNAPSHOT_MANIFEST_INVALID"


def test_parse_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    manifest_path = root / "snapshot-manifest.json"
    source = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        source.replace('{"files":', '{"key_id":"duplicate","files":', 1),
        encoding="utf-8",
    )

    with pytest.raises(SnapshotTrustError) as error:
        parse_snapshot_manifest(manifest_path)

    assert error.value.code == "SNAPSHOT_MANIFEST_INVALID"


def test_parse_manifest_rejects_noncanonical_bytes(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    manifest_path = root / "snapshot-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(root), indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SnapshotTrustError) as error:
        parse_snapshot_manifest(manifest_path)

    assert error.value.code == "SNAPSHOT_MANIFEST_INVALID"


def test_integrity_rejects_missing_listed_file(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    trust = parse_snapshot_manifest(root / "snapshot-manifest.json")
    (root / "components.json").unlink()

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot_integrity(root, trust)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_integrity_rejects_same_size_hash_mismatch(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    trust = parse_snapshot_manifest(root / "snapshot-manifest.json")
    path = root / "components.json"
    original = path.read_bytes()
    path.write_bytes(b"X" * len(original))

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot_integrity(root, trust)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


@pytest.mark.parametrize(
    "relative_path",
    ["extra.json", "nested/extra.jsonl", "corpus/extra.md", "indexes/vectors.bin"],
)
def test_integrity_rejects_unlisted_governed_files(
    tmp_path: Path, relative_path: str
) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    trust = parse_snapshot_manifest(root / "snapshot-manifest.json")
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unlisted")

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot_integrity(root, trust)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_verify_snapshot_rejects_ungoverned_fifo(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    try:
        os.mkfifo(root / "ignored.txt")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"FIFO creation is unavailable: {exc}")

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


@pytest.mark.parametrize("target", ["components.json", "snapshot-manifest.json", "snapshot-manifest.sig"])
def test_verify_snapshot_rejects_snapshot_symlinks(tmp_path: Path, target: str) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    path = root / target
    replacement = tmp_path / f"external-{target}"
    replacement.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(replacement)

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code in {"SNAPSHOT_INTEGRITY_FAILED", "SNAPSHOT_MANIFEST_INVALID"}


def test_verify_snapshot_rejects_symlink_allowed_signers(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    symlink = tmp_path / "allowed-signers-link"
    symlink.symlink_to(allowed_signers)

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, symlink)

    assert error.value.code == "SNAPSHOT_TRUST_BOUNDARY"


def test_verify_snapshot_rejects_non_ed25519_signer(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    rsa_key = tmp_path / "trust" / "rsa_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-N", "", "-f", str(rsa_key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    public_key = rsa_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    allowed_signers.write_text(f"reqmap-snapshot {public_key}\n", encoding="utf-8")
    signature = root / "snapshot-manifest.sig"
    signature.unlink()
    manifest = root / "snapshot-manifest.json"
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(rsa_key),
            "-n",
            "reqmap-snapshot",
            str(manifest),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    manifest.with_suffix(manifest.suffix + ".sig").replace(signature)

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_UNTRUSTED"


def test_verify_snapshot_rejects_rsa_signature_with_mixed_trusted_keys(
    tmp_path: Path,
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    rsa_key = tmp_path / "trust" / "mixed_rsa_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-N", "", "-f", str(rsa_key)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    rsa_public_key = rsa_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    with allowed_signers.open("a", encoding="utf-8") as stream:
        stream.write(f"reqmap-snapshot {rsa_public_key}\n")
    signature = root / "snapshot-manifest.sig"
    signature.unlink()
    manifest = root / "snapshot-manifest.json"
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(rsa_key),
            "-n",
            "reqmap-snapshot",
            str(manifest),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    manifest.with_suffix(manifest.suffix + ".sig").replace(signature)

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_UNTRUSTED"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (FileNotFoundError(), "SNAPSHOT_VERIFIER_MISSING"),
        (subprocess.TimeoutExpired(["ssh-keygen"], 10), "SNAPSHOT_SIGNATURE_TIMEOUT"),
    ],
)
def test_signature_verifier_maps_process_failures_without_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected_code: str,
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("reqmap.snapshot_trust.subprocess.run", fail)

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot_signature(root, allowed_signers)

    assert error.value.code == expected_code
    assert "ssh-keygen" not in error.value.message_ru
    assert error.value.details == {}
    assert error.value.__cause__ is None


def test_signature_verifier_hides_stderr_on_untrusted_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    secret_diagnostic = "PRIVATE-SSH-DIAGNOSTIC"

    def reject(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 255, b"", secret_diagnostic.encode())

    monkeypatch.setattr("reqmap.snapshot_trust.subprocess.run", reject)

    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot_signature(root, allowed_signers)

    assert error.value.code == "SNAPSHOT_UNTRUSTED"
    assert secret_diagnostic not in str(error.value)
    assert secret_diagnostic not in error.value.message_ru
    assert error.value.details == {}
