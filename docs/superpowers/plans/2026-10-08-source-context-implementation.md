# План реализации проверенного контекста исходных строк

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Исключить доказательный вывод по строке, независимость или полный
внешний контекст которой не проверены, сохранив исходные строки и все условия.

**Architecture:** Неизменный snapshot входа и проверенная карта питают один
детерминированный resolver. Его effective obligations проходят существующие
evidence gates в CLI/MCP и legacy/deep; replay и экспорт воспроизводят то же
решение из сохранённых bytes. Подготовительные задачи добавляют чистые функции,
затем один согласованный шаг включает gate и новые версии во всех runtime paths.

**Tech Stack:** Python 3.11+, dataclasses, pytest, существующие JSON/JSONL,
SQLite, OpenSSH Ed25519 и openpyxl; без новых runtime dependencies.

**Spec:** [Согласованный контракт](../specs/2026-10-08-source-context-design.md).

**Статус:** спецификация согласована пользователем 08.10.2026; этот план
подготовлен для проверки. Реализация не начата. База кода — `8dd19f3`;
план не означает готовность исходных 1949 строк к предметной оценке.

## Global Constraints

- Исходные XLSX, KB, binding catalog и собственная грамматика 1.2 не меняются.
- Каждая исходная строка, её порядок, technical ID, текст и quote сохраняются.
- Без reviewed independent/linked с `context_complete=true` итог —
  `insufficient_evidence`; правило одинаково для XLSX, TXT и inline texts.
- Нет автоматического наследования по parent/ID, транзитивного наследования,
  общего разрешения независимости или утверждения карты через model/MCP proposal.
- Наследуются только interface `api`/`gui` и существующие constraints:
  duration (`within`, canonical decimal `0..999999999999`, `ms`),
  host_failure/gpu (`eq`, `"true"`, `null`). Все остальные поля остаются своими.
- Origin — только span другой импортированной `Requirement.text`. Неизвестная
  обязательная оговорка, в том числе из source field, требует unresolved.
- Карта: schema 1.0, максимум 25 MiB (`25 * 1024 * 1024`), 64 links на строку.
  Deep: подпись точных bytes, namespace `reqmap-source-context`, внешний trust.
- Context map/resolver 1.0/1.0; SourceBinding/engine 2.0/2.0;
  result legacy/deep 1.2/2.2; MCP tool/workflow 3.0/3.0; proposal schema 2.
- Prompts decomposition/mapping/deep mapping 2.1/1.4/2.2; seed_version 2;
  SQLite STATE_VERSION 1 и прежние девять MCP tools сохраняются.
- Старые active/checkpoints несовместимы; finalized 1.0/1.1/2.0/2.1 читаются
  исторически с проверкой artifact hashes, без исполнения нового resolver.
- Пять артефактов и 5/7 листов XLSX сохраняются. Цитаты не обрезаются молча.
- Никаких OpenStack/Kolla/host actions, новых model endpoints, автоматической
  правки trust/ключей, публикации приватных книг или разметки всего корпуса.

## Review Focus

1. Одинаковые source IDs/цитаты на разных листах, emoji и повторное вхождение:
   адресовать точную строку и codepoint span, а не похожий текст — Tasks 1–2.
2. Два уровня заголовков, условие после списка, разные условия атомов одной строки:
   учитывать только полные прямые связи; неоднозначность unresolved — Task 3.
3. Исчезновение входа/карты, смена карты и отзыв signer после старта:
   сохранить замороженный смысл, но перепроверить текущий deep trust — Tasks 4–5.
4. Модель удаляет GUI, подсовывает reviewed через clarification или использует
   заголовок как evidence: не повысить результат прежних gates — Task 5.
5. Старый finalized результат и новый отчёт с длинным контекстом/formula-like
   цитатами: сохранить историческую публикацию и полный безопасный вывод — Tasks 5–6.

## Файлы и порядок работы

| Файлы | Ответственность |
| --- | --- |
| `src/reqmap/source_context_models.py` | Immutable snapshot, refs, map, decisions и provenance |
| `src/reqmap/source_context.py` | Capture, безопасная загрузка/trust, resolver и digest его кода |
| `src/reqmap/source_context_codec.py` | Строгий JSON, frozen payload, восстановление и проверка решений |
| `src/reqmap/binding_models.py`, `binding_engine.py`, `binding_runtime.py`, `binding_codec.py` | Effective obligations и gate в общем binding contract |
| `src/reqmap/cli.py`, `pipeline.py`, `deep_pipeline.py`, `models.py` | Захват CLI input, run signature, checkpoints |
| `src/reqmap/agent_types.py`, `agent_store.py`, `agent_service.py`, `agent_context.py`, `agent_replay.py`, `agent_finalize.py` | Frozen MCP seed, replay, context, historical outputs |
| `src/reqmap/config.py`, `config_common.py`, `agent_config.py` | Операторский путь карты и проверка пересечений |
| `src/reqmap/binding_export.py`, существующие `export_*` и `crosscheck*` | Канонический контроль и отображение условий |
| `tests/source_context_support.py`, `tests/test_source_context*.py` | Синтетические примеры, отрицательные и сквозные проверки |

Порядок: 1 → 2 → 3 → 4 → 5 → 6 → 7. Задачи 1–4 не подключают новый путь
к публичному runtime. Задача 5 одновременно меняет producers, decoders,
validators, versions и прежние тестовые fixtures. Промежуточные коммиты
не объявляются готовым релизом. В задаче 6 завершается человекочитаемый вывод.
После каждого GREEN добавлять только перечисленные в Files изменённые пути,
сохранять RED/GREEN и SHA в локальном progress ledger, затем делать commit.

### Task 1: Заморозить случаи и определить неизменный вход

**Files:** Create `src/reqmap/source_context_models.py`,
`src/reqmap/source_context.py`, `tests/source_context_support.py`,
`tests/test_source_context_snapshot.py`,
`tests/fixtures/source_context/cases.json`,
`tests/fixtures/historical_source_context/README.md` и синтетические
`legacy/`, `deep/` внутри последнего каталога. Read `agent_input.py`,
`input_text.py`, `input_xlsx.py`, `binding_models.py`, `export_json.py`.

**Interfaces:**
- Consumes `Requirement`, `SourceCoordinate`, `InputProfile`, `BoundObligation`,
  `BoundConstraint`, `SourceSpan` и `canonical_json_bytes` текущего кода.
- Produces `SourceDocumentSnapshot(content: bytes, source_kind: str,
  source_name: str, text_mode: str | None, input_profile: InputProfile | None,
  input_sha256: str, input_profile_sha256: str | None,
  requirements_sha256: str, requirements: tuple[Requirement, ...])`.
- `capture_source_document(*, content: bytes, source_kind: str, source_name: str,
  requirements: tuple[Requirement, ...], input_profile: InputProfile | None,
  text_mode: str | None = None) -> SourceDocumentSnapshot` в `source_context.py`.
  Kinds `xlsx`, `txt`, `texts`; text_mode `single`/`lines` только для txt.
- `SourceRowRef(requirement_id: str, coordinate: SourceCoordinate,
  source_sha256: str)`; `SourceFragmentRef(row: SourceRowRef, span: SourceSpan)`.
  Wire origin хранит `row` и `span`; target — непосредственно SourceRowRef.
- `SourceContextLink(link_id: str, origin: SourceFragmentRef, field: str,
  value: str | BoundConstraint)`; constraint в карте не имеет `source_spans`.
  `SourceContextEntry(target: SourceRowRef, disposition: str, review_state: str,
  reviewed_by: str | None, reviewed_at: str | None, reason_ru: str,
  context_complete: bool, links: tuple[SourceContextLink, ...])`.
  `SourceContextMap` содержит schema_version, map_id, source_kind/source_name,
  три source digests и `rows: tuple[SourceContextEntry, ...]`.

- [x] Зафиксировать synthetic cases до реализации: own text, origin texts,
  dispositions, typed links, ожидаемые status/diagnostic codes, количество
  строк/атомов. Включить все классы §7 спецификации; expectations не вычислять
  resolver. На старом коде сохранить воспроизведение detached API `supported`
  против желаемого `insufficient_evidence` в локальный RED log; не закреплять
  ошибку как норму и не оставлять failing/xfail test в основном suite.
- [x] Старым кодом изготовить анонимные finalized legacy 1.1/deep 2.1 outputs
  и seed/receipt состояния; сохранить пять файлов, hashes, версии и способ
  генерации в fixture README. Не копировать приватные результаты. Имеющиеся
  исторические 1.0/2.0 fixtures сохранить без пересоздания.
- [x] Добавить `test_snapshot_preserves_rows_and_ignores_derived_grouping`,
  `test_full_profile_defaults_are_hashed`, `test_snapshot_text_modes_roundtrip`:
  `assert snapshot.requirements == original_rows`; digest не меняется от
  parent/group, меняется от порядка/source_fields/hints/профиля; single/lines
  восстанавливают разные исходные границы без нормализации Unicode.
- [x] RED: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_snapshot.py`.
  Ожидается отсутствие нового API; сохранённый baseline отдельно доказывает дефект.
- [x] Реализовать перечисленные immutable типы и capture. Проверять соответствие
  rows повторному импорту из переданных bytes; исключать только derived grouping
  из сравнения/digest. Сохранить text_mode для точного CLI stdin replay.
  Хешировать полный нормализованный профиль, включая defaults, либо null.
  Импорты типов между binding/models/context делать без import cycle.
- [x] GREEN: та же команда плюс `tests/test_input_text.py tests/test_input_xlsx.py
  tests/test_input_profiles.py tests/test_agent_input.py`; все проходят.
- [x] Commit: `test: freeze source context cases and immutable input contract`.

### Task 2: Строгая карта, конфигурация и deep trust

**Files:** Create `src/reqmap/source_context_codec.py`,
`tests/test_source_context_map.py`, `tests/test_source_context_trust.py`;
modify `source_context.py`, `source_context_models.py`, `config.py`,
`config_common.py`, `agent_config.py` в `src/reqmap/`,
`tests/test_config.py`, `tests/test_agent_config.py`, `tests/source_context_support.py`.

**Interfaces:**
- Consumes Task 1 refs/map/snapshot, `AnalysisProfile`, `KnowledgeTrustConfig`,
  существующий `agent_input.read_regular_bytes(path: Path, maximum: int) -> bytes`.
- Codec: `decode_source_context_map(raw: bytes, document: SourceDocumentSnapshot)
  -> SourceContextMap`; `encode_source_context_map(mapping: SourceContextMap)
  -> dict[str, object]`. Не использовать encode вместо exact bytes для подписи.
- Runtime: `load_source_context_map(path: Path | None,
  document: SourceDocumentSnapshot, *, profile: AnalysisProfile,
  trust: KnowledgeTrustConfig | None) -> LoadedSourceContext`.
- Models: `ContextTrust(profile: str, namespace: str | None,
  signer_identity: str | None, signature_sha256: str | None,
  allowed_signers_sha256: str | None)` и
  `LoadedSourceContext(map_bytes: bytes | None, signature_bytes: bytes | None,
  map_sha256: str | None, mapping: SourceContextMap | None, trust: ContextTrust)`.
- `verify_source_context_signature(map_bytes: bytes, signature_bytes: bytes,
  trust: KnowledgeTrustConfig) -> ContextTrust` в `source_context.py`;
  `source_context_output_diagnostics(path: Path | None, output_roots: tuple[Path, ...],
  trust: KnowledgeTrustConfig | None) -> tuple[str, ...]`.
- AppConfig/AgentConfig: `source_context_path: Path | None = None`, strict field
  allowlist, путь относительно config. Поле не добавляется в MCP arguments.

- [x] Тесты `test_map_exact_refs_and_unicode_spans`, `test_map_rejects_ambiguous_json`,
  `test_map_limits`, `test_map_does_not_accept_semantic_bypass`: проверять точный
  код `SOURCE_CONTEXT_REF_INVALID` для чужой координаты/hash/UTF-16 offsets,
  `SOURCE_CONTEXT_INPUT_MISMATCH` для любого source digest; `INVALID` для
  duplicate JSON key/target/link_id, unknown fields, bool-as-int, неверного UTC,
  пустого reviewer/reason, illegal disposition/links/context_complete.
- [x] Проверить границы duration `0`, `999999999999`, запрет `01`, `-1`, дроби,
  `1000000000000`; 64 links разрешены, 65 запрещены; ровно 25 MiB и превышение
  на byte. Duplicate link_id запрещён во всей карте. Draft проходит структурную
  проверку, но reviewed_by/at должны быть null; draft не означает reviewed.
- [x] `test_deep_signature_exact_bytes_and_namespace`, `test_safe_map_read_and_overlap`:
  временные test keys; правильная подпись принимается, другая namespace/identity,
  не-Ed25519, изменение bytes, symlink/FIFO/race, map/signature/trust под output
  отклоняются; отсутствие карты допустимо и в deep. Не менять настоящий trust.
- [x] RED: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_map.py tests/test_source_context_trust.py tests/test_config.py tests/test_agent_config.py`.
  Новые parser/config/trust assertions падают по отсутствующим возможностям.
- [x] Реализовать функции с exact-key JSON decoder и existing safe reader.
  Ошибки формы/чтения карты переводить в `SOURCE_CONTEXT_INVALID`, refs/hash
  отдельно; ошибки подписи в `SOURCE_CONTEXT_UNTRUSTED`. Freeze signature и
  allowed_signers bytes перед `ssh-keygen -Y verify`, timeout 10 s, shell=False,
  namespace `reqmap-source-context`, identity из действующей trust policy.
  Подпись/allowed_signers ограничить существующим пределом 1 MiB.
  Ключевые assertions: `assert loaded.mapping.schema_version == "1.0"`;
  после плохого span `assert error.value.code == "SOURCE_CONTEXT_REF_INVALID"`;
  после подмены подписи `assert error.value.code == "SOURCE_CONTEXT_UNTRUSTED"`.
- [x] GREEN: повторить RED-команду и `tests/test_binding_catalog.py
  tests/test_agent_input.py`; существующий binding trust не меняется.
- [x] Commit: `feat: validate reviewed source context maps and trust`.

### Task 3: Чистый resolver и provenance эффективных обязательств

**Files:** Modify `src/reqmap/source_context_models.py`,
`src/reqmap/source_context.py`, `tests/source_context_support.py`;
create `tests/test_source_context_resolver.py`.

**Interfaces:**
- Consumes snapshot и LoadedSourceContext, `bind_source(requirement) -> SourceBinding`.
- Models: `ConstraintOrigins(semantic_key: tuple[str, str, str, str | None],
  source_refs: tuple[SourceFragmentRef, ...])`,
  `EffectiveObligation(obligation: BoundObligation,
  interface_refs: tuple[SourceFragmentRef, ...],
  constraint_refs: tuple[ConstraintOrigins, ...])`.
- `SourceContextIssue(code: str, message_ru: str, field: str | None,
  source_refs: tuple[SourceFragmentRef, ...])`;
  `SourceContextDecision(target: SourceRowRef, state: str, map_sha256: str | None,
  entry_sha256: str | None, context_id: str, resolver_version: str,
  resolver_sha256: str, applied_links: tuple[SourceContextLink, ...],
  effective_obligations: tuple[EffectiveObligation, ...],
  diagnostics: tuple[SourceContextIssue, ...])`.
  States: independent/linked/unreviewed/unresolved/conflict.
- `resolve_source_context(document: SourceDocumentSnapshot,
  loaded: LoadedSourceContext) -> tuple[SourceContextDecision, ...]`;
  `source_context_resolver_sha256() -> str` в `source_context.py`.

- [x] `test_missing_draft_and_unresolved_have_no_admissible_context`:
  state/codes UNREVIEWED либо UNRESOLVED; own unresolved не становится bound.
  `test_links_apply_to_all_own_atoms_without_rewriting_quotes`:
  ids/quotes/actor/action/object/direction/contour/phase/release равны оригиналу;
  все атомы получают одинаковый проверенный набор external constraints.
- [x] `test_conflicts_do_not_choose_nearest_or_stronger_condition`:
  own API/external GUI, два external интерфейса, разные duration дают CONFLICT;
  одинаковые semantic keys дают одно constraint и все origin refs;
  разные host_failure/gpu/duration объединяются без удаления своих условий.
- [x] `test_no_transitive_or_group_id_inheritance`: grandparent не применяется
  без прямой ссылки target; после прямой ссылки присутствуют оба источника.
  Условие после списка/cross-sheet принимается по exact ref; source field
  с непредставимым условием и разные контексты атомов используют unresolved.
- [x] `test_cycle_and_self_link_are_preflight_errors`: CYCLE для applicable
  reviewed links; draft/unresolved notes не образуют действующий граф.
  `test_context_identity_covers_annotation_and_code`: изменение review/refs/
  value/input/profile/entry/resolver меняет context_id; порядок rows сохранён.
- [x] RED: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_resolver.py`.
  Ожидаются отсутствующие resolver/decision API.
- [x] Реализовать deterministic resolver: preflight всего графа, затем каждая
  строка отдельно; не обходить origin decisions ради наследования. У внешних
  constraints `source_spans=()`; локальные spans остаются только локальными.
  Canonical decision hash включает source identity и typed/provenance данные,
  но не самого себя. Resolver digest — SHA-256 canonical manifest отсортированных
  имён/hashes ровно шести файлов §5 спецификации, включая три context-модуля.
  Для GUI-кейса: `assert decision.state == "linked"` и
  `assert decision.effective_obligations[0].obligation.interface == "gui"`;
  для конфликта: `assert decision.state == "conflict"`.
- [x] GREEN: повторить команду и `tests/test_binding_source.py`; own grammar
  code/hash неизменны. Все synthetic resolver cases имеют независимые expectations.
- [x] Commit: `feat: resolve explicit source context without implicit inheritance`.

### Task 4: Переносимый frozen snapshot и строгий replay codec

**Files:** Modify три `src/reqmap/source_context*.py`,
`tests/source_context_support.py`; create `tests/test_source_context_codec.py`.

**Interfaces:**
- Models: `SourceContextSnapshot(document: SourceDocumentSnapshot,
  loaded: LoadedSourceContext, decisions: tuple[SourceContextDecision, ...],
  grammar_version: str, grammar_sha256: str)`.
- `freeze_source_context(document: SourceDocumentSnapshot,
  loaded: LoadedSourceContext) -> SourceContextSnapshot` в `source_context.py`.
- Codec: `encode_source_context_snapshot(snapshot: SourceContextSnapshot)
  -> dict[str, object]`; `decode_source_context_snapshot(raw: object, *,
  profile: AnalysisProfile, trust: KnowledgeTrustConfig | None)
  -> SourceContextSnapshot`; `validate_source_context_snapshot(snapshot:
  SourceContextSnapshot, *, profile: AnalysisProfile,
  trust: KnowledgeTrustConfig | None) -> None`.
  Bytes — строгий base64; null map означает именно зафиксированное отсутствие.
- `inspect_source_context_record(raw: object) -> SourceContextSnapshot` в codec
  проверяет структуру, input/map/decisions и hashes без текущего внешнего trust.
  Это граница offline export/crosscheck, не авторизация active acceptance.
  Полный decode вызывает эту проверку и дополнительно проверяет deep подпись.

- [x] `test_frozen_context_roundtrip_without_external_files`: после freeze
  удалить input/map/signature, decode из JSON возвращает те же document,
  normalized map (включая draft), decisions и context_id без чтения этих путей.
  Для deep текущий allowed_signers остаётся внешней обязательной проверкой.
- [x] `test_replay_detects_tampered_decision_even_when_status_unchanged`:
  менять quote/typed value/entry digest/bytes/порядок строк/effective obligation;
  decoder отказывает CHANGED, а не принимает сохранённое вычисление на веру.
  `test_replay_rechecks_revoked_signer`: UNTRUSTED после отзыва; изменение только
  unrelated signers не меняет frozen context_id и не подменяет snapshot bytes.
- [x] RED: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_codec.py`.
  Ожидается отсутствие freeze/codec, затем проверяемые ошибки tamper cases.
- [x] Реализовать exact-key decoder с повторным импортом input, map validation
  и resolver. Сравнивать canonical decisions целиком; сохранённый trust digest
  описывает момент capture, текущая проверка разрешения signer выполняется
  отдельно. Некорректный сохранённый payload — CHANGED; trust — UNTRUSTED;
  версия/hash resolver отличаются — явная contract mismatch без пересчёта
  активного результата под новым кодом. Никаких fallback на external map.
  Основные assertions: `assert restored.decisions == frozen.decisions`;
  после подмены `assert error.value.code == "SOURCE_CONTEXT_CHANGED"`.
- [x] GREEN: повторить команду и все `tests/test_source_context_*.py`.
- [x] Commit: `feat: freeze and revalidate portable source context snapshots`.

### Task 5: Одновременно включить gate, CLI/MCP persistence и версии

**Files:** Modify `src/reqmap/{models,binding_models,binding_engine,binding_runtime,
binding_codec,binding_export,cli,pipeline,deep_pipeline,agent_types,agent_store,
agent_service,agent_context,agent_replay,agent_finalize,agent_tools,prompts}.py`;
при необходимости адаптировать потребителей существующих binding interfaces
в `decomposition.py`, `mapping.py`, `deep_mapping.py` без смены proposal schema.
Create `tests/test_source_context_runtime.py`, `tests/test_source_context_sessions.py`;
modify `tests/{factories,binding_factories,deep_factories,agent_support,
source_context_support}.py` и существующие тесты этих runtime modules.

**Interfaces:**
- Consumes freeze/codec/resolver Tasks 1–4. `AnalysisRequest` получает
  `source_document: SourceDocumentSnapshot | None = None`; CLI создаёт его из
  уже прочитанных bytes до grouping, MCP — из InputSnapshot. CLI inline → texts,
  stdin → txt с текущим text_mode; названия/координаты источников не меняются.
- `requirement_context(requirement: Requirement, catalog: BindingCatalog | None,
  source_context: SourceContextSnapshot | None = None) -> BindingContext`.
  BindingContext хранит snapshot; SourceBinding получает
  `context_decision: SourceContextDecision | None` и contract_version 2.0.
  Собственный `bind_source` по-прежнему разбирает только свою строку.
- `BindingDiagnostic.source_refs: tuple[SourceFragmentRef, ...] = ()`;
  `BindingDecision.uncovered_context_refs: tuple[SourceFragmentRef, ...] = ()`,
  `context_id: str | None = None`. Original obligations и effective obligations
  различны; `context_obligation` использует повторно проверенное effective.
- SessionSeed: обязательный context snapshot для seed_version=2, отдельный
  decoder старого payload без версии. SessionSettings/binding_contract содержат
  source_context_version/resolver_version/resolver_sha256 и новые версии.
  Seed input и context.document сравниваются, если input остаётся дублирован.
- Run metadata: `source_context` — encoded snapshot; binding contract дополнен
  resolver identity. `_run_signature`/`_deep_run_signature` включают source,
  map и resolver digests. Checkpoint namespace хранит frozen payload и проверяет
  его до использования результатов. Новая карта при новом старте — новый run.

- [x] Добавить общий параметризованный runtime matrix: CLI/MCP × legacy/deep ×
  reviewed independent/API link/GUI link/no map/draft/unresolved/conflict.
  `test_context_gate_is_shared_by_all_entrypoints` проверяет exact codes,
  положительные API controls и сохранение всех исходных строк, включая origins.
  `test_direct_acceptor_without_snapshot_is_unreviewed` запрещает обход через
  Python accept_mapping/accept_deep_mapping. Старые gates всё ещё могут отказать
  при reviewed карте; supported/partial/not_supported/not_applicable без неё
  все превращаются в insufficient, а не в доказанный отрицательный результат.
- [x] `test_model_cannot_approve_or_remove_context`: fake source_context fields
  в proposal/start отклоняются; clarifications/hints/parent_session_id не
  повышают доверие. Canonical selection не разрешает убрать трудный атом;
  origin нельзя выдать за implementation evidence. Snapshot/decision подмена
  через вручную сконструированный BindingContext выявляется до acceptance.
- [x] `test_active_session_uses_frozen_map_and_current_trust`: restart после
  удаления/изменения input/map сохраняет digest и решения, новая session видит
  новую карту; revoked signer блокирует active. CLI checkpoint replay не читает
  внешнюю карту повторно после выбора frozen run; новая run signature отделена.
- [x] `test_old_active_rejected_and_finalized_returned_historically`: все четыре
  старых result schema; старые active/checkpoints отвергаются с явной mismatch,
  finalized проходит прежние hashes и `historical=true` без вызова resolver.
  Повторный request_id/receipt, restart во время finalize и sealed publication
  сохраняют прежнюю идемпотентность; SQLite tables/STATE_VERSION не меняются.
- [x] RED: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_runtime.py tests/test_source_context_sessions.py`.
  Ожидаемый семантический RED: detached/no-map ещё supported; версии/seed старые.
- [x] Реализовать перечисленные интерфейсы и все версии Global Constraints
  одним изменением. `requirement_context` без snapshot даёт UNREVIEWED; pipeline
  AnalysisRequest без raw snapshot отклоняется preflight с INPUT_MISMATCH,
  чтобы полный run всегда можно было воспроизвести из сохранённых bytes.
  Клиентские inputs всегда получают полный snapshot. Не вводить публичный
  compatibility flag. Binding codec/export validator перепроверяют effective
  source и context_id; промпты показывают ограничения, но модель их не утверждает.
- [x] Обновить только синтетические positive fixtures: reviewed independent
  разрешать явно для конкретных перечисленных строк/bytes. Не делать autouse
  fixture, объявляющую независимыми все входы. Отдельные no-map tests остаются.
  Full snapshot JSON уже экспортируется; human-readable rendering — Task 6.
  Для acceptor без snapshot:
  `assert accepted.binding_decision.support_status is SupportStatus.INSUFFICIENT_EVIDENCE`;
  для reviewed API control:
  `assert accepted.binding_decision.support_status is SupportStatus.SUPPORTED`.
- [x] GREEN: повторить RED-команду; затем `PYTHONPATH=src .venv/bin/python -m pytest -q`.
  Все существующие и новые tests проходят, прежние historical fixtures неизменны.
- [x] Commit: `feat: enforce reviewed source context across CLI and MCP contracts`.

### Task 6: Полный контекст в пяти артефактах и crosscheck

**Files:** Modify `src/reqmap/{binding_export,export_json,export_deep_json,
export_markdown,export_deep_markdown,export_xlsx,export_deep_xlsx,crosscheck,
crosscheck_deep,pipeline,deep_pipeline,agent_finalize}.py`;
create `tests/test_source_context_exports.py`; modify `tests/test_binding_exports.py`,
`tests/test_export_markdown.py`, `tests/test_export_deep_markdown.py`,
`tests/test_export_xlsx.py`, `tests/test_export_deep_xlsx.py`,
`tests/test_crosscheck.py`, `tests/test_crosscheck_deep.py`, `tests/test_agent_exports.py`.

**Interfaces:**
- Consumes SourceBinding 2.0, BindingDecision/context refs и encoded snapshot.
- `source_context_display(decision: SourceContextDecision, obligation_id: str)
  -> dict[str, object]` в binding_export: state, context_id, own quote,
  effective interface/constraints и полный ordered список coords/spans/quotes.
  Использовать один display payload в Markdown/XLSX и их verifier.
- Существующий `validate_binding_run(run)` дополнительно сверяет snapshot,
  decision и supported-aspect refs; crosscheck сравнивает canonical display
  payload/decisions, не только количества или статусы.
  Для encoded metadata использует `inspect_source_context_record`; проверка
  целостности отчёта не заявляет актуальное доверие signer. Active CLI/MCP
  проверяют текущий trust через полный decode до mapping/finalize.

- [x] `test_five_artifacts_preserve_context_and_local_spans`: JSON включает
  normalized draft/reviewed map, original/effective obligations; Markdown/XLSX
  показывают условия рядом с атомом и точные origin coords/quotes; parent offsets
  никогда не попадают в локальные source_spans/uncovered. Supported aspect
  сохраняет собственную буквальную цитату и явные applied context refs.
- [x] `test_export_tamper_with_same_status_fails_crosscheck`: удалить/подменить
  condition/ref/map digest в любом доказательном представлении → CHANGED либо
  соответствующая crosscheck error. `test_long_and_formula_like_context_quotes`:
  длинные/emoji/`=...` quotes сохраняются полностью, XLSX без formulas; превышение
  лимита MCP ответа даёт явную size error, не усечённый context.
- [x] RED: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_exports.py`.
  Ожидаются отсутствующие context columns/условия и неполный crosscheck.
- [x] Реализовать display/render/verify и поля run.jsonl/manifest: source/map/
  resolver digests, contract, trust metadata, diagnostic codes. В result.json
  остаётся весь frozen snapshot; в XLSX сохраняются 5/7 листов. Использовать
  существующее безопасное дробление длинных ячеек и plain-text запись.
- [x] GREEN: автоматические проверки прошли (134 tests), включая exact quotes.
  Ручное открытие синтетического XLSX/Markdown пропущено по прямому решению
  пользователя 08.10.2026 («пропускаем ручную проверку»); это не результат PASS.
- [x] Commit: `feat: expose and crosscheck source context in every report`.

### Task 7: Приёмка, установленный пакет и отдельный живой eval

**Files:** Create `tests/test_source_context_acceptance.py`,
`tests/fixtures/source_context/live_eval.json`,
`docs/acceptance/2026-10-08-source-context-runtime.md`;
modify `README.md`, `RUNBOOK.md`, `OUTPUT_SCHEMA.md`, `CLIENTS_CODEX_OPENCODE.md`,
`TROUBLESHOOTING.md`, `INSTALL_OFFLINE.md`, этот план и overarching plan.

**Interfaces:** Consumes публичные CLI и прежние девять MCP tools версии 3.0;
не добавляет новые runtime API. Acceptance fixture содержит неизменные тексты,
reviewed synthetic map, независимые expected codes/statuses/refs и hashes.

- [x] До запуска модели freeze live_eval с cases Task 1 и контрольными
  evidence/predicate IDs; tests `test_cli_mcp_context_acceptance_matrix` и
  `test_installed_context_contract` сверяют строки/атомы, exact codes/refs,
  пять artifact hashes, restart и `reqmap_finalize(allow_partial=false)` +
  `reqmap_get_result`. RED должен обнаружить реальный integration gap, если он
  остался; исправлять в ответственном модуле с regression, не правкой expectation.
- [x] GREEN: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_acceptance.py tests/test_distribution.py`.
  В `test_installed_context_contract` использовать tmp_path и subprocess
  `./install.sh <tmp_path>/venv` с текущим Python, `PIP_NO_INDEX=1`; вызвать
  установленный `reqmap --version`, `reqmap agent serve` через stdio и CLI
  analyze с локальным scripted transport существующих acceptance fixtures.
  Проверить версии 3.0/3.0 и результаты 1.2/2.2 с точными context refs.
- [x] Выполнить `PYTHONPATH=src .venv/bin/python -m pytest -q` и `git diff --check`.
  Ожидается полный GREEN; в отчёте
  записать команды, counts, code/resolver/input/map hashes и границы проверки.
- [x] Провести независимое ревью всей ветки согласно выбранному execution skill.
  Исправить подтверждённые замечания и повторить затронутые проверки;
  полный suite повторять после runtime fixes, не ради неизменённой документации.
- [ ] Провести отдельный eval настоящим IDE-клиентом по frozen synthetic input:
  зафиксировать версии клиента/модели, prompts, session/request IDs, ожидаемые и
  фактические atoms/statuses/codes/refs и hashes артефактов. Scripted backend
  не выдавать за этот запуск. Если клиент недоступен, явно оставить этот checkbox
  незавершённым и сообщить блокировку, не объявлять полный этап законченным.
- [x] Обновить русские инструкции: подготовка и review карты, отсутствие карты,
  отдельная настройка deep namespace сопровождающим, frozen session/restart,
  новые версии и historical outputs. Для реальных книг сначала нужна утверждённая
  карта конкретной выборки; отдельно считать reviewed/unreviewed, own unresolved
  и нехватку evidence. Приёмка synthetic не доказывает покрытие 1949 строк.
- [x] Commit: `docs: record source context acceptance and operator workflow`.
  Merge/push выполняются только в рамках отдельного распоряжения пользователя.

## Самопроверка плана и передача в работу

Проверены все разделы спецификации: snapshot/refs — Task 1; schema/trust — 2;
семантика и own/effective provenance — 3; frozen replay — 4; общий gate,
контракты/история — 5; диагностика/пять файлов — 6; приёмка — 7.
Пять Review Focus случаев имеют именованные проверки. Согласованы интерфейсы
между задачами, immutable input capture до grouping, отсутствие unsigned deep
fallback и единый момент включения новых версий. Самопроверка не заменяет
независимое ревью будущего кода; ни один checkbox исполнения сейчас не закрыт.
Проверки подготовки документа: `tests/test_docs_language.py` — 23 passed;
61 относительная ссылка в четырёх изменённых документах разрешается;
незаполненных маркеров и ошибок `git diff --check` нет. Runtime не менялся.

Рекомендуемый способ — Native: последовательное исполнение в этой сессии и
одно независимое ревью всей ветки после реализации. Задачи тесно связаны
типами, frozen payload и согласованным обновлением контрактов. Альтернатива —
Subagent-driven с отдельными implementer/reviewer на каждую задачу. До кода
пользователь проверяет этот план и выбирает способ исполнения.


## Исполнение 08.10.2026

Пользователь выбрал inline-исполнение («давай дальше»). Tasks1–5 реализованы и
проверены; Task6 закрыт в согласованном объёме, 134 automated export/crosscheck
tests проходят. Ручная проверка XLSX/Markdown пропущена по решению пользователя
08.10.2026 и больше не блокирует приёмку. Task7: frozen15-row eval,
installed/scripted приёмка и документация подготовлены; результаты полного suite
и независимого review фиксируются в [отчёте](../../acceptance/2026-10-08-source-context-runtime.md).
Живой IDE/model eval отдельно не выполнен, весь этап не объявляется завершённым.
Ни merge, ни push не выполняются. Первоначальные сведения о самопроверке выше
описывают подготовку плана, а не текущую приёмку реализации.

Независимое ревью нашло один Important: смена только грамматики могла заново
интерпретировать frozen payload до проверки совместимости. Исправление хранит
отдельную идентичность грамматики в snapshot/context_id и учитывает её в кешах,
MCP historical/active и CLI resume. Пять regression cases: RED 5 failed,
GREEN 5 passed; профильный набор 108 passed. Итоговый полный suite после fix:
1840 passed, 3 прежних fork warnings, 543.55 s; `git diff --check` прошёл.
Minor и отказов от оценки нет.
