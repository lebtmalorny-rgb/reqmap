"""Strict decoding of persisted binding decisions; replay remains authoritative."""
from dataclasses import fields
import re

from reqmap.binding_models import BindingDecision, BindingDiagnostic, SourceSpan
from reqmap.models import SupportStatus


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
        source_spans=_array(raw["source_spans"], _span), evidence_ids=_array(raw["evidence_ids"], _text))


def decode_binding_decision(raw):
    if raw is None:
        return None
    raw = _object(raw, BindingDecision)
    return BindingDecision(_text(raw["obligation_id"]), _hash(raw["source_sha256"]),
        SupportStatus(_text(raw["support_status"])), _array(raw["predicate_ids"], _text),
        _array(raw["evidence_ids"], _text), _array(raw["diagnostics"], _diagnostic),
        _array(raw["uncovered"], _span), None if raw["catalog_sha256"] is None else _hash(raw["catalog_sha256"]),
        _text(raw["engine_version"]))
