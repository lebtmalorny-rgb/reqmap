"""Deep preflight, orchestration, and verified schema-2 resume."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import json
from pathlib import Path
import stat

import pytest

from reqmap.config import (
    AnalysisProfile,
    AppConfig,
    KnowledgeTrustConfig,
    ModelConfig,
)
from reqmap.deep_models import ResponsibilityContour
from reqmap.deep_pipeline import analyze_deep, preflight_deep
from reqmap.errors import ModelError
from reqmap.models import AnalysisRequest, AnalysisState
from tests.deep_factories import (
    deep_mapping_response,
    mixed_kolla_host_kb,
    responsibility_selection,
    signed_v2_snapshot,
)
from tests.factories import requirement


class FakeModel:
    def __init__(
        self,
        responses: tuple[dict[str, object] | Exception, ...] = (),
        *,
        preflight_error: Exception | None = None,
    ) -> None:
        self._responses = deque(responses)
        self.preflight_error = preflight_error
        self.preflight_calls = 0
        self.calls: list[tuple[str, dict[str, object]]] = []

    def preflight(self) -> None:
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error

    def complete_json(
        self,
        stage: str,
        system_prompt: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        del system_prompt
        self.calls.append((stage, payload))
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def _config(
    root: Path,
    allowed_signers: Path,
    *,
    model: str = "local-model",
    seed: int | None = 7,
    top_k: int = 8,
    profile: AnalysisProfile = AnalysisProfile.DEEP,
) -> AppConfig:
    return AppConfig(
        model=ModelConfig(
            base_url="http://127.0.0.1:8000/v1/private?token=hidden",
            model=model,
            api_key_env="REQMAP_API_KEY",
            api_key="top-secret",
            seed=seed,
        ),
        knowledge_path=root,
        input_profile=None,
        top_k=top_k,
        analysis_profile=profile,
        knowledge_trust=KnowledgeTrustConfig(allowed_signers),
    )


def _request(tmp_path: Path, *texts: str) -> AnalysisRequest:
    values = texts or ("Создание сервера через Nova API",)
    requirements = tuple(
        replace(
            requirement(ordinal=index, requirement_id=f"REQ-{index:04d}"),
            source_id=f"source-{index}",
            text=text,
        )
        for index, text in enumerate(values, 1)
    )
    return AnalysisRequest(
        requirements=requirements,
        input_sha256="a" * 64,
        input_kind="text",
        source_path=None,
        output_dir=tmp_path / "output",
    )


def _decomposition(text: str) -> dict[str, object]:
    return {
        "atoms": [
            {"text": text, "source_quote": text, "mandatory": True},
        ]
    }


def _completed_responses(text: str) -> tuple[dict[str, object], ...]:
    return (_decomposition(text), deep_mapping_response())


def _signed_config(tmp_path: Path, **changes: object) -> AppConfig:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    return _config(root, allowed_signers, **changes)


def _checkpoint(request: AnalysisRequest, requirement_id: str) -> Path:
    matches = tuple(
        (request.output_dir / ".work").glob(f"*/{requirement_id}.json")
    )
    assert len(matches) == 1
    return matches[0]


def _mixed_selection(
    contour: str, component_ref: str, related_indexes: list[int]
) -> dict[str, object]:
    return responsibility_selection(
        contour=contour,
        component_ref=component_ref,
        executor_ref="actor:kolla_ansible",
        target_contour="host_os",
        target_ref="rocky_linux_9.kernel_sysctl",
        action_ref="ACTION-KOLLA-SYSCTL",
        effect_ref="EFFECT-HOST-SYSCTL",
        lifecycle_phase="reconfigure",
        evidence_ids=["EV-KOLLA-SYSCTL"],
        related_indexes=related_indexes,
    )


def _forge_not_supported_status(payload: dict[str, object]) -> None:
    requirement_payload = payload["requirement"]
    assert isinstance(requirement_payload, dict)
    atom_payload = requirement_payload["atom_results"][0]
    record_payload = payload["responsibility_records"][0]
    assert isinstance(atom_payload, dict)
    assert isinstance(record_payload, dict)
    requirement_payload["support_status"] = "not_supported"
    atom_payload["support_status"] = "not_supported"
    record_payload["support_status"] = "not_supported"


def _remove_required_procedure(payload: dict[str, object]) -> None:
    requirement_payload = payload["requirement"]
    record_payload = payload["responsibility_records"][0]
    assert isinstance(requirement_payload, dict)
    assert isinstance(record_payload, dict)
    requirement_payload["procedure_graph_ids"] = []
    requirement_payload["diagnostics"] = []
    record_payload["procedure_step_ids"] = []
    payload["procedure_graphs"] = []


def test_deep_preflight_verifies_signed_snapshot_before_model(tmp_path: Path) -> None:
    config = _signed_config(tmp_path)
    (config.knowledge_path / "actions.jsonl").write_text("tampered\n", encoding="utf-8")
    model = FakeModel()

    result = preflight_deep(config, model)

    assert result.ok is False
    assert result.diagnostics[0].startswith("SNAPSHOT_INTEGRITY_FAILED:")
    assert model.preflight_calls == 0
    assert model.calls == []


def test_deep_profile_reports_recognizable_v1_without_model_call(tmp_path: Path) -> None:
    root = Path("knowledge/epoxy-2025.1")
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text("unused\n", encoding="utf-8")
    model = FakeModel()

    result = preflight_deep(_config(root, allowed_signers), model)

    assert result.ok is False
    assert result.diagnostics[0].startswith("KNOWLEDGE_SCHEMA_UNSUPPORTED:")
    assert model.preflight_calls == 0


def test_tampered_v2_metadata_keeps_specific_integrity_diagnostic(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    (config.knowledge_path / "metadata.json").write_text(
        json.dumps(
            {"openstack_release": "2025.1", "snapshot_sha256": "0" * 64},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    model = FakeModel()

    result = preflight_deep(config, model)

    assert result.ok is False
    assert result.diagnostics[0].startswith("SNAPSHOT_INTEGRITY_FAILED:")
    assert model.preflight_calls == 0


def test_deep_invalid_model_metadata_fails_before_cleanup_or_model(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    invalid = replace(
        config,
        model=replace(config.model, model="/private/local-model"),
    )
    request = _request(tmp_path)
    request.output_dir.mkdir()
    stale = request.output_dir / "result.json"
    stale.write_text("stale", encoding="utf-8")
    model = FakeModel()

    run = analyze_deep(request, invalid, model)

    assert run.run_status == "FAILED"
    assert run.diagnostics[0].startswith("CONFIG_INVALID:")
    assert run.metadata["model"] == "invalid-model"
    assert model.preflight_calls == 0
    assert model.calls == []
    assert stale.read_text(encoding="utf-8") == "stale"
    assert not (request.output_dir / "run.jsonl").exists()
    assert not (request.output_dir / "manifest.json").exists()


@pytest.mark.parametrize(
    ("request_factory", "config_factory", "diagnostic_prefix"),
    (
        (
            lambda root: _request(root),
            lambda _root: object(),
            "CONFIG_INVALID:",
        ),
        (
            lambda _root: object(),
            lambda root: _signed_config(root),
            "REQUEST_INVALID:",
        ),
    ),
)
def test_deep_noncanonical_public_inputs_fail_closed_without_model_or_output(
    tmp_path: Path,
    request_factory,
    config_factory,
    diagnostic_prefix: str,
) -> None:
    model = FakeModel()

    run = analyze_deep(  # type: ignore[arg-type]
        request_factory(tmp_path),
        config_factory(tmp_path),
        model,
    )

    assert run.run_status == "FAILED"
    assert run.diagnostics[0].startswith(diagnostic_prefix)
    assert model.preflight_calls == 0
    assert model.calls == []
    assert not (tmp_path / "output").exists()


def test_deep_reserved_work_file_fails_before_trust_model_or_output(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    request = _request(tmp_path)
    request.output_dir.mkdir()
    work_file = request.output_dir / ".work"
    work_file.write_text("reserved collision", encoding="utf-8")
    model = FakeModel()

    run = analyze_deep(request, config, model)

    assert run.run_status == "FAILED"
    assert run.diagnostics[0].startswith("OUTPUT_INVALID:")
    assert model.preflight_calls == 0
    assert model.calls == []
    assert work_file.read_text(encoding="utf-8") == "reserved collision"
    assert sorted(path.name for path in request.output_dir.iterdir()) == [".work"]


def test_deep_pipeline_processes_mixed_contours_without_source_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _signed_config(tmp_path)
    from reqmap.knowledge_v2 import load_knowledge_v2

    loaded = load_knowledge_v2(
        config.knowledge_path,
        config.knowledge_trust.allowed_signers_path,  # type: ignore[union-attr]
    )
    kb, _retrieval = mixed_kolla_host_kb(loaded)
    monkeypatch.setattr("reqmap.deep_pipeline.load_knowledge_v2", lambda *_args: kb)
    source = kb.sources["SRC-MINIMAL"]
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(AssertionError("runtime source read"))
            if path == kb.root / source.local_path
            else _ORIGINAL_READ_TEXT(path, *args, **kwargs)
        ),
    )
    text = "Применить sysctl через Kolla-Ansible"
    response = deep_mapping_response(
        _mixed_selection("host_os", "rocky_linux_9", [2]),
        _mixed_selection("kolla_ansible", "kolla_ansible", [1]),
        procedure_template_ids=(),
    )

    run = analyze_deep(
        _request(tmp_path, text), config, FakeModel((_decomposition(text), response))
    )

    assert run.run_status == "SUCCESS"
    assert tuple(item.contour for item in run.responsibility_records) == (
        ResponsibilityContour.KOLLA_ANSIBLE,
        ResponsibilityContour.HOST_OS,
    )
    assert run.metadata["release_profile"] == {
        "source_release": "2025.1",
        "target_release": "2025.1",
        "kolla_ansible_release": "2025.1",
        "host_profile": "rocky_linux_9",
    }
    serialized = json.dumps(run.metadata, ensure_ascii=False)
    assert "base_url" not in serialized
    assert "127.0.0.1" not in serialized
    assert "top-secret" not in serialized
    assert str(config.knowledge_path) not in serialized


_ORIGINAL_READ_TEXT = Path.read_text


def test_deep_pipeline_isolates_failed_requirement_and_keeps_sibling(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    first = "Создание сервера через Nova API альфа"
    second = "Создание сервера через Nova API бета"
    invalid = {"unexpected": True}
    model = FakeModel(
        (
            _decomposition(first),
            invalid,
            invalid,
            _decomposition(second),
            deep_mapping_response(),
        )
    )

    run = analyze_deep(_request(tmp_path, first, second), config, model)

    assert run.requirements[0].analysis_state is AnalysisState.MODEL_FAILED
    assert run.requirements[0].support_status is None
    assert run.requirements[1].analysis_state is AnalysisState.COMPLETED
    assert run.run_status == "PARTIAL"
    assert tuple(item.requirement_id for item in run.responsibility_records) == (
        "REQ-0002",
    )
    work_dir = _request(tmp_path, first, second).output_dir / ".work"
    assert tuple(work_dir.glob("*/REQ-0001.json")) == ()


def test_deep_preflight_failure_writes_only_safe_diagnostic_pair(tmp_path: Path) -> None:
    config = _signed_config(tmp_path)
    request = _request(tmp_path, "alpha", "beta")
    request.output_dir.mkdir()
    for name in ("result.json", "result.xlsx", "report.md", "run.jsonl", "manifest.json"):
        (request.output_dir / name).write_text("stale", encoding="utf-8")
    model = FakeModel(preflight_error=ModelError("MODEL_DOWN", "Модель недоступна."))

    run = analyze_deep(request, config, model)

    assert run.run_status == "FAILED"
    assert model.preflight_calls == 1
    assert model.calls == []
    assert all(
        item.analysis_state is AnalysisState.SKIPPED
        and item.support_status is None
        and not item.atom_results
        and not item.responsibility_ids
        and not item.procedure_graph_ids
        for item in run.requirements
    )
    assert sorted(path.name for path in request.output_dir.iterdir()) == [
        "manifest.json",
        "run.jsonl",
    ]
    assert set(run.metadata) == {
        "reqmap_version",
        "analysis_profile",
        "model",
        "seed",
        "top_k",
        "input_sha256",
        "snapshot_id",
        "manifest_sha256",
        "key_id",
        "signer_identity",
        "prompt_versions",
        "release_profile",
        "retry_counts",
    }


def test_deep_resume_uses_exact_schema2_closure_and_secure_modes(tmp_path: Path) -> None:
    config = _signed_config(tmp_path)
    text = "Создание сервера через Nova API"
    request = _request(tmp_path, text)
    first = analyze_deep(request, config, FakeModel(_completed_responses(text)))

    resumed_model = FakeModel()
    resumed = analyze_deep(request, config, resumed_model)

    assert first == resumed
    assert resumed_model.preflight_calls == 1
    assert resumed_model.calls == []
    checkpoint = _checkpoint(request, "REQ-0001")
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "run_signature",
        "requirement",
        "responsibility_records",
        "procedure_graphs",
        "evidence",
    }
    assert payload["schema_version"] == "2.0"
    assert payload["run_signature"] == checkpoint.parent.name
    assert len(payload["responsibility_records"]) == 1
    assert len(payload["procedure_graphs"]) == 1
    assert len(payload["evidence"]) == 1
    serialized = checkpoint.read_text(encoding="utf-8")
    assert "corpus_candidates" not in serialized
    assert "raw_response" not in serialized
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert stat.S_IMODE(checkpoint.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "change",
    (
        {"model": "changed-model"},
        {"seed": 8},
        {"top_k": 9},
        {"profile": AnalysisProfile.LEGACY},
    ),
)
def test_deep_resume_signature_invalidates_significant_config(
    tmp_path: Path, change: dict[str, object]
) -> None:
    config = _signed_config(tmp_path)
    text = "Создание сервера через Nova API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    changed = _config(
        config.knowledge_path,
        config.knowledge_trust.allowed_signers_path,  # type: ignore[union-attr]
        **change,
    )
    model = FakeModel(_completed_responses(text))

    analyze_deep(request, changed, model)

    if change.get("profile") is AnalysisProfile.LEGACY:
        assert model.calls == []
    else:
        assert [stage for stage, _ in model.calls] == [
            "decomposition",
            "deep_mapping",
        ]


@pytest.mark.parametrize(
    "mutator",
    (
        lambda payload: payload.update({"unknown": True}),
        lambda payload: payload.update({"schema_version": "1.0"}),
        lambda payload: payload["requirement"].update({"analysis_state": "skipped"}),
        lambda payload: payload["responsibility_records"][0].update(
            {"action_ref": "ACTION-UNKNOWN"}
        ),
        lambda payload: payload["evidence"].clear(),
        _forge_not_supported_status,
        _remove_required_procedure,
    ),
)
def test_deep_resume_strictly_rejects_malformed_or_dangling_closure(
    tmp_path: Path, mutator
) -> None:
    config = _signed_config(tmp_path)
    text = "Создание сервера через Nova API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    checkpoint = _checkpoint(request, "REQ-0001")
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    mutator(payload)
    checkpoint.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    model = FakeModel(_completed_responses(text))

    resumed = analyze_deep(request, config, model)

    assert resumed.requirements[0].analysis_state is AnalysisState.COMPLETED
    assert [stage for stage, _ in model.calls] == ["decomposition", "deep_mapping"]


def test_valid_second_requirement_resumes_with_source_prefix_placeholders(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    first = "Создание сервера через Nova API альфа"
    second = "Создание сервера через Nova API бета"
    request = _request(tmp_path, first, second)
    analyze_deep(
        request,
        config,
        FakeModel((*_completed_responses(first), *_completed_responses(second))),
    )
    _checkpoint(request, "REQ-0001").unlink()
    model = FakeModel(_completed_responses(first))

    resumed = analyze_deep(request, config, model)

    assert [stage for stage, _ in model.calls] == ["decomposition", "deep_mapping"]
    assert tuple(item.analysis_state for item in resumed.requirements) == (
        AnalysisState.COMPLETED,
        AnalysisState.COMPLETED,
    )
    assert tuple(item.requirement.requirement_id for item in resumed.requirements) == (
        "REQ-0001",
        "REQ-0002",
    )


def test_duplicate_json_keys_and_symlinked_checkpoint_recompute(tmp_path: Path) -> None:
    config = _signed_config(tmp_path)
    text = "Создание сервера через Nova API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    checkpoint = _checkpoint(request, "REQ-0001")
    source = checkpoint.read_text(encoding="utf-8")
    checkpoint.write_text(
        source.replace('"schema_version":"2.0"', '"schema_version":"2.0","schema_version":"2.0"'),
        encoding="utf-8",
    )
    duplicate_model = FakeModel(_completed_responses(text))
    analyze_deep(request, config, duplicate_model)
    assert len(duplicate_model.calls) == 2

    target = tmp_path / "forged.json"
    target.write_text(checkpoint.read_text(encoding="utf-8"), encoding="utf-8")
    checkpoint.unlink()
    checkpoint.symlink_to(target)
    symlink_model = FakeModel(_completed_responses(text))
    analyze_deep(request, config, symlink_model)
    assert len(symlink_model.calls) == 2
