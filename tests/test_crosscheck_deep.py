"""Independent deep JSON/XLSX/Markdown cross-artifact verification."""

from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook
import pytest

import reqmap.crosscheck_deep as crosscheck_module
from reqmap.crosscheck_deep import crosscheck_deep
from reqmap.deep_aggregation import aggregate_deep_groups
from reqmap.export_deep_json import write_deep_canonical_json
from reqmap.export_deep_markdown import write_deep_markdown
from reqmap.export_deep_xlsx import write_deep_xlsx
from reqmap.models import AnalysisState
from tests.test_export_deep_json import deep_run
from tests.test_export_deep_markdown import problematic_run


def _artifacts(tmp_path: Path, run=None):
    run = deep_run() if run is None else run
    json_path = tmp_path / "result.json"
    xlsx_path = tmp_path / "result.xlsx"
    markdown_path = tmp_path / "report.md"
    write_deep_canonical_json(run, json_path)
    write_deep_xlsx(run, xlsx_path)
    write_deep_markdown(run, markdown_path)
    return run, json_path, xlsx_path, markdown_path


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def _replace_semantic_cell(
    text: str,
    *,
    heading: str,
    row_id: str,
    column: str,
    value: str,
) -> str:
    lines = text.splitlines()
    section = lines.index(f"## {heading}")
    header_index = next(
        index for index in range(section + 1, len(lines))
        if lines[index].startswith("| ")
    )
    headers = lines[header_index][2:-2].split(" | ")
    column_index = headers.index(column)
    for index in range(header_index + 2, len(lines)):
        if not lines[index].startswith("| "):
            break
        cells = lines[index][2:-2].split(" | ")
        if row_id in cells:
            cells[column_index] = value
            lines[index] = "| " + " | ".join(cells) + " |"
            return "\n".join(lines) + "\n"
    raise AssertionError(f"row {row_id} not found under {heading}")


def _swap_summary_tables(text: str) -> str:
    lines = text.splitlines()
    summary = lines.index("## Сводка")
    next_section = next(
        index for index in range(summary + 1, len(lines))
        if lines[index].startswith("## ")
    )
    ranges: list[tuple[int, int]] = []
    index = summary + 1
    while index < next_section:
        if not lines[index].startswith("| "):
            index += 1
            continue
        start = index
        index += 2
        while index < next_section and lines[index].startswith("| "):
            index += 1
        ranges.append((start, index))
    assert len(ranges) == 2
    (first_start, first_end), (second_start, second_end) = ranges
    swapped = (
        lines[:first_start]
        + lines[second_start:second_end]
        + lines[first_end:second_start]
        + lines[first_start:first_end]
        + lines[second_end:]
    )
    return "\n".join(swapped) + "\n"


def _failed_preflight_run():
    run = deep_run()
    skipped = replace(
        run.requirements[0],
        analysis_state=AnalysisState.SKIPPED,
        support_status=None,
        atom_results=(),
        responsibility_ids=(),
        procedure_graph_ids=(),
        diagnostics=("SKIPPED: deep preflight failed",),
    )
    return replace(
        run,
        run_status="FAILED",
        requirements=(skipped,),
        groups=aggregate_deep_groups((skipped,), ()),
        responsibility_records=(),
        procedure_graphs=(),
        evidence=(),
        diagnostics=("SNAPSHOT_UNTRUSTED: signature verification failed",),
    )


def test_deep_crosscheck_accepts_consistent_artifacts(tmp_path: Path) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)

    assert crosscheck_deep(run, json_path, xlsx_path, markdown_path) == ()


def test_deep_crosscheck_rejects_unknown_non_table_markdown_content(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    markdown_path.write_text(
        markdown_path.read_text(encoding="utf-8")
        + "PRIVATE api_key=top-secret-api-key /Users/private/input.xlsx\n",
        encoding="utf-8",
    )

    assert "CROSSCHECK_DEEP_MARKDOWN_STRUCTURE" in _codes(
        crosscheck_deep(run, json_path, xlsx_path, markdown_path)
    )


def test_deep_crosscheck_rejects_reordered_summary_tables(tmp_path: Path) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    markdown_path.write_text(
        _swap_summary_tables(markdown_path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    assert "CROSSCHECK_DEEP_MARKDOWN_STRUCTURE" in _codes(
        crosscheck_deep(run, json_path, xlsx_path, markdown_path)
    )


def test_deep_crosscheck_accepts_failed_preflight_empty_graph_artifacts(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(
        tmp_path,
        _failed_preflight_run(),
    )

    assert crosscheck_deep(run, json_path, xlsx_path, markdown_path) == ()


def test_deep_crosscheck_detects_duplicate_json_keys_and_schema_deviation(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    payload = json_path.read_text(encoding="utf-8")
    json_path.write_text('{"run_id":"forged",' + payload[1:], encoding="utf-8")

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert "CROSSCHECK_DEEP_JSON_DUPLICATE_KEY" in codes
    assert "CROSSCHECK_DEEP_JSON_BYTES" in codes


def test_deep_crosscheck_collects_independent_xlsx_relationship_mismatches(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    workbook = load_workbook(xlsx_path)
    workbook["Ответственность"].cell(2, 6).value = "forged-executor"
    workbook["Процедуры"].cell(2, 14).value = '["forged-step"]'
    workbook["Доказательства"].cell(2, 10).value = '["forged-entity"]'
    workbook.save(xlsx_path)
    workbook.close()

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert {
        "CROSSCHECK_DEEP_XLSX_RESPONSIBILITY",
        "CROSSCHECK_DEEP_XLSX_PROCEDURE",
        "CROSSCHECK_DEEP_XLSX_EVIDENCE",
    }.issubset(codes)


def test_deep_crosscheck_detects_formula_hidden_sheet_and_reordered_schema(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    workbook = load_workbook(xlsx_path)
    workbook["Требования"].cell(2, 7).value = "=1+1"
    workbook["Диагностика"].sheet_state = "hidden"
    workbook._sheets[0], workbook._sheets[1] = workbook._sheets[1], workbook._sheets[0]
    workbook.save(xlsx_path)
    workbook.close()

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert "CROSSCHECK_DEEP_XLSX_FORMULA" in codes
    assert "CROSSCHECK_DEEP_XLSX_HIDDEN" in codes
    assert "CROSSCHECK_DEEP_XLSX_SHEETS" in codes


def test_deep_crosscheck_detects_duplicate_ids_and_deleted_rows(tmp_path: Path) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    workbook = load_workbook(xlsx_path)
    sheet = workbook["Ответственность"]
    sheet.append([cell.value for cell in sheet[2]])
    workbook["Процедуры"].delete_rows(2)
    workbook.save(xlsx_path)
    workbook.close()

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert "CROSSCHECK_DEEP_XLSX_DUPLICATE_ID" in codes
    assert "CROSSCHECK_DEEP_XLSX_RESPONSIBILITY" in codes
    assert "CROSSCHECK_DEEP_XLSX_PROCEDURE" in codes


def test_deep_crosscheck_detects_markdown_marker_duplication_and_mutation(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    text = markdown_path.read_text(encoding="utf-8")
    marker = next(line for line in text.splitlines() if line.startswith("<!-- reqmap-counts:"))
    markdown_path.write_text(
        text.replace(marker, marker.replace('"atoms":1', '"atoms":99') + "\n" + marker),
        encoding="utf-8",
    )

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert "CROSSCHECK_DEEP_MARKDOWN_MARKER" in codes


def test_deep_crosscheck_rejects_any_extra_malformed_marker_occurrence(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    markdown_path.write_text(
        markdown_path.read_text(encoding="utf-8")
        + "\n<!-- reqmap-counts:not-json -->\n",
        encoding="utf-8",
    )

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert "CROSSCHECK_DEEP_MARKDOWN_MARKER" in codes


def test_deep_crosscheck_does_not_call_markdown_renderer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)

    def forbidden_renderer(_run):
        raise AssertionError("crosscheck called Markdown renderer")

    monkeypatch.setattr(
        crosscheck_module,
        "_markdown_bytes",
        forbidden_renderer,
        raising=False,
    )

    assert crosscheck_deep(run, json_path, xlsx_path, markdown_path) == ()


@pytest.mark.parametrize(
    ("heading", "row_id", "column", "value", "expected_code"),
    (
        ("Требования и группы", "GRP-A", "Source requirement IDs", '["REQ-FORGED"]', "CROSSCHECK_DEEP_MARKDOWN_REQUIREMENTS_GROUPS"),
        ("Атомарные утверждения", "REQ-0001-A001", "Support status", "not_supported", "CROSSCHECK_DEEP_MARKDOWN_ATOMS"),
        ("Executor → target", "REQ-0001-A001-R001", "Связь", "FORGED → TARGET", "CROSSCHECK_DEEP_MARKDOWN_EXECUTOR_TARGET"),
        ("Контуры ответственности", "REQ-0001-A001-R001", "Evidence IDs", '["EV-FORGED"]', "CROSSCHECK_DEEP_MARKDOWN_RESPONSIBILITIES"),
        ("Контуры ответственности", "REQ-0001-A001-R001", "Procedure step IDs", '["STEP-FORGED"]', "CROSSCHECK_DEEP_MARKDOWN_RESPONSIBILITIES"),
        ("Процедуры", "REQ-0001-P001-S001", "Preconditions", '["forged"]', "CROSSCHECK_DEEP_MARKDOWN_PROCEDURES"),
        ("Процедуры", "REQ-0001-P001-S001", "Evidence IDs", '["EV-FORGED"]', "CROSSCHECK_DEEP_MARKDOWN_PROCEDURES"),
        ("Процедуры", "REQ-0001-P001-S001", "Evidence versions", '["2026.1"]', "CROSSCHECK_DEEP_MARKDOWN_PROCEDURES"),
        ("Процедуры", "REQ-0001-P001-S001", "Depends on", '["forged"]', "CROSSCHECK_DEEP_MARKDOWN_PROCEDURES"),
        ("Процедуры", "REQ-0001-P001-S001", "Rollback step ID", "STEP-FORGED", "CROSSCHECK_DEEP_MARKDOWN_PROCEDURES"),
        ("Версии и область применимости", "REQ-0001-A001-R001", "Constraint", "2026.1", "CROSSCHECK_DEEP_MARKDOWN_VERSIONS"),
        ("Каталог evidence", "EV-NOVA-CREATE", "Locator", "forged#locator", "CROSSCHECK_DEEP_MARKDOWN_EVIDENCE"),
        ("Каталог evidence", "EV-NOVA-CREATE", "Version constraint", "2026.1", "CROSSCHECK_DEEP_MARKDOWN_EVIDENCE"),
        ("Каталог evidence", "EV-NOVA-CREATE", "Supports entity refs", '["FORGED"]', "CROSSCHECK_DEEP_MARKDOWN_EVIDENCE"),
    ),
)
def test_deep_crosscheck_semantically_detects_markdown_table_mutations(
    tmp_path: Path,
    heading: str,
    row_id: str,
    column: str,
    value: str,
    expected_code: str,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    original = markdown_path.read_text(encoding="utf-8")
    marker = next(line for line in original.splitlines() if line.startswith("<!-- reqmap-counts:"))
    markdown_path.write_text(
        _replace_semantic_cell(
            original,
            heading=heading,
            row_id=row_id,
            column=column,
            value=value,
        ),
        encoding="utf-8",
    )

    changed = markdown_path.read_text(encoding="utf-8")
    assert marker in changed
    assert expected_code in _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))


def test_deep_crosscheck_semantically_detects_normalized_diagnostic_mutation(
    tmp_path: Path,
) -> None:
    run = problematic_run()
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path, run)
    text = markdown_path.read_text(encoding="utf-8")
    markdown_path.write_text(
        _replace_semantic_cell(
            text,
            heading="Нормализованная диагностика",
            row_id="REQ-0001-A001",
            column="Diagnostic",
            value="forged_diagnostic",
        ),
        encoding="utf-8",
    )

    assert "CROSSCHECK_DEEP_MARKDOWN_DIAGNOSTICS" in _codes(
        crosscheck_deep(run, json_path, xlsx_path, markdown_path)
    )


def test_deep_crosscheck_accepts_nonempty_conflict_warning_and_failure_tables(
    tmp_path: Path,
) -> None:
    run = problematic_run()
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path, run)

    assert crosscheck_deep(run, json_path, xlsx_path, markdown_path) == ()


@pytest.mark.parametrize(
    ("heading", "row_id", "column", "expected_code"),
    (
        ("Конфликты evidence", "REQ-0001-A001", "Диагностика", "CROSSCHECK_DEEP_MARKDOWN_CONFLICTS"),
        ("Предупреждения procedures и rollback", "REQ-0001-P001", "Диагностика", "CROSSCHECK_DEEP_MARKDOWN_WARNINGS"),
        ("Ошибки обработки", "RUN-0001", "Причина", "CROSSCHECK_DEEP_MARKDOWN_FAILURES"),
    ),
)
def test_deep_crosscheck_detects_diagnostic_projection_mutations(
    tmp_path: Path,
    heading: str,
    row_id: str,
    column: str,
    expected_code: str,
) -> None:
    run = problematic_run()
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path, run)
    text = markdown_path.read_text(encoding="utf-8")
    markdown_path.write_text(
        _replace_semantic_cell(
            text,
            heading=heading,
            row_id=row_id,
            column=column,
            value="forged_projection",
        ),
        encoding="utf-8",
    )

    assert expected_code in _codes(
        crosscheck_deep(run, json_path, xlsx_path, markdown_path)
    )


def test_deep_crosscheck_returns_independent_read_issues_for_bad_artifacts(
    tmp_path: Path,
) -> None:
    run, json_path, xlsx_path, markdown_path = _artifacts(tmp_path)
    json_path.write_bytes(b"\xff")
    xlsx_path.write_bytes(b"not an xlsx")
    markdown_path.write_bytes(b"\xff")

    codes = _codes(crosscheck_deep(run, json_path, xlsx_path, markdown_path))

    assert "CROSSCHECK_DEEP_JSON_READ" in codes
    assert "CROSSCHECK_DEEP_XLSX_READ" in codes
    assert "CROSSCHECK_DEEP_MARKDOWN_READ" in codes
