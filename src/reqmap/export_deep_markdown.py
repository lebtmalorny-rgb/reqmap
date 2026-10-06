"""Deterministic Russian engineering report for canonical deep results."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from reqmap.analysis_origin import origin_text

from reqmap.deep_models import DeepRunResult, ResponsibilityContour
from reqmap.export_deep_json import _deep_run_payload, validate_deep_run_result
from reqmap.export_deep_xlsx import _diagnostic_rows, canonical_counts
from reqmap.export_json import atomic_write_bytes
from reqmap.models import AnalysisState, SupportStatus


_PROBLEM_STATUSES = frozenset(
    {SupportStatus.PARTIAL, SupportStatus.NOT_SUPPORTED, SupportStatus.INSUFFICIENT_EVIDENCE}
)


def write_deep_markdown(run: DeepRunResult, path: Path) -> str:
    """Atomically publish a deterministic, allowlisted deep Markdown report."""
    validate_deep_run_result(run)
    payload = _markdown_bytes(run)
    atomic_write_bytes(path, payload)
    return hashlib.sha256(payload).hexdigest()


def _markdown_bytes(run: DeepRunResult) -> bytes:
    return (_render_markdown(run) + "\n").encode("utf-8")


def _render_markdown(run: DeepRunResult) -> str:
    from reqmap.binding_export import binding_markdown
    marker = json.dumps(
        canonical_counts(run), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    sections = (
        binding_markdown(run),
        _summary(run),
        _requirements_groups(run),
        _atoms(run),
        _contours(run),
        _executor_targets(run),
        _versions(run),
        _procedures(run),
        _evidence_catalog(run),
        _normalized_diagnostics(run),
        _evidence_conflicts(run),
        _procedure_warnings(run),
        _problematic_entities(run),
        _processing_failures(run),
    )
    return (
        "# Глубокий отчёт reqmap\n\n"
        f"<!-- reqmap-counts:{marker} -->\n\n"
        + "\n\n".join(sections)
        + ("\n\nПроисхождение анализа (заявлено клиентом): `" + origin_text(run.metadata["analysis_origin"]) + "`" if "analysis_origin" in run.metadata else "")
    )


def _summary(run: DeepRunResult) -> str:
    counts = canonical_counts(run)
    payload = _deep_run_payload(run)
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    trust = metadata["knowledge_trust"]
    assert isinstance(trust, dict)
    lines = [
        "## Сводка", "", f"Статус запуска: `{_cell(run.run_status)}`", "",
        "| Показатель | Количество |", "|---|---:|",
        f"| Исходные требования | {counts['requirements']} |",
        f"| Атомарные утверждения | {counts['atoms']} |",
        f"| Записи ответственности | {counts['responsibilities']} |",
        f"| Шаги процедур | {counts['procedure_steps']} |",
        f"| Доказательства | {counts['evidence']} |",
        f"| Диагностические записи | {counts['diagnostics']} |", "",
        "### Проверенные метаданные запуска", "",
        "| Параметр | Значение |", "|---|---|",
        f"| Профиль | {_cell(metadata['analysis_profile'])} |",
        f"| Модель | {_cell(metadata['model'])} |",
        f"| Snapshot ID | {_cell(metadata['snapshot_id'])} |",
        f"| Key ID | {_cell(trust['key_id'])} |",
        f"| Signer identity | {_cell(trust['signer_identity'])} |",
        f"| Manifest SHA-256 | {_cell(trust['manifest_sha256'])} |",
    ]
    return "\n".join(lines)


def _requirements_groups(run: DeepRunResult) -> str:
    records = {item.record_id: item for item in run.responsibility_records}
    rows: list[tuple[object, ...]] = []
    for result in run.requirements:
        component_refs = tuple(dict.fromkeys(
            records[item].component_ref
            for item in result.responsibility_ids
        ))
        rows.append((
            "requirement", result.requirement.requirement_id,
            _json_text((result.requirement.requirement_id,)),
            _json_text(result.requirement.group_ids), result.analysis_state.value,
            None if result.support_status is None else result.support_status.value,
            _json_text(tuple(item.atom.atom_id for item in result.atom_results)),
            _json_text(result.responsibility_ids),
            _json_text(result.procedure_graph_ids), _json_text(component_refs),
            _json_text(result.diagnostics), result.requirement.text,
        ))
    for group in run.groups:
        rows.append((
            "group", group.group_id, _json_text(group.source_requirement_ids), "[]",
            _json_text(tuple(item.value for item in group.analysis_states)),
            None if group.support_status is None else group.support_status.value,
            "[]", _json_text(group.responsibility_ids), "[]",
            _json_text(group.component_refs), "[]", None,
        ))
    return _table(
        "Требования и группы",
        (
            "Type", "Entity ID", "Source requirement IDs", "Group IDs",
            "Analysis state", "Support status", "Atom IDs", "Responsibility IDs",
            "Procedure graph IDs", "Component refs", "Diagnostics",
            "Full requirement text",
        ),
        tuple(rows),
    )


def _atoms(run: DeepRunResult) -> str:
    return _table(
        "Атомарные утверждения",
        (
            "Atom ID", "Requirement ID", "Ordinal", "Text", "Source quote",
            "Mandatory", "Analysis state", "Support status", "Responsibility IDs",
            "Supported aspects", "Unconfirmed aspects", "Diagnostics",
        ),
        tuple(
            (
                item.atom.atom_id, item.atom.requirement_id, item.atom.ordinal,
                item.atom.text, item.atom.source_quote, item.atom.mandatory,
                item.analysis_state.value,
                None if item.support_status is None else item.support_status.value,
                _json_text(item.responsibility_ids), _json_text(item.supported_aspects),
                _json_text(item.unconfirmed_aspects), _json_text(item.diagnostics),
            )
            for result in run.requirements
            for item in result.atom_results
        ),
    )


def _contours(run: DeepRunResult) -> str:
    by_contour = Counter(record.contour for record in run.responsibility_records)
    lines = [
        "## Контуры ответственности", "",
        "| Record ID | Requirement ID | Atom ID | Contour | Component | Executor | Target contour | Target | Action | Effect | Phase | Version scope | Evidence IDs | Support status | Related records | Procedure step IDs | Diagnostics |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in (
            record.record_id, record.requirement_id, record.atomic_claim_id,
            record.contour.value, record.component_ref, record.executor_ref,
            record.target_contour.value, record.target_ref, record.action_ref,
            record.effect_ref, record.lifecycle_phase.value,
            _json_text({
                "source_release": record.version_scope.source_release,
                "target_release": record.version_scope.target_release,
                "kolla_ansible_release": record.version_scope.kolla_ansible_release,
                "host_profile": record.version_scope.host_profile,
                "version_constraint": record.version_scope.version_constraint,
            }),
            _json_text(record.evidence_ids), record.support_status.value,
            _json_text(record.related_record_ids),
            _json_text(record.procedure_step_ids), _json_text(record.diagnostics),
        )) + " |"
        for record in run.responsibility_records
    )
    lines.extend(("", "### Количество записей по контурам", "", "| Контур | Количество записей |", "|---|---:|"))
    lines.extend(
        f"| {_cell(contour.value)} | {by_contour[contour]} |"
        for contour in ResponsibilityContour
    )
    return "\n".join(lines)


def _executor_targets(run: DeepRunResult) -> str:
    lines = [
        "## Executor → target", "",
        "| Record ID | Связь | Target contour | Action | Effect | Related records |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in (
            record.record_id,
            f"{record.executor_ref} → {record.target_ref}",
            record.target_contour.value,
            record.action_ref,
            record.effect_ref,
            _json_text(record.related_record_ids),
        )) + " |"
        for record in run.responsibility_records
    )
    if not run.responsibility_records:
        lines.append("| — | — | — | — | — | — |")
    return "\n".join(lines)


def _versions(run: DeepRunResult) -> str:
    lines = [
        "## Версии и область применимости", "",
        "| Record ID | Phase | Source release | Target release | Kolla-Ansible | Host profile | Constraint |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in (
            record.record_id, record.lifecycle_phase.value,
            record.version_scope.source_release, record.version_scope.target_release,
            record.version_scope.kolla_ansible_release, record.version_scope.host_profile,
            record.version_scope.version_constraint,
        )) + " |"
        for record in run.responsibility_records
    )
    if not run.responsibility_records:
        lines.append("| — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _procedures(run: DeepRunResult) -> str:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    lines = [
        "## Процедуры", "",
        "| Graph ID | Requirement ID | Template ID | Graph diagnostics | Step ID | Phase | Contour | Executor | Target | Action | Preconditions | Success criteria | Evidence IDs | Evidence versions | Depends on | Rollback step ID |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for graph in run.procedure_graphs:
        for step in graph.steps:
            lines.append(
                "| " + " | ".join(_cell(value) for value in (
                    graph.graph_id, graph.requirement_id, graph.template_id,
                    _json_text(graph.diagnostics), step.step_id, step.phase.value,
                    step.contour.value, step.executor_ref, step.target_ref,
                    step.action_ref, _json_text(step.preconditions),
                    _json_text(step.success_criteria), _json_text(step.evidence_ids),
                    _json_text(tuple(evidence_by_id[item].version_constraint for item in step.evidence_ids)),
                    _json_text(step.depends_on), step.rollback_step_id,
                )) + " |"
            )
    return "\n".join(lines)


def _evidence_catalog(run: DeepRunResult) -> str:
    return _table(
        "Каталог evidence",
        (
            "Evidence ID", "Claim", "Claim kind", "Polarity", "Strength",
            "Source ID", "Locator", "Version constraint", "Applicable contours",
            "Supports entity refs", "Local excerpt", "Review state",
        ),
        tuple(
            (
                item.evidence_id, item.claim, item.claim_kind, item.polarity.value,
                item.strength.value, item.source_id, item.locator,
                item.version_constraint,
                _json_text(tuple(contour.value for contour in item.applicable_contours)),
                _json_text(item.supports_entity_refs), item.local_excerpt,
                item.review_state,
            )
            for item in run.evidence
        ),
    )


def _normalized_diagnostics(run: DeepRunResult) -> str:
    return _table(
        "Нормализованная диагностика",
        (
            "Order", "Scope", "Entity ID", "Requirement ID",
            "Full requirement text", "Atom ID", "Source quote", "Diagnostic",
        ),
        _diagnostic_rows(run),
    )


def _evidence_conflicts(run: DeepRunResult) -> str:
    rows = tuple(row for row in _diagnostic_rows(run) if row[7] == "evidence_conflict")
    lines = ["## Конфликты evidence", ""]
    lines.extend((
        "| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Диагностика |",
        "|---|---|---|---|---|---|---|",
    ))
    lines.extend("| " + " | ".join(_cell(value) for value in row[1:]) + " |" for row in rows)
    records_by_id = {item.record_id: item for item in run.responsibility_records}
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    selected_ids: list[str] = []
    for result in run.requirements:
        for atom in result.atom_results:
            if "evidence_conflict" not in atom.diagnostics:
                continue
            for record_id in atom.responsibility_ids:
                for evidence_id in records_by_id[record_id].evidence_ids:
                    if evidence_id not in selected_ids:
                        selected_ids.append(evidence_id)
    selected = tuple(
        evidence_by_id[item]
        for item in selected_ids
        if item in evidence_by_id
        and evidence_by_id[item].strength.value == "direct"
        and evidence_by_id[item].polarity.value in {"positive", "negative"}
    )
    lines.extend(("", "| Evidence ID | Polarity | Strength | Source ID | Locator | Version constraint | Claim |", "|---|---|---|---|---|---|---|"))
    lines.extend(
        "| " + " | ".join(_cell(value) for value in (
            item.evidence_id, item.polarity.value, item.strength.value,
            item.source_id, item.locator, item.version_constraint, item.claim,
        )) + " |"
        for item in selected
    )
    return "\n".join(lines)


def _procedure_warnings(run: DeepRunResult) -> str:
    prefixes = ("procedure_gap", "rollback_unverified")
    rows = tuple(row for row in _diagnostic_rows(run) if str(row[7]).startswith(prefixes))
    lines = [
        "## Предупреждения procedures и rollback", "",
        "Диагностики `procedure_gap` и `rollback_unverified` не заменяются придуманными шагами.", "",
    ]
    lines.extend((
        "| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Диагностика |",
        "|---|---|---|---|---|---|---|",
    ))
    lines.extend("| " + " | ".join(_cell(value) for value in row[1:]) + " |" for row in rows)
    return "\n".join(lines)


def _problematic_entities(run: DeepRunResult) -> str:
    lines = ["## Проблемные требования и атомы", ""]
    rows: list[tuple[object, ...]] = []
    for result in run.requirements:
        if result.support_status in _PROBLEM_STATUSES or result.diagnostics:
            rows.append((
                "requirement", result.requirement.requirement_id,
                result.requirement.text, None, None,
                None if result.support_status is None else result.support_status.value,
                _json_text(result.diagnostics),
            ))
        for atom in result.atom_results:
            if atom.support_status in _PROBLEM_STATUSES or atom.diagnostics:
                rows.append((
                    "atom", atom.atom.atom_id, result.requirement.text,
                    atom.atom.atom_id, atom.atom.source_quote,
                    None if atom.support_status is None else atom.support_status.value,
                    _json_text(atom.diagnostics),
                ))
    lines.extend((
        "| Scope | Entity ID | Полный текст требования | Atom ID | Исходная цитата | Статус | Диагностика |",
        "|---|---|---|---|---|---|---|",
    ))
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _processing_failures(run: DeepRunResult) -> str:
    lines = ["## Ошибки обработки", ""]
    rows: list[tuple[object, ...]] = []
    diagnostic_rows = _diagnostic_rows(run)
    for row in diagnostic_rows:
        diagnostic = str(row[7])
        if diagnostic.startswith(("evidence_conflict", "procedure_gap", "rollback_unverified")):
            continue
        rows.append(row[1:])
    for result in run.requirements:
        if result.analysis_state is AnalysisState.COMPLETED:
            continue
        rows.append((
            "requirement_state", result.requirement.requirement_id,
            result.requirement.requirement_id, result.requirement.text,
            None, None, result.analysis_state.value,
        ))
    lines.extend((
        "| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Причина |",
        "|---|---|---|---|---|---|---|",
    ))
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _json_text(value: object) -> str:
    if isinstance(value, tuple):
        value = list(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _table(
    heading: str,
    headers: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
) -> str:
    lines = [
        f"## {heading}", "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in rows
    )
    return "\n".join(lines)


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
        .replace("\u0085", "<br>")
        .replace("\u2028", "<br>")
        .replace("\u2029", "<br>")
    )
