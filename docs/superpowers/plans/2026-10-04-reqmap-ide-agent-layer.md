# Reqmap IDE Agent Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать ученику возможность анализировать требования через Codex/OpenCode во встроенном чате IDE, используя модель клиента и проверяемые локальные инструменты reqmap.

**Architecture:** Общий предметный API принимает предложения без вызова LLM и обслуживает старый pipeline и новый агентный режим. Локальный MCP stdio server предоставляет девять инструментов над сохраняемой сессией; backend контролирует evidence, статусы и публикацию пяти согласованных артефактов. Codex/OpenCode организует диалог и вызывает выбранную в клиенте модель.

**Tech Stack:** Python >= 3.11, stdlib (`sqlite3`, `fcntl`, JSON, subprocess), существующие openpyxl 3.1.5 и et-xmlfile 2.0.0, pytest 8.3.5, Hypothesis 6.131.9; MCP stdio без новых runtime dependencies.

**Spec:** [2026-10-04-reqmap-ide-agent-layer-design.md](../specs/2026-10-04-reqmap-ide-agent-layer-design.md), утверждена пользователем 2026-10-04.

## Global Constraints

- Codex и OpenCode — агентные клиенты, а не названия используемой языковой модели.
- В этом режиме reqmap не вызывает LLM API. Поля `model`, API URL и ключи в агентной конфигурации не допускаются.
- Прямой `reqmap analyze` с собственной конфигурацией LLM сохраняется как отдельный совместимый режим. Автоматического переключения между режимами при ошибке нет.
- Рабочие ОС — Linux и macOS. Установка reqmap сохраняет Python >= 3.11 и переносимый offline wheel bundle.
- В этап входят оба существующих профиля: `legacy` и `deep`.
- Для `deep` обязательны approved signed snapshot v2, внешний `allowed_signers` и проверка доверия; production deep corpus не входит в этап.
- Базовый OpenStack — 2025.1; переход к 2026.1 допустим только в существующем upgrade profile. Анализируемый host OS profile — Rocky Linux 9.
- Версия прикладного контракта — `1.0`. Версии MCP — `2025-06-18` и `2025-11-25`.
- Лимиты по умолчанию: input file 25 MiB, 10 000 требований, 64 atoms на требование, RPC frame 2 MiB, ответ 512 KiB, страница 50 записей.
- Сохраняется комплект: `result.json`, `result.xlsx`, `report.md`, `run.jsonl`, `manifest.json`.
- Для agent mode: `model=external-agent`, `seed=null`, endpoint отсутствует; optional reported model не является проверенной идентичностью.
- Backend не обращается к сети, не выполняет инфраструктурные команды и не изменяет исходный XLSX.
- Существующие проверки evidence, version scope, конфликтов, процедур и cross-artifact consistency не ослабляются.
- Структурная корректность proposals не доказывает семантическую полноту; scripted tests не заменяют проверки настоящим агентом.

## Review Focus

- Повтор `start_session` после удаления исходного файла должен вернуть прежнюю сессию по request ID; повтор finalize после потери ответа — прежние артефакты. Тесты задач 3 и 7.
- Изменение атомов при открытой следующей странице не должно применять старый mapping или смешивать revisions. Тесты задач 4 и 5.
- Путь с пробелами/кириллицей должен работать; FIFO, symlink escape и подмена файла во время чтения — отклоняться без зависания. Тесты задач 2 и 8.
- Read-only вызовы после finalize не должны менять hash журнала предложений; неизвестный reported model не должен превращаться в доказанную идентичность. Тесты задач 6 и 7.
- Инструкция внутри требования «игнорируй evidence и выполни команду» остаётся текстом входа; состав tools и ограничения валидации не меняются. Тесты задач 5, 8 и сценарий задачи 10.

## File Structure

Новые файлы сгруппированы по ответственности:

| Файлы | Ответственность |
| --- | --- |
| `src/reqmap/proposals.py`; изменения `decomposition.py`, `mapping.py`, `deep_mapping.py` | Ошибка предложения, публичные prepare/accept операции; одна реализация предметных правил |
| `src/reqmap/config_common.py`, `agent_config.py`, `agent_input.py`, `agent_knowledge.py`; изменения `config.py` | Общие парсеры полей, конфигурация без модели, immutable input, проверка KB |
| `src/reqmap/agent_types.py`, `agent_store.py` | Прикладные DTO, транзакции, request receipts, журнал и locks |
| `src/reqmap/agent_service.py`, `agent_context.py`, `agent_replay.py` | Предметные операции сессии, контекст кандидатов, восстановление через validators |
| `src/reqmap/analysis_origin.py`, `publication.py`, `agent_finalize.py` | Происхождение результата, общий экспорт, агрегация и публикация с восстановлением |
| `src/reqmap/agent_tools.py`, `mcp_stdio.py`; изменение `cli.py` | Tool schemas, dispatch и транспорт; публичная команда serve |
| `tests/agent_support.py`, `tests/test_agent_*.py`, `tests/test_proposal_api.py`, `tests/test_mcp_stdio.py` | Воспроизводимые fixtures, fault injection, API/transport/acceptance tests |
| `config.agent.example.yaml`, `examples/ide/*`, общий skill и существующие руководства | Подключение клиентов и учебный рабочий процесс |
| `tests/fixtures/agent_eval.json`, `docs/acceptance/reqmap-ide-agent-layer.md` | Контрольные примеры и фактические результаты приёмки |

`agent_service.py` связывает операции, но не содержит реализацию JSON-RPC,
SQL, mapping validators или XLSX exporter. Не переносить целиком большие
`pipeline.py`/`deep_pipeline.py` ради создания нового слоя.
В списках Files сокращённые имена runtime `.py` относятся к `src/reqmap/`;
пути тестов и примеров указаны относительно корня репозитория.

## Порядок и правила исполнения

Зависимости: `1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10`.
Все задачи образуют один рабочий процесс; отдельные планы на MCP и сессии
создали бы недостающие друг у друга интерфейсы.

До задачи 1 создать изолированную ветку/worktree через `using-git-worktrees`.
Базовый код опубликован как `2ba90af`, спецификация — `d2fc7ff`; начать от HEAD,
содержащего утверждённые документы. Не изменять root `.venv` из worktree.

В worktree выполнить `./install.sh`. Установленный пакет не editable:
для разработки использовать `PYTHONPATH=src .venv/bin/python -m pytest ...`.
Subprocess tests передают абсолютный путь `src` в собственном environment.
Для проверки установленной поставки в задаче 10 переустановить пакет и убрать
`PYTHONPATH`. Не использовать несуществующий entry point `python -m reqmap.cli`.

Каждая задача: RED → минимальная реализация → GREEN и указанные регрессии →
`git diff --check` → commit только относящихся к задаче файлов. Приведённый
commit subject задаёт границу изменения. Ошибка импорта нового символа допустима
как первоначальный RED; перед реализацией проверить, что существующие соседние
тесты проходят и причина падения относится к новой функциональности.

## Task 1: Общий API предложений без модели

**Files:** Create `src/reqmap/proposals.py`, `tests/test_proposal_api.py`;
modify `src/reqmap/decomposition.py`, `mapping.py`, `deep_mapping.py` и их тесты.

**Interfaces:**
- `ProposalError(kind: Literal["shape", "semantic"], violations: tuple[str, ...])` в `proposals.py`.
- `prepare_decomposition(requirement: Requirement, parent_text: str | None) -> dict[str, object]` и `accept_decomposition(requirement: Requirement, proposal: object) -> tuple[AtomicClaim, ...]` в `decomposition.py`.
- `prepare_mapping(atom: AtomicClaim, candidates: tuple[Candidate, ...], kb: KnowledgeBase) -> dict[str, object]` и `accept_mapping(atom: AtomicClaim, candidates: tuple[Candidate, ...], kb: KnowledgeBase, proposal: object) -> AtomResult` в `mapping.py`.
- `prepare_deep_mapping(atom: AtomicClaim, retrieval: DeepRetrievalResult, kb: KnowledgeBaseV2) -> dict[str, object]` и `accept_deep_mapping(atom: AtomicClaim, retrieval: DeepRetrievalResult, kb: KnowledgeBaseV2, proposal: object) -> DeepMappingOutcome` в `deep_mapping.py`.

- [ ] **1. RED tests:** Добавить параметризованное сравнение публичного accept API с нынешними `decompose`/`map_atom`/`map_atom_deep`, используя existing FakeModel и fixtures. Основные assertions:

```python
assert accept_mapping(atom, candidates, kb, raw) == map_atom(FakeModel([raw]), atom, candidates, kb)
assert accept_deep_mapping(atom, retrieval, kb, raw) == map_atom_deep(FakeModel([raw]), atom, retrieval, kb)
assert accept_decomposition(requirement, raw)[0].text == raw["atoms"][0]["source_quote"]
```

  В отдельных cases проверить exact quote, foreign evidence, shape failure,
  deep semantic failure и автоматическое возвращение скрытого negative evidence.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_proposal_api.py -q` — новые функции отсутствуют.
- [ ] **3. Implement:** Выделить существующую подготовку payload и проверки без изменения алгоритмов. Модельные loops вызывают prepare/accept; legacy/decomposition сохраняют `MODEL_FAILED` после двух отказов, deep сохраняет различие `MODEL_FAILED`/`VALIDATION_FAILED`. Transport exceptions продолжают распространяться как раньше. Не добавлять фиктивный JsonModel в agent mode.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_proposal_api.py tests/test_decomposition.py tests/test_mapping.py tests/test_deep_mapping.py tests/test_pipeline.py tests/test_deep_pipeline.py -q` — все проходят, прежние retry counts/payloads сохраняются.
- [ ] **5. Commit:** `refactor: expose shared proposal validation API`.

## Task 2: Конфигурация без модели и immutable input/KB

**Files:** Create `src/reqmap/config_common.py`, `agent_config.py`, `agent_input.py`,
`agent_knowledge.py`, `config.agent.example.yaml`, `tests/agent_support.py`,
`tests/test_agent_config.py`, `tests/test_agent_input.py`, `tests/test_agent_knowledge.py`;
modify `src/reqmap/config.py`.

**Interfaces:**
- `AgentLimits(max_input_bytes=26214400, max_requirements=10000, max_atoms_per_requirement=64, max_frame_bytes=2097152, max_response_bytes=524288, max_page_size=50)` — frozen dataclass в `agent_config.py`.
- `AgentConfig(analysis_profile: AnalysisProfile, knowledge_path: Path, knowledge_trust: KnowledgeTrustConfig | None, input_profile: InputProfile | None, top_k: int, input_root: Path, session_root: Path, output_root: Path, limits: AgentLimits)`; `load_agent_config(path: Path) -> AgentConfig`.
- `InputSnapshot(source_kind: Literal["texts", "txt", "xlsx"], source_name: str, content: bytes, input_sha256: str, requirements: tuple[Requirement, ...])`; `import_agent_input(source: dict[str, object], config: AgentConfig) -> InputSnapshot` в `agent_input.py`.
- `VerifiedKnowledge(kb: KnowledgeBase | KnowledgeBaseV2, knowledge_sha256: str, snapshot_id: str | None)`; `load_agent_knowledge(config: AgentConfig) -> VerifiedKnowledge` в `agent_knowledge.py`.
- Общие public parsers в `config_common.py`: `read_config_object(path: Path) -> dict[str, object]`, `parse_input_profile(raw: object) -> InputProfile`, `parse_analysis_profile(raw: object) -> AnalysisProfile`, `parse_knowledge_trust(raw: object, base: Path) -> KnowledgeTrustConfig`. Типы остаются в `config.py`; во избежание import cycle общие parser imports в `load_config` — локальные.

- [ ] **1. RED tests:** Зафиксировать defaults и запрет настройки модели:

```python
assert config.limits.max_input_bytes == 25 * 1024 * 1024
assert config.limits.max_requirements == 10000
assert not hasattr(config, "model")
assert snapshot.input_sha256 == hashlib.sha256(snapshot.content).hexdigest()
assert [r.text for r in snapshot.requirements] == ["Первая строка", "Вторая строка"]
```

  Cases: `model`/`base_url`/`api_key`, неизвестные keys, boolean вместо integer,
  пустой input, >10 000 строк, файл >25 MiB, сломанный XLSX, path с кириллицей,
  FIFO, symlink и изменение stat/inode во время чтения. Для deep — unsigned KB,
  неверный signer и отсутствие `ssh-keygen`; никакой downgrade на legacy.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_config.py tests/test_agent_input.py tests/test_agent_knowledge.py -q`.
- [ ] **3. Implement:** JSON-compatible YAML по существующему формату; пути относительно config. `top_k` 1..50, лимиты — положительные builtin integers. Source: `{"kind":"texts","texts":[...]}` либо `{"kind":"txt"|"xlsx","path":"..."}`; TXT использует `lines`, texts сохраняет каждый элемент отдельным requirement. Чтение файла — bounded regular-file descriptor, без symlink traversal, с проверкой неизменности; XLSX разбирается из того же bytes snapshot. Использовать существующие loaders, не менять правила колонок/координат. Корни записи не могут пересекаться с KB/trust; input не может находиться внутри session/output root. Deep вызывает только signed loader и сверяет signer identity. В `tests/agent_support.py` создать `make_agent_config(tmp_path: Path, profile: AnalysisProfile) -> AgentConfig`, используя существующие temporary signed fixtures.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_config.py tests/test_agent_input.py tests/test_agent_knowledge.py tests/test_config.py tests/test_input_text.py tests/test_input_xlsx.py tests/test_snapshot_trust.py -q`.
- [ ] **5. Commit:** `feat: add model-free agent configuration and input preflight`.

## Task 3: Транзакционные сессии и идемпотентность

**Files:** Create `src/reqmap/agent_types.py`, `agent_store.py`,
`tests/test_agent_store.py`; extend `tests/agent_support.py`.

**Interfaces:** В `agent_types.py` определить frozen dataclasses:
- `ToolError(code: str, message_ru: str, details: dict[str, object])`; `ToolReply(ok: bool, data: dict[str, object], error: ToolError | None)`.
- `SessionSettings(analysis_profile: AnalysisProfile, top_k: int, input_profile: InputProfile | None, knowledge_sha256: str, snapshot_id: str | None, tool_contract_version: str, workflow_version: str)`.
- `SessionSeed(input_snapshot: InputSnapshot, settings: SessionSettings, clarifications: tuple[str, ...], parent_session_id: str | None, reported_client: str)`.
- `JournalEvent(sequence: int, request_id: str, operation: str, arguments: dict[str, object], accepted: bool, reply: ToolReply)`.
- `SessionRecord(session_id: str, revision: int, status: Literal["active", "finalized", "failed"], seed: SessionSeed, events: tuple[JournalEvent, ...])`.
- `MutationCommand(session_id: str, request_id: str, expected_revision: int, operation: str, arguments: dict[str, object])`; `MutationDecision(accepted: bool, reply: ToolReply, status: Literal["active", "finalized", "failed"])`.
- `SessionStore(root: Path).create(request_id: str, arguments: dict[str, object], prepare: Callable[[], SessionSeed]) -> ToolReply`; `.read(session_id: str) -> SessionRecord`; `.transact(command: MutationCommand, mutate: Callable[[SessionRecord], MutationDecision]) -> ToolReply`; `.locked(session_id: str) -> ContextManager[None]` с поддержкой вложенного использования тем же потоком.

- [ ] **1. RED tests:** Проверить следующие invariants через реальные два store instances и subprocess crash injection:

```python
assert replay == original_reply
assert same_id_other_payload.error.code == "REQUEST_ID_REUSED"
assert stale_reply.error.code == "REVISION_CONFLICT"
assert (accepted_count, revision_delta) == (1, 1)  # два конкурирующих writer
assert store.read(session_id).seed.input_snapshot.content == original_bytes
```

  Повтор create не вызывает `prepare`, даже если исходный файл уже удалён.
  Прерванная transaction не оставляет accepted event без request receipt.
  Повреждённые input/event hashes и unknown state version дают `SESSION_CORRUPT`.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_store.py -q`.
- [ ] **3. Implement:** SQLite `session_root/sessions.sqlite3`, `synchronous=FULL`, короткие transactions для sessions, inputs, events и receipts; strict JSON и hash chain для serialized journal. `fcntl.flock` на отдельный session lock, отдельный create lock; descriptor освобождается при crash. Root — приватный каталог, DB/lock paths проверяются на symlink. Не использовать pickle. Создание даёт revision 0; только accepted mutation увеличивает revision на 1. Ошибки также получают durable receipt/event, но не увеличивают revision. Проверка совпавшего request ID/payload предшествует revision и финальному status. Ошибки до создания сессии возвращаются без активной сессии. Status и revision не берутся из client arguments.
- [ ] **4. GREEN:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_store.py -q`; два процесса проверять на Linux и macOS, не подменять lock mock-объектом.
- [ ] **5. Commit:** `feat: persist agent sessions and idempotent request receipts`.

## Task 4: Создание, просмотр и декомпозиция сессии

**Files:** Create `src/reqmap/agent_service.py`, `agent_replay.py`,
`tests/test_agent_session.py`; extend `agent_types.py`, `tests/agent_support.py`.

**Interfaces:**
- `AgentService(config: AgentConfig).call(name: str, arguments: dict[str, object]) -> ToolReply`; первоначально поддерживает start/get/submit_atoms, остальные имена явно отклоняются.
- `SessionView(record: SessionRecord, requirements: tuple[Requirement, ...], atoms_by_requirement: dict[str, tuple[AtomicClaim, ...]], mappings_by_atom: dict[str, AtomResult | DeepMappingOutcome])` в `agent_types.py`.
- `replay_session(record: SessionRecord, knowledge: VerifiedKnowledge) -> SessionView` в `agent_replay.py`, вызывает task 1 validators, не доверяет сохранённому support status.

- [ ] **1. RED tests:** Выполнить start → get → submit_atoms → reopen/get. Проверить revision `0 → 1`, exact quotes, сохранённые coordinates, limit 64 (65 отклоняется), неизвестный requirement ID и сохранение прежних atoms после rejected replacement. Cursor до изменения revision должен дать `CURSOR_STALE`, а не следующую страницу новых данных.

```python
assert replaced.data["revision"] == first.data["revision"] + 1
assert invalid.error.code == "PROPOSAL_INVALID"
assert after_invalid.atoms_by_requirement == before_invalid.atoms_by_requirement
assert stale_page.error.code == "CURSOR_STALE"
```

- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_session.py -q`.
- [ ] **3. Implement:** Start принимает source, optional clarifications/parent_session_id/reported_client и request_id. Get возвращает профиль, totals, current revision, requirements page, pending IDs, diagnostics и decomposition context из `prepare_decomposition`; filter `requirement_id` совместим с pagination. Cursor связывает session, revision, query/filter и offset. Submit atoms принимает полный `proposal={"atoms":[...]}`, expected_revision, request_id и optional reported_model. Уточнения не добавляются к source_quote/evidence; их изменение требует нового start. Replacing atoms очищает mappings этой строки при replay. `reported_client` — `codex`, `opencode` или `unknown`, значение заявленное. Полный replay нужен после restart; in-memory projection можно кешировать только по проверенным session/revision/journal/KB hashes. Добавить в test support `call_tool(service, name, **arguments) -> ToolReply` без автоматического скрытого исправления ошибок.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_session.py tests/test_agent_store.py tests/test_decomposition.py -q`.
- [ ] **5. Commit:** `feat: add resumable requirement decomposition sessions`.

## Task 5: Контекст доказательств и приём mapping

**Files:** Create `src/reqmap/agent_context.py`, `tests/test_agent_context.py`,
`tests/test_agent_mapping.py`; modify `agent_service.py`, `agent_replay.py`.

**Interfaces:**
- `get_atom_context(view: SessionView, atom_id: str, knowledge: VerifiedKnowledge) -> dict[str, object]` в `agent_context.py`; содержит context_id, rules, response_schema и существующий prepare payload.
- `search_knowledge(view: SessionView, query: str, knowledge: VerifiedKnowledge, cursor: str | None, page_size: int) -> dict[str, object]`; `get_evidence(view: SessionView, evidence_id: str, knowledge: VerifiedKnowledge, cursor: str | None) -> dict[str, object]`.
- `AgentService.call` добавляет get_atom_context/search_knowledge/get_evidence/submit_mapping. Mapping arguments: session_id, atom_id, context_id, proposal, request_id, expected_revision, optional reported_model.

- [ ] **1. RED tests:** Для legacy/deep сравнить accepted mapping с task 1 API. Cases: чужая сессия/atom/evidence, старый context после replacement, найденный search evidence вне candidates, скрытый negative evidence, неправильный version scope, процедура вне шаблонов, injected instruction в тексте требования.

```python
assert stale_context.error.code == "CONTEXT_MISMATCH"
assert off_candidate.error.code == "PROPOSAL_INVALID"
assert discovery["authoritative_for_mapping"] is False
assert "EV-CONFLICT-NEGATIVE" in accepted_record.evidence_ids
assert result_after_rejection == last_accepted_result
```

  Подмена KB на диске в живом процессе и после restart даёт `KNOWLEDGE_CHANGED`,
  а не использование незаметно обновлённого snapshot.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_context.py tests/test_agent_mapping.py -q`.
- [ ] **3. Implement:** context hash — canonical JSON из session ID, atom content, source hints, profile, KB hash, `top_k`, workflow/tool contract и текущих prompt versions. Retrieval использует точный atom text и source hints как прежний pipeline. Полный context не обрезать; если превышен response limit, вернуть `RESPONSE_TOO_LARGE`. Search — deterministic локальный retrieval по query и pagination с явным advisory marker; он не изменяет allowlist mapping. Evidence ID lookup выдаёт provenance и фрагменты до 16 KiB с continuation cursor, не открывает URL. При чтении локального excerpt проверить принадлежность snapshot и digest. Replay accepted mappings заново вызывает accept API; результаты rejected attempts не применяются. Ошибки не заменяют последний принятый результат. Нормализация/понижение статуса считаются принятым результатом с диагностикой.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_context.py tests/test_agent_mapping.py tests/test_agent_session.py tests/test_mapping.py tests/test_deep_mapping.py tests/test_deep_retrieval.py -q`.
- [ ] **5. Commit:** `feat: expose grounded evidence context and mapping tools`.

## Task 6: Происхождение анализа и общий экспорт

**Files:** Create `src/reqmap/analysis_origin.py`, `publication.py`,
`tests/test_agent_exports.py`; modify `cli.py`, `export_json.py`,
`export_deep_json.py`, оба XLSX/Markdown exporters, `manifest.py`,
`crosscheck.py`, `crosscheck_deep.py`, `OUTPUT_SCHEMA.md`.

**Interfaces:**
- `validate_analysis_origin(raw: object) -> dict[str, object]` в `analysis_origin.py`.
- Exact fields: `mode`, `session_id`, `revision`, `tool_contract_version`, `workflow_version`, `proposal_journal_sha256`, `reported_client`, `identity_verified`; optional `reported_model`. Mode строго `external_agent`, identity_verified строго false; безопасные ограниченные identifiers для reported fields.
- `publish_artifacts(run: RunResult | DeepRunResult, output: Path, *, redacted_values: tuple[str, ...] = ()) -> tuple[CrosscheckIssue, ...]` в `publication.py`, общий для CLI и agent finalization.

- [ ] **1. RED tests:** Проверить origin во всех форматах и manifest, неизменность outputs без origin и отказ при tampering:

```python
assert canonical["metadata"]["model"] == "external-agent"
assert canonical["metadata"]["seed"] is None
assert canonical["metadata"]["analysis_origin"]["identity_verified"] is False
assert manifest["analysis_origin"] == canonical["metadata"]["analysis_origin"]
assert crosscheck_issues_after_origin_tamper
```

  Cases: unknown origin field, `identity_verified=true`, invalid hash, token/URL
  в reported model, origin вместе с ненулевым seed или endpoint. READ/get_result
  не входит в proposal hash. Hash вычисляется по упорядоченным submit_atoms/
  submit_mapping events (accepted и rejected), исключая export/read события.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_exports.py -q`.
- [ ] **3. Implement:** Optional origin добавлять только при наличии. Для нового режима валидировать согласованность model/seed/отсутствия endpoint; без origin оставить прежний контракт. Общий reported_model указывать только если каждое принятое предложение явно сообщает одно и то же значение; иначе поле отсутствует, а отдельные заявления остаются в журнале. В XLSX добавить conditional metadata row `analysis_origin` с canonical JSON, в Markdown — conditional блок происхождения с тем же набором значений; crosscheck проверяет точное соответствие. Manifest не включает собственный hash. Выделить нынешние `_publish_artifacts`/`_publish_deep_artifacts` в общий публичный API, сохранив CLI diagnostics/redaction и обработку failure; не менять unrelated CLI parsing. `run.jsonl` agent export включает proposal audit entries до финальной записи exporters; сохранённая история модели не запрашивается.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_exports.py tests/test_json_manifest.py tests/test_export_deep_json.py tests/test_crosscheck.py tests/test_crosscheck_deep.py tests/test_cli_analyze.py tests/test_cli_deep.py -q`.
- [ ] **5. Commit:** `feat: export external-agent provenance through shared publishers`.

## Task 7: Финализация, частичный результат и восстановление публикации

**Files:** Create `src/reqmap/agent_finalize.py`, `tests/test_agent_finalize.py`;
modify `agent_service.py`, `agent_store.py`, `agent_replay.py`.

**Interfaces:**
- `build_agent_run(view: SessionView, knowledge: VerifiedKnowledge) -> RunResult | DeepRunResult` и `finalize_session(store: SessionStore, config: AgentConfig, command: MutationCommand) -> ToolReply` в `agent_finalize.py`.
- Store: `.reserve_publication(command: MutationCommand, journal_sha256: str) -> dict[str, object]`, `.complete_publication(command: MutationCommand, reply: ToolReply) -> ToolReply`, `.publication(session_id: str) -> dict[str, object] | None`. Stored intent exact fields: session_id, request_id, payload_sha256, source_revision, final_revision, proposal_journal_sha256, staging_name, final_name, status (`pending`/`committed`/`failed`), artifact_hashes. Reservation не увеличивает revision; завершение увеличивает её один раз.
- `AgentService.call` добавляет finalize/get_result. Finalize: session_id/request_id/expected_revision/allow_partial=false. Get_result: session_id/cursor/page_size.

- [ ] **1. RED tests:** Полный и частичный legacy/deep, ноль completed rows, deep gaps/conflicts, XLSX immutability; затем crash до directory rename, после rename и до receipt commit:

```python
assert blocked.error.code == "ANALYSIS_INCOMPLETE"
assert partial_run.run_status == "PARTIAL"
assert len(partial_run.requirements) == original_count
assert unprocessed.analysis_state is AnalysisState.SKIPPED
assert unprocessed.support_status is None
assert repeated_finalize == first_success
assert hashes_after_read == hashes_before_read
```

  Если ни одна строка не завершена, явный partial export имеет `FAILED`, а не
  фиктивный PARTIAL/SUCCESS. Tampered artifact после finalize не выдаётся как
  актуальный проверенный отчёт. Сбой crosscheck возвращает только diagnostics.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_finalize.py -q`.
- [ ] **3. Implement:** Восстановить view через validators, затем использовать существующие aggregators, `instantiate_procedure_graphs`, status functions и evidence closure. Отсутствующие atoms/results представлять skipped с null status, сохраняя все строки. При любом итоговом статусе кроме SUCCESS и `allow_partial=false` вернуть диагностику без публикации. Origin revision — зарезервированная final_revision. Экспортировать в серверный staging под output_root, выполнить crosscheck/hash verification, затем atomic directory rename в UUID-каталог; не перезаписывать другой output. Per-session lock держится до завершения; SQLite transaction не держится во время XLSX export. Pending intent блокирует другие mutation calls. После crash pending intent восстанавливается по тому же request ID: полный каталог проверяется и фиксируется, незавершённый staging пересобирается из той же revision. Controlled export error завершает intent как failed; новая сессия нужна для нового анализа. Get_result перечитывает manifest/hashes перед выдачей путей.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_finalize.py tests/test_agent_store.py tests/test_agent_exports.py tests/test_deep_aggregation.py tests/test_procedure.py -q`.
- [ ] **5. Commit:** `feat: finalize agent sessions with recoverable artifact publication`.

## Task 8: MCP stdio и публичная команда запуска

**Files:** Create `src/reqmap/agent_tools.py`, `mcp_stdio.py`,
`tests/test_agent_tools.py`, `tests/test_mcp_stdio.py`, `tests/test_cli_agent.py`;
modify `src/reqmap/cli.py`, `agent_service.py`, `agent_finalize.py`
для применения проверки размера ответа до фиксации mutations.

**Interfaces:**
- `tool_definitions() -> tuple[dict[str, object], ...]`, `call_tool(service: AgentService, name: str, arguments: object) -> dict[str, object]` в `agent_tools.py`; последнее возвращает MCP CallToolResult.
- `serve_stdio(service: AgentService, reader: BinaryIO, writer: BinaryIO, diagnostics: TextIO, limits: AgentLimits) -> int` в `mcp_stdio.py`.
- CLI: `reqmap agent serve --config PATH`; config/startup failure — exit 2 и stderr, штатный EOF — exit 0. До MCP handshake stdout пуст.
- Exact tool names: `reqmap_start_session`, `reqmap_get_session`, `reqmap_submit_atoms`, `reqmap_get_atom_context`, `reqmap_search_knowledge`, `reqmap_get_evidence`, `reqmap_submit_mapping`, `reqmap_finalize`, `reqmap_get_result`. Эти же полные имена принимает `AgentService.call` во всех предыдущих задачах.

- [ ] **1. RED tests:** Реальный subprocess через installed console entry point с development PYTHONPATH; каждая версия initialize → initialized → list → call → EOF. Проверить девять точных tool names, отсутствие model/shell tools и structured/text equivalence:

```python
assert result["capabilities"] == {"tools": {}}
assert len(listed["tools"]) == 9
assert json.loads(reply["content"][0]["text"]) == reply["structuredContent"]
assert reply["isError"] is (not reply["structuredContent"]["ok"])
assert all(json.loads(line)["jsonrpc"] == "2.0" for line in stdout_lines)
```

  Cases: запрос до initialize, unsupported version negotiation, duplicate JSON
  keys, NaN, UTF-8 error, неизвестный method/tool, notification без response,
  frame >2 MiB, response >512 KiB, медленная/оборванная запись frame,
  request с текстом-инструкцией, config path с пробелами. Не отзывать уже
  committed mutation при поздней cancellation notification. Oversized mutation
  reply даёт `RESPONSE_TOO_LARGE`, при этом revision и canonical state прежние.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_tools.py tests/test_mcp_stdio.py tests/test_cli_agent.py -q`.
- [ ] **3. Implement:** JSON schemas описывают exact contracts задач 4/5/7 и unknown-fields rejection; server validation не полагается на клиентскую JSON Schema. Unknown method `-32601`, malformed JSON `-32700`, invalid envelope `-32600`, invalid protocol params/unknown tool `-32602`; ошибки предметных tools — CallToolResult с isError. Batch JSON-RPC не поддерживается заявленными версиями. На unsupported requested version отвечать `2025-11-25`, не заявлять более новую. stdout — только newline JSON-RPC, stderr не содержит исходных proposals/секретов. Read-only tools имеют соответствующие annotations, mutations не помечены read-only. Bounded frame reader ограничивает память до разбора; превышение frame завершает соединение после допустимой protocol error без обработки остатка как нового запроса. До фиксации mutation проверить размер сериализованного ответа; превышение даёт `RESPONSE_TOO_LARGE` без изменения revision. Размер учитывает structured и text representations, а не только внутренний data object. При pagination уменьшать число записей в странице до лимита; одну слишком большую запись отклонять явно. RPC ID и прикладной request_id различны.
- [ ] **4. GREEN/regression:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_tools.py tests/test_mcp_stdio.py tests/test_cli_agent.py tests/test_cli_version.py tests/test_docs_language.py -q`.
- [ ] **5. Commit:** `feat: serve reqmap agent tools over MCP stdio`.

## Task 9: Подключение клиентов и инструкция для школьников

**Files:** Create `examples/ide/codex.config.toml`, `examples/ide/opencode.json`,
`examples/ide/jetbrains-mcp.json`, `examples/ide/jetbrains-acp.json`, `tests/test_agent_client_configs.py`;
modify `.agents/skills/reqmap/SKILL.md`, `tests/test_skill_contract.py`, README,
`CLIENTS_CODEX_OPENCODE.md`, `BEGINNER_GUIDE.md`, `INSTALL_OFFLINE.md`, `RUNBOOK.md`,
`TROUBLESHOOTING.md`.

**Interfaces:** Каждый пример запускает тот же console entry point
`.venv/bin/reqmap agent serve --config ...`; paths — отдельные аргументы без shell
interpolation. Файлы — примеры, не автоматическая перезапись пользовательских
настроек. В PyCharm один маршрут регистрации MCP на агента, без дубликатов.

- [ ] **1. RED tests:** TOML через `tomllib`, JSON через `json`; подставить временный абсолютный checkout с пробелами и вызвать сервер по command/args. Assert discovery девяти tools без model config. Обновить skill contract тест: две явно раздельные ветви analyze/agent, обязательный finalize, полный набор артефактов, gaps и запрет infrastructure execution; удалить старое ограничение 220 слов, которое не описывает новую функциональность. Не заменять его тестом на количество слов.
- [ ] **2. Проверить RED:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_client_configs.py tests/test_skill_contract.py -q`.
- [ ] **3. Implement:** Перед написанием client settings сверить текущие официальные Codex/OpenCode/JetBrains docs по ссылкам спецификации. Перед изменением skill прочитать применимые skill-authoring instructions. Skill использует инструменты для source IDs и context, прекращает автоматические повторы после двух отказов, не делает fallback на отдельный LLM, сообщает реальный статус и links. Руководство: установка Linux/macOS → установка/вход клиента → открытие checkout → подключение MCP → проверка tools → учебный запрос → получение пяти файлов → продолжение session ID. Codex/OpenCode называются клиентами, provider/model — отдельными настройками. VS Code/OpenCode terminal route честно исключён из встроенного чата; целевые три маршрута из спецификации сохранены. Для deep не выдавать synthetic snapshot за production. Старые shell примеры остаются отдельным режимом.
- [ ] **4. GREEN и ручная проверка copy/paste:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_client_configs.py tests/test_skill_contract.py tests/test_docs_language.py -q`; проверить каждую команду установки/запуска на временной поставке. Reversible prose-only edits не покрывать отдельными поверхностными тестами.
- [ ] **5. Commit:** `docs: connect reqmap to IDE agents and add student workflow`.

## Task 10: Сквозная приёмка, масштаб и настоящие агенты

**Files:** Create `tests/test_agent_acceptance.py`, `tests/test_agent_scale.py`,
`tests/fixtures/agent_eval.json`, `docs/acceptance/reqmap-ide-agent-layer.md`;
extend `tests/agent_support.py`, `tests/test_distribution.py`.

**Interfaces:**
- Test support: `scripted_agent_flow(command: list[str], config_path: Path, source: dict[str, object], profile: AnalysisProfile) -> dict[str, object]` — MCP client на stdlib subprocess; отправляет явные proposals из synthetic fixtures, не содержит LLM или HTTP server.
- Eval case fields: id, profile, input, required_obligations, expected_statuses, required_evidence_ids, expected_clarification, forbidden_actions. Для deep — existing signed synthetic gold snapshot; для учебного legacy — shipped KB. Отчёт отличает scripted, real-agent и непроверенные cases.

- [ ] **1. RED acceptance:** Добавить flow для texts/TXT/XLSX в обоих профилях, restart после atoms, исправление rejected mapping и partial finalize. Запретить `OpenAICompatibleClient` constructor и любые socket connect в backend; subprocess проверять также в Linux `--network none`.

```python
assert input_xlsx.read_bytes() == before_bytes
assert set(published_files) == {"result.json", "result.xlsx", "report.md", "run.jsonl", "manifest.json"}
assert len(run["requirements"]) == 1950
assert all_rows_seen_once_across_pages
assert backend_model_calls == 0
assert backend_network_attempts == 0
```

  Масштабный тест использует 1950 требований и минимум один atom на строку;
  финализация и counts обязательны, не только импорт. Измерить wall time и peak
  memory, не приписывать эти цифры настоящей модели. Настроить tool timeout
  в примерах по измеренной финализации с запасом.
- [ ] **2. Выполнить новые тесты:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_acceptance.py tests/test_agent_scale.py tests/test_distribution.py -q`. Если уже проходят на реализациях 1–9, не создавать искусственный RED; зафиксировать integration coverage.
- [ ] **3. Проверить поставку:** Полный suite один раз после последних изменений: `PYTHONPATH=src .venv/bin/python -m pytest -q`. Затем чистый offline install на macOS и Linux/Python 3.11 из копии поставки, без dev PYTHONPATH; `python -m pip check`, wheel audit и installed MCP flow. Зафиксировать реальные команды/версии/результаты в acceptance report; старые 933 tests не выдавать за текущий результат.
- [ ] **4. Настоящие IDE/агенты:** Выполнить VS Code+Codex, PyCharm+Codex и PyCharm+OpenCode ACP; Linux и macOS должны быть представлены в общей матрице. Для каждого записать версии, видимость skill/tools, клиентский provider/model label, prompt, session ID и hashes результата. Frozen eval: несколько обязательств, неоднозначность, negative evidence, conflict, prompt injection. Проверить zero false-supported на этих случаях и отсутствие пропуска required obligations; отклонения записать и исправить workflow до готовности. Подключённый клиент использует свою авторизацию, второй API key не создаётся. Если реального клиента/доступа нет, продолжить доступные проверки и явно оставить соответствующие строки `not run`, не объявляя школьный маршрут готовым.
- [ ] **5. Завершить review:** Перед merge применить verification-before-completion и whole-branch review согласно выбранному способу исполнения. Исправить подтверждённые findings и повторить только затронутые проверки. Commit: `test: verify IDE agent workflow and portable distribution`. Публикация и merge выполняются по действующей авторизации пользователя; наличие планового пункта само по себе не заменяет её.

## Покрытие спецификации и итоговый результат

| Раздел спецификации | Задачи |
| --- | --- |
| 1–2: роль клиента/модели/backend и границы | 1, 4–5, 9–10 |
| 3: три маршрута чата IDE | 8–10 |
| 4: конфигурация и portable install | 2, 8–10 |
| 5: общий предметный API | 1, 5–7 |
| 6: девять tools и proposals | 4–5, 7–8 |
| 7: durable state, повторы, restart | 3–5, 7, 10 |
| 8: protocol, limits, paths | 2–3, 5, 8 |
| 9: statuses, artifacts и provenance | 6–7, 10 |
| 10: обновление клиентского контракта | 9 |
| 11: уровни приёмки и ограничения гарантий | 1–10 и acceptance report |

Готовность кода, установленной поставки, интеграции с IDE и качества анализа
фиксируются раздельно. Итоговый отчёт содержит фактические результаты, открытые
ограничения и commit SHA; строка `not run` не превращается в PASS по документации.
Работа по продукту начинается после ревью этого плана и выбора способа исполнения.
