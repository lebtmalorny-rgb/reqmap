"""Maintenance tools for canonical schema-v2 snapshot manifests and signatures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from reqmap.export_json import canonical_json_bytes
from reqmap.snapshot_trust import (
    SnapshotTrustError,
    build_snapshot_manifest,
    sign_snapshot_manifest,
    verify_snapshot,
    write_snapshot_manifest,
)
from tools.kb.build_snapshot_v2 import main as build_main
from tools.kb.sign_snapshot_v2 import main as sign_main


FIXTURE = Path(__file__).parent / "fixtures" / "kb_v2_minimal"


def unsigned_v2_snapshot(tmp_path: Path, *, status: str = "approved") -> Path:
    root = tmp_path / "snapshot"
    shutil.copytree(FIXTURE, root)
    metadata_path = root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["snapshot_status"] = status
    metadata_path.write_bytes(canonical_json_bytes(metadata))
    return root


def generate_private_key(tmp_path: Path, *, key_type: str = "ed25519") -> Path:
    trust = tmp_path / f"trust-{key_type}"
    trust.mkdir()
    private_key = trust / "signing-key"
    arguments = ["ssh-keygen", "-q", "-t", key_type]
    if key_type == "rsa":
        arguments.extend(["-b", "2048"])
    arguments.extend(["-N", "", "-f", str(private_key)])
    subprocess.run(
        arguments,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return private_key


def allowed_signers_for(private_key: Path) -> Path:
    public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    allowed_signers = private_key.parent / "allowed-signers"
    allowed_signers.write_text(f"reqmap-snapshot {public_key}\n", encoding="utf-8")
    return allowed_signers


def test_build_manifest_is_deterministic_sorted_and_canonical(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)

    first = write_snapshot_manifest(root)
    first_bytes = (root / "snapshot-manifest.json").read_bytes()
    second = write_snapshot_manifest(root)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first_bytes == canonical_json_bytes(first)
    assert (root / "snapshot-manifest.json").read_bytes() == first_bytes
    assert [item["path"] for item in first["files"]] == sorted(
        item["path"] for item in first["files"]
    )
    assert first == build_snapshot_manifest(root)


def test_build_tool_accepts_exact_path_option(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)

    assert build_main(["--path", str(root)]) == 0
    assert (root / "snapshot-manifest.json").is_file()

    with pytest.raises(SystemExit) as positional:
        build_main([str(root)])
    with pytest.raises(SystemExit) as extra:
        build_main(["--path", str(root), "extra"])
    assert positional.value.code == 2
    assert extra.value.code == 2


def test_build_refuses_hidden_temporary_file_and_preserves_manifest(
    tmp_path: Path,
) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    manifest = root / "snapshot-manifest.json"
    before = manifest.read_bytes()
    (root / ".snapshot-manifest.json.orphan").write_bytes(b"temporary")

    with pytest.raises(SnapshotTrustError):
        write_snapshot_manifest(root)

    assert manifest.read_bytes() == before


def test_build_refuses_unsafe_existing_manifest_target(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    external = tmp_path / "external-manifest"
    external.write_bytes(b"preserve")
    (root / "snapshot-manifest.json").symlink_to(external)

    with pytest.raises(SnapshotTrustError):
        write_snapshot_manifest(root)

    assert external.read_bytes() == b"preserve"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_build_refuses_special_file_before_writing_manifest(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    os.mkfifo(root / "ignored.txt")

    with pytest.raises(SnapshotTrustError):
        write_snapshot_manifest(root)

    assert not (root / "snapshot-manifest.json").exists()


def test_build_validates_snapshot_before_replacing_safe_manifest(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    manifest = root / "snapshot-manifest.json"
    manifest.write_bytes(b"prior-safe-manifest\n")
    (root / "components.json").write_bytes(b"{}\n")

    with pytest.raises(Exception):
        write_snapshot_manifest(root)

    assert manifest.read_bytes() == b"prior-safe-manifest\n"


def test_sign_tool_refuses_private_key_inside_snapshot(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    key = root / "private-key"
    key.write_bytes(b"private")

    with pytest.raises(SnapshotTrustError, match="вне snapshot"):
        sign_snapshot_manifest(root, key)


def test_sign_tool_refuses_symlink_private_key(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)
    linked_key = tmp_path / "linked-key"
    linked_key.symlink_to(private_key)

    with pytest.raises(SnapshotTrustError, match="symlink"):
        sign_snapshot_manifest(root, linked_key)


def test_sign_tool_refuses_unapproved_snapshot(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path, status="draft")
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)

    with pytest.raises(SnapshotTrustError) as error:
        sign_snapshot_manifest(root, private_key)

    assert error.value.code == "SNAPSHOT_NOT_APPROVED"
    assert not (root / "snapshot-manifest.sig").exists()


def test_sign_tool_refuses_noncanonical_or_stale_manifest(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)
    manifest = root / "snapshot-manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SnapshotTrustError) as noncanonical:
        sign_snapshot_manifest(root, private_key)
    assert noncanonical.value.code == "SNAPSHOT_MANIFEST_INVALID"

    write_snapshot_manifest(root)
    (root / "components.json").write_bytes(b"X" + (root / "components.json").read_bytes())
    with pytest.raises(SnapshotTrustError) as stale:
        sign_snapshot_manifest(root, private_key)
    assert stale.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_sign_tool_refuses_rsa_key_and_cleans_generated_signature(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path, key_type="rsa")

    with pytest.raises(SnapshotTrustError, match="Ed25519"):
        sign_snapshot_manifest(root, private_key)

    assert not (root / "snapshot-manifest.json.sig").exists()
    assert not (root / "snapshot-manifest.sig").exists()


def test_sign_refuses_unsafe_existing_signature_target(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)
    external = tmp_path / "external-signature"
    external.write_bytes(b"preserve")
    (root / "snapshot-manifest.sig").symlink_to(external)

    with pytest.raises(SnapshotTrustError):
        sign_snapshot_manifest(root, private_key)

    assert external.read_bytes() == b"preserve"


def test_sign_process_contract_hides_diagnostics_and_preserves_prior_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)
    signature = root / "snapshot-manifest.sig"
    signature.write_bytes(b"prior-safe-signature")
    secret_stderr = b"PRIVATE-DIAGNOSTIC"
    calls: list[tuple[object, dict[str, object]]] = []

    def reject(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 255, b"", secret_stderr)

    monkeypatch.setattr("reqmap.snapshot_trust.subprocess.run", reject)

    with pytest.raises(SnapshotTrustError) as error:
        sign_snapshot_manifest(root, private_key)

    assert calls == [
        (
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-f",
                str(private_key),
                "-n",
                "reqmap-snapshot",
                str(root / "snapshot-manifest.json"),
            ],
            {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "timeout": 10,
                "check": False,
                "shell": False,
            },
        )
    ]
    assert signature.read_bytes() == b"prior-safe-signature"
    assert str(private_key) not in str(error.value)
    assert secret_stderr.decode() not in str(error.value)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (FileNotFoundError(), "SNAPSHOT_SIGNER_MISSING"),
        (subprocess.TimeoutExpired(["ssh-keygen"], 10), "SNAPSHOT_SIGNATURE_TIMEOUT"),
    ],
)
def test_sign_maps_process_exceptions_without_key_disclosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected_code: str,
) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("reqmap.snapshot_trust.subprocess.run", fail)

    with pytest.raises(SnapshotTrustError) as error:
        sign_snapshot_manifest(root, private_key)

    assert error.value.code == expected_code
    assert str(private_key) not in str(error.value)
    assert error.value.__cause__ is None


def test_build_sign_verify_round_trip_and_tamper_failure(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    private_key = generate_private_key(tmp_path)
    allowed_signers = allowed_signers_for(private_key)

    write_snapshot_manifest(root)
    signature = sign_snapshot_manifest(root, private_key)
    trust = verify_snapshot(root, allowed_signers)

    assert signature == root / "snapshot-manifest.sig"
    assert trust.snapshot_id == "epoxy-2025.1-deep-001"
    components = root / "components.json"
    payload = components.read_bytes()
    components.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    with pytest.raises(SnapshotTrustError) as error:
        verify_snapshot(root, allowed_signers)
    assert error.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_sign_cli_accepts_exact_options_without_disclosing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = unsigned_v2_snapshot(tmp_path)
    write_snapshot_manifest(root)
    private_key = generate_private_key(tmp_path)

    assert sign_main(["--path", str(root), "--private-key", str(private_key)]) == 0
    captured = capsys.readouterr()
    assert str(private_key) not in captured.out
    assert str(private_key) not in captured.err

    with pytest.raises(SystemExit) as missing_option:
        sign_main(["--path", str(root), str(private_key)])
    with pytest.raises(SystemExit) as extra:
        sign_main(
            ["--path", str(root), "--private-key", str(private_key), "extra"]
        )
    assert missing_option.value.code == 2
    assert extra.value.code == 2
