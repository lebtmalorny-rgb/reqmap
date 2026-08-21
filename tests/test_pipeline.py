"""Оркестрация анализа, preflight и безопасный requirement-level resume."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import json
from pathlib import Path
import stat

import pytest

from reqmap.config import AppConfig, ModelConfig
from reqmap.errors import ModelError
from reqmap.models import (
    AnalysisRequest,
    AnalysisState,
    AtomResult,
    RequirementResult,
    SupportStatus,
)
from reqmap.pipeline import analyze, preflight, run_status
from tests.factories import atom, requirement


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


def valid_decomposition(text: str) -> dict[str, object]:
    return {
        "atoms": [
            {
                "text": text,
                "source_quote": text,
                "mandatory": True,
            }
        ]
    }


def not_applicable_mapping() -> dict[str, object]:
    return {
        "support_status": "not_applicable",
        "supported_aspects": [],
        "unconfirmed_aspects": [],
        "mappings": [],
    }


def invalid_mapping() -> dict[str, object]:
    return {"unexpected": True}


def supported_nova_mapping() -> dict[str, object]:
    operation = "Compute instances через REST API"
    return {
        "support_status": "supported",
        "supported_aspects": [],
        "unconfirmed_aspects": [],
        "mappings": [
            {
                "component_id": "nova",
                "role_ru": operation,
                "relation": "implements",
                "phase": "runtime",
                "implementation_source": "upstream",
                "mechanism": "openstack_api",
                "steps": [
                    {
                        "action_ru": f"Вызвать {operation}",
                        "mechanism": "openstack_api",
                        "command": None,
                        "api_operation": operation,
                    }
                ],
                "evidence_ids": ["E-NOVA-SCOPE-001"],
                "support_status": "supported",
                "reason_ru": (
                    "Nova реализует управление виртуальными машинами через "
                    "Compute API."
                ),
            }
        ],
    }


def config_for(
    *,
    top_k: int = 8,
    knowledge_path: Path = Path("knowledge/epoxy-2025.1"),
) -> AppConfig:
    return AppConfig(
        model=ModelConfig(
            base_url="http://127.0.0.1:8000/v1/private?token=hidden",
            model="local-model",
            api_key_env="REQMAP_API_KEY",
            api_key="top-secret",
            seed=7,
        ),
        knowledge_path=knowledge_path,
        input_profile=None,
        top_k=top_k,
    )


def request_for(tmp_path: Path, count: int = 1) -> AnalysisRequest:
    texts = (
        "Провести инструктаж персонала альфа",
        "Провести инструктаж персонала бета",
    )
    requirements = tuple(
        replace(
            requirement(ordinal=index, requirement_id=f"REQ-{index:04d}"),
            text=texts[index - 1],
            source_id=f"source-{index}",
        )
        for index in range(1, count + 1)
    )
    return AnalysisRequest(
        requirements=requirements,
        input_sha256="a" * 64,
        input_kind="text",
        source_path=None,
        output_dir=tmp_path / "result",
    )


def test_preflight_validates_snapshot_and_model() -> None:
    model = FakeModel()

    result = preflight(config_for(), model)

    assert result.ok is True
    assert result.knowledge_sha256 is not None
    assert len(result.knowledge_sha256) == 64
    assert model.preflight_calls == 1
    assert model.calls == []


def test_pipeline_processes_remaining_requirements_after_model_failure(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (
            valid_decomposition("Провести инструктаж персонала альфа"),
            invalid_mapping(),
            invalid_mapping(),
            valid_decomposition("Провести инструктаж персонала бета"),
            not_applicable_mapping(),
        )
    )

    run = analyze(request_for(tmp_path, count=2), config_for(), model)

    assert run.requirements[0].analysis_state is AnalysisState.MODEL_FAILED
    assert run.requirements[0].support_status is None
    assert run.requirements[1].analysis_state is AnalysisState.COMPLETED
    assert run.requirements[1].support_status is SupportStatus.NOT_APPLICABLE
    assert run.run_status == "PARTIAL"
    assert run.metadata["endpoint_origin"] == "http://127.0.0.1:8000"
    assert "top-secret" not in json.dumps(
        run.metadata,
        ensure_ascii=False,
    )
    assert [stage for stage, _payload in model.calls] == [
        "decomposition",
        "mapping",
        "mapping",
        "decomposition",
        "mapping",
    ]


def test_subject_model_error_is_redacted_before_entering_run_result(
    tmp_path: Path,
) -> None:
    secret_error = ModelError(
        "MODEL_DOWN",
        (
            "Сбой http://127.0.0.1:8000/v1/private?token=hidden "
            "для ключа top-secret."
        ),
    )
    model = FakeModel(
        (
            valid_decomposition("Провести инструктаж персонала альфа"),
            secret_error,
        )
    )

    run = analyze(request_for(tmp_path), config_for(), model)

    assert run.requirements[0].analysis_state is AnalysisState.MODEL_FAILED
    assert [stage for stage, _payload in model.calls] == [
        "decomposition",
        "mapping",
    ]
    serialized = json.dumps(
        {
            "run": run.diagnostics,
            "requirement": run.requirements[0].diagnostics,
        },
        ensure_ascii=False,
    )
    assert "top-secret" not in serialized
    assert "/v1/private" not in serialized
    assert "token=hidden" not in serialized


def test_decomposition_model_error_is_redacted_before_run_result(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (
            ModelError(
                "MODEL_DOWN",
                (
                    "Сбой http://127.0.0.1:8000/v1/private?token=hidden "
                    "для ключа top-secret."
                ),
            ),
        )
    )

    run = analyze(request_for(tmp_path), config_for(), model)

    assert run.requirements[0].analysis_state is AnalysisState.MODEL_FAILED
    assert [stage for stage, _payload in model.calls] == ["decomposition"]
    serialized = json.dumps(
        {
            "run": run.diagnostics,
            "requirement": run.requirements[0].diagnostics,
        },
        ensure_ascii=False,
    )
    assert "top-secret" not in serialized
    assert "/v1/private" not in serialized
    assert "token=hidden" not in serialized


def test_preflight_failure_skips_subject_calls_and_writes_only_diagnostics(
    tmp_path: Path,
) -> None:
    error = ModelError(
        "MODEL_DOWN",
        (
            "Сбой http://127.0.0.1:8000/v1/private?token=hidden "
            "для ключа top-secret."
        ),
    )
    model = FakeModel(preflight_error=error)
    request = request_for(tmp_path, count=2)

    run = analyze(request, config_for(), model)

    assert run.run_status == "FAILED"
    assert model.preflight_calls == 1
    assert model.calls == []
    assert all(
        item.analysis_state is AnalysisState.SKIPPED
        and item.support_status is None
        and item.atom_results == ()
        and item.mappings == ()
        for item in run.requirements
    )
    assert sorted(path.name for path in request.output_dir.iterdir()) == [
        "manifest.json",
        "run.jsonl",
    ]
    for forbidden in ("result.json", "result.xlsx", "report.md"):
        assert not (request.output_dir / forbidden).exists()

    manifest = json.loads(
        (request.output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["run_status"] == "FAILED"
    assert [item["analysis_state"] for item in manifest["requirements"]] == [
        "skipped",
        "skipped",
    ]
    serialized = "".join(
        path.read_text(encoding="utf-8")
        for path in (
            request.output_dir / "manifest.json",
            request.output_dir / "run.jsonl",
        )
    )
    assert "top-secret" not in serialized
    assert "/v1/private" not in serialized
    assert "token=hidden" not in serialized
    assert "top-secret" not in " ".join(run.diagnostics)


def test_completed_requirement_resumes_without_subject_model_call(
    tmp_path: Path,
) -> None:
    request = request_for(tmp_path)
    source_text = "Создать виртуальную машину через API"
    source = replace(request.requirements[0], text=source_text)
    request = replace(request, requirements=(source,))
    first_model = FakeModel(
        (
            valid_decomposition(source_text),
            supported_nova_mapping(),
        )
    )
    first = analyze(request, config_for(), first_model)

    resumed_model = FakeModel()
    resumed = analyze(request, config_for(), resumed_model)

    assert first.requirements == resumed.requirements
    assert resumed.run_status == "SUCCESS"
    assert [item.evidence_id for item in resumed.evidence] == [
        "E-NOVA-SCOPE-001"
    ]
    assert resumed_model.preflight_calls == 1
    assert resumed_model.calls == []
    checkpoints = tuple((request.output_dir / ".work").glob("*/REQ-0001.json"))
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["run_signature"] == checkpoint.parent.name
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert stat.S_IMODE(checkpoint.parent.stat().st_mode) == 0o700


def test_resume_signature_changes_with_significant_parameter(tmp_path: Path) -> None:
    request = request_for(tmp_path)
    first_model = FakeModel(
        (
            valid_decomposition("Провести инструктаж персонала альфа"),
            not_applicable_mapping(),
        )
    )
    analyze(request, config_for(top_k=8), first_model)
    changed_model = FakeModel(
        (
            valid_decomposition("Провести инструктаж персонала альфа"),
            not_applicable_mapping(),
        )
    )

    analyze(request, config_for(top_k=9), changed_model)

    assert len(changed_model.calls) == 2
    signatures = tuple(path for path in (request.output_dir / ".work").iterdir())
    assert len(signatures) == 2


@pytest.mark.parametrize("forged_component", ["unknown", "neutron"])
def test_resume_rejects_forged_mapping_component_against_knowledge(
    tmp_path: Path,
    forged_component: str,
) -> None:
    request = request_for(tmp_path)
    source_text = "Создать виртуальную машину через API"
    request = replace(
        request,
        requirements=(replace(request.requirements[0], text=source_text),),
    )
    first_model = FakeModel(
        (valid_decomposition(source_text), supported_nova_mapping())
    )
    analyze(request, config_for(), first_model)
    checkpoint = next(
        (request.output_dir / ".work").glob("*/REQ-0001.json")
    )
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    raw_result = payload["requirement"]
    raw_result["atom_results"][0]["mappings"][0][
        "component_id"
    ] = forged_component
    raw_result["mappings"][0]["component_id"] = forged_component
    checkpoint.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    recompute_model = FakeModel(
        (valid_decomposition(source_text), supported_nova_mapping())
    )
    resumed = analyze(request, config_for(), recompute_model)

    assert len(recompute_model.calls) == 2
    assert resumed.requirements[0].mappings[0].component_id == "nova"


def test_pipeline_rejects_output_symlink_before_model_preflight(
    tmp_path: Path,
) -> None:
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    output_link = tmp_path / "linked-output"
    output_link.symlink_to(real_output, target_is_directory=True)
    request = replace(request_for(tmp_path), output_dir=output_link)
    model = FakeModel()

    run = analyze(request, config_for(), model)

    assert run.run_status == "FAILED"
    assert model.preflight_calls == 0
    assert model.calls == []
    assert tuple(real_output.iterdir()) == ()


def test_pipeline_rejects_symlink_in_output_path_ancestor(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    request = replace(
        request_for(tmp_path),
        output_dir=linked_parent / "nested-output",
    )
    model = FakeModel()

    run = analyze(request, config_for(), model)

    assert run.run_status == "FAILED"
    assert model.preflight_calls == 0
    assert model.calls == []
    assert tuple(real_parent.iterdir()) == ()


def test_pipeline_rejects_source_xlsx_at_result_xlsx_path(
    tmp_path: Path,
) -> None:
    request = request_for(tmp_path)
    request.output_dir.mkdir()
    source = request.output_dir / "result.xlsx"
    source.write_bytes(b"source")
    request = replace(request, input_kind="xlsx", source_path=source)
    model = FakeModel()

    run = analyze(request, config_for(), model)

    assert run.run_status == "FAILED"
    assert model.preflight_calls == 0
    assert model.calls == []
    assert source.read_bytes() == b"source"


def test_run_status_is_independent_from_subject_support() -> None:
    completed = RequirementResult(
        requirement=requirement(),
        analysis_state=AnalysisState.COMPLETED,
        support_status=SupportStatus.NOT_SUPPORTED,
        atom_results=(
            AtomResult(
                atom=atom(),
                analysis_state=AnalysisState.COMPLETED,
                support_status=SupportStatus.NOT_SUPPORTED,
                mappings=(),
            ),
        ),
        mappings=(),
    )
    skipped = replace(
        completed,
        analysis_state=AnalysisState.SKIPPED,
        support_status=None,
        atom_results=(),
    )

    assert run_status((completed,), preflight_ok=True) == "SUCCESS"
    assert run_status((completed, skipped), preflight_ok=True) == "PARTIAL"
    assert run_status((skipped,), preflight_ok=True) == "FAILED"
    assert run_status((completed,), preflight_ok=False) == "FAILED"
    assert run_status((), preflight_ok=True) == "FAILED"
