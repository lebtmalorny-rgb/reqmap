"""Canonical and fail-closed JSON export for schema-v2 deep results."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import stat

import pytest

from reqmap.export_deep_json import (
    validate_deep_run_result,
    write_deep_canonical_json,
)
from tests.test_deep_aggregation import _run as aggregated_deep_run


FORBIDDEN_METADATA = {
    "api_key": "top-secret-api-key",
    "credentials": "private-credentials",
    "authorization": "Bearer private-authorization",
    "base_url": "http://user:password@127.0.0.1:8000/v1/private?token=hidden",
    "allowed_signers_path": "/Users/private/trust/allowed_signers",
    "source_url": "https://private.invalid/source",
    "local_path": "/Users/private/input.xlsx",
    "output_path": "/Users/private/output",
    "private_key_path": "/Users/private/signing_key",
    "raw_model_messages": ["private raw model response"],
    "arbitrary_metadata": {"danger": "private arbitrary value"},
}


def deep_run():
    run = aggregated_deep_run(with_procedure=True)
    return replace(
        run,
        metadata={
            "reqmap_version": "0.2.0",
            "analysis_profile": "deep",
            "model": "local-model",
            "seed": 7,
            "top_k": 12,
            "input_sha256": "a" * 64,
            "snapshot_id": "epoxy-2025.1-deep-001",
            "manifest_sha256": "b" * 64,
            "key_id": "reqmap-maintenance-2026",
            "signer_identity": "reqmap-snapshot",
            "prompt_versions": {
                "decomposition": "1.0",
                "deep_mapping": "2.0",
                "untrusted_stage": "private prompt",
            },
            "release_profile": {
                "source_release": "2025.1",
                "target_release": "2025.1",
                "kolla_ansible_release": "2025.1",
                "host_profile": "rocky_linux_9",
            },
            "retry_counts": {
                "decomposition": 0,
                "deep_mapping": 1,
                "untrusted_stage": 99,
            },
            **FORBIDDEN_METADATA,
        },
    )


def test_deep_json_is_deterministic_and_contains_normalized_graph(
    tmp_path: Path,
) -> None:
    run = deep_run()
    reversed_metadata = dict(reversed(tuple(run.metadata.items())))
    reversed_metadata["ignored_second_value"] = "must-not-change-result"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    first_hash = write_deep_canonical_json(run, first)
    second_hash = write_deep_canonical_json(
        replace(run, metadata=reversed_metadata), second
    )

    assert first_hash == second_hash
    assert first.read_bytes() == second.read_bytes()
    assert first_hash == hashlib.sha256(first.read_bytes()).hexdigest()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "2.0"
    assert payload["responsibility_records"][0]["atomic_claim_id"] == (
        "REQ-0001-A001"
    )
    assert payload["responsibility_records"][0]["contour"] == (
        "openstack_runtime"
    )
    assert payload["procedure_graphs"][0]["steps"][0]["evidence_ids"]
    assert payload["metadata"]["knowledge_trust"] == {
        "key_id": "reqmap-maintenance-2026",
        "manifest_sha256": "b" * 64,
        "signer_identity": "reqmap-snapshot",
    }
    assert payload["metadata"]["prompt_versions"] == {
        "decomposition": "1.0",
        "deep_mapping": "2.0",
    }
    assert payload["metadata"]["retry_counts"] == {
        "decomposition": 0,
        "deep_mapping": 1,
    }
    assert first.read_bytes().endswith(b"\n")
    assert stat.S_IMODE(first.stat().st_mode) == 0o600


def test_deep_json_uses_an_explicit_metadata_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "result.json"

    write_deep_canonical_json(deep_run(), path)

    serialized = path.read_text(encoding="utf-8")
    for key, value in FORBIDDEN_METADATA.items():
        assert key not in serialized
        if isinstance(value, str):
            assert value not in serialized
    assert "private raw model response" not in serialized
    assert "private arbitrary value" not in serialized
    assert "untrusted_stage" not in serialized


def test_deep_json_rejects_invalid_graph_before_destination_publication(
    tmp_path: Path,
) -> None:
    run = deep_run()
    malformed = replace(run, requirements=())
    path = tmp_path / "result.json"

    with pytest.raises(ValueError, match="responsibility|flattening"):
        validate_deep_run_result(malformed)
    with pytest.raises(ValueError, match="responsibility|flattening"):
        write_deep_canonical_json(malformed, path)

    assert not path.exists()


def test_deep_json_reuses_atomic_symlink_protection(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "result.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        write_deep_canonical_json(deep_run(), link)

    assert target.read_text(encoding="utf-8") == "keep"
