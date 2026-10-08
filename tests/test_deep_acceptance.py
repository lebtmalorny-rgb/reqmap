"""Public CLI acceptance over an approved signed synthetic snapshot and HTTP LLM."""

from pathlib import Path
import json


def test_deep_acceptance_is_traceable_cross_artifact_and_offline(tmp_path) -> None:
    assert Path("tests/fixtures/deep_gold.json").is_file(), "deep acceptance fixtures missing"
    from tests.deep_acceptance_support import run_gold, assert_artifacts
    cases, result, output, requests = run_gold(tmp_path)
    assert result["schema_version"] == "2.2"
    assert result["run_status"] == "PARTIAL"
    assert_artifacts(output, result)
    assert len(result["requirements"]) == len(cases)
    assert {method for method, _path, _body in requests} == {"GET"}
    assert all((method, path) in {("GET", "/v1/models"), ("POST", "/v1/chat/completions")} for method, path, _body in requests)
    assert requests[0][:2] == ("GET", "/v1/models")
    assert result["responsibility_records"] == []
    assert result["procedure_graphs"] == []
    for row in result["requirements"]:
        assert row["support_status"] == "insufficient_evidence"
        assert row["source_binding"]["unresolved_fragments"]
        assert row["atom_results"][0]["atom"]["source_quote"] == row["requirement"]["text"]


def test_deep_resume_reuses_only_verified_completed_results(tmp_path):
    from reqmap.cli import main
    from tests.deep_acceptance_support import gold_snapshot, model_server, cli_arguments, assert_artifacts
    root, signers = gold_snapshot(tmp_path)
    with model_server() as server:
        args = cli_arguments(tmp_path, root, signers, server, [
            "Создание сервера через Nova API.", "Применить sysctl через Kolla-Ansible.",
        ])
        assert main(args) == 4
        first = json.loads((tmp_path / "out/result.json").read_text())
        count = len(server.requests)
        assert main(args) == 4
        assert [request[:2] for request in server.requests[count:]] == [("GET", "/v1/models")]
        second = json.loads((tmp_path / "out/result.json").read_text())
        assert second == first
        assert_artifacts(tmp_path / "out", second)


def test_every_signed_runtime_file_is_checked_before_model_access(tmp_path):
    from reqmap.cli import main
    from tests.deep_acceptance_support import gold_snapshot, model_server, cli_arguments
    root, signers = gold_snapshot(tmp_path)
    manifest = json.loads((root / "snapshot-manifest.json").read_text())
    with model_server() as server:
        args = cli_arguments(tmp_path, root, signers, server, ["Создание сервера через Nova API."])
        for entry in manifest["files"]:
            path = root / entry["path"]
            original = path.read_bytes()
            for mode in ("modified", "missing"):
                if mode == "modified":
                    path.write_bytes(original + b"\n")
                else:
                    path.unlink()
                try:
                    assert main(args) == 3, (entry["path"], mode)
                    assert server.requests == [], (entry["path"], mode)
                    assert {p.name for p in (tmp_path / "out").iterdir() if p.is_file()} == {"run.jsonl", "manifest.json"}
                    diagnostic = (tmp_path / "out/run.jsonl").read_text()
                    assert "SNAPSHOT_" in diagnostic
                finally:
                    path.write_bytes(original)
