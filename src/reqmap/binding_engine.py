"""Deterministic intersection of complete obligations and reviewed predicates."""
from __future__ import annotations

from dataclasses import replace
import hashlib

from reqmap.binding_models import (
    BINDING_ENGINE_VERSION, BindingContext, BindingDecision, BindingDiagnostic,
    BoundObligation, SourceSpan,
)
from reqmap.binding_source import bind_source, canonical_atoms
from reqmap.models import EvidenceClaimScope, EvidencePolarity, Requirement, SupportStatus, to_dict
from reqmap.proposals import ProposalError


_INSUFFICIENT = SupportStatus.INSUFFICIENT_EVIDENCE
_FIELDS = {
    "actor": "SOURCE_ACTION_UNPROVEN",
    "action": "SOURCE_ACTION_UNPROVEN",
    "object": "SOURCE_OBJECT_UNPROVEN",
    "direction": "SOURCE_DIRECTION_UNPROVEN",
    "interface": "SOURCE_INTERFACE_UNPROVEN",
    "contour": "EVIDENCE_CONTOUR_MISMATCH",
    "lifecycle_phase": "SOURCE_CONDITION_UNPROVEN",
    "release_scope": "EVIDENCE_VERSION_MISMATCH",
}
_MESSAGES = {
    "SOURCE_UNPARSED": "Исходное обязательство не распознано целиком.",
    "SOURCE_AMBIGUOUS": "Исходное обязательство допускает неоднозначный разбор.",
    "SOURCE_CONTEXT_REQUIRED": "Нет проверенной связи с полной исходной строкой.",
    "SOURCE_ACTION_UNPROVEN": "Не подтверждено действие исходного обязательства.",
    "SOURCE_OBJECT_UNPROVEN": "Не подтверждён объект исходного обязательства.",
    "SOURCE_DIRECTION_UNPROVEN": "Не подтверждена направленность обязательства или запрета.",
    "SOURCE_INTERFACE_UNPROVEN": "Не подтверждён требуемый интерфейс.",
    "SOURCE_CONDITION_UNPROVEN": "Не подтверждены все условия и область применения обязательства.",
    "EVIDENCE_VERSION_MISMATCH": "Доказательство относится к другой версии.",
    "EVIDENCE_CONTOUR_MISMATCH": "Доказательство относится к другому контуру.",
    "EVIDENCE_MISSING": "Нет выбранного применимого доказательства для полного обязательства.",
    "EVIDENCE_CONTEXT_ONLY": "Общая справка не доказывает конкретное обязательство.",
    "BINDING_CATALOG_MISSING": "Каталог проверенных предикатов не настроен.",
    "BINDING_UNREVIEWED": "Нет выбранного проверенного предиката.",
    "EVIDENCE_CONFLICT": "Применимые полные предикаты противоречат друг другу.",
}


def diagnostic(obligation, code, field="source", evidence_ids=(), spans=None):
    return BindingDiagnostic(code, _MESSAGES[code], obligation.requirement_id,
                             obligation.obligation_id, field,
                             obligation.source_spans if spans is None else spans, tuple(evidence_ids))


def check_binding(obligation, predicate_ids, catalog, prior_status, cited_evidence_ids):
    """One predicate must match every field; never promote the evidence gate."""
    diagnostics, matched, attempted, applicable = [], [], [], []
    cited = set(cited_evidence_ids)
    if obligation.parse_state != "bound":
        code = "SOURCE_AMBIGUOUS" if obligation.parse_state == "ambiguous" else "SOURCE_UNPARSED"
        diagnostics.append(diagnostic(obligation, code))
    if catalog is None:
        diagnostics.append(diagnostic(obligation, "BINDING_CATALOG_MISSING", "catalog"))
    elif not predicate_ids:
        diagnostics.append(diagnostic(obligation, "BINDING_UNREVIEWED", "predicate_ids"))
    else:
        # Reviewed contradictions are backend facts, not model selections.
        selected = set(predicate_ids)
        for pid in dict.fromkeys((*predicate_ids, *catalog.predicates)):
            is_selected = pid in selected
            p = catalog.predicates.get(pid)
            if p is None or p.review_state != "reviewed":
                if is_selected:
                    diagnostics.append(diagnostic(obligation, "BINDING_UNREVIEWED", "predicate_ids"))
                continue
            if is_selected:
                attempted.append(pid)
            has_cited_proof = bool(p.evidence_ids) and set(p.evidence_ids).issubset(cited)
            if not has_cited_proof and is_selected:
                diagnostics.append(diagnostic(obligation, "EVIDENCE_MISSING", "evidence_ids", p.evidence_ids))
            if obligation.parse_state != "bound":
                continue
            mismatches = []
            for field, code in _FIELDS.items():
                if field == "interface" and obligation.interface == "unspecified":
                    continue
                if getattr(obligation, field) != getattr(p, field):
                    mismatches.append(diagnostic(obligation, code, field, p.evidence_ids))
            required = {c.semantic_key for c in obligation.constraints}
            guaranteed = {c.semantic_key for c in p.constraints}
            # Exact conditions only. In particular P => X cannot establish unconditional X.
            if required != guaranteed or p.assumptions:
                spans = tuple(s for c in obligation.constraints if c.semantic_key not in guaranteed for s in c.source_spans)
                mismatches.append(diagnostic(obligation, "SOURCE_CONDITION_UNPROVEN", "constraints",
                                             p.evidence_ids, spans or obligation.source_spans))
            if not mismatches:
                applicable.append(p)
                if (obligation.interface == "unspecified"
                        and not (p.polarity is EvidencePolarity.POSITIVE and obligation.direction == "capability")):
                    mismatches.append(diagnostic(obligation, "SOURCE_INTERFACE_UNPROVEN", "interface", p.evidence_ids))
            if is_selected:
                if mismatches:
                    diagnostics.extend(mismatches)
                elif has_cited_proof:
                    matched.append(p)
    # Opposite facts conflict only in the SAME complete scope. API and GUI
    # alternatives do not contradict each other for an unspecified interface.
    conflicts = [p for p in applicable if any(
        p.polarity is not q.polarity
        and all(getattr(p, field) == getattr(q, field) for field in _FIELDS)
        and {c.semantic_key for c in p.constraints} == {c.semantic_key for c in q.constraints}
        for q in applicable)]
    polarities = {p.polarity for p in matched}
    status = _INSUFFICIENT
    if conflicts:
        conflict_evidence = tuple(dict.fromkeys(e for p in conflicts for e in p.evidence_ids))
        diagnostics = [diagnostic(obligation, "EVIDENCE_CONFLICT", "polarity", conflict_evidence)]
    elif EvidencePolarity.POSITIVE in polarities and prior_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL}:
        status = prior_status
    elif EvidencePolarity.NEGATIVE in polarities and prior_status is SupportStatus.NOT_SUPPORTED:
        status = SupportStatus.NOT_SUPPORTED
    elif matched:
        diagnostics.append(diagnostic(obligation, "EVIDENCE_MISSING", "prior_status", cited_evidence_ids))
    if status is not _INSUFFICIENT:
        # Failed alternative candidates are not gaps in a fully proven obligation.
        diagnostics = []
    return BindingDecision(
        obligation.obligation_id, hashlib.sha256(obligation.source_quote.encode("utf-8")).hexdigest(),
        status, tuple(p.predicate_id for p in (conflicts or matched)) if (conflicts or matched) else tuple(attempted),
        tuple(dict.fromkeys(cited_evidence_ids)), tuple(dict.fromkeys(diagnostics)),
        obligation.source_spans if status is _INSUFFICIENT else (),
        None if catalog is None else catalog.catalog_sha256,
    )


def _fail(code):
    raise ProposalError("semantic", (f"{code}: контекст не соответствует canonical source/KB.",))


def context_obligation(atom, context, kb):
    """Recompute code-owned source semantics; never accept caller-forged fields."""
    if context is None:
        return BoundObligation(atom.obligation_id or f"{atom.requirement_id}-O{atom.ordinal:03d}",
            atom.requirement_id, (SourceSpan(0, len(atom.source_quote), atom.source_quote),),
            atom.source_quote, "unresolved", None, True,
            None, None, None, None, None, None, None, None)
    if type(context) is not BindingContext or context.engine_version != BINDING_ENGINE_VERSION:
        _fail("SOURCE_CONTEXT_REQUIRED")
    binding = context.source_binding
    canonical = bind_source(Requirement(binding.requirement_id, "", binding.source_text, 1, binding.coordinate))
    if canonical != binding or atom not in canonical_atoms(canonical):
        _fail("SOURCE_SPAN_INVALID")
    if context.catalog is not None:
        from reqmap.binding_catalog import knowledge_digest
        if context.catalog.knowledge_sha256 != knowledge_digest(kb):
            _fail("BINDING_KNOWLEDGE_MISMATCH")
    return next(o for o in binding.obligations if o.obligation_id == atom.obligation_id)


_SELECTION_KEYS = {"proposal_schema_version", "obligation_id", "predicate_ids"}


def binding_proposal(atom, proposal, context):
    """Strip only validated v2 selection fields before the prior evidence parser."""
    if type(proposal) is not dict:
        raise ProposalError("shape", ("Ответ должен быть JSON object.",))
    if (type(proposal.get("proposal_schema_version")) is not int
            or proposal.get("proposal_schema_version") != 2
            or proposal.get("obligation_id") != (atom.obligation_id or f"{atom.requirement_id}-O{atom.ordinal:03d}")
            or type(proposal.get("predicate_ids")) is not list
            or any(type(p) is not str or not p.strip() for p in proposal.get("predicate_ids", []))
            or len(set(proposal.get("predicate_ids", []))) != len(proposal.get("predicate_ids", []))):
        raise ProposalError("shape", ("PROPOSAL_SCHEMA: требуются version=2, canonical obligation_id и unique predicate_ids.",))
    return {k: v for k, v in proposal.items() if k not in _SELECTION_KEYS}, tuple(proposal["predicate_ids"])


def binding_payload(atom, context, kb, payload):
    if context is None:
        return payload
    obligation = context_obligation(atom, context, kb)
    predicates = () if context.catalog is None else tuple(context.catalog.predicates.values())
    evidence_ids = {e["evidence_id"] for e in payload["evidence"]}
    allowed = [p for p in predicates if set(p.evidence_ids).issubset(evidence_ids)]
    return {**payload, "source_binding": to_dict(context.source_binding), "obligation": to_dict(obligation),
            "binding_engine_version": context.engine_version,
            "binding_catalog_sha256": None if context.catalog is None else context.catalog.catalog_sha256,
            "predicates": [{k: v for k, v in to_dict(p).items()
                            if k not in {"locators", "source_ids", "source_sha256s", "reviewed_by", "reviewed_at"}}
                           for p in allowed],
            "response_schema": {**payload["response_schema"], "proposal_schema_version": 2,
                                "obligation_id": atom.obligation_id, "predicate_ids": [p.predicate_id for p in allowed]}}


def mapping_decision(atom, context, kb, predicate_ids, prior_status, records):
    """Prove only cited exact relations; inspect their catalog contradictions."""
    from reqmap.knowledge import KnowledgeBase
    from reqmap.binding_catalog import _validate_relations
    from reqmap.errors import ReqmapError
    obligation = context_obligation(atom, context, kb)
    catalog = None if context is None else context.catalog
    evidence_ids = tuple(dict.fromkeys(e for r in records for e in r.evidence_ids))
    allowed, checked = [], {}
    if catalog is not None:
        for pid in catalog.predicates:
            p = catalog.predicates.get(pid)
            if p is None:
                continue
            # Defensive public-Python boundary: metadata must still match this KB.
            try:
                _validate_relations(p, kb)
            except ReqmapError:
                continue
            for r in records:
                if type(kb) is KnowledgeBase:
                    matches = p.component_ref == r.component_id and p.lifecycle_phase == r.phase.value
                else:
                    matches = (p.component_ref == r.component_ref and p.action_ref == r.action_ref
                        and p.effect_ref == r.effect_ref and p.target_ref == r.target_ref
                        and p.contour == r.contour.value and p.lifecycle_phase == r.lifecycle_phase.value)
                if matches:
                    checked[pid] = p
                    if pid in predicate_ids and set(p.evidence_ids).issubset(r.evidence_ids):
                        allowed.append(pid)
                    break
        from types import MappingProxyType
        catalog = replace(catalog, predicates=MappingProxyType(checked))
    decision = check_binding(obligation, tuple(allowed), catalog, prior_status, evidence_ids)
    extras = []
    if context is None:
        extras.append(diagnostic(obligation, "SOURCE_CONTEXT_REQUIRED"))
    context_eids = tuple(e for e in evidence_ids
                         if getattr(kb.evidence[e], "claim_scope", None) is EvidenceClaimScope.CONTEXT)
    if context_eids:
        extras.append(diagnostic(obligation, "EVIDENCE_CONTEXT_ONLY", "evidence_ids", context_eids))
    return replace(decision, source_sha256=(atom.source_sha256 or decision.source_sha256),
                   diagnostics=tuple(dict.fromkeys((*decision.diagnostics, *extras))))


def apply_decision(result, decision):
    """Aspects are source quotes and gaps, never model-authored proof labels."""
    supported = (result.atom.source_quote,) if decision.support_status in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL} else ()
    unconfirmed = tuple(dict.fromkeys(span.quote for span in decision.uncovered))
    if decision.support_status is SupportStatus.PARTIAL:
        unconfirmed = unconfirmed or (result.atom.source_quote,)
    return replace(result, support_status=decision.support_status, binding_decision=decision,
        supported_aspects=supported, unconfirmed_aspects=unconfirmed,
        diagnostics=tuple(dict.fromkeys((*result.diagnostics, *(f"{d.code}: {d.message_ru}" for d in decision.diagnostics)))))


def apply_record_decision(record, decision):
    reasons = tuple(f"{d.code}: {d.message_ru}" for d in decision.diagnostics)
    if hasattr(record, "reason_ru"):
        return replace(record, support_status=decision.support_status,
                       reason_ru=" ".join((record.reason_ru, *reasons)) if reasons else record.reason_ru)
    return replace(record, support_status=decision.support_status,
                   diagnostics=tuple(dict.fromkeys((*record.diagnostics, *reasons))))
