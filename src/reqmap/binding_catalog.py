"""Strict reviewed predicates bound to an immutable legacy/deep KB snapshot."""
from __future__ import annotations

from dataclasses import fields
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from reqmap.agent_input import read_regular_bytes
from reqmap.binding_models import BindingCatalog, BoundConstraint, EvidencePredicate
from reqmap.binding_trust import verify_binding_signature
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.knowledge import KnowledgeBase
from reqmap.knowledge_v2 import KnowledgeBaseV2
from reqmap.models import EvidenceClaimScope, EvidencePolarity, EvidenceStrength
from reqmap.output_safety import symlink_component


_MANIFEST_FIELDS = {"binding_schema_version", "catalog_id", "knowledge_sha256", "release_scope", "files"}
_PREDICATE_FIELDS = {f.name for f in fields(EvidencePredicate)}
_SHA = re.compile(r"[0-9a-f]{64}")
_INTERFACES = {"api", "gui", "cli", "config"}
_CONTOURS = {"openstack_runtime", "kolla_ansible", "host_os"}
_PHASES = {"preflight", "deploy", "runtime", "reconfigure", "upgrade", "migrate", "recover", "verify", "rollback"}


def _fail(code, message):
    raise ReqmapError(code, f"{code}: {message}")


def knowledge_digest(knowledge: KnowledgeBase | KnowledgeBaseV2) -> str:
    if type(knowledge) is KnowledgeBase:
        return knowledge.snapshot_sha256
    if type(knowledge) is KnowledgeBaseV2 and knowledge.trust is not None:
        return knowledge.trust.manifest_sha256
    _fail("BINDING_UNTRUSTED", "требуется проверенная база знаний.")


def _object(payload: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-finite value")
    try:
        result = json.loads(payload.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
        if type(result) is not dict:
            raise ValueError("not an object")
        return result
    except (ValueError, UnicodeError) as exc:
        raise ReqmapError("BINDING_SCHEMA", "BINDING_SCHEMA: требуется строгий JSON object.") from exc


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
        _fail("BINDING_SCHEMA", "ожидается ограниченная непустая строка без управляющих символов.")
    return value


def _strings(value, *, unique=True):
    if type(value) is not list or not value:
        _fail("BINDING_SCHEMA", "ожидается непустой массив строк.")
    result = tuple(_text(item) for item in value)
    if unique and len(set(result)) != len(result):
        _fail("BINDING_SCHEMA", "дублирующиеся ссылки запрещены.")
    return result


def _constraints(value):
    if type(value) is not list:
        _fail("BINDING_SCHEMA", "constraints должны быть массивом.")
    result, names = [], set()
    for item in value:
        if type(item) is not dict or set(item) != {"name", "operator", "value", "unit"}:
            _fail("BINDING_SCHEMA", "некорректный constraint.")
        name, operator, val = (_text(item[key]) for key in ("name", "operator", "value"))
        unit = None if item["unit"] is None else _text(item["unit"])
        if operator not in {"eq", "within"} or name in names:
            _fail("BINDING_SCHEMA", "неизвестный operator или повтор constraint.")
        names.add(name)
        result.append(BoundConstraint(name, operator, val, unit))
    return tuple(result)


def _predicate(raw):
    if set(raw) != _PREDICATE_FIELDS:
        _fail("BINDING_SCHEMA", "недостающие или неизвестные поля predicate.")
    refs = {key: _strings(raw[key], unique=key == "evidence_ids")
            for key in ("evidence_ids", "source_ids", "locators", "source_sha256s")}
    if len({len(value) for value in refs.values()}) != 1:
        _fail("BINDING_SCHEMA", "source metadata должны соответствовать каждому evidence по порядку.")
    if any(_SHA.fullmatch(digest) is None for digest in refs["source_sha256s"]):
        _fail("BINDING_SCHEMA", "некорректный hash source.")
    strings = {key: _text(raw[key]) for key in _PREDICATE_FIELDS - refs.keys()
               - {"action_ref", "effect_ref", "target_ref", "assumptions", "constraints", "polarity"}}
    if (strings["direction"] not in {"capability", "prohibition"}
            or strings["interface"] not in _INTERFACES or strings["contour"] not in _CONTOURS
            or strings["lifecycle_phase"] not in _PHASES):
        _fail("BINDING_SCHEMA", "неизвестное семантическое enum значение.")
    if strings["review_state"] != "reviewed":
        _fail("BINDING_UNREVIEWED", "predicate не прошёл review.")
    try:
        date.fromisoformat(strings["reviewed_at"])
        polarity = EvidencePolarity(raw["polarity"])
    except (ValueError, TypeError):
        _fail("BINDING_SCHEMA", "некорректная дата review или polarity.")
    optional = {key: None if raw[key] is None else _text(raw[key]) for key in ("action_ref", "effect_ref", "target_ref")}
    return EvidencePredicate(**refs, **strings, **optional, polarity=polarity,
                             assumptions=_constraints(raw["assumptions"]), constraints=_constraints(raw["constraints"]))


def _validate_relations(predicate, knowledge):
    deep = type(knowledge) is KnowledgeBaseV2
    capability = knowledge.capabilities.get(predicate.capability_ref)
    component = None if capability is None else (capability.component_ref if deep else capability.component_id)
    if component != predicate.component_ref or predicate.actor != predicate.component_ref:
        _fail("BINDING_RELATION", "неверная capability/component/actor связь.")
    if deep:
        action = knowledge.actions.get(predicate.action_ref)
        effect = knowledge.effects.get(predicate.effect_ref)
        target = knowledge.targets.get(predicate.target_ref)
        if (action is None or effect is None or target is None
                or action.component_ref != predicate.component_ref
                or action.target_ref != target.target_id or effect.target_ref != target.target_id
                or effect.effect_id not in action.effect_refs
                or action.contour.value != predicate.contour
                or {"openstack_api": "api", "kolla_ansible_action": "cli", "host_configuration": "config"}.get(action.interface_type) != predicate.interface
                or action.version_scope.version_constraint != predicate.release_scope):
            _fail("BINDING_RELATION", "неверная action/effect/target/interface/version связь.")
        related_evidence = set(capability.evidence_ids) | set(action.evidence_ids) | set(effect.evidence_ids)
    elif any(value is not None for value in (predicate.action_ref, predicate.effect_ref, predicate.target_ref)):
        _fail("BINDING_RELATION", "legacy predicate не может изобретать deep refs.")
    for index, eid in enumerate(predicate.evidence_ids):
        evidence = knowledge.evidence.get(eid)
        source = knowledge.sources.get(predicate.source_ids[index])
        if evidence is None or source is None or evidence.source_id != predicate.source_ids[index]:
            _fail("BINDING_RELATION", "неизвестное evidence или неверный source.")
        source_hash = source.content_sha256 if deep else source.sha256
        if source.provenance != "official" or evidence.locator != predicate.locators[index] or source_hash != predicate.source_sha256s[index]:
            _fail("BINDING_RELATION", "неофициальный source либо изменённый locator/hash.")
        if evidence.polarity is not predicate.polarity or evidence.version_constraint != predicate.release_scope:
            _fail("BINDING_RELATION", "неверная polarity/version evidence.")
        if evidence.strength is not EvidenceStrength.DIRECT:
            _fail("BINDING_STRENGTH", "для predicate требуется прямое доказательство.")
        if deep:
            entities = {predicate.capability_ref, predicate.action_ref, predicate.effect_ref}
            if (eid not in related_evidence or not entities.intersection(evidence.supports_entity_refs)
                    or predicate.contour not in {contour.value for contour in evidence.applicable_contours}
                    or evidence.review_state != "reviewed" or source.source_type == "project_policy"):
                _fail("BINDING_RELATION", "evidence не принадлежит выбранной deep relation.")
        else:
            if evidence.claim_scope is not EvidenceClaimScope.SPECIFIC:
                _fail("EVIDENCE_CONTEXT_ONLY", "общая справка не подтверждает predicate.")
            if (evidence.component_id != predicate.component_ref or evidence.capability_id != predicate.capability_ref
                    or evidence.provenance != "official"):
                _fail("BINDING_RELATION", "legacy evidence принадлежит другой capability.")


def load_binding_catalog(path: Path, knowledge: KnowledgeBase | KnowledgeBaseV2,
                         allowed_signers_path: Path | None = None) -> BindingCatalog:
    path = path.absolute()
    if symlink_component(path) or not path.is_dir():
        _fail("BINDING_IO", "каталог отсутствует или содержит symlink.")
    try:
        manifest_bytes = read_regular_bytes(path / "binding-manifest.json", 1024 * 1024)
        raw = _object(manifest_bytes)
        if (set(raw) != _MANIFEST_FIELDS or type(raw["binding_schema_version"]) is not int
                or raw["binding_schema_version"] != 1 or canonical_json_bytes(raw) != manifest_bytes):
            _fail("BINDING_SCHEMA", "неверная canonical manifest schema.")
        if raw["knowledge_sha256"] != knowledge_digest(knowledge):
            _fail("BINDING_KNOWLEDGE_MISMATCH", "каталог относится к другой базе.")
        release = knowledge.base_release if type(knowledge) is KnowledgeBaseV2 else knowledge.release
        if raw["release_scope"] != release:
            _fail("BINDING_KNOWLEDGE_MISMATCH", "не совпадает release каталога и KB.")
        files = raw["files"]
        if (type(files) is not list or len(files) != 1 or type(files[0]) is not dict
                or set(files[0]) != {"path", "size", "sha256"} or files[0]["path"] != "predicates.jsonl"
                or type(files[0]["size"]) is not int or not 0 < files[0]["size"] <= 25 * 1024 * 1024
                or type(files[0]["sha256"]) is not str or _SHA.fullmatch(files[0]["sha256"]) is None):
            _fail("BINDING_SCHEMA", "manifest должен описывать ровно predicates.jsonl.")
        data = read_regular_bytes(path / "predicates.jsonl", 25 * 1024 * 1024)
        if len(data) != files[0]["size"] or hashlib.sha256(data).hexdigest() != files[0]["sha256"]:
            _fail("BINDING_INTEGRITY", "hash/размер predicates не совпадает с manifest.")
        signer = None
        if type(knowledge) is KnowledgeBaseV2:
            signer = knowledge.trust.signer_identity
            verify_binding_signature(path, allowed_signers_path, manifest_bytes, signer)
        predicates = {}
        for line in data.splitlines():
            predicate = _predicate(_object(line))
            if predicate.predicate_id in predicates:
                _fail("BINDING_SCHEMA", "повторный predicate_id.")
            if predicate.release_scope != release:
                _fail("BINDING_RELATION", "predicate относится к другой версии.")
            _validate_relations(predicate, knowledge)
            predicates[predicate.predicate_id] = predicate
        if not predicates:
            _fail("BINDING_SCHEMA", "пустой каталог.")
        # Snapshot of exactly verified bytes; fail if named files changed during the load.
        if (read_regular_bytes(path / "binding-manifest.json", 1024 * 1024) != manifest_bytes
                or read_regular_bytes(path / "predicates.jsonl", 25 * 1024 * 1024) != data):
            _fail("BINDING_INTEGRITY", "каталог изменился во время проверки.")
        return BindingCatalog(_text(raw["catalog_id"]), raw["knowledge_sha256"], release,
                              MappingProxyType(predicates), hashlib.sha256(manifest_bytes).hexdigest(),
                              signer_identity=signer)
    except ReqmapError as exc:
        if exc.code.startswith(("BINDING_", "EVIDENCE_")):
            raise
        raise ReqmapError("BINDING_IO", "BINDING_IO: невозможно безопасно прочитать каталог.") from exc
    except OSError as exc:
        raise ReqmapError("BINDING_IO", "BINDING_IO: каталог недоступен.") from exc
