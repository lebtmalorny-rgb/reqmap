"""Канонический JSON, воспроизводимый manifest и безопасный JSONL-журнал."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import stat

import pytest

from reqmap.export_json import write_canonical_json
from reqmap.manifest import RunLogger, write_manifest
from reqmap.models import (
    AnalysisState,
    AtomResult,
    Evidence,
    EvidencePolarity,
    EvidenceStrength,
    GroupResult,
    RequirementResult,
    RunResult,
    SourceField,
    SourceHint,
    SupportStatus,
)
from tests.factories import atom, mapping, requirement


def completed_run() -> RunResult:
    source = replace(
        requirement(),
        group_ids=("GRP-001",),
        source_fields=(SourceField("Приоритет", "Высокий"),),
        source_hints=(SourceHint("Nova", "Компонент"),),
    )
    claim = atom()
    implementation = mapping()
    atom_result = AtomResult(
        atom=claim,
        analysis_state=AnalysisState.COMPLETED,
        support_status=SupportStatus.SUPPORTED,
        mappings=(implementation,),
        supported_aspects=("Synthetic role",),
    )
    requirement_result = RequirementResult(
        requirement=source,
        analysis_state=AnalysisState.COMPLETED,
        support_status=SupportStatus.SUPPORTED,
        atom_results=(atom_result,),
        mappings=(implementation,),
    )
    group = GroupResult(
        group_id="GRP-001",
        source_requirement_ids=(source.requirement_id,),
        support_status=SupportStatus.SUPPORTED,
        component_ids=("nova",),
        mapping_ids=(implementation.mapping_id,),
        analysis_states=(AnalysisState.COMPLETED,),
    )
    evidence = Evidence(
        evidence_id="evidence-1",
        component_id="nova",
        capability_id="CAP-NOVA",
        polarity=EvidencePolarity.POSITIVE,
        strength=EvidenceStrength.DIRECT,
        claim_ru="Nova подтверждает синтетическую возможность.",
        source_id="SRC-NOVA",
        locator="section 1",
        version_constraint="2025.1",
        source_url="https://docs.openstack.org/nova/2025.1/",
        local_path="sources/nova.md",
        source_sha256="b" * 64,
        retrieved_at="2026-08-20",
        provenance="official",
    )
    return RunResult(
        run_id="run-test",
        schema_version="1.0",
        run_status="SUCCESS",
        requirements=(requirement_result,),
        groups=(group,),
        evidence=(evidence,),
        metadata={
            "reqmap_version": "0.1.0",
            "knowledge_version": "2025.1",
            "input_sha256": "a" * 64,
            "knowledge_sha256": "b" * 64,
            "model": "local-model",
            "endpoint_origin": "http://127.0.0.1:8000",
            "seed": 7,
            "prompt_versions": {"decomposition": "1.0", "mapping": "1.0"},
            "started_at": "2026-08-21T10:00:00+03:00",
            "finished_at": "2026-08-21T10:01:00+03:00",
            "retry_counts": {"decomposition": 1, "mapping": 0},
        },
    )


def test_canonical_json_is_stable_complete_and_returns_sha256(
    tmp_path: Path,
) -> None:
    run = completed_run()
    first_path = tmp_path / "a.json"
    second_path = tmp_path / "b.json"

    first_hash = write_canonical_json(run, first_path)
    second_hash = write_canonical_json(run, second_path)

    assert first_hash == second_hash
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_hash == hashlib.sha256(first_path.read_bytes()).hexdigest()
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["requirements"][0]["requirement"]["text"] == (
        "Synthetic requirement"
    )
    assert payload["requirements"][0]["atom_results"][0]["atom"][
        "source_quote"
    ] == "Synthetic requirement"
    assert payload["requirements"][0]["mappings"][0]["steps"][0][
        "action_ru"
    ] == "Synthetic action"
    assert payload["groups"][0]["source_requirement_ids"] == ["REQ-0001"]
    assert payload["evidence"][0]["evidence_id"] == "evidence-1"
    assert first_path.read_bytes().endswith(b"\n")
    assert stat.S_IMODE(first_path.stat().st_mode) == 0o600


def test_canonical_json_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "result.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        write_canonical_json(completed_run(), link)

    assert target.read_text(encoding="utf-8") == "keep"


def test_canonical_json_rejects_secret_bearing_metadata(tmp_path: Path) -> None:
    run = completed_run()
    nested_values = (
        {"api_key": "top-secret"},
        [{"api_key": "top-secret"}],
        ({"api_key": "top-secret"},),
    )

    for index, nested in enumerate(nested_values):
        unsafe = replace(
            run,
            metadata={
                **run.metadata,
                "nested": nested,
            },
        )
        path = tmp_path / f"result-{index}.json"

        with pytest.raises(ValueError, match="secret|metadata"):
            write_canonical_json(unsafe, path)

        assert not path.exists()


def test_canonical_json_rejects_malformed_nested_run_records(
    tmp_path: Path,
) -> None:
    run = completed_run()
    source_result = run.requirements[0]
    source_atom = source_result.atom_results[0]
    source_mapping = source_atom.mappings[0]
    failed_with_status = replace(
        source_result,
        atom_results=(
            replace(source_atom, analysis_state=AnalysisState.MODEL_FAILED),
        ),
    )
    foreign_mapping = replace(source_mapping, atom_id="REQ-9999-A001")
    foreign_mapping_result = replace(
        source_result,
        atom_results=(replace(source_atom, mappings=(foreign_mapping,)),),
        mappings=(foreign_mapping,),
    )
    forged_flattening = replace(source_result, mappings=())
    forged_group = replace(run.groups[0], component_ids=("neutron",))
    malformed_runs = (
        replace(run, requirements=(failed_with_status,)),
        replace(run, requirements=(foreign_mapping_result,)),
        replace(run, requirements=(forged_flattening,)),
        replace(run, groups=(forged_group,)),
    )

    for index, malformed in enumerate(malformed_runs):
        path = tmp_path / f"invalid-{index}.json"
        with pytest.raises(ValueError):
            write_canonical_json(malformed, path)
        assert not path.exists()


def test_json_writer_does_not_chmod_existing_parent_directory(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "existing-output"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)

    write_canonical_json(completed_run(), parent / "result.json")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


def test_manifest_is_allowlisted_safe_and_has_no_self_hash(tmp_path: Path) -> None:
    run = completed_run()
    unsafe_metadata = {
        **run.metadata,
        "api_key": "top-secret",
        "prompt_versions": {
            **run.metadata["prompt_versions"],
            "api_key": "nested-secret",
        },
        "retry_counts": {
            **run.metadata["retry_counts"],
            "api_key": "nested-secret",
        },
        "endpoint_origin": (
            "http://user:password@127.0.0.1:8000/v1/private?token=hidden"
        ),
    }
    run = replace(run, metadata=unsafe_metadata)
    path = tmp_path / "manifest.json"

    write_manifest(
        run,
        {
            "result.json": "json-hash",
            "result.xlsx": "xlsx-hash",
            "report.md": "report-hash",
            "run.jsonl": "log-hash",
            "manifest.json": "self-hash-must-not-appear",
        },
        path,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["run_id"] == "run-test"
    assert payload["run_status"] == "SUCCESS"
    assert payload["artifact_hashes"] == {
        "report.md": "report-hash",
        "result.json": "json-hash",
        "result.xlsx": "xlsx-hash",
        "run.jsonl": "log-hash",
    }
    assert payload["endpoint_origin"] == "http://127.0.0.1:8000"
    assert payload["prompt_versions"] == {
        "decomposition": "1.0",
        "mapping": "1.0",
    }
    assert payload["retry_counts"] == {"decomposition": 1, "mapping": 0}
    serialized = path.read_text(encoding="utf-8")
    assert "api_key" not in serialized
    assert "top-secret" not in serialized
    assert "password" not in serialized
    assert "/v1/private" not in serialized
    assert "token=hidden" not in serialized
    assert "self-hash-must-not-appear" not in serialized
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_run_log_is_jsonl_recursive_redacted_and_append_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.jsonl"
    logger = RunLogger(path, redacted_values=("top-secret", "hidden-token"))

    logger.write(
        "model_retry",
        "warning",
        "Повторный вызов модели с top-secret",
        reason={"nested": ["hidden-token", "безопасно"]},
        secret_keyed={"top-secret": "значение"},
    )
    logger.write("run_finished", "info", "Запуск завершён", count=1)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["event"] == "model_retry"
    assert records[0]["message_ru"] == (
        "Повторный вызов модели с [REDACTED]"
    )
    assert records[0]["reason"] == {
        "nested": ["[REDACTED]", "безопасно"]
    }
    assert records[0]["secret_keyed"] == {"[REDACTED]": "значение"}
    assert records[1]["count"] == 1
    assert "top-secret" not in path.read_text(encoding="utf-8")
    assert "hidden-token" not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_run_logger_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "run.jsonl"
    link.symlink_to(target)
    logger = RunLogger(link)

    with pytest.raises(ValueError, match="symlink"):
        logger.write("event", "info", "Сообщение")

    assert target.read_text(encoding="utf-8") == "keep\n"
