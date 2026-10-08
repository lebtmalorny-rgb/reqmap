"""Strict decoding of persisted binding decisions; replay remains authoritative."""
from dataclasses import fields
import re

from reqmap.binding_models import BindingDecision, BindingDiagnostic, SourceSpan
from reqmap.models import SupportStatus, SourceCoordinate


def _object(raw, cls):
    if type(raw) is not dict or set(raw) != {f.name for f in fields(cls)}:
        raise ValueError("BINDING_SCHEMA: invalid persisted fields")
    return raw


def _text(raw):
    if type(raw) is not str or not raw.strip():
        raise ValueError("BINDING_SCHEMA: expected nonempty string")
    return raw


def _array(raw, decode):
    if type(raw) is not list:
        raise ValueError("BINDING_SCHEMA: expected array")
    return tuple(decode(v) for v in raw)


def _hash(raw):
    if type(raw) is not str or re.fullmatch(r"[0-9a-f]{64}", raw) is None:
        raise ValueError("BINDING_SCHEMA: invalid hash")
    return raw


def _span(raw):
    return SourceSpan(**_object(raw, SourceSpan))


def _diagnostic(raw):
    raw = _object(raw, BindingDiagnostic)
    return BindingDiagnostic(**{k: _text(raw[k]) for k in ("code", "message_ru", "requirement_id", "obligation_id", "field")},
        source_spans=_array(raw["source_spans"], _span), evidence_ids=_array(raw["evidence_ids"], _text),
        source_refs=_array(raw["source_refs"], _source_ref))


def _source_ref(raw):
    from reqmap.source_context_models import SourceFragmentRef, SourceRowRef
    raw = _object(raw, SourceFragmentRef)
    row = _object(raw["row"], SourceRowRef)
    coordinate = SourceCoordinate(**_object(row["coordinate"], SourceCoordinate))
    return SourceFragmentRef(SourceRowRef(_text(row["requirement_id"]), coordinate, _hash(row["source_sha256"])), _span(raw["span"]))


def decode_binding_decision(raw):
    if raw is None:
        return None
    raw = _object(raw, BindingDecision)
    return BindingDecision(_text(raw["obligation_id"]), _hash(raw["source_sha256"]),
        SupportStatus(_text(raw["support_status"])), _array(raw["predicate_ids"], _text),
        _array(raw["evidence_ids"], _text), _array(raw["diagnostics"], _diagnostic),
        _array(raw["uncovered"], _span), None if raw["catalog_sha256"] is None else _hash(raw["catalog_sha256"]),
        _text(raw["engine_version"]), _array(raw["uncovered_context_refs"], _source_ref),
        None if raw["context_id"] is None else _hash(raw["context_id"]))


def decode_source_binding(raw, requirement, source_context=None):
    if raw is None:
        return None
    from reqmap.binding_source import bind_source
    from reqmap.models import to_dict
    from reqmap.export_json import canonical_json_bytes
    from reqmap.binding_runtime import requirement_context
    binding = requirement_context(requirement, None, source_context).source_binding
    if canonical_json_bytes(raw) != canonical_json_bytes(to_dict(binding)):
        raise ValueError("SOURCE_BINDING_CHANGED")
    return binding
