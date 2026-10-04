"""CLI dispatch, deep publication, and knowledge-v2 maintenance contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from reqmap import publication

import reqmap.cli as cli
import reqmap.manifest as manifest_module
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from reqmap.crosscheck import CrosscheckIssue
from reqmap.deep_aggregation import aggregate_deep_groups
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.manifest import write_deep_preflight_artifacts
from reqmap.models import AnalysisState
from tests.deep_factories import signed_v2_snapshot
from tests.test_export_deep_json import deep_run
from tests.test_pipeline import config_for


def _run_cli_in_fork(arguments: list[str], results) -> None:
    results.put(cli.main(arguments))


def _arguments(output: Path) -> list[str]:
    return [
        "analyze",
        "--requirement",
        "Создать VM",
        "--config",
        "deep-config.json",
        "--output",
        str(output),
    ]


def _install_deep_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    run,
    *,
    preflight_pair: bool = False,
) -> SimpleNamespace:
    captured = SimpleNamespace(request=None, config=None, model=None)
    config = replace(
        config_for(),
        analysis_profile=AnalysisProfile.DEEP,
        knowledge_trust=KnowledgeTrustConfig(Path("trusted-signers")),
    )
    monkeypatch.setattr(cli, "load_config", lambda *_args: config)
    monkeypatch.setattr(
        cli,
        "OpenAICompatibleClient",
        lambda model_config: SimpleNamespace(model_config=model_config),
    )

    def fake_analyze_deep(request, actual_config, model):
        captured.request = request
        captured.config = actual_config
        captured.model = model
        if preflight_pair:
            write_deep_preflight_artifacts(run, request.output_dir)
        return run

    monkeypatch.setattr(cli, "analyze_deep", fake_analyze_deep, raising=False)
    monkeypatch.setattr(
        cli,
        "analyze",
        lambda *_args: pytest.fail("legacy pipeline received a deep profile"),
    )
    return captured


def _partial_run():
    run = deep_run()
    diagnostic = "procedure_gap: обязательный verify-шаг не подтверждён"
    requirement = replace(run.requirements[0], diagnostics=(diagnostic,))
    record = replace(
        run.responsibility_records[0],
        diagnostics=("evidence_conflict: применимые источники противоречат",),
    )
    graph = replace(run.procedure_graphs[0], diagnostics=(diagnostic,))
    return replace(
        run,
        run_status="PARTIAL",
        requirements=(requirement,),
        groups=aggregate_deep_groups((requirement,), (record,)),
        responsibility_records=(record,),
        procedure_graphs=(graph,),
        diagnostics=(),
    )


def _failed_preflight_run():
    run = deep_run()
    skipped = replace(
        run.requirements[0],
        requirement=cli._requirements_from_arguments(("Создать VM",))[0],
        analysis_state=AnalysisState.SKIPPED,
        support_status=None,
        atom_results=(),
        responsibility_ids=(),
        procedure_graph_ids=(),
        diagnostics=("SKIPPED: deep preflight failed",),
    )
    return replace(
        run,
        run_status="FAILED",
        requirements=(skipped,),
        groups=aggregate_deep_groups((skipped,), ()),
        responsibility_records=(),
        procedure_graphs=(),
        evidence=(),
        diagnostics=("SNAPSHOT_UNTRUSTED: подпись не прошла проверку",),
    )


def _failed_subject_run():
    run = deep_run()
    atom = replace(
        run.requirements[0].atom_results[0],
        analysis_state=AnalysisState.MODEL_FAILED,
        support_status=None,
        responsibility_ids=(),
        diagnostics=("MODEL_FAILED: предметный анализ не завершён",),
    )
    requirement = replace(
        run.requirements[0],
        analysis_state=AnalysisState.MODEL_FAILED,
        support_status=None,
        atom_results=(atom,),
        responsibility_ids=(),
        procedure_graph_ids=(),
        diagnostics=atom.diagnostics,
    )
    return replace(
        run,
        run_status="FAILED",
        requirements=(requirement,),
        groups=aggregate_deep_groups((requirement,), ()),
        responsibility_records=(),
        procedure_graphs=(),
        evidence=(),
        diagnostics=atom.diagnostics,
    )


def _artifact_names(output: Path) -> set[str]:
    return {path.name for path in output.iterdir() if path.is_file()}


def test_cli_dispatches_deep_profile_without_legacy_exporters_and_hashes_exact_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured = _install_deep_pipeline(monkeypatch, deep_run())
    for name in ("write_canonical_json", "write_xlsx", "write_markdown", "crosscheck"):
        monkeypatch.setattr(
            publication,
            name,
            lambda *_args, _name=name: pytest.fail(
                f"legacy publisher {_name} received DeepRunResult"
            ),
        )
    output = tmp_path / "deep"

    assert cli.main(_arguments(output)) == 0

    assert captured.request.requirements[0].text == "Создать VM"
    assert captured.config.analysis_profile is AnalysisProfile.DEEP
    assert _artifact_names(output) == {
        "manifest.json",
        "report.md",
        "result.json",
        "result.xlsx",
        "run.jsonl",
    }
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert "manifest.json" not in manifest["artifact_hashes"]
    assert manifest["artifact_hashes"] == {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in ("report.md", "result.json", "result.xlsx", "run.jsonl")
    }
    events = [json.loads(line)["event"] for line in (output / "run.jsonl").read_text().splitlines()]
    assert events == ["analysis_finished", "artifacts_verified"]
    terminal = capsys.readouterr()
    assert "Профиль анализа: deep" in terminal.out
    assert "Статус запуска: SUCCESS" in terminal.out
    assert terminal.out.count("SHA-256") == 5


@pytest.mark.parametrize("mutation", ("replace", "append", "overwrite"))
def test_cli_deep_rejects_log_mutation_during_final_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    _install_deep_pipeline(monkeypatch, deep_run())
    output = tmp_path / f"mutated-log-{mutation}"
    forged_payload = (
        b'{"event":"forged","source_url":"https://attacker.invalid/private"}\n'
    )
    forged_prefix = b"FORGED0000"
    real_write = manifest_module.os.write
    substituted = False
    overwritten_size = 0

    def substitute_after_final_write(descriptor: int, payload) -> int:
        nonlocal substituted, overwritten_size
        written = real_write(descriptor, payload)
        if not substituted and b'"event":"artifacts_verified"' in bytes(payload):
            substituted = True
            if mutation == "replace":
                replacement = output / "forged.jsonl"
                replacement.write_bytes(forged_payload)
                replacement.replace(output / "run.jsonl")
            elif mutation == "append":
                with (output / "run.jsonl").open("ab") as stream:
                    stream.write(forged_payload)
            else:
                attack_fd = os.open(output / "run.jsonl", os.O_WRONLY)
                try:
                    overwritten_size = os.fstat(attack_fd).st_size
                    assert os.pwrite(attack_fd, forged_prefix, 0) == len(
                        forged_prefix
                    )
                finally:
                    os.close(attack_fd)
        return written

    monkeypatch.setattr(manifest_module.os, "write", substitute_after_final_write)

    assert cli.main(_arguments(output)) == 5
    assert substituted
    published_log = (output / "run.jsonl").read_bytes()
    if mutation == "replace":
        assert published_log == forged_payload
    elif mutation == "append":
        assert published_log.endswith(forged_payload)
        assert b'"event":"artifacts_verified"' in published_log
    else:
        assert published_log.startswith(forged_prefix)
        assert len(published_log) == overwritten_size
    assert not (output / "manifest.json").exists()
    terminal = capsys.readouterr()
    assert "run.jsonl SHA-256" not in terminal.out
    assert hashlib.sha256(published_log).hexdigest() not in terminal.out
    assert "attacker.invalid" not in terminal.out + terminal.err


def test_cli_deep_does_not_announce_log_substituted_during_failure_recording(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_deep_pipeline(monkeypatch, deep_run())
    monkeypatch.setattr(
        publication,
        "write_deep_xlsx",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    output = tmp_path / "substituted-failure-log"
    forged_payload = (
        b'{"event":"forged","source_url":"https://attacker.invalid/private"}\n'
    )
    forged_digest = hashlib.sha256(forged_payload).hexdigest()
    real_write = manifest_module.os.write
    substituted = False

    def substitute_after_failure_write(descriptor: int, payload) -> int:
        nonlocal substituted
        written = real_write(descriptor, payload)
        if not substituted and b'"event":"export_failed"' in bytes(payload):
            substituted = True
            replacement = output / "forged.jsonl"
            replacement.write_bytes(forged_payload)
            replacement.replace(output / "run.jsonl")
        return written

    monkeypatch.setattr(manifest_module.os, "write", substitute_after_failure_write)

    assert cli.main(_arguments(output)) == 5
    assert substituted
    assert (output / "run.jsonl").read_bytes() == forged_payload
    assert not (output / "manifest.json").exists()
    terminal = capsys.readouterr()
    assert "run.jsonl SHA-256" not in terminal.out
    assert forged_digest not in terminal.out
    assert "attacker.invalid" not in terminal.out + terminal.err


def test_cli_deep_rejects_prefix_overwrite_between_logger_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_deep_pipeline(monkeypatch, deep_run())
    output = tmp_path / "overwritten-between-writes"
    forged_prefix = b"FORGED0000"
    real_write_json = cli.write_deep_canonical_json
    attacked = False

    def write_json_then_overwrite_prefix(run, path: Path) -> str:
        nonlocal attacked
        digest = real_write_json(run, path)
        before = (output / "run.jsonl").stat()
        attack_fd = os.open(output / "run.jsonl", os.O_WRONLY)
        try:
            assert os.pwrite(attack_fd, forged_prefix, 0) == len(forged_prefix)
        finally:
            os.close(attack_fd)
        after = (output / "run.jsonl").stat()
        assert (after.st_dev, after.st_ino, after.st_size) == (
            before.st_dev,
            before.st_ino,
            before.st_size,
        )
        attacked = True
        return digest

    monkeypatch.setattr(
        publication,
        "write_deep_canonical_json",
        write_json_then_overwrite_prefix,
    )

    assert cli.main(_arguments(output)) == 5
    assert attacked
    published_log = (output / "run.jsonl").read_bytes()
    assert published_log.startswith(forged_prefix)
    assert b'"event":"artifacts_verified"' not in published_log
    assert not (output / "manifest.json").exists()
    terminal = capsys.readouterr()
    assert "run.jsonl SHA-256" not in terminal.out
    assert hashlib.sha256(published_log).hexdigest() not in terminal.out


def test_cli_deep_partial_reports_normalized_gap_and_conflict_occurrences(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_deep_pipeline(monkeypatch, _partial_run())

    assert cli.main(_arguments(tmp_path / "partial")) == 4

    terminal = capsys.readouterr().out
    assert "Статус запуска: PARTIAL" in terminal
    assert "procedure_gap: 2" in terminal
    assert "evidence_conflict: 1" in terminal


def test_cli_deep_failed_preflight_reports_only_current_diagnostic_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _failed_preflight_run()
    _install_deep_pipeline(monkeypatch, run, preflight_pair=True)
    monkeypatch.setattr(
        cli,
        "_publish_deep_artifacts",
        lambda *_args: pytest.fail("failed preflight reached final exporters"),
        raising=False,
    )
    output = tmp_path / "preflight"

    assert cli.main(_arguments(output)) == 3

    assert _artifact_names(output) == {"manifest.json", "run.jsonl"}
    terminal = capsys.readouterr().out
    assert "Профиль анализа: deep" in terminal
    assert terminal.count("SHA-256") == 2


@pytest.mark.parametrize("mutation", ("groups", "requirement"))
def test_cli_rejects_malformed_same_class_failed_preflight_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    run = _failed_preflight_run()
    if mutation == "groups":
        malformed = replace(run, groups=deep_run().groups)
    else:
        requirement = replace(
            run.requirements[0],
            requirement=replace(
                run.requirements[0].requirement,
                text="Подменённый текст требования с тем же ID",
            ),
        )
        malformed = replace(run, requirements=(requirement,))
    _install_deep_pipeline(monkeypatch, malformed)
    output = tmp_path / mutation

    assert cli.main(_arguments(output)) == 6
    assert not output.exists()


@pytest.mark.parametrize(
    ("target", "field_path", "replacement"),
    [
        ("log", ("level",), "warning"),
        ("log", ("message_ru",), "Изменённое сообщение"),
        ("manifest", ("schema_version",), "9.0"),
        ("manifest", ("input_sha256",), "0" * 64),
        ("manifest", ("model",), "different-model"),
        ("manifest", ("release_profile", "host_profile"), "other_os"),
        ("manifest", ("snapshot_id",), "different-snapshot"),
        ("manifest", ("knowledge_trust", "key_id"), "different-key"),
        ("manifest", ("retry_counts", "decomposition"), 99),
        ("manifest", ("api_key",), "top-secret"),
    ],
)
def test_cli_deep_preflight_rejects_any_noncanonical_diagnostic_pair_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    target: str,
    field_path: tuple[str, ...],
    replacement: object,
) -> None:
    run = _failed_preflight_run()
    output = tmp_path / f"changed-{target}-{'-'.join(field_path)}"
    write_deep_preflight_artifacts(run, output)
    path = output / ("run.jsonl" if target == "log" else "manifest.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    current = payload
    for key in field_path[:-1]:
        current = current[key]
    current[field_path[-1]] = replacement
    path.write_bytes(canonical_json_bytes(payload))
    if target == "log":
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact_hashes"]["run.jsonl"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        manifest_path.write_bytes(canonical_json_bytes(manifest))
    _install_deep_pipeline(monkeypatch, run)

    assert cli.main(_arguments(output)) == 3
    assert "SHA-256" not in capsys.readouterr().out


def test_cli_deep_failed_preflight_does_not_report_stale_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "stale"
    prior = _failed_preflight_run()
    write_deep_preflight_artifacts(prior, output)
    unsafe = replace(
        prior,
        diagnostics=("OUTPUT_SYMLINK: output не прошёл безопасную проверку",),
    )
    _install_deep_pipeline(monkeypatch, unsafe)

    assert cli.main(_arguments(output)) == 3

    assert "SHA-256" not in capsys.readouterr().out


def test_cli_deep_subject_failure_publishes_final_artifacts_and_returns_six(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_deep_pipeline(monkeypatch, _failed_subject_run())
    output = tmp_path / "subject-failed"

    assert cli.main(_arguments(output)) == 6
    assert _artifact_names(output) == {
        "manifest.json",
        "report.md",
        "result.json",
        "result.xlsx",
        "run.jsonl",
    }


def test_cli_deep_crosscheck_failure_returns_five_and_records_closed_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_deep_pipeline(monkeypatch, deep_run())
    monkeypatch.setattr(
        publication,
        "crosscheck_deep",
        lambda *_args: (CrosscheckIssue("DEEP_TEST", "Синтетическое расхождение."),),
        raising=False,
    )
    output = tmp_path / "crosscheck"

    assert cli.main(_arguments(output)) == 5

    events = [json.loads(line)["event"] for line in (output / "run.jsonl").read_text().splitlines()]
    assert events[-1] == "crosscheck_failed"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_hashes"]["run.jsonl"] == hashlib.sha256(
        (output / "run.jsonl").read_bytes()
    ).hexdigest()


def test_cli_deep_export_failure_returns_five_and_manifests_only_safe_existing_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_deep_pipeline(monkeypatch, deep_run())
    monkeypatch.setattr(
        publication,
        "write_deep_xlsx",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("private endpoint detail")),
        raising=False,
    )
    output = tmp_path / "export-failed"

    assert cli.main(_arguments(output)) == 5

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["artifact_hashes"]) == {"result.json", "run.jsonl"}
    assert json.loads((output / "run.jsonl").read_text().splitlines()[-1])["event"] == "export_failed"
    serialized = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.iterdir()
        if path.suffix != ".xlsx"
    )
    assert "private endpoint detail" not in serialized


def test_cli_rejects_deep_pipeline_returning_legacy_result_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.test_json_manifest import completed_run

    _install_deep_pipeline(monkeypatch, completed_run())
    output = tmp_path / "mismatch"

    assert cli.main(_arguments(output)) == 6
    assert not output.exists()


def test_knowledge_validate_v1_and_signed_v2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([
        "knowledge", "validate", "--path", "tests/fixtures/kb_minimal"
    ]) == 0
    v1 = capsys.readouterr().out
    assert "schema=1" in v1 and "release=2025.1" in v1

    root, allowed_signers = signed_v2_snapshot(tmp_path)
    assert cli.main([
        "knowledge", "validate", "--path", str(root),
        "--allowed-signers", str(allowed_signers),
    ]) == 0
    v2 = capsys.readouterr().out
    assert "schema=2" in v2 and "release=2025.1" in v2
    assert "trust=verified" in v2
    assert str(root) not in v2
    assert str(allowed_signers) not in v2


def test_knowledge_validate_v2_requires_allowed_signers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = signed_v2_snapshot(tmp_path)

    assert cli.main(["knowledge", "validate", "--path", str(root)]) == 2
    assert "--allowed-signers" in capsys.readouterr().err


def test_knowledge_validate_never_falls_back_from_v2_trust_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "v2"
    root.mkdir()
    (root / "metadata.json").write_text(
        '{"knowledge_schema_version":2}\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        cli,
        "load_knowledge_v2",
        lambda *_args: (_ for _ in ()).throw(ReqmapError("TRUST", "trust failed")),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "load_knowledge",
        lambda *_args: pytest.fail("v2 trust failure fell back to v1"),
    )

    assert cli.main([
        "knowledge", "validate", "--path", str(root),
        "--allowed-signers", str(tmp_path / "trust"),
    ]) == 2


@pytest.mark.parametrize(
    "metadata",
    [
        b'{"knowledge_schema_version":2,"knowledge_schema_version":2}\n',
        b'[]\n',
        b'{"knowledge_schema_version":true}\n',
        b'{"knowledge_schema_version":"2"}\n',
        b'{"knowledge_schema_version":null}\n',
        b'{"openstack_release":NaN}\n',
        b'{"openstack_release":Infinity}\n',
        b'{"openstack_release":-Infinity}\n',
        b'{malformed}\n',
        b" " * (1024 * 1024 + 1),
    ],
)
def test_knowledge_validate_rejects_non_strict_or_oversized_metadata_before_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata: bytes,
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "metadata.json").write_bytes(metadata)
    monkeypatch.setattr(cli, "load_knowledge", lambda *_args: pytest.fail("v1 loader called"))
    monkeypatch.setattr(
        cli, "load_knowledge_v2", lambda *_args: pytest.fail("v2 loader called"), raising=False
    )

    assert cli.main(["knowledge", "validate", "--path", str(root)]) == 2


def test_knowledge_validate_rejects_metadata_symlink_traversal_before_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metadata.json").write_text('{}\n', encoding="utf-8")
    root = tmp_path / "linked"
    root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(cli, "load_knowledge", lambda *_args: pytest.fail("loader called"))

    assert cli.main(["knowledge", "validate", "--path", str(root)]) == 2


def test_knowledge_validate_rejects_metadata_replacement_during_strict_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    metadata = root / "metadata.json"
    metadata.write_text('{}\n', encoding="utf-8")
    replacement = root / "replacement.json"
    replacement.write_text('{}\n', encoding="utf-8")
    real_open = cli.os.open

    def replace_then_open(path, flags):
        replacement.replace(metadata)
        return real_open(path, flags)

    monkeypatch.setattr(cli.os, "open", replace_then_open)
    monkeypatch.setattr(cli, "load_knowledge", lambda *_args: pytest.fail("loader called"))

    assert cli.main(["knowledge", "validate", "--path", str(root)]) == 2


def test_knowledge_validate_rejects_same_inode_mutation_during_strict_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    metadata = root / "metadata.json"
    metadata.write_text('{"unused":1}\n', encoding="utf-8")
    inode = metadata.stat().st_ino
    real_read = cli.os.read
    mutated = False

    def mutate_then_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            metadata.write_text('{}\n', encoding="utf-8")
            assert metadata.stat().st_ino == inode
        return real_read(descriptor, size)

    monkeypatch.setattr(cli.os, "read", mutate_then_read)
    monkeypatch.setattr(cli, "load_knowledge", lambda *_args: pytest.fail("loader called"))
    monkeypatch.setattr(
        cli, "load_knowledge_v2", lambda *_args: pytest.fail("v2 loader called")
    )

    assert cli.main(["knowledge", "validate", "--path", str(root)]) == 2


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX FIFO and a bounded forked child",
)
def test_cli_deep_fifo_substitution_before_publication_returns_five_promptly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "fifo-output"
    output.mkdir()
    os.mkfifo(output / "run.jsonl", mode=0o600)
    _install_deep_pipeline(monkeypatch, deep_run())
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    process = context.Process(
        target=_run_cli_in_fork,
        args=(_arguments(output), results),
    )

    process.start()
    process.join(timeout=2)
    blocked = process.is_alive()
    if blocked:
        process.terminate()
        process.join(timeout=1)
    try:
        assert not blocked, "CLI blocked while opening substituted run.jsonl FIFO"
        assert results.get(timeout=1) == 5
    finally:
        results.close()
        process.close()


def test_knowledge_migrate_v1_creates_unsigned_draft_and_prints_safe_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "draft"

    assert cli.main([
        "knowledge", "migrate-v1",
        "--source", "tests/fixtures/kb_minimal",
        "--output", str(output),
    ]) == 0

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["snapshot_status"] == "draft"
    assert not (output / "snapshot-manifest.sig").exists()
    summary = capsys.readouterr().out
    assert "unsigned draft" in summary
    assert "components=" in summary and "inferred_actions=0" in summary


def test_knowledge_help_is_localized_and_exposes_no_private_key_signing_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["knowledge", "--help"]) == 0

    help_text = capsys.readouterr().out.lower()
    assert "использование:" in help_text
    assert "migrate-v1" in help_text
    assert "\n  sign " not in help_text
    assert "private-key" not in help_text
    assert cli.main(["knowledge", "sign", "--private-key", "secret"]) == 2
