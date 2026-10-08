"""Strict context wire formats: no inferred review, permissive fields or refs."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re

from reqmap.binding_models import BoundConstraint, SourceSpan
from reqmap.errors import ReqmapError
from reqmap.models import SourceCoordinate, to_dict
from reqmap.source_context_models import (
    MAX_CONTEXT_BYTES, MAX_CONTEXT_LINKS, SourceDocumentSnapshot, SourceRowRef,
    SourceFragmentRef, SourceContextLink, SourceContextEntry, SourceContextMap,
)


def _fail(message, code="SOURCE_CONTEXT_INVALID"):
    raise ReqmapError(code, message)


def _keys(raw, keys, code="SOURCE_CONTEXT_INVALID"):
    if type(raw) is not dict or set(raw) != set(keys.split()):
        _fail("Некорректные поля контекста.", code)


def _text(raw):
    if type(raw) is not str or not raw.strip():
        _fail("Ожидалась непустая строка контекста.")
    return raw


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-finite JSON")
    try:
        if type(raw) is not bytes:
            raise ValueError("expected bytes")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReqmapError("SOURCE_CONTEXT_INVALID", "Требуется однозначный JSON контекста.") from exc


def _row_ref(raw, rows):
    code = "SOURCE_CONTEXT_REF_INVALID"
    _keys(raw, "requirement_id coordinate source_sha256", code)
    _keys(raw["coordinate"], "source_name sheet row", code)
    coordinate = raw["coordinate"]
    if (type(raw["requirement_id"]) is not str or type(raw["source_sha256"]) is not str
            or type(coordinate["source_name"]) is not str
            or (coordinate["sheet"] is not None and type(coordinate["sheet"]) is not str)
            or (coordinate["row"] is not None and type(coordinate["row"]) is not int)):
        _fail("Некорректная ссылка строки.", code)
    row = rows.get(raw["requirement_id"])
    if (row is None or to_dict(row.coordinate) != coordinate
            or hashlib.sha256(row.text.encode()).hexdigest() != raw["source_sha256"]):
        _fail("Ссылка не указывает на точную исходную строку.", code)
    return SourceRowRef(row.requirement_id, SourceCoordinate(**coordinate), raw["source_sha256"])


def _fragment_ref(raw, rows):
    code = "SOURCE_CONTEXT_REF_INVALID"
    _keys(raw, "row span", code)
    row = _row_ref(raw["row"], rows)
    _keys(raw["span"], "start end quote", code)
    try:
        span = SourceSpan(**raw["span"])
        if rows[row.requirement_id].text[span.start:span.end] != span.quote:
            raise ValueError("foreign occurrence")
    except (ValueError, TypeError) as exc:
        raise ReqmapError(code, "Некорректное вхождение цитаты контекста.") from exc
    return SourceFragmentRef(row, span)


def _constraint(raw):
    _keys(raw, "name operator value unit")
    name, operator, value, unit = (raw[k] for k in ("name", "operator", "value", "unit"))
    if name == "duration":
        valid = (operator == "within" and unit == "ms" and type(value) is str
                 and re.fullmatch(r"0|[1-9][0-9]{0,11}", value) is not None)
    elif name in ("host_failure", "gpu"):
        valid = operator == "eq" and value == "true" and unit is None
    else:
        valid = False
    if not valid:
        _fail("Неподдерживаемый тип или значение условия контекста.")
    return BoundConstraint(name, operator, value, unit)


def decode_source_context_map(raw: bytes, document: SourceDocumentSnapshot) -> SourceContextMap:
    if type(raw) is not bytes or len(raw) > MAX_CONTEXT_BYTES:
        _fail("Карта контекста превышает допустимый размер.")
    payload = _json(raw)
    _keys(payload, "schema_version map_id source_kind source_name input_sha256 input_profile_sha256 requirements_sha256 rows")
    if payload["schema_version"] != "1.0":
        _fail("Неизвестная версия карты контекста.")
    _text(payload["map_id"])
    for key in ("source_kind", "source_name", "input_sha256", "input_profile_sha256", "requirements_sha256"):
        if payload[key] != getattr(document, key):
            _fail("Карта относится к другому входу или профилю.", "SOURCE_CONTEXT_INPUT_MISMATCH")
    if type(payload["rows"]) is not list:
        _fail("rows должны быть массивом.")
    rows = {r.requirement_id: r for r in document.requirements}
    entries, targets, link_ids = [], set(), set()
    for raw_entry in payload["rows"]:
        _keys(raw_entry, "target disposition review_state reviewed_by reviewed_at reason_ru context_complete links")
        target = _row_ref(raw_entry["target"], rows)
        if target.requirement_id in targets:
            _fail("Повтор target запрещён.")
        targets.add(target.requirement_id)
        disposition, review = raw_entry["disposition"], raw_entry["review_state"]
        complete = raw_entry["context_complete"]
        links = raw_entry["links"]
        _text(raw_entry["reason_ru"])
        if (disposition not in ("independent", "linked", "unresolved")
                or review not in ("draft", "reviewed") or type(complete) is not bool
                or type(links) is not list or len(links) > MAX_CONTEXT_LINKS
                or (disposition == "independent" and links)
                or (disposition == "linked" and not links)
                or (disposition == "unresolved" and complete)):
            _fail("Несогласованные состояние проверки и связи.")
        if review == "draft":
            if raw_entry["reviewed_by"] is not None or raw_entry["reviewed_at"] is not None:
                _fail("Draft не содержит аттестации проверки.")
        else:
            _text(raw_entry["reviewed_by"])
            timestamp = _text(raw_entry["reviewed_at"])
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", timestamp):
                    raise ValueError("not UTC")
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ReqmapError("SOURCE_CONTEXT_INVALID", "Неверное UTC-время review.") from exc
            if disposition in ("independent", "linked") and not complete:
                _fail("Reviewed требует проверки полного контекста.")
        decoded_links = []
        for link in links:
            _keys(link, "link_id origin field value")
            link_id = _text(link["link_id"])
            if link_id in link_ids:
                _fail("Повтор link_id запрещён во всей карте.")
            link_ids.add(link_id)
            origin = _fragment_ref(link["origin"], rows)
            if link["field"] == "interface" and link["value"] in ("api", "gui"):
                value = link["value"]
            elif link["field"] == "constraint":
                value = _constraint(link["value"])
            else:
                _fail("Недопустимое поле или значение связи.")
            decoded_links.append(SourceContextLink(link_id, origin, link["field"], value))
        entries.append(SourceContextEntry(target, disposition, review, raw_entry["reviewed_by"],
                       raw_entry["reviewed_at"], raw_entry["reason_ru"], complete, tuple(decoded_links)))
    return SourceContextMap(**{k: v for k, v in payload.items() if k != "rows"}, rows=tuple(entries))


def encode_source_context_map(mapping: SourceContextMap) -> dict[str, object]:
    raw = to_dict(mapping)
    for entry in raw["rows"]:
        for link in entry["links"]:
            if link["field"] == "constraint":
                del link["value"]["source_spans"]
    return raw
