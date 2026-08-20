# План реализации агента `reqmap`

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ SUB-SKILL: используйте `superpowers:subagent-driven-development` (рекомендуется) либо `superpowers:executing-plans` и выполняйте план по задачам. Для отслеживания используются checkbox-шаги (`- [ ]`).

**Цель:** создать переносимый CLI-агент, который автономно сопоставляет русскоязычные требования с одним или несколькими компонентами OpenStack Epoxy 2025.1, различает runtime и design-time реализацию и выпускает согласованные JSON, XLSX, Markdown и журнал запуска.

**Архитектура:** `reqmap` является владельцем предметного pipeline: неизменяемый импорт, декомпозиция требования локальной LLM, детерминированный поиск по локальной базе знаний, fail-closed сопоставление, валидация, агрегация и экспорт. Codex и OpenCode только загружают общий repo-scoped skill и запускают тот же CLI. Каноническим результатом остаётся JSON; остальные артефакты строятся только из него.

**Стек:** Python 3.11+, стандартная библиотека, `openpyxl==3.1.5`, `et-xmlfile==2.0.0`, `pytest==8.3.5`, `hypothesis==6.131.9`, `setuptools==75.8.0`, `wheel==0.45.1`.

**Спецификация:** [`docs/superpowers/specs/2026-08-20-reqmap-agent-design.md`](../specs/2026-08-20-reqmap-agent-design.md)

## Глобальные ограничения

- Поддерживаемая предметная версия — только OpenStack Epoxy 2025.1.
- Целевая платформа — обычный Linux с Python 3.11 или новее, без Docker/Podman.
- После получения Git-репозитория штатная установка и анализ не требуют PyPI или внешнего Интернета.
- В `vendor/wheels` допускаются только universal pure-Python wheels с ABI/platform-тегом `none-any`; Python-тег `py2.py3` допустим при успешной установке и тестах на Python 3.11.
- Конфигурация `config.yaml` в v1 использует строгий JSON-compatible YAML 1.2 с полнострочными русскими комментариями `#` и читается стандартным `json`-парсером после удаления только таких строк; inline comments запрещены.
- Локальная LLM доступна через OpenAI-compatible Chat Completions API.
- Исходный XLSX открывается только для чтения, не изменяется и не включается в поставку как эталонный набор.
- Каждая исходная строка получает уникальный технический `REQ-NNNN`, а исходный ID сохраняется отдельно и может повторяться.
- Полные и частичные дубли исходных требований не удаляются.
- Сопоставление many-to-many выполняется на уровне атомарных утверждений.
- Подтверждённый вывод без зарегистрированного локального evidence запрещён.
- Отсутствие evidence означает `insufficient_evidence`, а не `not_supported`.
- `not_supported` допустим только при прямом отрицательном evidence, несовместимой версии либо доказанном конфликте с обязательной частью требования.
- Runtime выполняется через OpenStack API; design-time требует отдельного шага `kolla-ansible reconfigure`.
- Любое `host_os_change` содержит `Kolla-Ansible`, конкретную подсистему ОС, evidence и отдельный design-time step.
- JSON является каноническим результатом; XLSX и Markdown генерируются из него.
- Пользовательская, эксплуатационная и переносимая документация, CLI help, ошибки и отчёты выполняются на русском; технические identifiers и enum-коды остаются английскими.
- MCP, web UI, выполнение OpenStack API и фактический запуск `kolla-ansible reconfigure` не входят в v1.
- Эталонный набор размеченных пользовательских требований не создаётся; тесты используют только синтетические fixtures и структурные инварианты.

## Карта файлов

### Упаковка и конфигурация

- `pyproject.toml` — metadata пакета, console entry point и зафиксированные зависимости.
- `requirements-vendor.lock` — полный список wheels, включаемых в автономную поставку.
- `config.example.yaml` — русский пример строгого JSON-compatible YAML с полнострочными комментариями.
- `install.sh` — автономная установка в `.venv` только из `vendor/wheels`.
- `.gitignore` — исключение окружений, результатов и двух пользовательских XLSX из Git.

### Код

- `src/reqmap/cli.py` — CLI, русские сообщения и коды завершения.
- `src/reqmap/config.py` — строгая загрузка конфигурации.
- `src/reqmap/errors.py` — типизированные ошибки без секретов.
- `src/reqmap/models.py` — enum и неизменяемые dataclasses канонической модели.
- `src/reqmap/ids.py` — стабильные ID требований, атомов, mappings и запусков.
- `src/reqmap/input_text.py` — импорт одного или нескольких текстовых требований.
- `src/reqmap/input_xlsx.py` — безопасный импорт XLSX и входные профили.
- `src/reqmap/grouping.py` — parent/group relations без удаления строк.
- `src/reqmap/knowledge.py` — загрузка и валидация snapshot базы знаний.
- `src/reqmap/retrieval.py` — детерминированный ранжированный поиск evidence.
- `src/reqmap/llm.py` — OpenAI-compatible клиент, retry и строгий JSON.
- `src/reqmap/prompts.py` — версионированные русские prompts и schemas ответа.
- `src/reqmap/decomposition.py` — разбиение требований на атомы.
- `src/reqmap/mapping.py` — many-to-many анализ, runtime/design-time и host OS.
- `src/reqmap/aggregation.py` — агрегация атомов, требований и групп.
- `src/reqmap/pipeline.py` — preflight, checkpoint, resume и orchestration.
- `src/reqmap/export_json.py` — канонический JSON.
- `src/reqmap/export_xlsx.py` — пять листов XLSX и повторное чтение.
- `src/reqmap/export_markdown.py` — русский инженерный отчёт.
- `src/reqmap/manifest.py` — hashes, manifest и JSONL-журнал.
- `src/reqmap/crosscheck.py` — согласованность JSON/XLSX/Markdown.

### База знаний и maintenance tooling

- `knowledge/epoxy-2025.1/metadata.json` — версия, дата и hash snapshot.
- `knowledge/epoxy-2025.1/components.json` — allowlist компонентов и подсистем.
- `knowledge/epoxy-2025.1/capabilities.jsonl` — нормализованные возможности.
- `knowledge/epoxy-2025.1/evidence.jsonl` — положительные и отрицательные доказательства.
- `knowledge/epoxy-2025.1/synonyms.json` — русские и технические синонимы.
- `knowledge/epoxy-2025.1/source-manifest.json` — официальные URL, версии, даты и hashes.
- `knowledge/epoxy-2025.1/sources/*.md` — короткие локальные evidence excerpts.
- `tools/kb/build_snapshot.py` — сборка metadata и контрольных сумм из подготовленных данных.
- `tools/kb/verify_urls.py` — отдельная online-maintenance проверка официальных URL.

### Интеграция и документация

- `.agents/skills/reqmap/SKILL.md` — общий skill для Codex/OpenCode.
- `README.md` — русское введение и быстрый старт.
- `INSTALL_OFFLINE.md` — перенос и установка без Интернета.
- `RUNBOOK.md` — эксплуатация, resume и диагностика.
- `KNOWLEDGE_BASE.md` — сопровождение evidence snapshot.
- `OUTPUT_SCHEMA.md` — каноническая модель и XLSX-листы.
- `CLIENTS_CODEX_OPENCODE.md` — подключение к CLI-агентам.
- `TROUBLESHOOTING.md` — русские причины и действия для отказов.

---

### Задача 1: инициализировать репозиторий и устанавливаемый пакет

**Файлы:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `requirements-vendor.lock`
- Create: `README.md`
- Create: `src/reqmap/__init__.py`
- Create: `src/reqmap/cli.py`
- Create: `tests/test_cli_version.py`

**Интерфейсы:**
- Produces: console script `reqmap = reqmap.cli:main`.
- Produces: `reqmap.__version__: str` со значением `0.1.0`.
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Шаг 1: создать Git-репозиторий и правила исключения пользовательских данных**

Выполнить `git init -b main`. В `.gitignore` записать:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.hypothesis/
*.py[cod]
build/
dist/
*.egg-info/
results/
/Требования к PV Stack v3.xlsx
/для СБТ_clean.xlsx
```

Проверить `git status --short`: обе исходные книги не должны появляться среди untracked files.

- [ ] **Шаг 2: написать падающий smoke test CLI**

```python
from reqmap.cli import main


def test_version_is_printed_in_russian_contract(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "reqmap 0.1.0\n"
```

- [ ] **Шаг 3: создать package metadata и минимальный CLI**

Ключевые секции `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools==75.8.0", "wheel==0.45.1"]
build-backend = "setuptools.build_meta"

[project]
name = "reqmap"
version = "0.1.0"
description = "Автономное сопоставление требований с OpenStack Epoxy 2025.1"
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["openpyxl==3.1.5", "et-xmlfile==2.0.0"]

[project.optional-dependencies]
dev = ["pytest==8.3.5", "hypothesis==6.131.9"]

[project.scripts]
reqmap = "reqmap.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
testpaths = ["tests"]
```

Минимальная реализация `cli.py`:

```python
import argparse
import sys

from reqmap import __version__


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--version"]:
        print(f"reqmap {__version__}")
        return 0
    parser = argparse.ArgumentParser(prog="reqmap", description="Сопоставление требований с OpenStack Epoxy 2025.1")
    parser.parse_args(arguments)
    return 0
```

Начальный `README.md`:

```markdown
# reqmap

`reqmap` — автономный CLI для проверяемого сопоставления требований с компонентами OpenStack Epoxy 2025.1.

До завершения реализации единственная доступная команда — `reqmap --version`. Утверждённая архитектура находится в `docs/superpowers/specs/2026-08-20-reqmap-agent-design.md`.
```

- [ ] **Шаг 4: собрать universal wheelhouse и установить dev-окружение**

`requirements-vendor.lock` содержит по одной строке:

```text
attrs==25.1.0
et-xmlfile==2.0.0
hypothesis==6.131.9
iniconfig==2.0.0
openpyxl==3.1.5
packaging==24.2
pluggy==1.5.0
Pygments==2.19.1
pytest==8.3.5
setuptools==75.8.0
sortedcontainers==2.4.0
wheel==0.45.1
```

В online maintenance environment выполнить:

```bash
python3 -m venv .venv
.venv/bin/python -m pip download --only-binary=:all: --platform any --implementation py --abi none --python-version 311 --dest vendor/wheels -r requirements-vendor.lock
.venv/bin/python -m pip install --no-index --find-links vendor/wheels -e '.[dev]'
find vendor/wheels -name '*.whl' ! -name '*-none-any.whl' -print
```

Последняя команда не должна вывести ни одного файла.

- [ ] **Шаг 5: запустить тест и зафиксировать bootstrap**

Run: `.venv/bin/python -m pytest tests/test_cli_version.py -v`

Expected: `1 passed`.

```bash
git add .gitignore pyproject.toml requirements-vendor.lock vendor/wheels README.md src/reqmap tests/test_cli_version.py docs
git commit -m "build: initialize reqmap package"
```

### Задача 2: реализовать строгую конфигурацию и безопасные ошибки

**Файлы:**
- Create: `config.example.yaml`
- Create: `src/reqmap/config.py`
- Create: `src/reqmap/errors.py`
- Create: `tests/test_config.py`

**Интерфейсы:**
- Produces: `load_config(path: Path, environ: Mapping[str, str]) -> AppConfig`.
- Produces: `AppConfig(model: ModelConfig, knowledge_path: Path, input_profile: InputProfile | None, top_k: int)`; поля `InputProfile` фиксируются ниже.
- Produces: `ReqmapError(code: str, message_ru: str, details: dict[str, object])`.

- [ ] **Шаг 1: написать тесты валидной конфигурации, неизвестного ключа и секрета**

```python
def test_load_config_reads_api_key_only_from_environment(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('{"model":{"base_url":"http://llm:8000/v1","model":"local","api_key_env":"REQMAP_API_KEY"},"knowledge_path":"knowledge/epoxy-2025.1","top_k":8}', encoding="utf-8")
    config = load_config(path, {"REQMAP_API_KEY": "secret"})
    assert config.model.api_key == "secret"
    assert "secret" not in repr(config)


def test_load_config_rejects_unknown_root_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('{"model":{"base_url":"http://llm/v1","model":"local"},"knowledge_path":"knowledge/epoxy-2025.1","unexpected":true}', encoding="utf-8")
    with pytest.raises(ConfigError, match="Неизвестные параметры: unexpected"):
        load_config(path, {})
```

- [ ] **Шаг 2: реализовать dataclasses и строгий JSON-compatible YAML loader**

```python
class ReqmapError(Exception):
    def __init__(self, code: str, message_ru: str, details: dict[str, object] | None = None):
        super().__init__(message_ru)
        self.code = code
        self.message_ru = message_ru
        self.details = {} if details is None else details


class ConfigError(ReqmapError):
    """Ошибка чтения или проверки конфигурации."""


@dataclass(frozen=True, repr=False)
class ModelConfig:
    base_url: str
    model: str
    api_key_env: str | None
    api_key: str | None
    timeout_seconds: float = 60.0
    retries: int = 2
    supports_response_format: bool = True
    seed: int | None = 1
    max_prompt_chars: int = 50000
    max_response_bytes: int = 1048576

    def __repr__(self) -> str:
        return f"ModelConfig(base_url={self.base_url!r}, model={self.model!r}, api_key=<redacted>)"


@dataclass(frozen=True)
class InputProfile:
    include_sheets: tuple[str, ...]
    exclude_sheets: tuple[str, ...]
    header_row: int
    id_column: str
    text_column: str
    priority_column: str | None = None
    expected_result_column: str | None = None
    parent_column: str | None = None
    hint_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    knowledge_path: Path
    input_profile: InputProfile | None
    top_k: int


def load_config(path: Path, environ: Mapping[str, str]) -> AppConfig:
    try:
        source = path.read_text(encoding="utf-8")
        without_comments = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        raw = json.loads(without_comments)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("CONFIG_INVALID", f"Не удалось прочитать конфигурацию: {exc}") from exc
    _reject_unknown(raw, {"model", "knowledge_path", "input_profile", "top_k"})
    return _build_app_config(raw, environ, path.parent)
```

`_build_app_config` проверяет `http://` либо `https://`, непустую модель, `top_k` от 1 до 50, timeout больше нуля и отсутствие plaintext-поля `api_key`.

- [ ] **Шаг 3: создать русский пример конфигурации**

```yaml
# Параметры локального OpenAI-compatible сервера
{
  "model": {
    "base_url": "http://127.0.0.1:8000/v1",
    "model": "local-model",
    "api_key_env": "REQMAP_API_KEY",
    "timeout_seconds": 60,
    "retries": 2,
    "supports_response_format": true,
    "seed": 1,
    "max_prompt_chars": 50000,
    "max_response_bytes": 1048576
  },
  "knowledge_path": "knowledge/epoxy-2025.1",
  "top_k": 8
}
```

Loader удаляет только строки, где первым непробельным символом является `#`. Inline comments и прочие YAML-конструкции отклоняются как `CONFIG_INVALID`; URL со знаком `#` внутри JSON-строки не изменяется.

- [ ] **Шаг 4: проверить ошибки и отсутствие секрета в выводе**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`

Expected: все тесты PASS; `secret` отсутствует в traceback и `repr`.

- [ ] **Шаг 5: зафиксировать конфигурационный контракт**

```bash
git add config.example.yaml src/reqmap/config.py src/reqmap/errors.py tests/test_config.py
git commit -m "feat: add strict offline configuration"
```

### Задача 3: создать каноническую модель и стабильные ID

**Файлы:**
- Create: `src/reqmap/models.py`
- Create: `src/reqmap/ids.py`
- Create: `tests/factories.py`
- Create: `tests/test_models.py`

**Интерфейсы:**
- Produces enums: `SupportStatus`, `AnalysisState`, `Phase`, `RelationType`, `ImplementationSource`, `EvidenceStrength`, `EvidencePolarity`.
- Produces dataclasses: `SourceCoordinate`, `SourceField`, `SourceHint`, `Requirement`, `AtomicClaim`, `Evidence`, `Candidate`, `ImplementationStep`, `Mapping`, `DecompositionOutcome`, `AtomResult`, `RequirementResult`, `GroupResult`, `AnalysisRequest`, `PreflightResult`, `RunResult`.
- Produces: `generated_requirement_id(ordinal: int) -> str`, `atom_id(requirement_id: str, ordinal: int) -> str`, `mapping_id(atom_id: str, ordinal: int) -> str`, `sha256_bytes(payload: bytes) -> str`.

- [ ] **Шаг 1: написать тесты независимых осей и ID**

```python
def test_mixed_requirement_uses_two_mapping_records():
    runtime = mapping(phase=Phase.RUNTIME)
    designtime = mapping(phase=Phase.DESIGNTIME)
    assert runtime.phase is not designtime.phase
    assert runtime.mapping_id != designtime.mapping_id


def test_generated_ids_are_stable_and_one_based():
    assert generated_requirement_id(1) == "REQ-0001"
    assert atom_id("REQ-0001", 2) == "REQ-0001-A002"
    assert mapping_id("REQ-0001-A002", 3) == "REQ-0001-A002-M003"
    assert sha256_bytes(b"reqmap") == hashlib.sha256(b"reqmap").hexdigest()
```

- [ ] **Шаг 2: реализовать enum-коды без локализации внутри данных**

```python
class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    NOT_SUPPORTED = "not_supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


class AnalysisState(str, Enum):
    COMPLETED = "completed"
    MODEL_FAILED = "model_failed"
    VALIDATION_FAILED = "validation_failed"
    SKIPPED = "skipped"


class Phase(str, Enum):
    RUNTIME = "runtime"
    DESIGNTIME = "designtime"


class RelationType(str, Enum):
    IMPLEMENTS = "implements"
    CONFIGURES = "configures"
    PREREQUISITE = "prerequisite"
    INTEGRATES = "integrates"
    HOST_OS_CHANGE = "host_os_change"


class ImplementationSource(str, Enum):
    UPSTREAM = "upstream"
    KOLLA_ANSIBLE = "kolla_ansible"
    PRODUCT_EXTENSION = "product_extension"
    EXTERNAL_COMPONENT = "external_component"


class EvidenceStrength(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    NONE = "none"


class EvidencePolarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
```

- [ ] **Шаг 3: реализовать frozen dataclasses и `to_dict`**

Ключевой контракт:

```python
@dataclass(frozen=True)
class SourceCoordinate:
    source_name: str
    sheet: str | None
    row: int | None


@dataclass(frozen=True)
class SourceHint:
    value: str
    column: str


@dataclass(frozen=True)
class SourceField:
    column: str
    value: str


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    source_id: str | None
    text: str
    ordinal: int
    coordinate: SourceCoordinate
    parent_id: str | None = None
    group_ids: tuple[str, ...] = ()
    source_fields: tuple[SourceField, ...] = ()
    source_hints: tuple[SourceHint, ...] = ()


@dataclass(frozen=True)
class AtomicClaim:
    atom_id: str
    requirement_id: str
    text: str
    source_quote: str
    mandatory: bool
    ordinal: int


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    component_id: str
    capability_id: str
    polarity: EvidencePolarity
    strength: EvidenceStrength
    claim_ru: str
    source_id: str
    locator: str
    version_constraint: str
    source_url: str | None
    local_path: str
    source_sha256: str
    retrieved_at: str
    provenance: str


@dataclass(frozen=True)
class Candidate:
    component_id: str
    capability_id: str | None
    evidence_ids: tuple[str, ...]
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImplementationStep:
    order: int
    phase: Phase
    action_ru: str
    mechanism: str
    command: str | None = None
    api_operation: str | None = None


@dataclass(frozen=True)
class Mapping:
    mapping_id: str
    atom_id: str
    component_id: str
    role_ru: str
    relation: RelationType
    phase: Phase
    implementation_source: ImplementationSource
    mechanism: str
    steps: tuple[ImplementationStep, ...]
    evidence_ids: tuple[str, ...]
    support_status: SupportStatus
    reason_ru: str


@dataclass(frozen=True)
class DecompositionOutcome:
    atoms: tuple[AtomicClaim, ...]
    analysis_state: AnalysisState
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtomResult:
    atom: AtomicClaim
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    mappings: tuple[Mapping, ...]
    supported_aspects: tuple[str, ...] = ()
    unconfirmed_aspects: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementResult:
    requirement: Requirement
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    atom_results: tuple[AtomResult, ...]
    mappings: tuple[Mapping, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupResult:
    group_id: str
    source_requirement_ids: tuple[str, ...]
    support_status: SupportStatus | None
    component_ids: tuple[str, ...]
    mapping_ids: tuple[str, ...]
    analysis_states: tuple[AnalysisState, ...]


@dataclass(frozen=True)
class AnalysisRequest:
    requirements: tuple[Requirement, ...]
    input_sha256: str
    input_kind: str
    source_path: Path | None
    output_dir: Path


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    diagnostics: tuple[str, ...]
    knowledge_sha256: str | None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    schema_version: str
    run_status: str
    requirements: tuple[RequirementResult, ...]
    groups: tuple[GroupResult, ...]
    evidence: tuple[Evidence, ...]
    metadata: Mapping[str, object]
    diagnostics: tuple[str, ...] = ()
```

`to_dict` рекурсивно преобразует dataclasses, tuple, `Path` и enum в JSON-compatible значения, не меняя порядок списков.

```python
def to_dict(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_dict(item) for item in value]
    return value
```

- [ ] **Шаг 4: запустить model tests**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`

Expected: PASS; сериализованный enum равен английскому коду.

- [ ] **Шаг 5: commit**

```bash
git add src/reqmap/models.py src/reqmap/ids.py tests/factories.py tests/test_models.py
git commit -m "feat: define canonical result model"
```

### Задача 4: реализовать неизменяемый импорт текста и XLSX

**Файлы:**
- Create: `src/reqmap/input_text.py`
- Create: `src/reqmap/input_xlsx.py`
- Create: `tests/test_input_text.py`
- Create: `tests/test_input_xlsx.py`

**Интерфейсы:**
- Consumes: `Requirement`, `SourceCoordinate`, `SourceHint`, `InputProfile`.
- Produces: `load_text(text: str, mode: Literal["single", "lines"], source_name: str) -> tuple[Requirement, ...]`.
- Produces: `load_xlsx(path: Path, profile: InputProfile | None) -> tuple[Requirement, ...]`.
- Produces: `detect_profile(workbook: Workbook) -> InputProfile`; неоднозначность вызывает `InputProfileError`.

- [ ] **Шаг 1: написать тесты сохранения текста, порядка, дублей и source hash**

```python
def test_lines_mode_preserves_duplicate_rows():
    result = load_text("Одинаковое требование\nОдинаковое требование\n", "lines", "stdin")
    assert [item.text for item in result] == ["Одинаковое требование", "Одинаковое требование"]
    assert [item.requirement_id for item in result] == ["REQ-0001", "REQ-0002"]


def test_xlsx_import_does_not_modify_source(tmp_path):
    path = make_workbook(tmp_path, headers=["ID", "Требование"], rows=[["1", "Исходный текст"]])
    before = sha256_file(path)
    result = load_xlsx(path, profile("ID", "Требование"))
    assert result[0].text == "Исходный текст"
    assert sha256_file(path) == before


def test_repeated_source_ids_get_distinct_technical_ids(tmp_path):
    path = make_workbook(tmp_path, headers=["ID", "Требование"], rows=[["1.1", "Первое"], ["1.1", "Второе"]])
    result = load_xlsx(path, profile("ID", "Требование"))
    assert [item.source_id for item in result] == ["1.1", "1.1"]
    assert [item.requirement_id for item in result] == ["REQ-0001", "REQ-0002"]
```

- [ ] **Шаг 2: реализовать text modes без перефразирования**

`single` сохраняет весь вход как одно требование, удаляя только один завершающий newline. `lines` создаёт отдельную запись на каждую непустую строку и сохраняет внутренние пробелы.

```python
def load_text(text: str, mode: str, source_name: str) -> tuple[Requirement, ...]:
    values = [text.removesuffix("\n")] if mode == "single" else [line for line in text.splitlines() if line.strip()]
    return tuple(_requirement_from_text(value, index, source_name) for index, value in enumerate(values, start=1))
```

- [ ] **Шаг 3: реализовать explicit XLSX profile и безопасное автоопределение**

Известные заголовки для ID: `ID`, `Идентификатор`, `Код`; для текста: `Требование`, `Формулировка требования`, `Описание требования`. Автоопределение разрешено только при одном совпадении каждой обязательной роли на одном листе.

```python
if len(id_matches) != 1 or len(text_matches) != 1:
    raise InputProfileError(
        "XLSX_PROFILE_AMBIGUOUS",
        "Структура XLSX неоднозначна. Укажите лист, строку заголовка и колонки во входном профиле.",
    )
```

Workbook открывать как `load_workbook(path, read_only=True, data_only=False)`. Каждой строке в порядке импорта назначать уникальный `REQ-NNNN`; значение ID-колонки сохранять отдельно как `source_id`, даже если оно повторяется. Priority, expected result, явный parent и прочие profile fields сохранять как `SourceField`. Формулы в колонке требования отклонять с русской диагностикой; скрытые листы обрабатывать только при явном включении в profile.

- [ ] **Шаг 4: проверить неоднозначность и координаты**

Run: `.venv/bin/python -m pytest tests/test_input_text.py tests/test_input_xlsx.py -v`

Expected: PASS; координата содержит имя файла, sheet и исходный row number; при двух текстовых колонках получен `XLSX_PROFILE_AMBIGUOUS`.

- [ ] **Шаг 5: commit**

```bash
git add src/reqmap/input_text.py src/reqmap/input_xlsx.py tests/test_input_text.py tests/test_input_xlsx.py
git commit -m "feat: add lossless text and xlsx inputs"
```

### Задача 5: реализовать группы, parent relations и source hints

**Файлы:**
- Create: `src/reqmap/grouping.py`
- Create: `tests/test_grouping.py`

**Интерфейсы:**
- Consumes: `tuple[Requirement, ...]`.
- Produces: `build_groups(requirements: tuple[Requirement, ...]) -> tuple[Requirement, ...]`.
- Правила: явный parent column имеет приоритет; затем dotted ID связывается с существующим ближайшим префиксом; повторяющийся source ID образует группу, но строки остаются отдельными.

- [ ] **Шаг 1: написать тест иерархии и повторяющихся ID**

```python
def test_grouping_keeps_every_source_row():
    rows = (
        requirement("1", "Родитель", ordinal=1),
        requirement("1.1", "Подпункт", ordinal=2),
        requirement("1.1", "Второй подпункт с тем же ID", ordinal=3),
    )
    grouped = build_groups(rows)
    assert len(grouped) == 3
    assert grouped[1].parent_id == grouped[0].requirement_id
    assert grouped[2].requirement_id != grouped[1].requirement_id
```

- [ ] **Шаг 2: реализовать детерминированные relations**

```python
def nearest_existing_parent(source_id: str, by_source_id: Mapping[str, list[Requirement]]) -> str | None:
    parts = source_id.split(".")
    for size in range(len(parts) - 1, 0, -1):
        candidates = by_source_id.get(".".join(parts[:size]), [])
        if candidates:
            return candidates[0].requirement_id
    return None
```

Нельзя копировать `source_hints`, mappings или status от parent к child. `group_ids` формируются как стабильные `GRP-` + первые 12 символов SHA-256 от типа правила и исходного ID.

- [ ] **Шаг 3: добавить property test сохранения количества и порядка**

```python
@given(st.lists(st.text(min_size=1), min_size=1, max_size=50))
def test_grouping_never_drops_or_reorders_rows(texts):
    source = tuple(requirement(str(index), text, ordinal=index) for index, text in enumerate(texts, start=1))
    grouped = build_groups(source)
    assert [item.ordinal for item in grouped] == list(range(1, len(source) + 1))
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_grouping.py -v`

Expected: PASS.

```bash
git add src/reqmap/grouping.py tests/test_grouping.py
git commit -m "feat: preserve requirement groups and hints"
```

### Задача 6: определить формат и валидатор локальной базы знаний

**Файлы:**
- Create: `src/reqmap/knowledge.py`
- Create: `tests/fixtures/kb_minimal/metadata.json`
- Create: `tests/fixtures/kb_minimal/components.json`
- Create: `tests/fixtures/kb_minimal/capabilities.jsonl`
- Create: `tests/fixtures/kb_minimal/evidence.jsonl`
- Create: `tests/fixtures/kb_minimal/synonyms.json`
- Create: `tests/fixtures/kb_minimal/source-manifest.json`
- Create: `tests/fixtures/kb_minimal/sources/nova-api.md`
- Create: `tests/test_knowledge.py`

**Интерфейсы:**
- Produces: `load_knowledge(path: Path, verify_snapshot_hash: bool = True) -> KnowledgeBase`; runtime всегда использует значение по умолчанию.
- Produces: `validate_knowledge(kb: KnowledgeBase) -> tuple[KnowledgeIssue, ...]`.
- `KnowledgeBase` предоставляет immutable mappings `components`, `capabilities`, `evidence`, `synonyms`.

- [ ] **Шаг 1: написать тесты referential integrity, hash и версии**

```python
def test_load_knowledge_rejects_wrong_release(kb_path):
    replace_json_field(kb_path / "metadata.json", "openstack_release", "2024.2")
    with pytest.raises(KnowledgeError, match="ожидается Epoxy 2025.1"):
        load_knowledge(kb_path)


def test_every_evidence_references_component_capability_and_source(kb_path):
    kb = load_knowledge(kb_path)
    assert validate_knowledge(kb) == ()
```

- [ ] **Шаг 2: зафиксировать JSON/JSONL schemas доменными dataclasses**

`components.json` содержит записи:

```json
{
  "components": [
    {"id":"nova","display_name":"Nova","kind":"openstack_service","release":"2025.1"},
    {"id":"kolla_ansible","display_name":"Kolla-Ansible","kind":"deployment_tool","release":"2025.1"}
  ]
}
```

Строка `evidence.jsonl`:

```json
{"id":"E-NOVA-API-001","component_id":"nova","capability_id":"CAP-NOVA-SERVER-API","polarity":"positive","strength":"direct","claim_ru":"Nova API управляет жизненным циклом серверов","source_id":"SRC-NOVA-API","locator":"раздел Servers","version_constraint":"2025.1"}
```

Официальный источник обязан иметь `source_url`, `retrieved_at`, `version`, `sha256`, `local_path` и `provenance="official"`. Источник локального проектного правила использует `provenance="project_policy"`, `source_url=null` и путь к утверждённой спецификации. Hash всегда вычисляется по байтам локального excerpt либо policy document.

```python
@dataclass(frozen=True)
class ComponentRecord:
    component_id: str
    display_name: str
    kind: str
    release: str


@dataclass(frozen=True)
class CapabilityRecord:
    capability_id: str
    component_id: str
    name_ru: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    component_ids: tuple[str, ...]
    source_url: str | None
    retrieved_at: str
    version: str
    sha256: str
    local_path: str
    provenance: str


@dataclass(frozen=True)
class KnowledgeBase:
    root: Path
    release: str
    snapshot_sha256: str
    components: Mapping[str, ComponentRecord]
    capabilities: Mapping[str, CapabilityRecord]
    evidence: Mapping[str, Evidence]
    sources: Mapping[str, SourceRecord]
    synonyms: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class KnowledgeIssue:
    code: str
    object_id: str
    message_ru: str
```

- [ ] **Шаг 3: реализовать loader и fail-closed validation**

Валидатор отклоняет duplicate IDs, неизвестные ссылки, пустые claims, отсутствующий local source, несовпавший SHA-256 и любую release, отличную от `2025.1`.

```python
def validate_knowledge(kb: KnowledgeBase) -> tuple[KnowledgeIssue, ...]:
    issues: list[KnowledgeIssue] = []
    issues.extend(_validate_unique_ids(kb))
    issues.extend(_validate_references(kb))
    issues.extend(_validate_source_hashes(kb))
    issues.extend(_validate_release(kb, expected="2025.1"))
    return tuple(sorted(issues, key=lambda item: (item.code, item.object_id)))
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_knowledge.py -v`

Expected: PASS, включая повреждённый hash и неизвестный component ID.

```bash
git add src/reqmap/knowledge.py tests/fixtures/kb_minimal tests/test_knowledge.py
git commit -m "feat: validate versioned evidence knowledge base"
```

### Задача 7: создать maintenance tooling и начальный snapshot Epoxy 2025.1

**Файлы:**
- Create: `tools/kb/build_snapshot.py`
- Create: `tools/kb/verify_urls.py`
- Create: `knowledge/epoxy-2025.1/metadata.json`
- Create: `knowledge/epoxy-2025.1/components.json`
- Create: `knowledge/epoxy-2025.1/capabilities.jsonl`
- Create: `knowledge/epoxy-2025.1/evidence.jsonl`
- Create: `knowledge/epoxy-2025.1/synonyms.json`
- Create: `knowledge/epoxy-2025.1/source-manifest.json`
- Create: `knowledge/epoxy-2025.1/sources/*.md`
- Create: `tests/test_epoxy_snapshot.py`

**Интерфейсы:**
- Consumes: подготовленные excerpt files и manifest.
- Produces: `build_snapshot(root: Path) -> str`, где строка — SHA-256 нормализованного snapshot.
- Produces: `verify_urls(root: Path, timeout: float) -> tuple[UrlCheck, ...]`; используется только в maintenance environment с Интернетом.

- [ ] **Шаг 1: написать тесты минимального покрытия snapshot**

```python
REQUIRED_COMPONENTS = {
    "keystone", "nova", "placement", "neutron", "glance", "cinder", "horizon",
    "heat", "octavia", "barbican", "manila", "swift", "designate", "ironic",
    "magnum", "trove", "watcher", "ceilometer", "aodh", "gnocchi", "cloudkitty",
    "masakari", "kolla_ansible", "host_os_chrony", "host_os_nftables",
    "host_os_networking", "host_os_kernel_sysctl", "host_os_package_management",
    "host_os_storage", "host_os_identity_access"
}


def test_epoxy_snapshot_has_direct_scope_evidence_for_every_component():
    kb = load_knowledge(Path("knowledge/epoxy-2025.1"))
    assert REQUIRED_COMPONENTS <= set(kb.components)
    evidenced = {item.component_id for item in kb.evidence.values() if item.strength is EvidenceStrength.DIRECT}
    assert REQUIRED_COMPONENTS <= evidenced
```

- [ ] **Шаг 2: создать явный component allowlist и source manifest**

Для OpenStack services использовать официальные versioned docs, например `https://docs.openstack.org/nova/2025.1/`, `https://docs.openstack.org/neutron/2025.1/` и `https://docs.openstack.org/keystone/2025.1/`; для `Kolla-Ansible` — `https://docs.openstack.org/kolla-ansible/2025.1/`. Для каждого manifest record зафиксировать конкретный URL страницы, а не поисковую выдачу или главную страницу без локатора.

Каждый component получает:

- прямой scope evidence;
- capability IDs только для функций, явно подтверждённых excerpt;
- configuration evidence, если функция design-time;
- API evidence, если функция runtime;
- русские синонимы без превращения синонима в evidence.

Host OS subsystem records имеют `kind="host_os_subsystem"`; их automation evidence ссылается на `Kolla-Ansible` и не утверждает поддержку без подходящей роли/override.

Правило «host OS changes только через Kolla-Ansible» регистрируется как `project_policy` evidence со ссылкой на утверждённую спецификацию. Для статуса `supported` одного policy evidence недостаточно: дополнительно требуется official evidence конкретного Kolla role, override или documented extension point.

Пример manifest record, которому должны следовать остальные записи:

```json
{
  "id": "SRC-NOVA-API-2025.1",
  "component_ids": ["nova"],
  "source_url": "https://docs.openstack.org/api-ref/compute/",
  "retrieved_at": "2026-08-20",
  "version": "2025.1",
  "local_path": "sources/SRC-NOVA-API-2025.1.md",
  "provenance": "official"
}
```

В pre-build manifest поле `sha256` отсутствует. `build_snapshot.py` добавляет его из bytes локального excerpt; отсутствие локального файла является ошибкой, а не основанием создать пустой excerpt. В committed snapshot `sha256` обязателен.

- [ ] **Шаг 3: реализовать воспроизводимую сборку snapshot**

```python
def build_snapshot(root: Path) -> str:
    populate_source_hashes(root / "source-manifest.json", root)
    kb = load_knowledge(root, verify_snapshot_hash=False)
    issues = validate_knowledge(kb)
    if issues:
        raise SystemExit("\n".join(f"{item.code}: {item.message_ru}" for item in issues))
    digest = snapshot_digest(root, names=("components.json", "capabilities.jsonl", "evidence.jsonl", "synonyms.json", "source-manifest.json"))
    update_metadata_hash(root / "metadata.json", digest)
    return digest
```

Сериализация `metadata.json` выполняется с `ensure_ascii=False`, `sort_keys=True`, `indent=2` и завершающим newline.

Private helper `populate_source_hashes(manifest_path: Path, root: Path) -> None` атомарно добавляет SHA-256 локальных sources в manifest до вызова strict loader.

- [ ] **Шаг 4: реализовать отдельную сетевую проверку provenance**

`verify_urls.py` делает только `HEAD`, при запрете сервером — `GET` с `Range: bytes=0-0`, разрешает HTTPS hosts `docs.openstack.org`, `releases.openstack.org` и `opendev.org`, записывает отчёт в stdout и никогда не вызывается из `reqmap analyze`.

```python
@dataclass(frozen=True)
class UrlCheck:
    source_id: str
    status: str
    message_ru: str


def verify_url(source: SourceRecord, timeout: float) -> UrlCheck:
    if source.provenance != "official" or source.source_url is None:
        return UrlCheck(source.source_id, "skipped", "Локальное проектное правило")
    parsed = urlparse(source.source_url)
    allowed_hosts = {"docs.openstack.org", "releases.openstack.org", "opendev.org"}
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        return UrlCheck(source.source_id, "failed", "URL не принадлежит разрешённому официальному OpenStack host")
    try:
        with urlopen(Request(source.source_url, method="HEAD"), timeout=timeout) as response:
            return UrlCheck(source.source_id, "passed", f"HTTP {response.status}")
    except HTTPError as exc:
        if exc.code not in {403, 405}:
            return UrlCheck(source.source_id, "failed", f"HTTP {exc.code}")
    try:
        request = Request(source.source_url, headers={"Range": "bytes=0-0"}, method="GET")
        with urlopen(request, timeout=timeout) as response:
            return UrlCheck(source.source_id, "passed", f"HTTP {response.status}")
    except (HTTPError, URLError) as exc:
        return UrlCheck(source.source_id, "failed", str(exc))
```

- [ ] **Шаг 5: собрать и проверить snapshot**

Run:

```bash
.venv/bin/python tools/kb/build_snapshot.py knowledge/epoxy-2025.1
.venv/bin/python -m pytest tests/test_epoxy_snapshot.py tests/test_knowledge.py -v
```

Expected: snapshot hash напечатан; все references/hashes/version constraints валидны; `REQUIRED_COMPONENTS` покрыты direct evidence.

- [ ] **Шаг 6: commit**

```bash
git add tools/kb knowledge/epoxy-2025.1 tests/test_epoxy_snapshot.py
git commit -m "data: add OpenStack Epoxy knowledge snapshot"
```

### Задача 8: реализовать детерминированный retriever

**Файлы:**
- Create: `src/reqmap/retrieval.py`
- Create: `tests/test_retrieval.py`

**Интерфейсы:**
- Consumes: `KnowledgeBase`, atom text, `tuple[SourceHint, ...]`, `top_k`.
- Produces: `retrieve(kb: KnowledgeBase, text: str, hints: tuple[SourceHint, ...], top_k: int) -> tuple[Candidate, ...]`.
- Candidate содержит component ID, capability ID, evidence IDs, numeric score и reasons.

- [ ] **Шаг 1: написать позитивные, негативные и hint tests**

```python
def test_retrieve_nova_server_api(kb):
    candidates = retrieve(kb, "создание виртуальной машины через API", (), 5)
    assert candidates[0].component_id == "nova"
    assert "E-NOVA-API-001" in candidates[0].evidence_ids


def test_source_hint_changes_order_but_not_evidence(kb):
    candidates = retrieve(kb, "неопределённая функция", (SourceHint("Nova", "Компонент"),), 5)
    nova = next(item for item in candidates if item.component_id == "nova")
    assert "source_hint" in nova.reasons
    assert nova.evidence_ids == ()


def test_host_os_candidate_always_adds_kolla_policy_candidate(kb):
    candidates = retrieve(kb, "применить net.ipv4.ip_forward через sysctl", (), 8)
    assert {item.component_id for item in candidates} >= {"host_os_kernel_sysctl", "kolla_ansible"}
```

- [ ] **Шаг 2: реализовать нормализацию и scoring**

Нормализация: Unicode NFKC, lowercase, замена `ё` на `е`, токены из букв/цифр/`_`/`-`, expansion только по зарегистрированным synonyms.

Score:

```text
8 * exact technical identifier
+ 5 * exact synonym phrase
+ 3 * token overlap with capability
+ 2 * token overlap with evidence claim
+ 0.5 * source hint match
```

```python
def candidate_score(
    exact_identifier: int,
    exact_synonym_phrase: int,
    capability_token_overlap: float,
    evidence_token_overlap: float,
    source_hint_match: int,
) -> float:
    return (
        8.0 * exact_identifier
        + 5.0 * exact_synonym_phrase
        + 3.0 * capability_token_overlap
        + 2.0 * evidence_token_overlap
        + 0.5 * source_hint_match
    )
```

Hint boost не создаёт evidence ID. Равные scores сортируются по `(component_id, capability_id or "")`.

Если в top results присутствует component с `kind="host_os_subsystem"`, retriever детерминированно добавляет `kolla_ansible` с project-policy evidence, даже если исчерпан `top_k`; это расширение только предоставляет обязательного кандидата и само по себе не подтверждает upstream capability.

- [ ] **Шаг 3: добавить синтетические false-positive tests**

```python
@pytest.mark.parametrize(("text", "forbidden"), [
    ("графический интерфейс управления", "horizon"),
    ("балансировка нагрузки виртуальных машин", "octavia"),
    ("выставление счетов", "ceilometer"),
    ("полноценная поддержка vGPU", "nova"),
])
def test_generic_terms_do_not_create_unproved_direct_match(kb, text, forbidden):
    candidates = retrieve(kb, text, (), 8)
    candidate = next((item for item in candidates if item.component_id == forbidden), None)
    assert candidate is None or candidate.evidence_ids == ()
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_retrieval.py -v`

Expected: PASS и повторный вызов возвращает byte-for-byte одинаковый список после `to_dict`.

```bash
git add src/reqmap/retrieval.py tests/test_retrieval.py
git commit -m "feat: add deterministic evidence retrieval"
```

### Задача 9: реализовать OpenAI-compatible LLM client

**Файлы:**
- Create: `src/reqmap/llm.py`
- Create: `tests/test_llm.py`

**Интерфейсы:**
- Produces protocol: `JsonModel.complete_json(stage: str, system_prompt: str, payload: dict[str, object]) -> dict[str, object]`.
- Produces: `OpenAICompatibleClient.preflight() -> None`.
- Produces: `ModelOutputError(code: str, message_ru: str, raw_response: str)` для безопасного корректирующего запроса; response ограничен `max_response_bytes`.
- Endpoint: `GET /models`, `POST /chat/completions` относительно `base_url`.

- [ ] **Шаг 1: написать fake-server tests для preflight, JSON и redaction**

```python
def test_complete_json_parses_chat_completion(fake_llm_server):
    fake_llm_server.enqueue({"choices":[{"message":{"content":"{\"atoms\":[]}"}}]})
    client = client_for(fake_llm_server)
    assert client.complete_json("decomposition", "system", {"text":"x"}) == {"atoms": []}


def test_http_error_never_contains_api_key(fake_llm_server):
    client = client_for(fake_llm_server, api_key="top-secret")
    fake_llm_server.enqueue_status(500, "failure")
    with pytest.raises(ModelError) as caught:
        client.complete_json("mapping", "system", {"text":"x"})
    assert "top-secret" not in str(caught.value)
```

- [ ] **Шаг 2: реализовать HTTP только стандартной библиотекой**

```python
request_body = {
    "model": config.model,
    "temperature": 0,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ],
}
if config.supports_response_format:
    request_body["response_format"] = {"type": "json_object"}
if config.seed is not None:
    request_body["seed"] = config.seed
```

Использовать `urllib.request`, заголовок Authorization добавлять только при наличии key. Ответ должен быть JSON object без Markdown fences; trailing text отклоняется.

До запроса сериализованный prompt проверяется против `max_prompt_chars`. HTTP body читается как `response.read(max_response_bytes + 1)`; превышение лимита приводит к `MODEL_RESPONSE_TOO_LARGE` без попытки разобрать усечённый JSON.

Prompt не обрезается. `MODEL_PROMPT_TOO_LARGE` и `MODEL_RESPONSE_TOO_LARGE` превращаются pipeline в `validation_failed` без support status и фиксируются в журнале.

- [ ] **Шаг 3: реализовать retries только для transport/429/5xx**

Backoff равен `0.25 * 2**attempt` и покрывается инъекцией `sleep: Callable[[float], None]`. Ошибки 400/401/403 и невалидный JSON не повторяются транспортным циклом.

```python
def should_retry(status: int | None, attempt: int, retries: int) -> bool:
    retryable = status is None or status == 429 or 500 <= status <= 599
    return retryable and attempt < retries
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_llm.py -v`

Expected: PASS; fake server видит `temperature=0`, model и optional seed.

```bash
git add src/reqmap/llm.py tests/test_llm.py
git commit -m "feat: add OpenAI compatible model client"
```

### Задача 10: реализовать атомарную декомпозицию с grounding

**Файлы:**
- Create: `src/reqmap/prompts.py`
- Create: `src/reqmap/decomposition.py`
- Create: `tests/test_decomposition.py`

**Интерфейсы:**
- Consumes: `JsonModel`, `Requirement`.
- Produces: `decompose(model: JsonModel, requirement: Requirement, parent_text: str | None) -> DecompositionOutcome`.
- Каждый atom содержит непустой `source_quote`, являющийся точной подстрокой исходного requirement text.

- [ ] **Шаг 1: написать тест составного требования и запрета invented text**

```python
def test_decompose_preserves_two_obligations(fake_model):
    fake_model.returns({"atoms":[
        {"text":"Создание VM через API","source_quote":"создание ВМ через API","mandatory":True},
        {"text":"Настройка sysctl","source_quote":"настройку sysctl","mandatory":True}
    ]})
    outcome = decompose(fake_model, requirement_text("Поддержать создание ВМ через API и настройку sysctl"), None)
    assert outcome.analysis_state is AnalysisState.COMPLETED
    assert [atom.atom_id for atom in outcome.atoms] == ["REQ-0001-A001", "REQ-0001-A002"]


def test_decompose_marks_repeated_invalid_quote_as_model_failed(fake_model):
    fake_model.returns({"atoms":[{"text":"Резервное копирование","source_quote":"backup","mandatory":True}]})
    fake_model.returns({"atoms":[{"text":"Резервное копирование","source_quote":"backup","mandatory":True}]})
    outcome = decompose(fake_model, requirement_text("Создание ВМ"), None)
    assert outcome.analysis_state is AnalysisState.MODEL_FAILED
    assert outcome.atoms == ()
    assert "не найден в исходной формулировке" in outcome.diagnostics[0]
```

- [ ] **Шаг 2: зафиксировать versioned prompt и response contract**

System prompt содержит:

```text
Ты выполняешь только декомпозицию исходного требования. Не определяй компоненты OpenStack.
Каждый атом должен описывать одно проверяемое обязательство и содержать точную цитату source_quote из requirement_text.
Не добавляй обязательства, которых нет в requirement_text. Верни только JSON object по переданной схеме.
```

`PROMPT_DECOMPOSITION_VERSION = "1.0"` включается в manifest.

- [ ] **Шаг 3: реализовать одну корректирующую попытку для schema error**

При первом невалидном ответе повторный вызов получает исходный response и список конкретных нарушений. При повторном отказе возвращается `DecompositionOutcome(atoms=(), analysis_state=MODEL_FAILED, diagnostics=(message_ru,))`; pipeline создаёт `RequirementResult` без support status и продолжает со следующей строкой.

Private helpers имеют signatures `decomposition_violations(response: Mapping[str, object], source_text: str) -> tuple[str, ...]` и `build_atoms(requirement: Requirement, response: Mapping[str, object]) -> tuple[AtomicClaim, ...]`.

```python
for attempt in range(2):
    try:
        response = model.complete_json("decomposition", DECOMPOSITION_PROMPT, payload)
        violations = decomposition_violations(response, requirement.text)
    except ModelOutputError as exc:
        response = {"raw_response": exc.raw_response}
        violations = (exc.message_ru,)
    if not violations:
        return DecompositionOutcome(build_atoms(requirement, response), AnalysisState.COMPLETED)
    payload = {"original_payload": payload, "invalid_response": response, "violations_ru": violations}
message_ru = "; ".join(violations)
return DecompositionOutcome((), AnalysisState.MODEL_FAILED, (message_ru,))
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_decomposition.py -v`

Expected: PASS; invented quote отклонён, порядок atoms стабилен.

```bash
git add src/reqmap/prompts.py src/reqmap/decomposition.py tests/test_decomposition.py
git commit -m "feat: add grounded atomic decomposition"
```

### Задача 11: реализовать many-to-many mapping и специальные правила реализации

**Файлы:**
- Create: `src/reqmap/mapping.py`
- Create: `tests/test_mapping.py`

**Интерфейсы:**
- Consumes: `JsonModel`, `AtomicClaim`, `tuple[Candidate, ...]`, `KnowledgeBase`.
- Produces: `map_atom(model: JsonModel, atom: AtomicClaim, candidates: tuple[Candidate, ...], kb: KnowledgeBase) -> AtomResult`.
- Validator function: `validate_atom_result(result: AtomResult, kb: KnowledgeBase) -> AtomResult` либо `ValidationError`.

- [ ] **Шаг 1: написать тест one-to-many и mixed phases**

```python
def test_one_atom_can_map_to_multiple_components(fake_model, kb):
    fake_model.returns(mapping_response(
        mappings=[runtime_mapping("nova", "E-NOVA-API-001"), runtime_mapping("neutron", "E-NEUTRON-PORT-001")]
    ))
    result = map_atom(fake_model, atom("создать VM с сетью"), candidates("nova", "neutron"), kb)
    assert {item.component_id for item in result.mappings} == {"nova", "neutron"}


def test_mixed_response_produces_separate_runtime_and_designtime_records(fake_model, kb):
    result = mapped_mixed_result(fake_model, kb)
    assert {item.phase for item in result.mappings} == {Phase.RUNTIME, Phase.DESIGNTIME}
```

- [ ] **Шаг 2: зафиксировать mapping prompt и allowlist validation**

Модель получает только atom, candidates и полные records выбранных evidence. Ответ каждого mapping содержит `component_id`, `relation`, `phase`, `implementation_source`, `mechanism`, `steps`, `evidence_ids`, `support_status`, `reason_ru`.

```python
unknown = {item.component_id for item in result.mappings} - {item.component_id for item in candidates}
if unknown:
    raise ValidationError("UNKNOWN_COMPONENT", f"Модель вернула компоненты вне списка кандидатов: {sorted(unknown)}")
```

`source_hints` в prompt помечаются как недоверенные и не входят в evidence block.

- [ ] **Шаг 3: реализовать runtime/design-time и host OS invariants**

```python
def validate_implementation(mapping: Mapping, all_mappings: tuple[Mapping, ...]) -> None:
    if mapping.phase is Phase.DESIGNTIME and not any(step.command == "kolla-ansible reconfigure" for step in mapping.steps):
        raise ValidationError("DESIGNTIME_WITHOUT_RECONFIGURE", "Design-time шаг обязан содержать kolla-ansible reconfigure")
    if any(step.phase is not mapping.phase for step in mapping.steps):
        raise ValidationError("STEP_PHASE_MISMATCH", "Фаза шага не совпадает с фазой mapping")
    if mapping.relation is RelationType.HOST_OS_CHANGE:
        component_ids = {item.component_id for item in all_mappings}
        if "kolla_ansible" not in component_ids or not any(item.component_id.startswith("host_os_") for item in all_mappings):
            raise ValidationError("HOST_OS_WITHOUT_KOLLA", "Изменение хостовой ОС требует Kolla-Ansible и конкретную подсистему ОС")
```

Host OS mappings всегда design-time. Runtime mappings обязаны иметь механизм `openstack_api` и конкретный `api_operation` либо endpoint family из evidence.

- [ ] **Шаг 4: защитить статусы доказательствами**

`supported` и `partial` требуют хотя бы один direct/indirect evidence для каждого подтверждённого аспекта. `not_supported` требует negative direct evidence либо version conflict. Без этого статус принудительно заменяется на `insufficient_evidence` с русской причиной. `partial` обязан иметь непустые `supported_aspects` и `unconfirmed_aspects`.

Невалидный mapping response получает ровно одну корректирующую попытку с перечнем schema/invariant violations. Повторный отказ возвращает `AtomResult(atom=atom, analysis_state=MODEL_FAILED, support_status=None, mappings=(), diagnostics=(message_ru,))`; он не превращается в `insufficient_evidence`.

```python
def validated_support_status(proposed: SupportStatus, cited: tuple[Evidence, ...], version_conflict: bool) -> SupportStatus:
    if proposed is SupportStatus.NOT_SUPPORTED:
        has_negative_direct = any(item.polarity is EvidencePolarity.NEGATIVE and item.strength is EvidenceStrength.DIRECT for item in cited)
        return proposed if has_negative_direct or version_conflict else SupportStatus.INSUFFICIENT_EVIDENCE
    if proposed in {SupportStatus.SUPPORTED, SupportStatus.PARTIAL} and not any(item.strength in {EvidenceStrength.DIRECT, EvidenceStrength.INDIRECT} for item in cited):
        return SupportStatus.INSUFFICIENT_EVIDENCE
    return proposed
```

- [ ] **Шаг 5: выполнить негативные тесты**

Run: `.venv/bin/python -m pytest tests/test_mapping.py -v`

Expected: PASS, включая rejection неизвестного component, design-time без reconfigure, host OS без Kolla, `not_supported` без negative evidence.

- [ ] **Шаг 6: commit**

```bash
git add src/reqmap/mapping.py tests/test_mapping.py
git commit -m "feat: validate many to many implementation mappings"
```

### Задача 12: реализовать агрегацию требований и групп

**Файлы:**
- Create: `src/reqmap/aggregation.py`
- Create: `tests/test_aggregation.py`

**Интерфейсы:**
- Produces: `aggregate_requirement(requirement: Requirement, atoms: tuple[AtomResult, ...]) -> RequirementResult`.
- Produces: `aggregate_groups(requirements: tuple[RequirementResult, ...]) -> tuple[GroupResult, ...]`.

- [ ] **Шаг 1: параметризовать точный приоритет статусов**

```python
@pytest.mark.parametrize(("states", "statuses", "expected"), [
    (["completed", "completed"], ["supported", "not_supported"], "not_supported"),
    (["model_failed", "completed"], [None, "supported"], None),
    (["completed", "completed"], ["supported", "insufficient_evidence"], "insufficient_evidence"),
    (["completed", "completed"], ["supported", "partial"], "partial"),
    (["completed", "completed"], ["not_applicable", "not_applicable"], "not_applicable"),
    (["completed", "completed"], ["supported", "not_applicable"], "supported"),
])
def test_requirement_status_precedence(states, statuses, expected):
    assert aggregate_status(make_atoms(states, statuses)) == expected
```

- [ ] **Шаг 2: реализовать агрегацию только обязательных atoms**

Порядок правил должен буквально соответствовать разделу 8.4 спецификации. Optional atom сохраняется в результате, но не понижает status исходного требования.

```python
def aggregate_status(atoms: tuple[AtomResult, ...]) -> SupportStatus | None:
    mandatory = tuple(item for item in atoms if item.atom.mandatory)
    if any(item.support_status is SupportStatus.NOT_SUPPORTED for item in mandatory):
        return SupportStatus.NOT_SUPPORTED
    if any(item.analysis_state is not AnalysisState.COMPLETED for item in mandatory):
        return None
    statuses = {item.support_status for item in mandatory}
    if SupportStatus.INSUFFICIENT_EVIDENCE in statuses:
        return SupportStatus.INSUFFICIENT_EVIDENCE
    if SupportStatus.PARTIAL in statuses:
        return SupportStatus.PARTIAL
    if statuses == {SupportStatus.NOT_APPLICABLE}:
        return SupportStatus.NOT_APPLICABLE
    return SupportStatus.SUPPORTED
```

- [ ] **Шаг 3: реализовать group union без замены построчных результатов**

```python
def aggregate_group(group_id: str, children: tuple[RequirementResult, ...]) -> GroupResult:
    mapping_ids = tuple(dict.fromkeys(mapping.mapping_id for child in children for mapping in child.mappings))
    component_ids = tuple(sorted({mapping.component_id for child in children for mapping in child.mappings}))
    return GroupResult(
        group_id=group_id,
        source_requirement_ids=tuple(child.requirement.requirement_id for child in children),
        support_status=aggregate_child_statuses(children),
        component_ids=component_ids,
        mapping_ids=mapping_ids,
        analysis_states=tuple(dict.fromkeys(child.analysis_state for child in children)),
    )
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_aggregation.py -v`

Expected: PASS; отдельные `RequirementResult` не мутируют и остаются в исходном порядке.

```bash
git add src/reqmap/aggregation.py tests/test_aggregation.py
git commit -m "feat: aggregate atom requirement and group results"
```

### Задача 13: реализовать pipeline, preflight, checkpoints и resume

**Файлы:**
- Create: `src/reqmap/pipeline.py`
- Create: `tests/test_pipeline.py`

**Интерфейсы:**
- Produces: `analyze(request: AnalysisRequest, config: AppConfig, model: JsonModel) -> RunResult`.
- Produces: `preflight(config: AppConfig, model: JsonModel) -> PreflightResult`.
- Checkpoint key: SHA-256 от input hash, knowledge snapshot hash, model name, prompt versions и значимых параметров.

- [ ] **Шаг 1: написать end-to-end test с fake model и minimal KB**

```python
def test_pipeline_processes_remaining_requirements_after_model_failure(tmp_path, fake_model, kb_path):
    fake_model.queue(valid_decomposition(), invalid_json_twice(), valid_decomposition(), valid_mapping())
    run = analyze(request_with_two_requirements(tmp_path), config_for(kb_path), fake_model)
    assert run.requirements[0].analysis_state is AnalysisState.MODEL_FAILED
    assert run.requirements[0].support_status is None
    assert run.requirements[1].analysis_state is AnalysisState.COMPLETED
    assert run.run_status == "PARTIAL"
```

- [ ] **Шаг 2: реализовать preflight без предметной обработки**

Проверить: config, каталог KB и его hash, `model.preflight()`, input readability, output parent writability. При отказе вернуть `RunResult(run_status="FAILED")`, не вызывать decomposition/mapping и не создавать `result.json`, `result.xlsx` или `report.md`. Для диагностики создать только завершённые `run.jsonl` и `manifest.json`; исходные требования в manifest отмечаются `skipped` без support status.

Private helper: `failed_preflight_run(request: AnalysisRequest, result: PreflightResult) -> RunResult` создаёт `RequirementResult` со state `SKIPPED`, пустыми atoms/mappings и незаполненным support status.

```python
preflight_result = preflight(config, model)
if not preflight_result.ok:
    return failed_preflight_run(request, preflight_result)
```

- [ ] **Шаг 3: реализовать checkpoint per requirement**

Checkpoint path вычисляется как `request.output_dir / ".work" / run_signature / f"{requirement_id}.json"`. Запись выполняется через temporary sibling + `os.replace`. Resume использует checkpoint только при полном совпадении signature и schema version.

Каталоги output и `.work` создаются с mode `0o700`, временные и checkpoint files — с `0o600`. Pipeline не следует symlink для output files и отклоняет output path, совпадающий с input XLSX.

```python
signature_payload = {
    "input_sha256": request.input_sha256,
    "knowledge_sha256": kb.snapshot_sha256,
    "model": config.model.model,
    "decomposition_prompt": PROMPT_DECOMPOSITION_VERSION,
    "mapping_prompt": PROMPT_MAPPING_VERSION,
    "top_k": config.top_k,
    "seed": config.model.seed,
}
```

- [ ] **Шаг 4: вычислять run status независимо от support status**

`SUCCESS`: все requirements `completed`. `PARTIAL`: хотя бы один завершён и хотя бы один failed/skipped. `FAILED`: preflight failed либо ни одно requirement не завершено. Предметный `not_supported` при успешной обработке не превращает run в `FAILED`.

```python
def run_status(results: tuple[RequirementResult, ...], preflight_ok: bool) -> str:
    if not preflight_ok or not results:
        return "FAILED"
    completed = sum(item.analysis_state is AnalysisState.COMPLETED for item in results)
    if completed == len(results):
        return "SUCCESS"
    if completed > 0:
        return "PARTIAL"
    return "FAILED"
```

- [ ] **Шаг 5: run и commit**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`

Expected: PASS; resume не вызывает fake model для уже завершённого requirement.

```bash
git add src/reqmap/pipeline.py tests/test_pipeline.py
git commit -m "feat: orchestrate resumable requirement analysis"
```

### Задача 14: реализовать канонический JSON, manifest и безопасный журнал

**Файлы:**
- Create: `src/reqmap/export_json.py`
- Create: `src/reqmap/manifest.py`
- Create: `tests/test_json_manifest.py`

**Интерфейсы:**
- Produces: `write_canonical_json(run: RunResult, path: Path) -> str` с возвращаемым SHA-256.
- Produces: `write_manifest(run: RunResult, artifact_hashes: Mapping[str, str], path: Path) -> None`.
- Produces: `RunLogger.write(event: str, level: str, message_ru: str, **safe_fields: object) -> None`.

- [ ] **Шаг 1: написать tests детерминизма, atomic write и redaction**

```python
def test_canonical_json_is_stable(tmp_path, completed_run):
    first = write_canonical_json(completed_run, tmp_path / "a.json")
    second = write_canonical_json(completed_run, tmp_path / "b.json")
    assert first == second
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_manifest_contains_no_api_key(tmp_path, completed_run):
    write_manifest(completed_run, {"result.json":"abc", "run.jsonl":"def"}, tmp_path / "manifest.json")
    assert "api_key" not in (tmp_path / "manifest.json").read_text(encoding="utf-8")


def test_run_log_is_jsonl_and_redacted(tmp_path):
    logger = RunLogger(tmp_path / "run.jsonl", redacted_values=("top-secret",))
    logger.write("model_retry", "warning", "Повторный вызов модели", reason="top-secret")
    record = json.loads((tmp_path / "run.jsonl").read_text(encoding="utf-8"))
    assert record["message_ru"] == "Повторный вызов модели"
    assert "top-secret" not in json.dumps(record, ensure_ascii=False)
```

- [ ] **Шаг 2: реализовать каноническую сериализацию**

Использовать `json.dumps(to_dict(run), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"`. Временный файл создаётся с mode `0o600`, затем `os.replace`.

- [ ] **Шаг 3: реализовать manifest без self-hash recursion**

`run.jsonl` закрывается после финального pipeline event и до manifest. Manifest содержит hashes входа, `result.json`, `result.xlsx`, `report.md`, `run.jsonl`, KB snapshot, model, endpoint origin без path/query, seed, prompt versions, schema version, timestamps, retry counts и run status. После записи manifest журнал больше не изменяется. Hash самого manifest не включается внутрь manifest; он печатается CLI после записи.

Private helper `atomic_write_json(path: Path, payload: Mapping[str, object]) -> None` использует ту же canonical serialization и `os.replace`, что `write_canonical_json`.

```python
manifest = {
    "schema_version": run.schema_version,
    "run_id": run.run_id,
    "run_status": run.run_status,
    "input_sha256": run.metadata["input_sha256"],
    "knowledge_sha256": run.metadata["knowledge_sha256"],
    "model": run.metadata["model"],
    "endpoint_origin": run.metadata["endpoint_origin"],
    "prompt_versions": run.metadata["prompt_versions"],
    "artifact_hashes": dict(sorted(artifact_hashes.items())),
}
atomic_write_json(path, manifest)
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_json_manifest.py -v`

Expected: PASS; JSON читается стандартным parser и содержит source rows, groups, atoms, mappings, steps, evidence, diagnostics; каждая строка `run.jsonl` является отдельным JSON object.

```bash
git add src/reqmap/export_json.py src/reqmap/manifest.py tests/test_json_manifest.py
git commit -m "feat: write canonical results and run manifest"
```

### Задача 15: реализовать XLSX exporter и OOXML-проверку

**Файлы:**
- Create: `src/reqmap/export_xlsx.py`
- Create: `tests/test_export_xlsx.py`

**Интерфейсы:**
- Produces: `write_xlsx(run: RunResult, path: Path) -> str`.
- Produces: `verify_xlsx(path: Path, run: RunResult) -> None`.

- [ ] **Шаг 1: написать test структуры пяти листов и many-to-many rows**

```python
def test_xlsx_contains_normalized_many_to_many_rows(tmp_path, mixed_run):
    path = tmp_path / "result.xlsx"
    write_xlsx(mixed_run, path)
    workbook = load_workbook(path, read_only=True, data_only=False)
    assert workbook.sheetnames == ["Требования", "Атомарные утверждения", "Сопоставления", "Доказательства", "Запуск"]
    rows = list(workbook["Сопоставления"].iter_rows(values_only=True))
    assert len(rows) == 1 + len(all_mappings(mixed_run))
    requirement_rows = list(workbook["Требования"].iter_rows(values_only=True))
    assert len(requirement_rows) == 1 + len(mixed_run.requirements) + len(mixed_run.groups)
```

- [ ] **Шаг 2: реализовать стабильные русские headers**

`Требования`: тип строки `requirement|group`, технический ID, исходный ID, файл, лист, строка, полный исходный текст, сохранённые priority/expected result/source fields, parent ID, group IDs, source requirement IDs, source hints, analysis state(s), support status, component IDs. Сначала в исходном порядке записываются все requirement rows, затем group rows. Group row не заменяет исходные строки и содержит объединение только подтверждённых component IDs дочерних требований.

`Атомарные утверждения`: atom ID, requirement ID, полный atom text, source quote, mandatory, state, status, supported aspects, unconfirmed aspects.

`Сопоставления`: mapping ID, atom ID, component ID, role, relation, phase, implementation source, mechanism, steps, evidence IDs, status, русское обоснование.

`Доказательства`: evidence ID, component, capability, polarity, strength, русское claim, official URL, local path, locator, version, hash.

`Запуск`: schema version, reqmap version, KB version/hash, model, безопасный endpoint, prompt versions, input hash, counts, run status.

Для каждой enum-оси XLSX содержит две колонки: стабильный английский `<field>_code` и русское `<field>_ru`. Например, `analysis_state_code=completed`, `analysis_state_ru=обработка завершена`; `phase_code=designtime`, `phase_ru=настройка при развёртывании`.

```python
SHEET_HEADERS = {
    "Требования": ("Тип строки", "Технический ID", "Исходный ID", "Файл", "Лист", "Строка", "Формулировка", "Исходные поля", "Parent ID", "Group IDs", "Source requirement IDs", "Source hints", "Состояние обработки: код", "Состояние обработки: русский", "Поддержка: код", "Поддержка: русский", "Компоненты"),
    "Атомарные утверждения": ("Atom ID", "Requirement ID", "Формулировка атома", "Исходная цитата", "Обязательный", "Состояние: код", "Состояние: русский", "Поддержка: код", "Поддержка: русский", "Подтверждённые аспекты", "Неподтверждённые аспекты"),
    "Сопоставления": ("Mapping ID", "Atom ID", "Component ID", "Роль", "Тип связи: код", "Тип связи: русский", "Фаза: код", "Фаза: русский", "Источник реализации", "Механизм", "Шаги", "Evidence IDs", "Поддержка: код", "Поддержка: русский", "Обоснование"),
    "Доказательства": ("Evidence ID", "Component ID", "Capability ID", "Полярность", "Сила", "Утверждение", "URL", "Локальный путь", "Локатор", "Версия", "SHA-256", "Происхождение"),
    "Запуск": ("Параметр", "Значение"),
}
```

- [ ] **Шаг 3: защитить от Excel formula injection и потери текста**

Любое пользовательское значение, начинающееся с `=`, `+`, `-` или `@`, записывается с буквальным значением и принудительным `cell.data_type = "s"`. Ведущий apostrophe не добавляется: повторное чтение обязано вернуть исходный текст byte-for-byte после UTF-8 encoding. Formulas в выходной книге запрещены.

```python
def write_literal(cell: Cell, value: str) -> None:
    cell.value = value
    cell.data_type = "s"
```

- [ ] **Шаг 4: реализовать ZIP/OOXML integrity и независимое чтение**

```python
def verify_xlsx(path: Path, run: RunResult) -> None:
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ExportError("XLSX_ZIP_INVALID", "Выходной XLSX повреждён")
    workbook = load_workbook(path, read_only=True, data_only=False)
    _verify_sheet_names(workbook)
    _verify_counts(workbook, run)
    _verify_no_formulas(workbook)
```

- [ ] **Шаг 5: run и commit**

Run: `.venv/bin/python -m pytest tests/test_export_xlsx.py -v`

Expected: PASS; source requirement text round-trips без сокращения, ZIP integrity PASS, formulas отсутствуют.

```bash
git add src/reqmap/export_xlsx.py tests/test_export_xlsx.py
git commit -m "feat: export verified Russian xlsx workbook"
```

### Задача 16: реализовать Markdown-отчёт и cross-artifact validation

**Файлы:**
- Create: `src/reqmap/export_markdown.py`
- Create: `src/reqmap/crosscheck.py`
- Create: `tests/test_export_markdown.py`
- Create: `tests/test_crosscheck.py`

**Интерфейсы:**
- Produces: `write_markdown(run: RunResult, path: Path) -> str`.
- Produces: `crosscheck(run: RunResult, json_path: Path, xlsx_path: Path, markdown_path: Path) -> tuple[CrosscheckIssue, ...]`.
- Produces: `CrosscheckIssue(code: str, message_ru: str)`.

- [ ] **Шаг 1: написать test обязательных русских разделов**

```python
def test_markdown_report_has_required_sections(tmp_path, mixed_run):
    path = tmp_path / "report.md"
    write_markdown(mixed_run, path)
    text = path.read_text(encoding="utf-8")
    for heading in ["# Отчёт reqmap", "## Сводка", "## Компоненты", "## Runtime и design-time", "## Изменения хостовой ОС", "## Неподтверждённые требования", "## Ошибки обработки"]:
        assert heading in text
```

- [ ] **Шаг 2: реализовать отчёт без скрытия failures**

Сводка отдельно показывает support statuses и analysis states. Для `not_supported`, `partial`, `insufficient_evidence`, `model_failed`, `validation_failed` выводятся requirement ID, полный исходный текст и причина. Таблица host OS содержит Kolla step и subsystem.

```python
sections = [
    render_summary(run),
    render_components(run),
    render_phases(run),
    render_host_os(run),
    render_unconfirmed(run),
    render_processing_errors(run),
]
path.write_text("# Отчёт reqmap\n\n" + "\n\n".join(sections) + "\n", encoding="utf-8")
```

Private render functions принимают `RunResult`, возвращают готовый Markdown `str` и экранируют `|`, CR/LF внутри table cells.

- [ ] **Шаг 3: реализовать cross-artifact contracts**

Сравнить counts requirements/atoms/mappings/evidence, все IDs, support statuses, run status и artifact hashes. Markdown marker вида `<!-- reqmap-counts:{"requirements":3,"atoms":5,"mappings":7,"evidence":6} -->` содержит только машинные counts и позволяет проверку без парсинга визуального текста.

```python
def crosscheck_counts(run: RunResult, xlsx_counts: Mapping[str, int], markdown_counts: Mapping[str, int]) -> tuple[CrosscheckIssue, ...]:
    expected = canonical_counts(run)
    issues: list[CrosscheckIssue] = []
    if dict(xlsx_counts) != expected:
        issues.append(CrosscheckIssue("CROSSCHECK_XLSX_COUNTS", "Количество записей XLSX не совпадает с JSON"))
    if dict(markdown_counts) != expected:
        issues.append(CrosscheckIssue("CROSSCHECK_MARKDOWN_COUNTS", "Количество записей Markdown не совпадает с JSON"))
    return tuple(issues)
```

- [ ] **Шаг 4: run и commit**

Run: `.venv/bin/python -m pytest tests/test_export_markdown.py tests/test_crosscheck.py -v`

Expected: PASS; намеренно удалённая строка XLSX даёт `CROSSCHECK_MAPPING_COUNT`.

```bash
git add src/reqmap/export_markdown.py src/reqmap/crosscheck.py tests/test_export_markdown.py tests/test_crosscheck.py
git commit -m "feat: add Russian report and artifact crosscheck"
```

### Задача 17: завершить CLI и общий skill Codex/OpenCode

**Файлы:**
- Modify: `src/reqmap/cli.py`
- Create: `.agents/skills/reqmap/SKILL.md`
- Create: `tests/test_cli_analyze.py`
- Create: `tests/test_skill_contract.py`

**Интерфейсы:**
- CLI: `reqmap analyze [INPUT.xlsx] --config PATH --output DIR`.
- CLI: `reqmap analyze --requirement TEXT [--requirement TEXT] --config PATH --output DIR`.
- CLI: `reqmap analyze --stdin --text-mode single|lines --config PATH --output DIR`.
- CLI: `reqmap knowledge validate --path knowledge/epoxy-2025.1`.
- Exit codes: `0=SUCCESS`, `2=usage/config`, `3=preflight FAILED`, `4=PARTIAL`, `5=export/crosscheck FAILED`, `6=analysis FAILED после успешного preflight`.

- [ ] **Шаг 1: написать CLI tests для трёх input modes и exit codes**

```python
def test_cli_multiple_requirements_invokes_one_pipeline(monkeypatch, tmp_path):
    captured = install_fake_pipeline(monkeypatch, status="SUCCESS")
    code = main(["analyze", "--requirement", "Создать VM", "--requirement", "Создать сеть", "--config", "config.example.yaml", "--output", str(tmp_path)])
    assert code == 0
    assert [item.text for item in captured.request.requirements] == ["Создать VM", "Создать сеть"]


def test_cli_partial_returns_four(monkeypatch, tmp_path):
    install_fake_pipeline(monkeypatch, status="PARTIAL")
    assert main(analyze_args(tmp_path)) == 4
```

- [ ] **Шаг 2: реализовать argparse subcommands и mutually exclusive inputs**

Ровно один источник: positional XLSX, один/несколько `--requirement`, либо `--stdin`. После начавшегося анализа при любом итоговом статусе CLI печатает paths пяти артефактов — `result.json`, `result.xlsx`, `report.md`, `run.jsonl`, `manifest.json` — и их SHA-256. Только при preflight `FAILED` печатаются два фактически созданных артефакта: `run.jsonl` и `manifest.json`. Пользовательские ошибки формулируются по-русски; traceback показывается только с `--debug`.

Input hash для XLSX вычисляется по исходным bytes файла, для stdin — по прочитанным UTF-8 bytes, для повторяемых `--requirement` — по canonical JSON array (`ensure_ascii=False`, compact separators, завершающий newline). Hash считается до декомпозиции и не зависит от LLM.

```python
analyze_parser = subparsers.add_parser("analyze", help="Проанализировать требования")
analyze_parser.add_argument("input", nargs="?", type=Path, help="Путь к исходному XLSX")
source_group = analyze_parser.add_mutually_exclusive_group()
source_group.add_argument("--requirement", dest="requirements", action="append", help="Формулировка требования; параметр можно повторять")
source_group.add_argument("--stdin", action="store_true", help="Прочитать требования из stdin")
analyze_parser.add_argument("--text-mode", choices=("single", "lines"), default="single")
analyze_parser.add_argument("--config", required=True, type=Path)
analyze_parser.add_argument("--output", required=True, type=Path)
```

После parse выполнить отдельную проверку: должен быть задан ровно один из `input`, `requirements` или `stdin`; argparse group сам не охватывает positional argument.

- [ ] **Шаг 3: создать repo-scoped skill**

`SKILL.md` обязан инструктировать клиента:

```markdown
---
name: reqmap
description: Сопоставляет одно или несколько требований с компонентами OpenStack Epoxy 2025.1 и создаёт JSON, XLSX, Markdown и журнал.
---

# Reqmap

1. Не выполняй собственное сопоставление компонентов.
2. Для вставленного текста передавай требования через stdin либо повторяемый `--requirement`, не через shell interpolation.
3. Для книги передавай абсолютный путь к XLSX.
4. Запусти `reqmap analyze` с указанными config и output.
5. Покажи пользователю run status, число требований и абсолютные пути к `result.json`, `result.xlsx`, `report.md`, `run.jsonl`, `manifest.json`.
6. При exit code 4 сообщи о частичном результате и перечисли IDs незавершённых требований.
7. Не запускай OpenStack API, Ansible, Kolla-Ansible или MCP.
```

- [ ] **Шаг 4: проверить, что skill не содержит предметной логики**

Test сканирует `SKILL.md`: обязательны команды `reqmap analyze`, запрещены `openstack server`, `kolla-ansible reconfigure`, отдельные таблицы соответствий и вызовы MCP.

- [ ] **Шаг 5: run и commit**

Run: `.venv/bin/python -m pytest tests/test_cli_analyze.py tests/test_skill_contract.py -v`

Expected: PASS для direct CLI contract и skill contract.

```bash
git add src/reqmap/cli.py .agents/skills/reqmap/SKILL.md tests/test_cli_analyze.py tests/test_skill_contract.py
git commit -m "feat: expose reqmap through cli agent skill"
```

### Задача 18: подготовить автономную установку и русскую документацию переноса

**Файлы:**
- Create: `install.sh`
- Modify: `README.md`
- Create: `INSTALL_OFFLINE.md`
- Create: `RUNBOOK.md`
- Create: `KNOWLEDGE_BASE.md`
- Create: `OUTPUT_SCHEMA.md`
- Create: `CLIENTS_CODEX_OPENCODE.md`
- Create: `TROUBLESHOOTING.md`
- Create: `tests/test_distribution.py`
- Create: `tests/test_docs_language.py`

**Интерфейсы:**
- Produces: `./install.sh [TARGET_DIR]`, default `.venv`.
- Produces: полностью автономный clone/install/run workflow.

- [ ] **Шаг 1: написать distribution tests до install script**

```python
def test_every_locked_requirement_has_universal_wheel():
    locked = locked_project_names(Path("requirements-vendor.lock"))
    wheel_projects = universal_wheel_project_names(Path("vendor/wheels"))
    assert locked <= wheel_projects


def test_required_russian_docs_exist():
    for name in ["README.md", "INSTALL_OFFLINE.md", "RUNBOOK.md", "KNOWLEDGE_BASE.md", "OUTPUT_SCHEMA.md", "CLIENTS_CODEX_OPENCODE.md", "TROUBLESHOOTING.md"]:
        text = Path(name).read_text(encoding="utf-8")
        assert len(re.findall(r"[А-Яа-яЁё]", text)) >= 100
```

- [ ] **Шаг 2: реализовать fail-fast `install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
target_dir="${1:-.venv}"
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
"$python_bin" -m venv "$target_dir"
"$target_dir/bin/python" -m pip install --no-index --find-links vendor/wheels setuptools==75.8.0 wheel==0.45.1
"$target_dir/bin/python" -m pip install --no-index --no-deps --find-links vendor/wheels .
"$target_dir/bin/reqmap" --version
"$target_dir/bin/reqmap" knowledge validate --path knowledge/epoxy-2025.1
```

Script проверяет существование всех locked wheels до создания venv и печатает русскую рекомендацию при отсутствии Python 3.11+.

- [ ] **Шаг 3: написать русские документы с воспроизводимыми командами**

Обязательное содержание:

- `README.md`: назначение, границы, быстрый запуск текста/XLSX, пять артефактов.
- `INSTALL_OFFLINE.md`: online staging, `git clone`/локальный mirror, checksum repository bundle, перенос, `./install.sh`, smoke test.
- `RUNBOOK.md`: preflight, запуск, exit codes, resume signature, backup results, чтение failures.
- `KNOWLEDGE_BASE.md`: schemas, evidence polarity/strength, maintenance-only URL verification, snapshot rebuild, review checklist.
- `OUTPUT_SCHEMA.md`: поля JSON, пять XLSX-листов, enums, aggregation precedence, cross-artifact rules.
- `CLIENTS_CODEX_OPENCODE.md`: обнаружение `.agents/skills`, прямые команды, отсутствие необходимости MCP в v1.
- `TROUBLESHOOTING.md`: недоступная модель, 401/429/5xx, invalid JSON, неоднозначный XLSX, KB hash mismatch, `PARTIAL`, недостающий wheel.

Все примеры используют вымышленные синтетические требования и не копируют строки из двух пользовательских книг.

- [ ] **Шаг 4: проверить установку без сети в чистом временном каталоге**

```bash
REQMAP_TEMP_DIR="$(mktemp -d)"
export REQMAP_TEMP_DIR
chmod +x install.sh
git add install.sh README.md INSTALL_OFFLINE.md RUNBOOK.md KNOWLEDGE_BASE.md OUTPUT_SCHEMA.md CLIENTS_CODEX_OPENCODE.md TROUBLESHOOTING.md tests/test_distribution.py tests/test_docs_language.py
reqmap_tree_id="$(git write-tree)"
git archive --format=tar "$reqmap_tree_id" | tar -xf - -C "$REQMAP_TEMP_DIR"
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY "$REQMAP_TEMP_DIR/install.sh" "$REQMAP_TEMP_DIR/.venv"
"$REQMAP_TEMP_DIR/.venv/bin/reqmap" --version
"$REQMAP_TEMP_DIR/.venv/bin/reqmap" knowledge validate --path "$REQMAP_TEMP_DIR/knowledge/epoxy-2025.1"
```

`REQMAP_TEMP_DIR` создаётся заранее через `mktemp -d`; после проверки удаляется только этот явно разрешённый каталог.

- [ ] **Шаг 5: run и commit**

Run: `.venv/bin/python -m pytest tests/test_distribution.py tests/test_docs_language.py -v`

Expected: PASS; ни один документ не зависит от Интернета для штатного запуска.

```bash
git add install.sh README.md INSTALL_OFFLINE.md RUNBOOK.md KNOWLEDGE_BASE.md OUTPUT_SCHEMA.md CLIENTS_CODEX_OPENCODE.md TROUBLESHOOTING.md tests/test_distribution.py tests/test_docs_language.py
git commit -m "docs: add Russian offline distribution guide"
```

### Задача 19: выполнить полный acceptance suite и проверить переносимый результат

**Файлы:**
- Create: `tests/test_acceptance.py`
- Create: `tests/fixtures/requirements_synthetic.json`
- Modify: `README.md`

**Интерфейсы:**
- Consumes все публичные интерфейсы предыдущих задач.
- Produces проверенный v1 repository artifact; новых предметных интерфейсов не добавляет.

- [ ] **Шаг 1: создать синтетический acceptance input без golden mappings**

Fixture содержит только формы входа и проверяемые инварианты:

```json
{
  "requirements": [
    "Предоставить создание виртуальной машины через API и подключение сети",
    "Применить параметр sysctl на compute-узлах средствами автоматизации развёртывания",
    "Поддержать неизвестную функцию Квантовый режим X"
  ]
}
```

Тест не хранит ожидаемую полную таблицу сопоставлений. Он проверяет сохранение трёх строк, many-to-many возможность, отдельный host OS/Kolla invariant и fail-closed unknown case.

- [ ] **Шаг 2: написать acceptance test с protocol-level fake LLM**

```python
def test_acceptance_contract(tmp_path, fake_openai_server, epoxy_kb):
    result_dir = run_cli_with_fixture(tmp_path, fake_openai_server, epoxy_kb)
    assert (result_dir / "result.json").is_file()
    assert (result_dir / "result.xlsx").is_file()
    assert (result_dir / "report.md").is_file()
    assert (result_dir / "run.jsonl").is_file()
    assert (result_dir / "manifest.json").is_file()
    run = read_result(result_dir / "result.json")
    assert len(run["requirements"]) == 3
    assert find_requirement(run, "Квантовый режим X")["support_status"] == "insufficient_evidence"
    assert crosscheck_paths(result_dir) == ()
```

- [ ] **Шаг 3: запустить полный suite и статические проверки**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m compileall -q src tools tests
git diff --check
find vendor/wheels -name '*.whl' ! -name '*-none-any.whl' -print
rg -n 'TO[D]O|TB[D]|FIX[M]E|PLACE[H]OLDER' src tests tools knowledge .agents README.md INSTALL_OFFLINE.md RUNBOOK.md KNOWLEDGE_BASE.md OUTPUT_SCHEMA.md CLIENTS_CODEX_OPENCODE.md TROUBLESHOOTING.md
```

Expected: все тесты PASS; compileall exit 0; `git diff --check` без ошибок; две последние команды ничего не выводят.

- [ ] **Шаг 4: проверить неизменность пользовательских XLSX**

До и после smoke run вычислить SHA-256 файлов `Требования к PV Stack v3.xlsx` и `для СБТ_clean.xlsx`. Hashes должны совпасть. Эти файлы не добавлять в Git и не использовать как golden fixtures.

```bash
reqmap_before_pv="$(shasum -a 256 'Требования к PV Stack v3.xlsx')"
reqmap_before_sbt="$(shasum -a 256 'для СБТ_clean.xlsx')"
.venv/bin/python -m pytest tests/test_acceptance.py -v
reqmap_after_pv="$(shasum -a 256 'Требования к PV Stack v3.xlsx')"
reqmap_after_sbt="$(shasum -a 256 'для СБТ_clean.xlsx')"
test "$reqmap_before_pv" = "$reqmap_after_pv"
test "$reqmap_before_sbt" = "$reqmap_after_sbt"
```

- [ ] **Шаг 5: обновить README фактическими проверенными командами и commit**

В README записать точную версию, подтверждённую Linux-платформу, число прошедших тестов, hash KB snapshot и ограничения live validation: suite не доказывает доступность конкретного пользовательского LLM endpoint и не выполняет OpenStack/Kolla operations.

```bash
git add tests/test_acceptance.py tests/fixtures/requirements_synthetic.json README.md
git commit -m "test: verify portable reqmap acceptance contract"
git status --short
```

Expected: clean worktree, кроме явно неотслеживаемых пользовательских файлов, которые скрыты `.gitignore`.

## Итоговая проверка соответствия спецификации

После задачи 19 исполнитель обязан составить таблицу «раздел спецификации → task/test» в итоговом сообщении. Минимальные связи:

| Требование | Реализация | Проверка |
|---|---|---|
| one/many/XLSX input | задачи 4 и 17 | `test_input_*`, `test_cli_analyze.py` |
| many-to-many | задачи 3, 11 | `test_mapping.py` |
| runtime/design-time | задача 11 | `test_mapping.py` |
| host OS только через Kolla | задача 11 | negative invariant tests |
| evidence/fail-closed | задачи 6–8, 11 | `test_knowledge.py`, `test_retrieval.py`, `test_mapping.py` |
| states отдельно от statuses | задачи 3, 12, 13 | `test_models.py`, `test_aggregation.py`, `test_pipeline.py` |
| JSON/XLSX/Markdown/manifest | задачи 14–16 | exporter и crosscheck tests |
| offline Linux install | задачи 1 и 18 | `test_distribution.py`, clean-directory smoke |
| Codex/OpenCode | задача 17 | `test_skill_contract.py` |
| русская документация | задача 18 | `test_docs_language.py` |
| отсутствие golden set | задачи 4, 19 | synthetic invariant-only fixtures |

Финальный отчёт обязан отдельно назвать непроверенные live-аспекты: качество выбранной локальной LLM на реальных требованиях, доступность конкретного endpoint, полнота KB для конкретного продуктового профиля и выполнение конфигурации в реальном OpenStack. Эти аспекты не должны выдаваться за подтверждённые unit/integration suite.
