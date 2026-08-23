"""Русская эксплуатационная документация и её обязательные контракты."""

from __future__ import annotations

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    "README.md",
    "INSTALL_OFFLINE.md",
    "RUNBOOK.md",
    "KNOWLEDGE_BASE.md",
    "OUTPUT_SCHEMA.md",
    "CLIENTS_CODEX_OPENCODE.md",
    "TROUBLESHOOTING.md",
)
FORBIDDEN_SOURCE_NAMES = (
    "Требования к PV Stack v3.xlsx",
    "для СБТ_clean.xlsx",
)


def _document(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", DOCS)
def test_required_document_is_substantive_russian_without_placeholders(
    name: str,
) -> None:
    text = _document(name)

    assert len(re.findall(r"[А-Яа-яЁё]", text)) >= 100
    assert "TODO" not in text
    assert "TBD" not in text
    assert not any(source_name in text for source_name in FORBIDDEN_SOURCE_NAMES)


@pytest.mark.parametrize(
    ("name", "required_fragments"),
    [
        (
            "README.md",
            (
                "OpenStack Epoxy 2025.1",
                "reqmap analyze --stdin",
                "reqmap analyze requirements.xlsx",
                "result.json",
                "result.xlsx",
                "report.md",
                "run.jsonl",
                "manifest.json",
                "не выполняет OpenStack API",
            ),
        ),
        (
            "INSTALL_OFFLINE.md",
            (
                "online staging",
                "git clone",
                "git bundle",
                "SHA-256",
                "./install.sh",
                "--version",
                "knowledge validate",
                "без доступа к Интернету",
            ),
        ),
        (
            "RUNBOOK.md",
            (
                "preflight",
                "resume signature",
                ".work",
                "result.json",
                "backup",
                "run.jsonl",
                "manifest.json",
                "PARTIAL",
            ),
        ),
        (
            "KNOWLEDGE_BASE.md",
            (
                "components.json",
                "capabilities.jsonl",
                "evidence.jsonl",
                "source-manifest.json",
                "positive",
                "negative",
                "direct",
                "indirect",
                "tools/kb/verify_urls.py",
                "tools/kb/build_snapshot.py",
                "чек-лист ревью",
            ),
        ),
        (
            "OUTPUT_SCHEMA.md",
            (
                "run_id",
                "run_status",
                "requirements",
                "groups",
                "evidence",
                "Требования",
                "Атомарные утверждения",
                "Сопоставления",
                "Доказательства",
                "Запуск",
                "not_supported",
                "insufficient_evidence",
                "crosscheck",
            ),
        ),
        (
            "CLIENTS_CODEX_OPENCODE.md",
            (
                ".agents/skills/reqmap/SKILL.md",
                "Codex",
                "OpenCode",
                "reqmap analyze",
                "MCP не требуется",
                "не выполняет собственное сопоставление",
            ),
        ),
        (
            "TROUBLESHOOTING.md",
            (
                "401",
                "429",
                "5xx",
                "invalid JSON",
                "неоднознач",
                "KNOWLEDGE",
                "PARTIAL",
                "wheel",
                "--debug",
            ),
        ),
    ],
)
def test_document_covers_its_operational_contract(
    name: str,
    required_fragments: tuple[str, ...],
) -> None:
    text = _document(name)

    for fragment in required_fragments:
        assert fragment.casefold() in text.casefold(), (
            f"{name}: отсутствует {fragment!r}"
        )


def test_runbook_documents_all_exit_codes() -> None:
    text = _document("RUNBOOK.md")

    for code in (0, 2, 3, 4, 5, 6):
        assert re.search(rf"\|\s*`?{code}`?\s*\|", text)


def test_markdown_local_links_resolve() -> None:
    pattern = re.compile(r"\[[^]]+\]\((?!https?://|#)([^)]+\.md)(?:#[^)]+)?\)")
    for name in DOCS:
        for target in pattern.findall(_document(name)):
            assert (ROOT / target).is_file(), f"{name}: broken link {target}"
