"""Русский Markdown-отчёт строится только из canonical RunResult."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from reqmap.aggregation import aggregate_groups, aggregate_requirement
from reqmap.export_markdown import write_markdown
from reqmap.models import (
    AnalysisState,
    ImplementationSource,
    ImplementationStep,
    Phase,
    RelationType,
    SupportStatus,
)
from tests.test_json_manifest import completed_run


def test_markdown_report_has_required_russian_sections_and_counts_marker(
    tmp_path: Path,
) -> None:
    run = completed_run()
    path = tmp_path / "report.md"

    digest = write_markdown(run, path)

    text = path.read_text(encoding="utf-8")
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    for heading in (
        "# Отчёт reqmap",
        "## Сводка",
        "## Компоненты",
        "## Runtime и design-time",
        "## Изменения хостовой ОС",
        "## Неподтверждённые требования",
        "## Ошибки обработки",
    ):
        assert heading in text
    assert (
        '<!-- reqmap-counts:{"atoms":1,"evidence":1,"mappings":1,'
        '"requirements":1} -->'
    ) in text
    assert "Статус запуска: `SUCCESS`" in text
    assert "Состояния обработки" in text
    assert "Статусы поддержки" in text


def test_markdown_preserves_full_unconfirmed_text_and_escapes_table_cells(
    tmp_path: Path,
) -> None:
    run = completed_run()
    source_result = run.requirements[0]
    source_atom = source_result.atom_results[0]
    source_mapping = source_atom.mappings[0]
    source = replace(
        source_result.requirement,
        text=(
            "Полный | текст\r\n<требования>"
            "<!-- reqmap-counts:{} -->"
        ),
    )
    partial_mapping = replace(
        source_mapping,
        support_status=SupportStatus.PARTIAL,
        reason_ru="Поддержано | частично\nнужна проверка",
    )
    partial_atom = replace(
        source_atom,
        atom=replace(
            source_atom.atom,
            source_quote=source.text,
        ),
        support_status=SupportStatus.PARTIAL,
        mappings=(partial_mapping,),
        supported_aspects=("Создание",),
        unconfirmed_aspects=("Удаление | ресурсов",),
    )
    partial_result = replace(
        source_result,
        requirement=source,
        support_status=SupportStatus.PARTIAL,
        atom_results=(partial_atom,),
        mappings=(partial_mapping,),
    )
    run = replace(
        run,
        requirements=(partial_result,),
        groups=aggregate_groups((partial_result,)),
    )
    path = tmp_path / "partial.md"

    write_markdown(run, path)

    text = path.read_text(encoding="utf-8")
    assert (
        "Полный \\| текст<br>&lt;требования&gt;"
        "&lt;!-- reqmap-counts:{} --&gt;"
    ) in text
    assert text.count("<!-- reqmap-counts:") == 1
    assert "Поддержано \\| частично<br>нужна проверка" in text
    assert "Удаление \\| ресурсов" in text
    assert "| partial |" in text


def test_markdown_reports_processing_failure_without_support_status(
    tmp_path: Path,
) -> None:
    run = completed_run()
    source_result = run.requirements[0]
    failed = replace(
        source_result,
        requirement=replace(
            source_result.requirement,
            text="Требование с ошибкой модели",
        ),
        analysis_state=AnalysisState.MODEL_FAILED,
        support_status=None,
        atom_results=(),
        mappings=(),
        diagnostics=("Локальная модель недоступна",),
    )
    run = replace(
        run,
        run_status="FAILED",
        requirements=(failed,),
        groups=aggregate_groups((failed,)),
        evidence=(),
    )
    path = tmp_path / "failed.md"

    write_markdown(run, path)

    text = path.read_text(encoding="utf-8")
    assert "REQ-0001" in text
    assert "Требование с ошибкой модели" in text
    assert "model_failed" in text
    assert "Локальная модель недоступна" in text


def test_markdown_reports_completed_processing_warning(tmp_path: Path) -> None:
    run = completed_run()
    source_result = run.requirements[0]
    warned_atom = replace(
        source_result.atom_results[0],
        diagnostics=("Ответ модели был исправлен валидатором",),
    )
    warned_result = aggregate_requirement(
        source_result.requirement,
        (warned_atom,),
    )
    run = replace(
        run,
        requirements=(warned_result,),
        groups=aggregate_groups((warned_result,)),
    )
    path = tmp_path / "warning.md"

    write_markdown(run, path)

    text = path.read_text(encoding="utf-8")
    assert "Ответ модели был исправлен валидатором" in text
    assert "| completed |" in text


def test_markdown_host_os_table_contains_subsystem_and_kolla_step(
    tmp_path: Path,
) -> None:
    run = completed_run()
    result = run.requirements[0]
    atom_result = result.atom_results[0]
    original = atom_result.mappings[0]
    kolla_step = ImplementationStep(
        order=1,
        phase=Phase.DESIGNTIME,
        action_ru="Выполнить kolla-ansible reconfigure",
        mechanism="kolla_ansible",
        command="kolla-ansible reconfigure",
    )
    host_step = ImplementationStep(
        order=1,
        phase=Phase.DESIGNTIME,
        action_ru="Настроить nftables",
        mechanism="nftables",
    )
    kolla = replace(
        original,
        component_id="kolla_ansible",
        relation=RelationType.CONFIGURES,
        phase=Phase.DESIGNTIME,
        implementation_source=ImplementationSource.KOLLA_ANSIBLE,
        mechanism="kolla_ansible",
        steps=(kolla_step,),
    )
    host = replace(
        original,
        mapping_id="REQ-0001-A001-M002",
        component_id="host_os_nftables",
        relation=RelationType.HOST_OS_CHANGE,
        phase=Phase.DESIGNTIME,
        implementation_source=ImplementationSource.KOLLA_ANSIBLE,
        mechanism="nftables",
        steps=(host_step,),
        evidence_ids=("evidence-2",),
    )
    mapped_atom = replace(atom_result, mappings=(kolla, host))
    mapped_result = replace(
        result,
        atom_results=(mapped_atom,),
        mappings=(kolla, host),
    )
    evidence = replace(
        run.evidence[0],
        evidence_id="evidence-2",
        component_id="host_os_nftables",
        capability_id="CAP-NFTABLES",
        source_id="SRC-NFTABLES",
        local_path="sources/nftables.md",
    )
    run = replace(
        run,
        requirements=(mapped_result,),
        groups=aggregate_groups((mapped_result,)),
        evidence=(*run.evidence, evidence),
    )
    path = tmp_path / "host-os.md"

    write_markdown(run, path)

    text = path.read_text(encoding="utf-8")
    assert "host_os_nftables" in text
    assert "kolla-ansible reconfigure" in text
    assert "Настроить nftables" in text


def test_markdown_is_deterministic_and_rejects_symlink(tmp_path: Path) -> None:
    run = completed_run()
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"

    first_digest = write_markdown(run, first)
    second_digest = write_markdown(run, second)

    assert first_digest == second_digest
    assert first.read_bytes() == second.read_bytes()

    target = tmp_path / "target.md"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        write_markdown(run, link)
    assert target.read_text(encoding="utf-8") == "keep"
