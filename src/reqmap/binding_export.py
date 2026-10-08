"""Render persisted source decisions consistently without consulting any model or KB."""
import re

from reqmap.binding_models import BindingDecision, SourceBinding
from reqmap.models import AnalysisState, SupportStatus, to_dict


def contract_payload(raw):
    keys = {"binding_engine_version", "grammar_version", "grammar_sha256", "binding_catalog_sha256",
            "binding_catalog_schema_version", "proposal_schema_version", "result_schema_version"}
    if type(raw) is dict and raw.get("result_schema_version") in {"1.2", "2.2"}:
        keys |= {"source_context_version", "resolver_version", "resolver_sha256"}
    if type(raw) is not dict or set(raw) != keys:
        raise ValueError("BINDING_CONTRACT_INVALID")
    for key in ("binding_engine_version", "grammar_version", "result_schema_version"):
        if type(raw[key]) is not str or re.fullmatch(r"[0-9]+\.[0-9]+", raw[key]) is None:
            raise ValueError("BINDING_CONTRACT_INVALID")
    for key in ("grammar_sha256", "binding_catalog_sha256"):
        if key == "binding_catalog_sha256" and raw[key] is None:
            continue
        if type(raw[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", raw[key]) is None:
            raise ValueError("BINDING_CONTRACT_INVALID")
    if type(raw["proposal_schema_version"]) is not int or raw["proposal_schema_version"] != 2:
        raise ValueError("BINDING_CONTRACT_INVALID")
    schema = raw["binding_catalog_schema_version"]
    if ((raw["binding_catalog_sha256"] is None and schema is not None)
            or (raw["binding_catalog_sha256"] is not None and (type(schema) is not int or schema != 1))):
        raise ValueError("BINDING_CONTRACT_INVALID")
    if "source_context_version" in raw:
        if (raw["source_context_version"] != "1.0" or raw["resolver_version"] != "1.0"
                or type(raw["resolver_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", raw["resolver_sha256"]) is None):
            raise ValueError("BINDING_CONTRACT_INVALID")
    return dict(raw)


def validate_binding_run(run):
    if run.schema_version not in {"1.1", "2.1", "1.2", "2.2"}:
        return
    from reqmap.binding_source import canonical_atoms
    from reqmap.binding_codec import decode_source_binding, decode_binding_decision
    contract = contract_payload(run.metadata.get("binding_contract"))
    if contract["result_schema_version"] != run.schema_version:
        raise ValueError("BINDING_RESULT_SCHEMA_MISMATCH")
    context = None
    if run.schema_version in {"1.2", "2.2"}:
        raw = run.metadata.get("source_context")
        if raw is not None:
            from reqmap.source_context_codec import inspect_source_context_record
            context = inspect_source_context_record(raw)
            from dataclasses import replace
            rows = tuple(replace(row.requirement, parent_id=None, group_ids=()) for row in run.requirements)
            if (rows != context.document.requirements
                    or run.metadata.get("input_sha256") != context.document.input_sha256):
                raise ValueError("SOURCE_CONTEXT_INPUT_MISMATCH")
            if any(d.resolver_sha256 != contract["resolver_sha256"] for d in context.decisions):
                raise ValueError("SOURCE_CONTEXT_CHANGED")
        elif any(row.analysis_state is AnalysisState.COMPLETED for row in run.requirements):
            raise ValueError("SOURCE_CONTEXT_CHANGED: snapshot missing")
    for row in run.requirements:
        binding = row.source_binding
        if type(binding) is not SourceBinding or decode_source_binding(to_dict(binding), row.requirement, context) != binding:
            raise ValueError("SOURCE_BINDING_MISSING")
        if (binding.grammar_version, binding.grammar_sha256) != (contract["grammar_version"], contract["grammar_sha256"]):
            raise ValueError("SOURCE_GRAMMAR_MISMATCH")
        if row.atom_results and tuple(a.atom for a in row.atom_results) != canonical_atoms(binding):
            raise ValueError("SOURCE_COVERAGE_GAP")
        if row.analysis_state is AnalysisState.COMPLETED and not row.atom_results:
            raise ValueError("SOURCE_COVERAGE_GAP")
        for result in row.atom_results:
            if result.analysis_state is not AnalysisState.COMPLETED:
                if result.binding_decision is not None:
                    raise ValueError("INCOMPLETE_BINDING_DECISION")
                continue
            decision = result.binding_decision
            if type(decision) is not BindingDecision or decode_binding_decision(to_dict(decision)) != decision:
                raise ValueError("BINDING_DECISION_MISSING")
            obligation = next(o for o in binding.obligations if o.obligation_id == result.atom.obligation_id)
            if (decision.obligation_id != obligation.obligation_id or decision.source_sha256 != binding.source_sha256
                    or decision.support_status is not result.support_status
                    or decision.engine_version != contract["binding_engine_version"]
                    or decision.catalog_sha256 != contract["binding_catalog_sha256"]):
                raise ValueError("BINDING_DECISION_MISMATCH")
            positive = decision.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}
            proven = positive or decision.support_status is SupportStatus.NOT_SUPPORTED
            if context is not None:
                source = binding.context_decision
                if source is None or decision.context_id != source.context_id:
                    raise ValueError("SOURCE_CONTEXT_CHANGED")
                if proven and source.state not in {"independent", "linked"}:
                    raise ValueError("SOURCE_CONTEXT_UNREVIEWED")
                valid_refs = {link.origin for link in source.applied_links}
                for diag in decision.diagnostics:
                    if any(ref not in valid_refs for ref in diag.source_refs):
                        raise ValueError("SOURCE_CONTEXT_REF_INVALID")
                if decision.uncovered_context_refs != tuple(dict.fromkeys(ref for d in decision.diagnostics for ref in d.source_refs)):
                    raise ValueError("SOURCE_CONTEXT_CHANGED")
            if proven and (obligation.parse_state != "bound" or not decision.predicate_ids or not decision.evidence_ids
                           or decision.catalog_sha256 is None or decision.uncovered):
                raise ValueError("BINDING_PROOF_MISSING")
            expected_gap = () if proven else obligation.source_spans
            if decision.uncovered != expected_gap:
                raise ValueError("BINDING_GAP_CHANGED")
            if result.supported_aspects != ((obligation.source_quote,) if positive else ()):
                raise ValueError("BINDING_ASPECT_CHANGED")
            for diag in decision.diagnostics:
                if diag.requirement_id != binding.requirement_id or diag.obligation_id != obligation.obligation_id:
                    raise ValueError("BINDING_DIAGNOSTIC_REF")
                if any(binding.source_text[s.start:s.end] != s.quote for s in diag.source_spans):
                    raise ValueError("SOURCE_SPAN_INVALID")


def binding_markdown(run):
    from reqmap.export_json import canonical_json_bytes
    rows = [row for row in run.requirements if row.source_binding is not None]
    if not rows:
        return ""
    parts = ["## Связь исходного обязательства с доказательством",
             "Смещения `[start,end)` измеряются в Unicode code points. Непокрытая цитата сохраняется полностью."]
    for row in rows:
        parts.append("### " + row.requirement.requirement_id)
        parts.append("```json\n" + canonical_json_bytes(to_dict(row.source_binding)).decode().strip() + "\n```")
        for atom in row.atom_results:
            if row.source_binding.context_decision is not None:
                parts.append("Контекст атома `" + atom.atom.atom_id + "` (условия и исходные ссылки):")
                display = source_context_display(row.source_binding.context_decision, atom.atom.obligation_id)
                parts.append("```json\n" + canonical_json_bytes(display).decode().strip() + "\n```")
            if atom.binding_decision is not None:
                parts.append("```json\n" + canonical_json_bytes(to_dict(atom.binding_decision)).decode().strip() + "\n```")
    return "\n\n".join(parts)


def source_context_display(decision, obligation_id):
    """One lossless display contract; references never become local offsets."""
    effective = next(item for item in decision.effective_obligations
                     if item.obligation.obligation_id == obligation_id)
    return {
        "state": decision.state,
        "context_id": decision.context_id,
        "map_sha256": decision.map_sha256,
        "resolver_sha256": decision.resolver_sha256,
        "own_quote": effective.obligation.source_quote,
        "effective_interface": effective.obligation.interface,
        "effective_constraints": to_dict(effective.obligation.constraints),
        "interface_refs": to_dict(effective.interface_refs),
        "constraint_refs": to_dict(effective.constraint_refs),
        "origins": to_dict(decision.applied_links),
    }


def atom_context_displays(run):
    return {atom.atom.atom_id: (
        source_context_display(row.source_binding.context_decision, atom.atom.obligation_id)
        if row.source_binding is not None and row.source_binding.context_decision is not None else None)
        for row in run.requirements for atom in row.atom_results}


def source_context_summary(run):
    """Capture-time trust metadata, without claiming current signer authorization."""
    raw = run.metadata.get("source_context")
    if raw is None:
        return None
    from reqmap.source_context_codec import inspect_source_context_record
    snapshot = inspect_source_context_record(raw)
    contract = contract_payload(run.metadata["binding_contract"])
    return {
        "input_sha256": snapshot.document.input_sha256,
        "input_profile_sha256": snapshot.document.input_profile_sha256,
        "requirements_sha256": snapshot.document.requirements_sha256,
        "map_sha256": snapshot.loaded.map_sha256,
        "resolver_version": contract["resolver_version"],
        "resolver_sha256": contract["resolver_sha256"],
        "trust_at_capture": to_dict(snapshot.loaded.trust),
        "diagnostic_codes": sorted({d.code for decision in snapshot.decisions for d in decision.diagnostics}
            | {d.code for row in run.requirements for atom in row.atom_results
               if atom.binding_decision is not None for d in atom.binding_decision.diagnostics}),
    }
