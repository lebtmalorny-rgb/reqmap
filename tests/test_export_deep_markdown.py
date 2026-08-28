"""Deterministic Russian Markdown export for canonical deep results."""

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from reqmap.export_deep_markdown import write_deep_markdown
from reqmap.models import EvidencePolarity
from tests.test_export_deep_json import FORBIDDEN_METADATA, deep_run


LINE_BOUNDARIES = (
    pytest.param("\u0085", id="next-line"),
    pytest.param("\u2028", id="line-separator"),
    pytest.param("\u2029", id="paragraph-separator"),
)


def line_boundary_run(separator: str):
    run = deep_run()
    result = run.requirements[0]
    source_quote = f"quoted{separator}source"
    atom_result = replace(
        result.atom_results[0],
        atom=replace(
            result.atom_results[0].atom,
            text=f"atom{separator}text",
            source_quote=source_quote,
        ),
    )
    changed_result = replace(
        result,
        requirement=replace(
            result.requirement,
            text=f"Requirement with {source_quote} and literal <br>.",
        ),
        atom_results=(atom_result,),
    )
    return replace(run, requirements=(changed_result,))


def problematic_run():
    run = deep_run()
    result = run.requirements[0]
    atom_result = replace(
        result.atom_results[0],
        diagnostics=("evidence_conflict",),
    )
    graph = replace(
        run.procedure_graphs[0],
        diagnostics=("rollback_unverified:PROC-NOVA",),
    )
    record = replace(
        run.responsibility_records[0],
        evidence_ids=("EV-NOVA-CREATE", "EV-NOVA-CREATE-NEG"),
        diagnostics=("responsibility_ambiguous:REQ-0001-A001",),
    )
    negative = replace(
        run.evidence[0],
        evidence_id="EV-NOVA-CREATE-NEG",
        claim="Nova does not support the conflicting operation.",
        polarity=EvidencePolarity.NEGATIVE,
        source_id="SRC-NOVA-NEG",
        locator="release-notes#unsupported-operation",
        version_constraint="2025.1",
        local_excerpt="The operation is unsupported.",
    )
    changed_result = replace(
        result,
        atom_results=(atom_result,),
        diagnostics=(
            "evidence_conflict",
            "rollback_unverified:PROC-NOVA",
        ),
    )
    return replace(
        run,
        run_status="PARTIAL",
        requirements=(changed_result,),
        responsibility_records=(record,),
        procedure_graphs=(graph,),
        evidence=(*run.evidence, negative),
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
        '<!-- reqmap-counts:{"atoms":1,"diagnostics":6,"evidence":2,'
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
    assert "evidence_conflict" in text
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


def test_deep_markdown_derives_both_exact_conflict_positions_from_atom_links(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conflict.md"

    write_deep_markdown(problematic_run(), path)
    text = path.read_text(encoding="utf-8")

    assert (
        "| Evidence ID | Polarity | Strength | Source ID | Locator | "
        "Version constraint | Claim |"
    ) in text
    assert (
        "| EV-NOVA-CREATE | positive | direct | SRC-NOVA | servers#create | "
        "2025.1 | Nova creates a server. |"
    ) in text
    assert (
        "| EV-NOVA-CREATE-NEG | negative | direct | SRC-NOVA-NEG | "
        "release-notes#unsupported-operation | 2025.1 | "
        "Nova does not support the conflicting operation. |"
    ) in text
    assert "evidence_conflict:" not in text


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


@pytest.mark.parametrize("separator", LINE_BOUNDARIES)
def test_deep_markdown_normalizes_valid_line_boundaries_inside_cells(
    tmp_path: Path,
    separator: str,
) -> None:
    path = tmp_path / "line-boundaries.md"

    write_deep_markdown(line_boundary_run(separator), path)
    text = path.read_text(encoding="utf-8")

    assert separator not in text
    assert "Requirement with quoted<br>source and literal &lt;br&gt;." in text
    assert "atom<br>text" in text
    assert "quoted<br>source" in text
    assert "literal <br>" not in text


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
