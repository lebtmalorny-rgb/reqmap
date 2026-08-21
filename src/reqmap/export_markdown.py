"""Детерминированный русский инженерный отчёт из canonical RunResult."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from reqmap.export_json import atomic_write_bytes, validate_run_result
from reqmap.export_xlsx import analysis_state_ru, support_status_ru
from reqmap.models import (
    AnalysisState,
    ImplementationStep,
    Mapping,
    RelationType,
    RunResult,
    SupportStatus,
)


_UNCONFIRMED_STATUSES = frozenset(
    {
        SupportStatus.PARTIAL,
        SupportStatus.NOT_SUPPORTED,
        SupportStatus.INSUFFICIENT_EVIDENCE,
    }
)


def write_markdown(run: RunResult, path: Path) -> str:
    """Атомарно записать русский Markdown-отчёт и вернуть SHA-256."""
    validate_run_result(run)
    payload = _markdown_bytes(run)
    atomic_write_bytes(path, payload)
    return hashlib.sha256(payload).hexdigest()


def _markdown_bytes(run: RunResult) -> bytes:
    return (_render_markdown(run) + "\n").encode("utf-8")


def _render_markdown(run: RunResult) -> str:
    counts = _canonical_counts(run)
    marker = json.dumps(
        counts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sections = (
        _render_summary(run),
        _render_components(run),
        _render_phases(run),
        _render_host_os(run),
        _render_unconfirmed(run),
        _render_processing_errors(run),
    )
    return (
        "# Отчёт reqmap\n\n"
        f"<!-- reqmap-counts:{marker} -->\n\n"
        + "\n\n".join(sections)
    )


def _canonical_counts(run: RunResult) -> dict[str, int]:
    return {
        "requirements": len(run.requirements),
        "atoms": sum(len(item.atom_results) for item in run.requirements),
        "mappings": sum(len(item.mappings) for item in run.requirements),
        "evidence": len(run.evidence),
    }


def _render_summary(run: RunResult) -> str:
    counts = _canonical_counts(run)
    analysis_counts = Counter(item.analysis_state for item in run.requirements)
    support_counts = Counter(item.support_status for item in run.requirements)
    lines = [
        "## Сводка",
        "",
        f"Статус запуска: `{run.run_status}`",
        "",
        "| Показатель | Количество |",
        "|---|---:|",
        f"| Исходные требования | {counts['requirements']} |",
        f"| Атомарные утверждения | {counts['atoms']} |",
        f"| Сопоставления | {counts['mappings']} |",
        f"| Доказательства | {counts['evidence']} |",
        "",
        "### Состояния обработки",
        "",
        "| Код | Русское название | Количество требований |",
        "|---|---|---:|",
    ]
    lines.extend(
        f"| {state.value} | {analysis_state_ru(state)} | {analysis_counts[state]} |"
        for state in AnalysisState
    )
    lines.extend(
        (
            "",
            "### Статусы поддержки",
            "",
            "| Код | Русское название | Количество требований |",
            "|---|---|---:|",
        )
    )
    lines.extend(
        f"| {status.value} | {support_status_ru(status)} | "
        f"{support_counts[status]} |"
        for status in SupportStatus
    )
    lines.append(
        f"| не определён | не определён | {support_counts[None]} |"
    )
    return "\n".join(lines)


def _render_components(run: RunResult) -> str:
    by_component: dict[str, list[tuple[str, Mapping]]] = defaultdict(list)
    for result in run.requirements:
        for item in result.mappings:
            by_component[item.component_id].append(
                (result.requirement.requirement_id, item)
            )
    lines = ["## Компоненты", ""]
    if not by_component:
        return "\n".join((*lines, "Сопоставления компонентов отсутствуют."))
    lines.extend(
        (
            "| Component ID | Сопоставлений | Требования | Фазы |",
            "|---|---:|---|---|",
        )
    )
    for component_id in sorted(by_component):
        records = by_component[component_id]
        requirement_ids = tuple(dict.fromkeys(item[0] for item in records))
        phases = tuple(dict.fromkeys(item[1].phase.value for item in records))
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(component_id),
                    str(len(records)),
                    _cell(", ".join(requirement_ids)),
                    _cell(", ".join(phases)),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _render_phases(run: RunResult) -> str:
    mappings = tuple(
        item for result in run.requirements for item in result.mappings
    )
    lines = ["## Runtime и design-time", ""]
    if not mappings:
        return "\n".join((*lines, "Сопоставления фаз отсутствуют."))
    lines.extend(
        (
            "| Фаза | Mapping ID | Atom ID | Component ID | Механизм | Шаги |",
            "|---|---|---|---|---|---|",
        )
    )
    for item in mappings:
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    item.phase.value,
                    item.mapping_id,
                    item.atom_id,
                    item.component_id,
                    item.mechanism,
                    _steps_text(item.steps),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _render_host_os(run: RunResult) -> str:
    by_atom: dict[str, list[Mapping]] = defaultdict(list)
    for result in run.requirements:
        for item in result.mappings:
            by_atom[item.atom_id].append(item)
    rows: list[tuple[str, str, str, str, str]] = []
    for atom_id, mappings in by_atom.items():
        host_mappings = tuple(
            item
            for item in mappings
            if item.relation is RelationType.HOST_OS_CHANGE
            or item.component_id.startswith("host_os_")
        )
        if not host_mappings:
            continue
        kolla_mappings = tuple(
            item
            for item in mappings
            if item.component_id == "kolla_ansible"
            or item.mechanism == "kolla_ansible"
        )
        rows.append(
            (
                atom_id,
                ", ".join(
                    dict.fromkeys(item.component_id for item in host_mappings)
                ),
                "; ".join(
                    _steps_text(item.steps) for item in kolla_mappings
                ),
                "; ".join(
                    _steps_text(item.steps) for item in host_mappings
                ),
                ", ".join(
                    dict.fromkeys(
                        evidence_id
                        for item in (*kolla_mappings, *host_mappings)
                        for evidence_id in item.evidence_ids
                    )
                ),
            )
        )
    lines = ["## Изменения хостовой ОС", ""]
    if not rows:
        return "\n".join((*lines, "Изменения хостовой ОС отсутствуют."))
    lines.extend(
        (
            "| Atom ID | Подсистема ОС | Шаг Kolla-Ansible | "
            "Шаг подсистемы | Evidence IDs |",
            "|---|---|---|---|---|",
        )
    )
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in rows
    )
    return "\n".join(lines)


def _render_unconfirmed(run: RunResult) -> str:
    rows: list[tuple[str, str, str, str]] = []
    for result in run.requirements:
        if result.support_status not in _UNCONFIRMED_STATUSES:
            continue
        reasons = tuple(
            dict.fromkeys(
                (
                    *(item.reason_ru for item in result.mappings),
                    *(
                        aspect
                        for atom_result in result.atom_results
                        for aspect in atom_result.unconfirmed_aspects
                    ),
                    *(
                        diagnostic
                        for atom_result in result.atom_results
                        for diagnostic in atom_result.diagnostics
                    ),
                    *result.diagnostics,
                )
            )
        )
        rows.append(
            (
                result.requirement.requirement_id,
                result.requirement.text,
                result.support_status.value,
                "; ".join(reasons) or "Причина не указана.",
            )
        )
    lines = ["## Неподтверждённые требования", ""]
    if not rows:
        return "\n".join((*lines, "Неподтверждённые требования отсутствуют."))
    lines.extend(
        (
            "| Requirement ID | Полная формулировка | Статус | Причина |",
            "|---|---|---|---|",
        )
    )
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in rows
    )
    return "\n".join(lines)


def _render_processing_errors(run: RunResult) -> str:
    rows: list[tuple[str, str, str, str]] = []
    for result in run.requirements:
        diagnostics = tuple(
            dict.fromkeys(
                (
                    *result.diagnostics,
                    *(
                        diagnostic
                        for atom_result in result.atom_results
                        for diagnostic in atom_result.diagnostics
                    ),
                )
            )
        )
        if result.analysis_state is AnalysisState.COMPLETED and not diagnostics:
            continue
        rows.append(
            (
                result.requirement.requirement_id,
                result.requirement.text,
                result.analysis_state.value,
                "; ".join(diagnostics)
                or analysis_state_ru(result.analysis_state),
            )
        )
    lines = ["## Ошибки обработки", ""]
    if not rows:
        return "\n".join((*lines, "Ошибки обработки отсутствуют."))
    lines.extend(
        (
            "| Requirement ID | Полная формулировка | Состояние | Причина |",
            "|---|---|---|---|",
        )
    )
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in rows
    )
    return "\n".join(lines)


def _steps_text(steps: tuple[ImplementationStep, ...]) -> str:
    rendered: list[str] = []
    for step in steps:
        details = [step.action_ru, f"mechanism={step.mechanism}"]
        if step.command is not None:
            details.append(f"command={step.command}")
        if step.api_operation is not None:
            details.append(f"api_operation={step.api_operation}")
        rendered.append(f"{step.order}. " + "; ".join(details))
    return " ".join(rendered)


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
