"""Closed grammar over complete source rows; never trust model semantics."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from reqmap.binding_models import (
    BoundConstraint, BoundObligation, SourceBinding, SourceFragment, SourceSpan,
)
from reqmap.models import AtomicClaim, Requirement
from reqmap.proposals import ProposalError


GRAMMAR_VERSION = "1.3"
# The executable grammar is part of context/resume identity, including algorithm changes.
GRAMMAR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_QUALIFIER = r"(?:за одну миллисекунду|за [0-9]{1,12} мс|при отказе узла|с GPU)"
_ENDING = r"(?: через (?P<interface>REST API|API|GUI))?(?P<conditions>(?: " + _QUALIFIER + r")*)\.?"
_PREFIX = r"(?:(?P<actor>Nova|Neutron|Cinder|Keystone|Система) (?P<neg>не )?долж(?:на|ен) )?"
_ACCUSATIVE = r"виртуальную машину|ВМ|сеть|сетевой порт|порт|блочный том|том|пользователя|проект|роль"
_GENITIVE = r"виртуальной машины|ВМ|сети|сетевого порта|порта|блочного тома|тома|пользователя|проекта|роли"
_LOCATIVE = r"виртуальной машине|ВМ|сети|сетевом порте|порте|блочном томе|томе|пользователе|проекте|роли"
_RULES = (
    ("api.resource.verbal.1", re.compile(_PREFIX +
        r"(?P<action>создавать|создать|удалять|удалить|переименовывать|переименовать) "
        r"(?P<object>" + _ACCUSATIVE + r")" + _ENDING, re.IGNORECASE)),
    ("api.resource.nominal.1", re.compile(_PREFIX + r"(?:обеспечивать )?"
        r"(?P<action>создание|удаление|переименование) "
        r"(?P<object>" + _GENITIVE + r")" + _ENDING, re.IGNORECASE)),
    ("api.resource.read.1", re.compile(_PREFIX + r"(?:обеспечивать )?"
        r"(?P<action>получать сведения о|получить сведения о|получение сведений о) "
        r"(?P<object>" + _LOCATIVE + r")" + _ENDING, re.IGNORECASE)),
)
_QUALIFIER_PATTERN = re.compile(_QUALIFIER, re.IGNORECASE)
_ACTIONS = {
    **dict.fromkeys(("создавать", "создать", "создание"), "create"),
    **dict.fromkeys(("удалять", "удалить", "удаление"), "delete"),
    **dict.fromkeys(("переименовывать", "переименовать", "переименование"), "rename"),
    **dict.fromkeys(("получать сведения о", "получить сведения о", "получение сведений о"), "read"),
}
_OBJECTS = {
    **dict.fromkeys(("виртуальную машину", "виртуальной машины", "виртуальной машине", "вм"), ("nova", "vm")),
    **dict.fromkeys(("сеть", "сети"), ("neutron", "network")),
    **dict.fromkeys(("сетевой порт", "сетевого порта", "сетевом порте", "порт", "порта", "порте"), ("neutron", "port")),
    **dict.fromkeys(("блочный том", "блочного тома", "блочном томе", "том", "тома", "томе"), ("cinder", "volume")),
    **dict.fromkeys(("пользователя", "пользователе"), (None, "user")),
    # Generic identity nouns do not establish the authorization contour.
    **dict.fromkeys(("проект", "проекта", "проекте"), (None, "project")),
    **dict.fromkeys(("роль", "роли"), (None, "role")),
}

# A closed API-label grammar, not endpoint extraction from arbitrary prose.
# Both the complete Russian label and the case-sensitive method/path must agree.
# Keep this table in the executable grammar file so its identity is frozen too.
_API_ROWS = (
    ("Создание тома", "POST /v3/{project_id}/volumes", "cinder", "create", "volume"),
    ("Список доступных томов с деталями", "GET /v3/{project_id}/volumes/detail", "cinder", "list_detail", "volume"),
    ("Список доступных томов", "GET /v3/{project_id}/volumes", "cinder", "list", "volume"),
    ("Показ деталей тома", "GET /v3/{project_id}/volumes/{volume_id}", "cinder", "read", "volume"),
    ("Удаление тома", "DELETE /v3/{project_id}/volumes/{volume_id}", "cinder", "delete", "volume"),
    ("Создание проекта", "POST /v3/projects", "keystone", "create", "project"),
    ("Удаление проекта", "DELETE /v3/projects/{project_id}", "keystone", "delete", "project"),
    ("Создание роли", "POST /v3/roles", "keystone", "create", "role"),
    ("Удаление роли", "DELETE /v3/roles/{role_id}", "keystone", "delete", "role"),
    ("Назначение роли пользователю проекта", "PUT /v3/projects/{project_id}/users/{user_id}/roles/{role_id}", "keystone", "assign", "project_user_role"),
    ("Назначение роли пользователю домена", "PUT /v3/domains/{domain_id}/users/{user_id}/roles/{role_id}", "keystone", "assign", "domain_user_role"),
    ("Удаление пользователя", "DELETE /v3/users/{user_id}", "keystone", "delete", "user"),
)
_API_RULES = tuple((
    f"api.endpoint.{actor}.{resource}.{action}.1",
    re.compile(r"(?i:" + re.escape(label) + r"): (?:вызов )?"
               + re.escape(operation) + r"[.;]?[ \t]*"),
    actor, action, resource,
) for label, operation, actor, action, resource in _API_ROWS)


def _parse_api_clause(req: Requirement, text: str, offset: int, ordinal: int):
    matches = [(rule_id, actor, action, resource)
               for rule_id, pattern, actor, action, resource in _API_RULES
               if pattern.fullmatch(text)]
    if len(matches) != 1:
        return None
    rule_id, actor, action, resource = matches[0]
    span = SourceSpan(offset, offset + len(text), text)
    return BoundObligation(
        f"{req.requirement_id}-O{ordinal:03d}", req.requirement_id, (span,), text,
        "bound", rule_id, True, actor, action, resource, "capability", "api",
        "openstack_runtime", "runtime", "2025.1",
    )


def _placeholder(requirement: Requirement, state="unresolved") -> BoundObligation:
    span = SourceSpan(0, len(requirement.text), requirement.text)
    return BoundObligation(f"{requirement.requirement_id}-O001", requirement.requirement_id,
        (span,), span.quote, state, None, True,
        None, None, None, None, None, None, None, None)


def _parse_clause(req: Requirement, text: str, offset: int, ordinal: int):
    api_clause = _parse_api_clause(req, text, offset, ordinal)
    if api_clause is not None:
        return api_clause
    matches = [(rule_id, pattern.fullmatch(text)) for rule_id, pattern in _RULES]
    matches = [(rule_id, match) for rule_id, match in matches if match is not None]
    if len(matches) != 1:
        return None
    rule_id, match = matches[0]
    owner, resource = _OBJECTS[match["object"].casefold()]
    actor = (match["actor"] or "система").casefold()
    if actor == "система":
        actor = owner
    if actor is None:
        return None
    conditions = []
    names = set()
    for condition in _QUALIFIER_PATTERN.finditer(text, match.start("conditions"), match.end("conditions")):
        quote = condition.group()
        normalized = quote.casefold()
        if normalized.startswith("за "):
            name, operator, value, unit = "duration", "within", (
                "1" if normalized == "за одну миллисекунду" else str(int(normalized.split()[1]))), "ms"
        elif normalized == "при отказе узла":
            name, operator, value, unit = "host_failure", "eq", "true", None
        else:
            name, operator, value, unit = "gpu", "eq", "true", None
        if name in names:
            return "ambiguous"
        names.add(name)
        conditions.append(BoundConstraint(name, operator, value, unit,
            (SourceSpan(offset + condition.start(), offset + condition.end(), quote),)))
    span = SourceSpan(offset, offset + len(text), text)
    return BoundObligation(
        f"{req.requirement_id}-O{ordinal:03d}", req.requirement_id, (span,), text,
        "bound", rule_id, True, actor,
        _ACTIONS[match["action"].casefold()], resource,
        "prohibition" if match["neg"] else "capability",
        (match["interface"] or "unspecified").casefold().removeprefix("rest "),
        "openstack_runtime", "runtime", "2025.1", tuple(conditions),
    )


def bind_source(requirement: Requirement) -> SourceBinding:
    if type(requirement) is not Requirement or type(requirement.text) is not str or not requirement.text.strip():
        raise ValueError("SOURCE_INVALID: требуется непустой canonical Requirement.")
    text = requirement.text
    fragments, obligations = [], []
    offset = 0
    # Only self-contained, completely recognized clauses can form a sequence.
    # A single endpoint row can end in '; ' without creating an empty clause.
    # Never strip those characters: they belong to the canonical source quote.
    clauses = [text] if _parse_api_clause(requirement, text, 0, 1) is not None else text.split("; ")
    for ordinal, clause in enumerate(clauses, 1):
        obligation = _parse_clause(requirement, clause, offset, ordinal)
        if obligation is None or obligation == "ambiguous":
            placeholder = _placeholder(requirement, "ambiguous" if obligation == "ambiguous" else "unresolved")
            obligations = [placeholder]
            fragments = [SourceFragment(placeholder.source_spans[0], "unresolved", None)]
            break
        obligations.append(obligation)
        fragments.append(SourceFragment(obligation.source_spans[0], "obligation", obligation.rule_id))
        offset += len(clause)
        if ordinal != len(clauses):
            fragments.append(SourceFragment(SourceSpan(offset, offset + 2, "; "), "syntax", "sequence.semicolon.1"))
            offset += 2
    return SourceBinding(
        requirement.requirement_id, requirement.coordinate, text,
        hashlib.sha256(text.encode("utf-8")).hexdigest(), tuple(fragments), tuple(obligations),
        tuple(f.span for f in fragments if f.kind == "unresolved"), GRAMMAR_VERSION, GRAMMAR_SHA256,
    )


def canonical_atoms(binding: SourceBinding) -> tuple[AtomicClaim, ...]:
    return tuple(AtomicClaim(
        f"{binding.requirement_id}-A{ordinal:03d}", binding.requirement_id,
        item.source_quote, item.source_quote, True, ordinal,
        item.obligation_id, item.source_spans, binding.source_sha256,
    ) for ordinal, item in enumerate(binding.obligations, 1))


def atom_selection_proposal(binding: SourceBinding) -> dict:
    """Public contract example with exact occurrences; no semantic model claims."""
    return {"proposal_schema_version": 2, "atoms": [
        {"text": item.source_quote, "source_quote": item.source_quote, "mandatory": True,
         "source_span": {"start": item.source_spans[0].start, "end": item.source_spans[0].end}}
        for item in binding.obligations
    ]}


def validate_atom_selection(binding: SourceBinding, proposal: object) -> tuple[AtomicClaim, ...]:
    def fail(code, detail):
        raise ProposalError("semantic", (f"{code}: {detail}",))
    if (type(proposal) is not dict or set(proposal) != {"proposal_schema_version", "atoms"}
            or type(proposal.get("proposal_schema_version")) is not int or proposal["proposal_schema_version"] != 2
            or type(proposal.get("atoms")) is not list or not proposal["atoms"]):
        raise ProposalError("shape", ("PROPOSAL_SCHEMA: требуются proposal_schema_version=2 и atoms.",))
    actual = []
    for item in proposal["atoms"]:
        if type(item) is not dict or set(item) != {"text", "source_quote", "source_span", "mandatory"}:
            raise ProposalError("shape", ("PROPOSAL_SCHEMA: неверные поля атома.",))
        if type(item["text"]) is not str or not item["text"].strip():
            raise ProposalError("shape", ("PROPOSAL_SCHEMA: text должен быть непустой строкой.",))
        if item["mandatory"] is not True:
            fail("SOURCE_MANDATORY", "обязательность задаёт backend, требуется true.")
        span = item["source_span"]
        if type(span) is not dict or set(span) != {"start", "end"}:
            fail("SOURCE_SPAN_INVALID", "требуются start/end.")
        start, end, quote = span["start"], span["end"], item["source_quote"]
        if (type(start) is not int or type(end) is not int or type(quote) is not str
                or not (0 <= start < end <= len(binding.source_text))
                or binding.source_text[start:end] != quote):
            fail("SOURCE_SPAN_INVALID", "смещение или цитата не соответствует источнику.")
        if any(start < other_end and other_start < end for other_start, other_end in actual):
            fail("SOURCE_SPAN_INVALID", "вхождения пересекаются или повторяются.")
        actual.append((start, end))
    expected = [(i.source_spans[0].start, i.source_spans[0].end) for i in binding.obligations]
    if sorted(actual) != expected:
        fail("SOURCE_COVERAGE_GAP", "proposal должен представлять все canonical обязательства целиком.")
    return canonical_atoms(binding)


def decode_atomic_claim(raw: object) -> AtomicClaim:
    """Decode current atoms or historical unbound atoms without dropping proof fields."""
    base = {"atom_id", "requirement_id", "text", "source_quote", "mandatory", "ordinal"}
    extra = {"obligation_id", "source_spans", "source_sha256"}
    if type(raw) is not dict or set(raw) not in (base, base | extra):
        raise ValueError("Некорректные поля AtomicClaim.")
    for key in ("atom_id", "requirement_id", "text", "source_quote"):
        if type(raw[key]) is not str or not raw[key]:
            raise ValueError("Некорректная строка AtomicClaim.")
    if type(raw["mandatory"]) is not bool or type(raw["ordinal"]) is not int or raw["ordinal"] < 1:
        raise ValueError("Некорректные mandatory/ordinal AtomicClaim.")
    oid, digest, spans = raw.get("obligation_id"), raw.get("source_sha256"), raw.get("source_spans", [])
    if type(spans) is not list:
        raise ValueError("Некорректные spans AtomicClaim.")
    decoded = []
    for value in spans:
        if type(value) is not dict or set(value) != {"start", "end", "quote"}:
            raise ValueError("Некорректный source span.")
        decoded.append(SourceSpan(value["start"], value["end"], value["quote"]))
    if oid is None and digest is None and not decoded:
        pass  # Historical unbound representation; never authorizes binding support.
    elif (type(oid) is not str or not oid.startswith(raw["requirement_id"] + "-O")
          or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None
          or len(decoded) != 1 or decoded[0].quote != raw["source_quote"]
          or raw["text"] != raw["source_quote"] or raw["mandatory"] is not True):
        raise ValueError("Неполный или некорректный source binding AtomicClaim.")
    return AtomicClaim(*(raw[key] for key in ("atom_id", "requirement_id", "text", "source_quote", "mandatory", "ordinal")),
                       oid, tuple(decoded), digest)
