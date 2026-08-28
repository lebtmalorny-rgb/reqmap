"""Deterministic Russian Markdown export for canonical deep results."""

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from reqmap.export_deep_markdown import write_deep_markdown
from tests.test_export_deep_json import FORBIDDEN_METADATA, deep_run


def problematic_run():
    run = deep_run()
    result = run.requirements[0]
    atom_result = replace(
        result.atom_results[0],
        diagnostics=("evidence_conflict:EV-NOVA-CREATE",),
    )
    graph = replace(
        run.procedure_graphs[0],
        diagnostics=("rollback_unverified:PROC-NOVA",),
    )
    record = replace(
        run.responsibility_records[0],
        diagnostics=("responsibility_ambiguous:REQ-0001-A001",),
    )
    changed_result = replace(
        result,
        atom_results=(atom_result,),
        diagnostics=(
            "evidence_conflict:EV-NOVA-CREATE",
            "rollback_unverified:PROC-NOVA",
        ),
    )
    return replace(
        run,
        run_status="PARTIAL",
        requirements=(changed_result,),
        responsibility_records=(record,),
        procedure_graphs=(graph,),
        diagnostics=("processing_notice:synthetic",),
    )


def test_deep_markdown_exposes_contours_links_versions_gaps_and_trust(
    tmp_path: Path,
) -> None:
    run = problematic_run()
    path = tmp_path / "report.md"

    digest = write_deep_markdown(run, path)
    text = path.read_text(encoding="utf-8")

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (
        '<!-- reqmap-counts:{"atoms":1,"diagnostics":6,"evidence":1,'
        '"procedure_steps":1,"requirements":1,"responsibilities":1} -->'
    ) in text
    for heading in (
        "## Сводка",
        "## Контуры ответственности",
        "## Executor → target",
        "## Версии и область применимости",
        "## Процедуры",
        "## Конфликты evidence",
        "## Предупреждения procedures и rollback",
        "## Ошибки обработки",
    ):
        assert heading in text
    assert "openstack_runtime" in text
    assert "ACTOR-NOVA-API → TARGET-NOVA-SERVER" in text
    assert "2025.1" in text
    assert "rollback_unverified:PROC-NOVA" in text
    assert "evidence_conflict:EV-NOVA-CREATE" in text
    assert "reqmap-maintenance-2026" in text
    assert "reqmap-snapshot" in text
    assert "local-model" in text
    assert text.count(run.requirements[0].requirement.text) >= 3
    for key, value in FORBIDDEN_METADATA.items():
        assert key not in text
        if isinstance(value, str):
            assert value not in text
    assert "allowed_signers" not in text
    assert "source_url" not in text
    assert "raw_model" not in text


def test_deep_markdown_escapes_table_cells_and_preserves_full_problem_text(
    tmp_path: Path,
) -> None:
    run = problematic_run()
    result = run.requirements[0]
    source_text = "Полный <текст> | требования\\строка\nвторая строка"
    atom_result = replace(
        result.atom_results[0],
        atom=replace(
            result.atom_results[0].atom,
            text="<атом>|значение",
            source_quote="<текст> | требования",
        ),
    )
    changed = replace(
        run,
        requirements=(
            replace(
                result,
                requirement=replace(result.requirement, text=source_text),
                atom_results=(atom_result,),
            ),
        ),
    )
    path = tmp_path / "escaped.md"

    write_deep_markdown(changed, path)
    text = path.read_text(encoding="utf-8")

    escaped = "Полный &lt;текст&gt; \\| требования\\\\строка<br>вторая строка"
    assert escaped in text
    assert "<текст> | требования" not in text


def test_deep_markdown_is_deterministic_and_rejects_symlink(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    run = problematic_run()

    assert write_deep_markdown(run, first) == write_deep_markdown(run, second)
    assert first.read_bytes() == second.read_bytes()

    target = tmp_path / "target.md"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "linked.md"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        write_deep_markdown(run, link)
    assert target.read_text(encoding="utf-8") == "keep"
