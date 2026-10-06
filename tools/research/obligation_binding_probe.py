#!/usr/bin/env python3
"""Disposable Stage 2 experiment; never imported by reqmap runtime.

Uses synthetic test KBs and a deliberately finite grammar. The annotations
below are experiment inputs, NOT reviewed OpenStack claims or a shipped KB.
Run from repository root with PYTHONPATH=src and the project virtualenv.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reqmap.decomposition import accept_decomposition
from reqmap.deep_mapping import accept_deep_mapping
from reqmap.deep_retrieval import retrieve_deep
from reqmap.mapping import accept_mapping
from reqmap.retrieval import retrieve
from tests.deep_factories import immutable_v2_kb, deep_mapping_response
from tests.factories import requirement
from tests.test_mapping import atom, kb as legacy_fixture, raw_mapping, response


GRAMMAR_VERSION = "research-finite-1"
_TAIL = r"(?P<conditions>(?: (?:за одну миллисекунду|за [0-9]+ мс|при отказе узла|с GPU))*)"
_RULES = (
    re.compile(r"Nova (?P<neg>не )?должна (?P<action>создавать|удалять) "
               r"(?P<object>виртуальную машину|ВМ|пользователя)"
               r"(?: через (?P<interface>API|GUI))?" + _TAIL + r"\.?", re.IGNORECASE),
    re.compile(r"Nova (?P<neg>не )?должна обеспечивать (?P<action>создание|удаление) "
               r"(?P<object>виртуальной машины|ВМ|пользователя)"
               r"(?: через (?P<interface>API|GUI))?" + _TAIL + r"\.?", re.IGNORECASE),
)
_CONDITION = re.compile(r"за одну миллисекунду|за [0-9]+ мс|при отказе узла|с GPU", re.IGNORECASE)


@dataclass(frozen=True)
class Fact:
    """Hand-annotated synthetic predicate, separately from model proposals."""
    evidence_id: str
    action: str = "create"
    object: str = "vm"
    direction: str = "capability"
    interface: str = "api"
    conditions: tuple[str, ...] = ()
    release: str = "2025.1"
    contour: str = "openstack_runtime"
    scope: str = "specific"
    reviewed: bool = True


def parse_source(text: str) -> dict:
    """Full match only; do not strip punctuation, numbers, negation, or suffixes."""
    for index, rule in enumerate(_RULES, 1):
        match = rule.fullmatch(text)
        if match is None:
            continue
        groups = match.groupdict()
        return dict(
            source_quote=text,
            source_sha256=hashlib.sha256(text.encode()).hexdigest(),
            source_span=[0, len(text)],
            rule_id=f"{GRAMMAR_VERSION}:{index}",
            action="create" if groups["action"].casefold() in {"создавать", "создание"} else "delete",
            object="user" if groups["object"].casefold() == "пользователя" else "vm",
            direction="prohibition" if groups["neg"] else "capability",
            interface=(groups["interface"] or "unspecified").casefold(),
            conditions=[dict(value=m.group().casefold(), start=m.start(), end=m.end())
                        for m in _CONDITION.finditer(text, match.start("conditions"), match.end("conditions"))],
            field_spans={name: list(match.span(name)) for name in ("action", "object", "neg", "interface")
                         if match.start(name) >= 0},
        )
    return dict(source_quote=text, source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                source_span=[0, len(text)], rule_id=None)


def bind(text: str, facts: tuple[Fact, ...]) -> dict:
    """A whole predicate must be proved; unrelated facts are never unioned."""
    parsed = parse_source(text)
    if parsed["rule_id"] is None:
        return dict(status="insufficient_evidence", parsed=parsed,
                    diagnostics=["SOURCE_UNPARSED"],
                    uncovered=[dict(start=0, end=len(text), quote=text)], evidence_ids=[])
    diagnostics: list[str] = []
    if not facts:
        diagnostics.append("EVIDENCE_MISSING")
    for fact in facts:
        gaps: list[str] = []
        if fact.scope != "specific":
            gaps.append("EVIDENCE_CONTEXT_ONLY")
        if not fact.reviewed:
            gaps.append("BINDING_UNREVIEWED")
        if fact.release != "2025.1":
            gaps.append("EVIDENCE_VERSION_MISMATCH")
        if fact.contour != "openstack_runtime":
            gaps.append("EVIDENCE_CONTOUR_MISMATCH")
        for field in ("action", "object", "direction"):
            if parsed[field] != getattr(fact, field):
                gaps.append(f"SOURCE_{field.upper()}_UNPROVEN")
        if parsed["interface"] not in {"unspecified", fact.interface}:
            gaps.append("SOURCE_INTERFACE_UNPROVEN")
        # A constrained fact cannot prove an unconditional requirement.
        # This probe only admits exact conditions, no guessed implication.
        if tuple(c["value"] for c in parsed["conditions"]) != fact.conditions:
            gaps.append("SOURCE_CONDITION_UNPROVEN")
        if not gaps:
            return dict(status="supported", parsed=parsed, diagnostics=[],
                        uncovered=[], evidence_ids=[fact.evidence_id])
        diagnostics.extend(gaps)
    return dict(status="insufficient_evidence", parsed=parsed,
                diagnostics=list(dict.fromkeys(diagnostics)),
                uncovered=[dict(start=0, end=len(text), quote=text)], evidence_ids=[])


def source_coverage(text: str, spans: list[tuple[int, int, str]]) -> dict:
    """Probe occurrence identity and gaps; offsets are Unicode code points."""
    occupied = set()
    for start, end, quote in spans:
        if type(start) is not int or type(end) is not int or not (0 <= start < end <= len(text)):
            return dict(ok=False, code="SOURCE_SPAN_INVALID")
        if text[start:end] != quote or occupied.intersection(range(start, end)):
            return dict(ok=False, code="SOURCE_SPAN_INVALID")
        occupied.update(range(start, end))
    gaps = [i for i, c in enumerate(text) if i not in occupied and c not in " \t\r\n.;"]
    return dict(ok=not gaps, code=None if not gaps else "SOURCE_COVERAGE_GAP",
                uncovered_positions=gaps)


def _baseline(text, legacy, deep):
    results = {}
    for profile in ("legacy", "deep"):
        try:
            if profile == "legacy":
                result = accept_mapping(atom(text), retrieve(legacy, text, (), 8), legacy,
                                        response(raw_mapping()))
            else:
                result = accept_deep_mapping(atom(text), retrieve_deep(deep, text, (), 8), deep,
                                             deep_mapping_response()).atom_result
            results[profile] = result.support_status.value
        except Exception as exc:
            # Record failure explicitly, never count it as proof of rejection.
            results[profile] = f"error:{type(exc).__name__}:{exc}"
    return results


def run() -> dict:
    cases_path = Path(__file__).with_name("obligation_binding_cases.json")
    cases_bytes = cases_path.read_bytes()
    cases = json.loads(cases_bytes)
    legacy = legacy_fixture.__wrapped__()
    rows = []
    with TemporaryDirectory(prefix="reqmap-binding-probe-") as temporary:
        deep = immutable_v2_kb(Path(temporary))
        # Assert the exact fixture relationships before attaching experiment semantics.
        action = deep.actions["ACTION-NOVA-CREATE"]
        assert (action.target_ref, action.operation, action.interface_type) == (
            "TARGET-NOVA-SERVER", "POST /v2.1/servers", "openstack_api")
        assert "EV-NOVA-CREATE" in action.evidence_ids
        assert legacy.evidence["E-NOVA"].claim_scope.value == "specific"
        for case in cases:
            text = case["text"]
            baseline = _baseline(text, legacy, deep)
            for value in baseline.values():
                if case["id"] == "parent_fragment":
                    assert value.startswith("error:ProposalError:"), (case["id"], baseline)
                else:
                    assert value == "supported", (case["id"], baseline)
            outcomes = {profile: bind(text, (Fact(evidence_id),))
                        for profile, evidence_id in (("legacy", "E-NOVA"), ("deep", "EV-NOVA-CREATE"))}
            for outcome in outcomes.values():
                assert outcome["status"] == case["expected"], (case["id"], outcome)
                if case.get("diagnostic"):
                    assert case["diagnostic"] in outcome["diagnostics"], (case["id"], outcome)
                assert outcome["parsed"]["source_quote"] == text
                assert all(text[g["start"]:g["end"]] == g["quote"] for g in outcome["uncovered"])
            rows.append(dict(**case, baseline=baseline, prototype=outcomes))

    text = cases[0]["text"]
    fact = Fact("SYNTHETIC")
    controls = []
    for name, facts, code in (
        ("missing", (), "EVIDENCE_MISSING"),
        ("context", (replace(fact, scope="context"),), "EVIDENCE_CONTEXT_ONLY"),
        ("unreviewed", (replace(fact, reviewed=False),), "BINDING_UNREVIEWED"),
        ("version", (replace(fact, release="2024.2"),), "EVIDENCE_VERSION_MISMATCH"),
        ("contour", (replace(fact, contour="host_os"),), "EVIDENCE_CONTOUR_MISMATCH"),
        ("restricted_fact", (replace(fact, conditions=("при отказе узла",)),), "SOURCE_CONDITION_UNPROVEN"),
    ):
        result = bind(text, facts)
        assert result["status"] == "insufficient_evidence" and code in result["diagnostics"]
        controls.append(dict(id=name, status=result["status"], diagnostics=result["diagnostics"]))
    gui = text.replace("API", "GUI")
    mixed = bind(gui, (fact, replace(fact, action="delete", interface="gui")))
    assert mixed["status"] == "insufficient_evidence"
    controls.append(dict(id="no_cross_fact_union", status=mixed["status"], diagnostics=mixed["diagnostics"]))
    latency = text[:-1] + " за 1 мс."
    positive = bind(latency, (replace(fact, conditions=("за 1 мс",)),))
    negative = bind(latency.replace("1 мс", "2 мс"), (replace(fact, conditions=("за 1 мс",)),))
    assert positive["status"] == "supported" and negative["status"] == "insufficient_evidence"
    controls.append(dict(id="exact_synthetic_condition", positive=positive["status"], changed=negative["status"]))

    source = text[:-1] + " за одну миллисекунду."
    quote = text[:-1]
    old_atoms = accept_decomposition(replace(requirement(), text=source),
        {"atoms": [{"text": quote, "source_quote": quote, "mandatory": False}]})
    assert len(old_atoms) == 1 and not old_atoms[0].mandatory
    coverage = {
        "omitted_condition": source_coverage(source, [(0, len(quote), quote)]),
        "complete": source_coverage(source, [(0, len(source), source)]),
        "overlap": source_coverage(source, [(0, len(source), source), (0, len(quote), quote)]),
        "repeated_occurrence": source_coverage(quote + "; " + quote,
            [(0, len(quote), quote), (len(quote) + 2, 2 * len(quote) + 2, quote)]),
        "one_of_two_occurrences": source_coverage(quote + "; " + quote, [(0, len(quote), quote)]),
    }
    assert {name: result["ok"] for name, result in coverage.items()} == {
        "omitted_condition": False, "complete": True, "overlap": False,
        "repeated_occurrence": True, "one_of_two_occurrences": False,
    }
    return dict(kind="synthetic_research_only", grammar=GRAMMAR_VERSION,
                probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                fixture_sha256=hashlib.sha256(cases_bytes).hexdigest(),
                cases=rows, controls=controls, coverage=coverage,
                decomposition_baseline=dict(omitted_condition_accepted=True, model_optional_accepted=True),
                totals=dict(text_cases=len(rows), profile_evaluations=2 * len(rows),
                    positive_cases=sum(c["expected"] == "supported" for c in cases),
                    negative_cases=sum(c["expected"] != "supported" for c in cases),
                    evidence_controls=len(controls), coverage_controls=len(coverage)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["totals"], ensure_ascii=False))
