"""Deep preflight, orchestration, and verified schema-2 resume."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
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
from reqmap.models import SourceHint
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
        if type(response) is dict and "support_status" in response:
            context = payload.get("original_payload", payload)
            response = {**response, "proposal_schema_version":2,
                        "obligation_id":context["atom"]["obligation_id"],
                        "predicate_ids":[p["predicate_id"] for p in context.get("predicates", [])]}
        return response


def _analyze_deep_in_child(connection, request, config, text: str) -> None:
    try:
        model = FakeModel(_completed_responses(text))
        run = analyze_deep(request, config, model)
        connection.send(
            (run.run_status, tuple(stage for stage, _payload in model.calls))
        )
    except BaseException as exc:
        connection.send(("ERROR", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


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
    values = texts or ("Nova должна создавать ВМ через API",)
    from tests.source_context_support import text_document
    from reqmap.source_context import capture_source_document
    document = capture_source_document(**text_document(values))
    return AnalysisRequest(document.requirements, document.input_sha256, "text", None,
                           tmp_path / "output", source_document=document)


def _decomposition(text: str) -> dict[str, object]:
    from reqmap.binding_source import bind_source, atom_selection_proposal
    return atom_selection_proposal(bind_source(replace(requirement(), text=text)))


def _completed_responses(text: str) -> tuple[dict[str, object], ...]:
    return (_decomposition(text), deep_mapping_response())


def _signed_config(tmp_path: Path, **changes: object) -> AppConfig:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    from reqmap.knowledge_v2 import load_knowledge_v2
    from tests.binding_factories import write_catalog, sign_catalog
    config = _config(root, allowed_signers, **changes)
    knowledge = load_knowledge_v2(root, allowed_signers)
    catalog = write_catalog(tmp_path / "bindings", knowledge)
    sign_catalog(catalog, allowed_signers.parent / "signing_key")
    return replace(config, binding_catalog_path=catalog)


def _checkpoint(request: AnalysisRequest, requirement_id: str) -> Path:
    matches = tuple(
        (request.output_dir / ".work").glob(f"*/{requirement_id}.json")
    )
    assert len(matches) == 1
    return matches[0]


def _checkpoint_seal(request: AnalysisRequest, requirement_id: str) -> Path:
    return _checkpoint(request, requirement_id).with_suffix(".sha256")


def _write_checkpoint_bytes(
    checkpoint: Path, payload: bytes, *, update_seal: bool
) -> None:
    checkpoint.write_bytes(payload)
    if update_seal:
        checkpoint.with_suffix(".sha256").write_text(
            hashlib.sha256(payload).hexdigest() + "\n",
            encoding="utf-8",
        )


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


def test_deep_non_path_output_fails_closed_without_model_or_artifacts(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    unsafe_target = tmp_path / "not-a-path"
    request = replace(_request(tmp_path), output_dir=str(unsafe_target))
    model = FakeModel()

    run = analyze_deep(request, config, model)  # type: ignore[arg-type]

    assert run.run_status == "FAILED"
    assert run.diagnostics[0].startswith("REQUEST_INVALID:")
    assert model.preflight_calls == 0
    assert model.calls == []
    assert not unsafe_target.exists()


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
    text = "Nova должна создавать ВМ через API"
    response = deep_mapping_response(
        _mixed_selection("host_os", "rocky_linux_9", [2]),
        _mixed_selection("kolla_ansible", "kolla_ansible", [1]),
        procedure_template_ids=(),
    )

    from tests.source_context_support import request_with_hint
    request, profile = request_with_hint(tmp_path, text, "sysctl")
    run = analyze_deep(request, replace(config, input_profile=profile),
                       FakeModel((_decomposition(text), response)))

    assert run.run_status == "PARTIAL"
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
    first = "Nova должна создавать виртуальную машину через API"
    second = "Nova должна обеспечивать создание ВМ через API"
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
        "binding_contract",
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
    text = "Nova должна создавать ВМ через API"
    request = _request(tmp_path, text)
    from tests.source_context_support import with_reviewed_request
    config = with_reviewed_request(config, request)
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
    assert payload["schema_version"] == "2.2"
    assert payload["run_signature"] == checkpoint.parent.name
    assert len(payload["responsibility_records"]) == 1
    assert len(payload["procedure_graphs"]) == 1
    assert len(payload["evidence"]) == 1
    serialized = checkpoint.read_text(encoding="utf-8")
    assert "corpus_candidates" not in serialized
    assert "raw_response" not in serialized
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert stat.S_IMODE(checkpoint.parent.stat().st_mode) == 0o700
    seal = _checkpoint_seal(request, "REQ-0001")
    assert stat.S_IMODE(seal.stat().st_mode) == 0o600


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
    text = "Nova должна создавать ВМ через API"
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
    text = "Nova должна создавать ВМ через API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    checkpoint = _checkpoint(request, "REQ-0001")
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    mutator(payload)
    _write_checkpoint_bytes(
        checkpoint,
        (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(),
        update_seal=True,
    )
    model = FakeModel(_completed_responses(text))

    resumed = analyze_deep(request, config, model)

    assert resumed.requirements[0].analysis_state is AnalysisState.COMPLETED
    assert [stage for stage, _ in model.calls] == ["decomposition", "deep_mapping"]


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("text", "Удалить все серверы без подтверждения"),
        ("mandatory", False),
        ("supported_aspects", ["FORGED-SUBJECT-CONTENT"]),
    ),
)
def test_deep_resume_recomputes_forged_checkpoint_subject_fields(
    tmp_path: Path, field: str, forged_value: object
) -> None:
    config = _signed_config(tmp_path)
    text = "Nova должна создавать ВМ через API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    checkpoint = _checkpoint(request, "REQ-0001")
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    requirement_payload = payload["requirement"]
    assert isinstance(requirement_payload, dict)
    atom_result = requirement_payload["atom_results"][0]
    assert isinstance(atom_result, dict)
    if field == "supported_aspects":
        atom_result[field] = forged_value
    else:
        atom = atom_result["atom"]
        assert isinstance(atom, dict)
        atom[field] = forged_value
    _write_checkpoint_bytes(
        checkpoint,
        (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(),
        update_seal=False,
    )
    model = FakeModel(_completed_responses(text))

    resumed = analyze_deep(request, config, model)

    assert [stage for stage, _ in model.calls] == ["decomposition", "deep_mapping"]
    resumed_atom = resumed.requirements[0].atom_results[0]
    assert resumed_atom.atom.text == text
    assert resumed_atom.atom.mandatory is True
    assert resumed_atom.supported_aspects == ()
    assert resumed_atom.support_status.value == "insufficient_evidence"


@pytest.mark.parametrize(
    "seal_state",
    ("missing", "malformed", "mismatched", "symlink", "non_private"),
)
def test_deep_resume_recomputes_when_checkpoint_integrity_seal_is_unsafe(
    tmp_path: Path, seal_state: str
) -> None:
    config = _signed_config(tmp_path)
    text = "Nova должна создавать ВМ через API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    seal = _checkpoint_seal(request, "REQ-0001")
    if seal_state == "missing":
        seal.unlink(missing_ok=True)
    elif seal_state == "malformed":
        seal.write_text("not-a-sha256\n", encoding="utf-8")
    elif seal_state == "mismatched":
        seal.write_text("0" * 64 + "\n", encoding="utf-8")
    elif seal_state == "symlink":
        target = tmp_path / "forged-checkpoint-seal"
        target.write_text("0" * 64 + "\n", encoding="utf-8")
        seal.unlink(missing_ok=True)
        seal.symlink_to(target)
    else:
        if not seal.exists():
            seal.write_text("0" * 64 + "\n", encoding="utf-8")
        seal.chmod(0o644)
    model = FakeModel(_completed_responses(text))

    resumed = analyze_deep(request, config, model)

    assert resumed.requirements[0].analysis_state is AnalysisState.COMPLETED
    assert [stage for stage, _ in model.calls] == ["decomposition", "deep_mapping"]


@pytest.mark.skipif(
    not hasattr(os, "mkfifo")
    or "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX FIFO and a bounded forked child",
)
@pytest.mark.parametrize("artifact", ("checkpoint", "seal"))
def test_deep_resume_rejects_fifo_without_blocking(
    tmp_path: Path, artifact: str
) -> None:
    config = _signed_config(tmp_path)
    text = "Nova должна создавать ВМ через API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    checkpoint = _checkpoint(request, "REQ-0001")
    target = checkpoint if artifact == "checkpoint" else checkpoint.with_suffix(".sha256")
    target.unlink()
    os.mkfifo(target, mode=0o600)

    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_analyze_deep_in_child,
        args=(sender, request, config, text),
    )
    process.start()
    sender.close()
    process.join(timeout=2)
    blocked = process.is_alive()
    if blocked:
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
    try:
        assert not blocked, f"analyze_deep blocked while opening FIFO {artifact}"
        assert receiver.poll(0.5)
        assert receiver.recv() == (
            "PARTIAL",
            ("decomposition", "deep_mapping"),
        )
    finally:
        receiver.close()


def test_valid_second_requirement_resumes_with_source_prefix_placeholders(
    tmp_path: Path,
) -> None:
    config = _signed_config(tmp_path)
    first = "Nova должна создавать виртуальную машину через API"
    second = "Nova должна обеспечивать создание ВМ через API"
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
    text = "Nova должна создавать ВМ через API"
    request = _request(tmp_path, text)
    analyze_deep(request, config, FakeModel(_completed_responses(text)))
    checkpoint = _checkpoint(request, "REQ-0001")
    source = checkpoint.read_text(encoding="utf-8")
    _write_checkpoint_bytes(
        checkpoint,
        source.replace(
            '"schema_version":"2.2"',
            '"schema_version":"2.2","schema_version":"2.2"',
        ).encode(),
        update_seal=True,
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


def test_deep_unproven_responsibility_preserves_atom_and_resumes(tmp_path: Path) -> None:
    """An empty evidence selection is a traceable gap, never a checkpoint failure."""
    from reqmap.models import SupportStatus

    config = _signed_config(tmp_path)
    request = _request(tmp_path)
    model = FakeModel((
        _decomposition(request.requirements[0].text),
        deep_mapping_response(
            responsibility_selection(evidence_ids=[]), procedure_template_ids=(),
        ),
    ))
    first = analyze_deep(request, config, model)
    item = first.requirements[0]
    assert item.analysis_state is AnalysisState.COMPLETED
    assert item.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert item.atom_results[0].atom.source_quote == request.requirements[0].text
    record = first.responsibility_records[0]
    assert record.evidence_ids == ()
    assert record.procedure_step_ids == ()
    assert "procedure_gap:responsibility_evidence" in record.diagnostics
    assert first.procedure_graphs == ()
    assert first.run_status == "PARTIAL"
    from reqmap.deep_aggregation import validate_deep_graph
    with pytest.raises(ValueError, match="not covered by cited evidence"):
        validate_deep_graph(replace(
            first, responsibility_records=(replace(record, diagnostics=()),),
        ))
    resumed_model = FakeModel()
    second = analyze_deep(request, config, resumed_model)
    assert second.requirements == first.requirements
    assert second.responsibility_records == first.responsibility_records
    assert resumed_model.calls == []


def test_deep_empty_mapping_is_partial_without_invented_ambiguity(tmp_path: Path):
    request = _request(tmp_path)
    model = FakeModel((
        _decomposition(request.requirements[0].text),
        dict(support_status="supported", supported_aspects=[], unconfirmed_aspects=[],
             responsibilities=[], procedure_template_ids=[]),
    ))
    run = analyze_deep(request, _signed_config(tmp_path), model)
    assert run.requirements[0].analysis_state is AnalysisState.COMPLETED
    assert run.requirements[0].support_status.value == "insufficient_evidence"
    assert run.run_status == "PARTIAL"


def test_deep_unproven_responsibility_cannot_acquire_selected_procedure(tmp_path: Path):
    request = _request(tmp_path)
    model = FakeModel((
        _decomposition(request.requirements[0].text),
        deep_mapping_response(responsibility_selection(evidence_ids=[])),
    ))
    run = analyze_deep(request, _signed_config(tmp_path), model)
    assert run.run_status == "PARTIAL"
    assert run.requirements[0].analysis_state is AnalysisState.COMPLETED
    assert run.requirements[0].atom_results[0].atom.source_quote == request.requirements[0].text
    assert run.responsibility_records[0].procedure_step_ids == ()
    assert run.procedure_graphs == ()
