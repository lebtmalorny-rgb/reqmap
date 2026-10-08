"""Strict context wire formats: no inferred review, permissive fields or refs."""
from __future__ import annotations

from datetime import datetime
import base64
import binascii
import hashlib
import json
import re

from reqmap.binding_models import BoundConstraint, SourceSpan
from reqmap.errors import ReqmapError
from reqmap.models import SourceCoordinate, to_dict
from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from reqmap.source_context_models import (
    MAX_CONTEXT_BYTES, MAX_CONTEXT_LINKS, SourceDocumentSnapshot, SourceRowRef,
    SourceFragmentRef, SourceContextLink, SourceContextEntry, SourceContextMap,
    SourceContextSnapshot, ContextTrust, LoadedSourceContext, SOURCE_CONTEXT_RESOLVER_VERSION,
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


def encode_source_context_snapshot(snapshot: SourceContextSnapshot) -> dict[str, object]:
    raw = to_dict(snapshot)
    raw["document"]["content"] = base64.b64encode(snapshot.document.content).decode("ascii")
    for key in ("map_bytes", "signature_bytes"):
        value = getattr(snapshot.loaded, key)
        raw["loaded"][key] = None if value is None else base64.b64encode(value).decode("ascii")
    raw["loaded"]["mapping"] = None if snapshot.loaded.mapping is None else encode_source_context_map(snapshot.loaded.mapping)
    return raw


def _unbase64(raw):
    if type(raw) is not str:
        raise ValueError("expected base64 string")
    value = base64.b64decode(raw, validate=True)
    if base64.b64encode(value).decode("ascii") != raw:
        raise ValueError("noncanonical base64")
    return value


def _document_record(raw):
    from reqmap.config_common import parse_input_profile
    from reqmap.models import Requirement, SourceField, SourceHint
    from reqmap.source_context import capture_source_document
    _keys(raw, "content source_kind source_name text_mode input_profile input_sha256 input_profile_sha256 requirements_sha256 requirements")
    profile = None if raw["input_profile"] is None else parse_input_profile(raw["input_profile"])
    if type(raw["requirements"]) is not list:
        raise ValueError("invalid requirements")
    requirements = []
    for row in raw["requirements"]:
        _keys(row, "requirement_id source_id text ordinal coordinate parent_id group_ids source_fields source_hints")
        coordinate = SourceCoordinate(**row["coordinate"])
        requirements.append(Requirement(**{k: v for k, v in row.items() if k not in ("coordinate", "group_ids", "source_fields", "source_hints")},
                            coordinate=coordinate, group_ids=tuple(row["group_ids"]),
                            source_fields=tuple(SourceField(**field) for field in row["source_fields"]),
                            source_hints=tuple(SourceHint(**hint) for hint in row["source_hints"])))
    return capture_source_document(content=_unbase64(raw["content"]), source_kind=raw["source_kind"],
        source_name=raw["source_name"], input_profile=profile, text_mode=raw["text_mode"], requirements=tuple(requirements))


def inspect_source_context_record(raw: object) -> SourceContextSnapshot:
    """Prove frozen integrity only. This does not authorize an active deep run."""
    from reqmap.export_json import canonical_json_bytes
    from reqmap.source_context import freeze_source_context, source_context_resolver_sha256
    try:
        _keys(raw, "document loaded decisions")
        if type(raw["decisions"]) is not list or not raw["decisions"]:
            raise ValueError("missing decisions")
        for decision in raw["decisions"]:
            if type(decision) is not dict:
                raise ValueError("invalid decision")
            if (decision.get("resolver_version") != SOURCE_CONTEXT_RESOLVER_VERSION
                    or decision.get("resolver_sha256") != source_context_resolver_sha256()):
                raise ReqmapError("SOURCE_CONTEXT_CONTRACT_MISMATCH", "Версия или код resolver изменены; начните новый анализ.")
        document = _document_record(raw["document"])
        value = raw["loaded"]
        _keys(value, "map_bytes signature_bytes map_sha256 mapping trust")
        _keys(value["trust"], "profile namespace signer_identity signature_sha256 allowed_signers_sha256")
        trust = ContextTrust(**value["trust"])
        map_bytes = None if value["map_bytes"] is None else _unbase64(value["map_bytes"])
        signature = None if value["signature_bytes"] is None else _unbase64(value["signature_bytes"])
        mapping = None if map_bytes is None else decode_source_context_map(map_bytes, document)
        if trust.profile not in ("legacy", "deep"):
            raise ValueError("invalid profile")
        if map_bytes is None or trust.profile == "legacy":
            if signature is not None or trust != ContextTrust(trust.profile, None, None, None, None):
                raise ValueError("inconsistent unsigned context")
        else:
            if (signature is None or len(signature) > 1024*1024
                    or trust.namespace != "reqmap-source-context"
                    or type(trust.signer_identity) is not str or not trust.signer_identity.strip()
                    or trust.signature_sha256 != hashlib.sha256(signature).hexdigest()
                    or type(trust.allowed_signers_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", trust.allowed_signers_sha256) is None):
                raise ValueError("inconsistent signed context")
        loaded = LoadedSourceContext(map_bytes, signature,
            None if map_bytes is None else hashlib.sha256(map_bytes).hexdigest(), mapping, trust)
        snapshot = freeze_source_context(document, loaded)
        # Whole-wire comparison includes normalized profiles, draft maps, bytes,
        # original rows and every semantic field. Equal statuses are insufficient.
        if canonical_json_bytes(encode_source_context_snapshot(snapshot)) != canonical_json_bytes(raw):
            raise ValueError("saved decisions differ from rederived source")
        return snapshot
    except ReqmapError as exc:
        if exc.code == "SOURCE_CONTEXT_CONTRACT_MISMATCH":
            raise
        raise ReqmapError("SOURCE_CONTEXT_CHANGED", "Сохранённый контекст не прошёл повторную проверку.") from exc
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error, RecursionError) as exc:
        raise ReqmapError("SOURCE_CONTEXT_CHANGED", "Сохранённый контекст повреждён или изменён.") from exc


def decode_source_context_snapshot(raw: object, *, profile: AnalysisProfile,
                                    trust: KnowledgeTrustConfig | None) -> SourceContextSnapshot:
    from reqmap.source_context import verify_source_context_signature
    snapshot = inspect_source_context_record(raw)
    if snapshot.loaded.trust.profile != profile.value:
        raise ReqmapError("SOURCE_CONTEXT_CHANGED", "Контекст относится к другому профилю.")
    if profile is AnalysisProfile.DEEP and snapshot.loaded.map_bytes is not None:
        current = verify_source_context_signature(snapshot.loaded.map_bytes, snapshot.loaded.signature_bytes, trust)
        if current.signer_identity != snapshot.loaded.trust.signer_identity:
            raise ReqmapError("SOURCE_CONTEXT_UNTRUSTED", "Identity подписанта карты изменена.")
    return snapshot


def validate_source_context_snapshot(snapshot: SourceContextSnapshot, *, profile: AnalysisProfile,
                                     trust: KnowledgeTrustConfig | None) -> None:
    restored = decode_source_context_snapshot(encode_source_context_snapshot(snapshot), profile=profile, trust=trust)
    if restored != snapshot:
        raise ReqmapError("SOURCE_CONTEXT_CHANGED", "Snapshot контекста изменён.")
