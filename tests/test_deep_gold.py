"""Frozen expectations are independent of the scripted acceptance model."""

from pathlib import Path
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "tests/fixtures/deep_gold.json"
REQUIRED_IDS = {
    "GOLD-OPENSTACK-RUNTIME", "GOLD-KOLLA-RECONFIGURE", "GOLD-HOST-OS",
    "GOLD-MIXED-KOLLA-HOST", "GOLD-HA-PROCEDURE-GAP", "GOLD-UPGRADE-2025-TO-2026",
    "GOLD-AMBIGUOUS", "GOLD-INSUFFICIENT", "GOLD-CONFLICT",
    "GOLD-ADVERSARIAL-FALSE-SUPPORT",
    *(f"GOLD-MIGRATE-{kind}" for kind in (
        "VM", "VOLUME", "NETWORK", "IMAGE", "CONTROL-PLANE", "DATABASE"
    )),
}


def test_frozen_gold_covers_all_required_cases() -> None:
    assert GOLD.is_file(), "Task 16 frozen deep gold set is missing"
    cases = json.loads(GOLD.read_text(encoding="utf-8"))["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert REQUIRED_IDS <= {case["id"] for case in cases}
    for case in cases:
        assert case["requirement"] and case["expected_atom_quotes"]
        assert all(quote in case["requirement"] for quote in case["expected_atom_quotes"])
        assert case["expected_analysis"] in {"completed", "validation_failed"}
        assert case["expected_support"] in {
            "supported", "partial", "not_supported", "insufficient_evidence", None
        }
        assert set(case) >= {
            "expected_contours", "expected_executor_targets", "expected_version_scopes",
            "required_diagnostics", "allowed_procedure_template_ids",
        }


@pytest.fixture(scope="module")
def deep_gold_results(tmp_path_factory):
    assert GOLD.is_file(), "Task 16 frozen deep gold set is missing"
    from tests.deep_acceptance_support import run_gold
    return run_gold(tmp_path_factory.mktemp("deep-gold"))


def test_gold_set_has_zero_false_supported(deep_gold_results) -> None:
    cases, result, _output, _requests = deep_gold_results
    actual = result["requirements"]
    assert len(actual) == len(cases)
    false_supported = [
        case["id"] for case, item in zip(cases, actual, strict=True)
        if case["expected_support"] != "supported" and item["support_status"] == "supported"
    ]
    assert false_supported == []


def test_every_gold_case_matches_frozen_semantics(deep_gold_results) -> None:
    cases, result, _output, _requests = deep_gold_results
    records = result["responsibility_records"]
    for case, item in zip(cases, result["requirements"], strict=True):
        selected = [record for record in records if record["requirement_id"] == item["requirement"]["requirement_id"]]
        assert item["requirement"]["text"] == case["requirement"], case["id"]
        assert item["analysis_state"] == case["expected_analysis"], (case["id"], item)
        assert item["support_status"] == case["expected_support"], (case["id"], item)
        assert [atom["atom"]["source_quote"] for atom in item["atom_results"]] == case["expected_atom_quotes"], case["id"]
        assert sorted({record["contour"] for record in selected}) == sorted(case["expected_contours"]), case["id"]
        assert sorted({(record["executor_ref"], record["target_ref"]) for record in selected}) == sorted(map(tuple, case["expected_executor_targets"])), case["id"]
        assert {json.dumps(record["version_scope"], sort_keys=True) for record in selected} == {json.dumps(scope, sort_keys=True) for scope in case["expected_version_scopes"]}, case["id"]
        for prefix in case["required_diagnostics"]:
            assert any(prefix in diagnostic for diagnostic in item["diagnostics"]), (case["id"], item["diagnostics"])
        graphs = [graph for graph in result["procedure_graphs"] if graph["requirement_id"] == item["requirement"]["requirement_id"]]
        assert {graph["template_id"] for graph in graphs} == set(case["allowed_procedure_template_ids"]), case["id"]
