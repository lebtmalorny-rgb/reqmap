"""Deterministic Russian engineering report for canonical deep results."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

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
    marker = json.dumps(
        canonical_counts(run), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    sections = (
        _summary(run),
        _contours(run),
        _executor_targets(run),
        _versions(run),
        _procedures(run),
        _evidence_conflicts(run),
        _procedure_warnings(run),
        _problematic_entities(run),
        _processing_failures(run),
    )
    return (
        "# Глубокий отчёт reqmap\n\n"
        f"<!-- reqmap-counts:{marker} -->\n\n"
        + "\n\n".join(sections)
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


def _contours(run: DeepRunResult) -> str:
    by_contour = Counter(record.contour for record in run.responsibility_records)
    lines = [
        "## Контуры ответственности", "",
        "| Контур | Количество записей |", "|---|---:|",
    ]
    lines.extend(
        f"| {_cell(contour.value)} | {by_contour[contour]} |"
        for contour in ResponsibilityContour
    )
    lines.extend(("", "| Record ID | Requirement ID | Atom ID | Контур | Component | Статус |", "|---|---|---|---|---|---|"))
    lines.extend(
        "| " + " | ".join(_cell(value) for value in (
            record.record_id, record.requirement_id, record.atomic_claim_id,
            record.contour.value, record.component_ref, record.support_status.value,
        )) + " |"
        for record in run.responsibility_records
    )
    if not run.responsibility_records:
        lines.append("| — | — | — | — | — | — |")
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
    lines = [
        "## Процедуры", "",
        "| Graph / Step | Requirement | Phase | Contour | Executor → target | Action | Depends on | Rollback | Evidence IDs | Criteria |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for graph in run.procedure_graphs:
        for step in graph.steps:
            lines.append(
                "| " + " | ".join(_cell(value) for value in (
                    f"{graph.graph_id} / {step.step_id}", graph.requirement_id,
                    step.phase.value, step.contour.value,
                    f"{step.executor_ref} → {step.target_ref}", step.action_ref,
                    _json_text(step.depends_on), step.rollback_step_id,
                    _json_text(step.evidence_ids), _json_text(step.success_criteria),
                )) + " |"
            )
    if not run.procedure_graphs:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _evidence_conflicts(run: DeepRunResult) -> str:
    rows = tuple(row for row in _diagnostic_rows(run) if str(row[7]).startswith("evidence_conflict"))
    lines = ["## Конфликты evidence", ""]
    if not rows:
        return "\n".join((*lines, "Конфликты evidence отсутствуют."))
    lines.extend((
        "| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Диагностика |",
        "|---|---|---|---|---|---|---|",
    ))
    lines.extend("| " + " | ".join(_cell(value) for value in row[1:]) + " |" for row in rows)
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    cited = tuple(dict.fromkeys(
        diagnostic.split(":", 1)[1]
        for *_, diagnostic in rows
        if ":" in diagnostic
    ))
    selected = tuple(evidence_by_id[item] for item in cited if item in evidence_by_id)
    if selected:
        lines.extend(("", "| Evidence ID | Polarity | Strength | Source ID | Locator | Claim |", "|---|---|---|---|---|---|"))
        lines.extend(
            "| " + " | ".join(_cell(value) for value in (
                item.evidence_id, item.polarity.value, item.strength.value,
                item.source_id, item.locator, item.claim,
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
    if not rows:
        return "\n".join((*lines, "Предупреждения отсутствуют."))
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
    if not rows:
        return "\n".join((*lines, "Проблемные требования и атомы отсутствуют."))
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
    if not rows:
        return "\n".join((*lines, "Ошибки обработки отсутствуют."))
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
    )
