"""Repo-scoped skill делегирует предметную работу только reqmap CLI."""

from pathlib import Path


SKILL = Path(".agents/skills/reqmap/SKILL.md")


def test_reqmap_skill_has_valid_discoverable_frontmatter() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: reqmap\n")
    frontmatter = text.split("---", 2)[1]
    description = next(
        line.removeprefix("description: ")
        for line in frontmatter.splitlines()
        if line.startswith("description: ")
    )
    assert description.startswith("Use when")
    assert "OpenStack Epoxy 2025.1" in description
    assert "требован" in description.lower()


def test_reqmap_skill_requires_cli_handoff_and_safe_inputs() -> None:
    text = SKILL.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "reqmap analyze" in text
    assert "stdin" in lowered
    assert "--requirement" in text
    assert "абсолют" in lowered and "xlsx" in lowered
    assert "не выполняй собственное сопоставление" in lowered
    for artifact in (
        "result.json",
        "result.xlsx",
        "report.md",
        "run.jsonl",
        "manifest.json",
    ):
        assert artifact in text
    assert "exit code 4" in lowered
    assert "незаверш" in lowered


def test_reqmap_skill_contains_no_subject_logic_or_external_actions() -> None:
    text = SKILL.read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in (
        "openstack server",
        "kolla-ansible reconfigure",
        "mcp__",
        "tools.",
        "requirement →",
    ):
        assert forbidden not in lowered
    assert "не запускай openstack api" in lowered
    assert "kolla-ansible" in lowered
    assert "mcp" in lowered
    assert len(text.split()) < 220


def test_skill_preserves_explicit_deep_profile_and_partial_gaps() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for fragment in ("analysis_profile", "legacy", "deep", "allowed_signers", "procedure_gap", "rollback_unverified"):
        assert fragment in text, fragment
