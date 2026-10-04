"""Контракт автономной поставки reqmap без PyPI и контейнеров."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "requirements-vendor.lock"
WHEELS_DIR = ROOT / "vendor" / "wheels"
INSTALLER = ROOT / "install.sh"


def _normalized_project(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_requirements(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", item)
        assert match is not None, f"Неканоническая lock-строка: {item}"
        project = _normalized_project(match.group(1))
        assert project not in result, f"Повтор package в lock: {project}"
        result[project] = match.group(2)
    return result


def universal_wheels(path: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for wheel in sorted(path.glob("*.whl")):
        parts = wheel.stem.split("-")
        if len(parts) == 5:
            distribution, version, python_tag, abi_tag, platform_tag = parts
        elif len(parts) == 6:
            distribution, version, build_tag, python_tag, abi_tag, platform_tag = parts
            assert re.fullmatch(
                r"\d.*",
                build_tag,
            ), f"Неканонический build tag wheel: {wheel.name}"
        else:
            raise AssertionError(f"Неканоническое имя wheel: {wheel.name}")
        assert abi_tag == "none", f"Wheel содержит ABI-зависимость: {wheel.name}"
        assert platform_tag == "any", f"Wheel зависит от платформы: {wheel.name}"
        assert python_tag in {
            "py3",
            "py2.py3",
        }, f"Wheel не является universal Python wheel: {wheel.name}"
        project = _normalized_project(distribution)
        assert project not in result, f"Повтор wheel для package: {project}"
        result[project] = (version, wheel.name)
    return result


def test_every_locked_requirement_has_exact_universal_wheel() -> None:
    locked = locked_requirements(LOCK_PATH)
    wheels = universal_wheels(WHEELS_DIR)

    assert locked
    assert set(locked) == set(wheels)
    assert {
        project: version for project, (version, _name) in wheels.items()
    } == locked


@pytest.mark.parametrize("build_tag", ["1", "1.local"])
def test_universal_wheel_parser_accepts_optional_pep427_build_tag(
    tmp_path: Path,
    build_tag: str,
) -> None:
    wheel = tmp_path / f"sample_package-1.2.3-{build_tag}-py3-none-any.whl"
    wheel.touch()

    assert universal_wheels(tmp_path) == {
        "sample-package": ("1.2.3", wheel.name)
    }


def test_install_script_is_executable_offline_and_fail_fast() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert os.access(INSTALLER, os.X_OK)
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
    assert text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "BASH_SOURCE[0]" in text
    assert "PYTHON_BIN" in text
    assert "requirements-vendor.lock" in text
    assert text.index("requirements-vendor.lock") < text.index("-m venv")
    assert text.count("--no-index") >= 2
    assert text.count("--find-links") >= 2
    assert '-r "$lock_path"' in text
    assert "knowledge validate" in text
    assert not re.search(r"\b(?:curl|wget)\b", text)
    assert len(re.findall(r"[А-Яа-яЁё]", text)) >= 40


def test_missing_wheel_is_reported_before_virtualenv_creation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    shutil.copy2(INSTALLER, repository / "install.sh")
    (repository / "requirements-vendor.lock").write_text(
        "missing-package==1.2.3\n",
        encoding="utf-8",
    )
    (repository / "vendor" / "wheels").mkdir(parents=True)
    target = repository / "target-venv"
    environment = {
        **os.environ,
        "PYTHON_BIN": sys.executable,
    }

    completed = subprocess.run(
        [str(repository / "install.sh"), str(target)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not target.exists()
    assert "wheel" in completed.stderr.lower()
    assert re.search(r"[А-Яа-яЁё]", completed.stderr)


@pytest.mark.parametrize("target_kind", ["parent", "symlink"])
def test_unsafe_relative_target_is_rejected_before_virtualenv_creation(
    tmp_path: Path,
    target_kind: str,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    shutil.copy2(INSTALLER, repository / "install.sh")
    (repository / "requirements-vendor.lock").write_text(
        "attrs==25.1.0\n",
        encoding="utf-8",
    )
    wheel_dir = repository / "vendor" / "wheels"
    wheel_dir.mkdir(parents=True)
    shutil.copy2(
        WHEELS_DIR / "attrs-25.1.0-py3-none-any.whl",
        wheel_dir,
    )
    escaped_parent = tmp_path / "escaped"
    if target_kind == "parent":
        target_argument = "../escaped/venv"
    else:
        escaped_parent.mkdir()
        (repository / "linked-parent").symlink_to(
            escaped_parent,
            target_is_directory=True,
        )
        target_argument = "linked-parent/venv"

    completed = subprocess.run(
        [str(repository / "install.sh"), target_argument],
        cwd=tmp_path,
        env={**os.environ, "PYTHON_BIN": sys.executable},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not (escaped_parent / "venv").exists()
    assert re.search(r"[А-Яа-яЁё]", completed.stderr)


def test_deep_example_requires_external_trust_and_no_shipped_production_snapshot():
    import json
    value = json.loads((ROOT / "config.deep.example.yaml").read_text())
    assert value["analysis_profile"] == "deep"
    assert value["knowledge_trust"]["allowed_signers_path"] == "trust/allowed_signers"
    assert not (ROOT / value["knowledge_path"]).exists()
    assert not list((ROOT / "tests/fixtures").rglob("*.sig"))
    for file in (ROOT / "tests/fixtures").rglob("*"):
        if file.is_file():
            assert b"BEGIN OPENSSH PRIVATE KEY" not in file.read_bytes()


def test_installed_agent_flow_from_offline_copy_without_dev_pythonpath(tmp_path,monkeypatch):
    import json
    from reqmap.config import AnalysisProfile
    from tests.agent_support import scripted_agent_flow
    repository=tmp_path/'portable checkout';repository.mkdir()
    for name in ('src','knowledge','vendor'):
        shutil.copytree(ROOT/name,repository/name,ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
    for name in ('install.sh','requirements-vendor.lock','pyproject.toml','README.md'):
        shutil.copy2(ROOT/name,repository/name)
    environment={**os.environ,'PYTHON_BIN':sys.executable,'PIP_NO_INDEX':'1'}
    environment.pop('PYTHONPATH',None)
    installed=subprocess.run(['bash','install.sh'],cwd=repository,env=environment,capture_output=True,timeout=90)
    assert installed.returncode==0,installed.stderr
    monkeypatch.delenv('PYTHONPATH',raising=False)
    config=repository/'agent.json'
    config.write_text(json.dumps(dict(knowledge_path='knowledge/epoxy-2025.1',input_root='.',session_root='sessions',output_root='reports')))
    result=scripted_agent_flow([str(repository/'.venv/bin/reqmap')],config,
        dict(kind='texts',texts=['создание виртуальной машины через Nova REST API']),AnalysisProfile.LEGACY)
    assert result['report']['run_status']=='SUCCESS'
    checked=subprocess.run([str(repository/'.venv/bin/python'),'-m','pip','check'],env=environment,capture_output=True)
    assert checked.returncode==0,checked.stderr
