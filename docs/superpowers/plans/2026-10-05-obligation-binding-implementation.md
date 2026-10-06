# План внедрения связи обязательства с доказательством

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking.

**Goal:** Запретить подтверждение требования, если доказательство не покрывает
действие, объект и все условия неизменённого исходного обязательства.

**Architecture:** Общий source binding строится до декомпозиции; отдельный
проверенный каталог связывает его с claims legacy/deep. Финальный статус
проходит прежний evidence validator и новый binding validator; CLI/MCP,
replay и экспорт используют один результат.

**Tech Stack:** Python 3.11+, dataclasses/enums, pytest, текущие stdio MCP,
JSON/JSONL и подпись SSH для deep binding catalog.

**Spec:** [Проект контракта](../specs/2026-10-05-obligation-binding-design.md).

**Статус:** проект плана для согласования; ни одна задача внедрения не выполнена.
База исследования — `c35cc65`; результаты конечной грамматики не являются
доказательством готовности production validator.

## Global Constraints

- Python 3.11+, без новых runtime dependencies и сетевых вызовов.
- OpenStack Epoxy 2025.1; никакие OpenStack/Kolla/host actions не исполняются.
- Исходные XLSX не меняются, не публикуются и не превращаются в fixtures.
- Context evidence и source hints не подтверждают обязательство.
- Сначала полная исходная строка, затем атомы; model IDs и labels неавторитетны.
- Неизвестные части означают `insufficient_evidence`, не `not_supported`.
- Prototype в `tools/research/` не импортируется runtime.
- Отсутствующий каталог не включает прежнее небезопасное подтверждение.
- Реальные claims, новый импорт XLSX и живая IDE-приёмка выполняются отдельно.

## Review Focus

1. Повторяющаяся цитата, emoji/скрытый символ и разные системы Unicode offsets:
   сохранить каждое вхождение и весь исходный текст — Task 1.
2. Агент удаляет условие, меняет обязательность или делит отрицание с действием:
   невозможно получить более сильный вывод — Tasks 1, 3.
3. Два правдивых claims относятся к разным операциям/условиям: нельзя получить
   новую составную гарантию их объединением — Tasks 2, 3.
4. Новый каталог/правило после restart и старый finalized result: запретить
   незаметный пересчёт под новым смыслом, сохранить исторический результат — Task 4.
5. Вложенные conditions, неизвестная единица и доказательство иной версии:
   сохранить gaps и не создавать ложное отрицание — Tasks 1, 3, 5.

## Структура файлов и зависимости

Новые `binding_models.py`, `binding_source.py`, `binding_catalog.py`,
`binding_engine.py` отвечают соответственно за immutable контракты, полный
разбор источника, проверенную разметку KB и решение о покрытии обязательства.
При необходимости код подписи каталога размещается в `binding_trust.py`,
не смешиваясь с предметным matcher. Существующие mapping/pipeline/agent/export
модули вызывают эти границы; посторонний refactoring не включён.

Последовательность: Task 1 → Task 2 → Task 3 → Task 4 → Task 5.
Task 2 потребляет типы Task 1; Task 3 — source binding и каталог; Task 4 —
версию и решение validator; Task 5 — сохранённые immutable records.

### Task 1: Полный источник и канонические обязательства

**Files:** Create `src/reqmap/binding_models.py`, `src/reqmap/binding_source.py`,
`tests/test_binding_source.py`; modify `src/reqmap/models.py`,
`src/reqmap/decomposition.py`, `tests/test_decomposition.py`.

**Interfaces:**
- Produces immutable `SourceSpan(start: int, end: int, quote: str)`,
  `BoundObligation`, `SourceBinding`, `BindingDiagnostic`, `BindingDecision`,
  `EvidencePredicate`, `BindingCatalog`, `BindingContext` с полями спецификации.
- `bind_source(requirement: Requirement) -> SourceBinding` вычисляет все
  обязательства либо сохраняет неизвестные fragments; не обращается к модели.
- `validate_atom_selection(binding: SourceBinding, proposal: object) -> tuple[AtomicClaim, ...]`
  выбирает canonical atoms; `AtomicClaim` сохраняет obligation ID и source spans.
- `BindingContext` объединяет `source_binding`, optional `catalog`,
  `engine_version`; каталог не влияет на разбор исходного текста.

- [ ] Добавить failing tests: `test_omitted_tail_cannot_form_complete_atoms`
  проверяет отказ `SOURCE_COVERAGE_GAP` для цитаты без `за одну миллисекунду`;
  `test_model_cannot_set_mandatory_false` отклоняет `false`;
  `test_partition_round_trip` проверяет точное восстановление строки.
- [ ] Добавить cases `test_repeated_quote_has_two_occurrences`,
  `test_offsets_are_unicode_codepoints`, `test_negation_cannot_be_detached`,
  `test_unknown_suffix_remains_unresolved`, `test_unknown_unit_is_not_discarded`.
  Для неизвестного текста не появляется подтверждаемый атом без gap.
  `test_unknown_source_creates_mandatory_placeholder` проверяет полный span,
  `parse_state=unresolved`, `mandatory=True` и completed/insufficient без
  дополнительного вызова модели для выдумывания интерпретации.
- [ ] Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_binding_source.py tests/test_decomposition.py`.
  Expected RED: исходная потеря условия воспроизводится; новые контракты отсутствуют.
- [ ] Реализовать immutable типы и полные правила из спецификации. Отдельный
  rule ID на каждый разрешённый синтаксис, максимум один однозначный результат.
  Конечную грамматику реализовать без произвольных regex из KB. В первом
  внедрении неизвестная составная фраза сохраняется целиком как unresolved.
- [ ] Повторить команду. Expected GREEN; исходные source-quote invariants
  сохранены, backward-поля не позволяют обходить новую selection-проверку.
- [ ] Commit: `feat: preserve complete source obligations before mapping`.

### Task 2: Каталог проверенных предикатов и адаптеры KB

**Files:** Create `src/reqmap/binding_catalog.py`, `src/reqmap/binding_trust.py`,
`tests/test_binding_catalog.py`, synthetic `tests/fixtures/binding_catalog/`;
modify `src/reqmap/config.py`, `src/reqmap/config_common.py`,
`src/reqmap/agent_config.py`, `src/reqmap/agent_knowledge.py`,
`tests/test_agent_config.py`.

**Interfaces:**
- Consumes типы Task 1 и существующие `KnowledgeBase`/`KnowledgeBaseV2`.
- Produces `load_binding_catalog(path: Path, knowledge: KnowledgeBase | KnowledgeBaseV2,
  allowed_signers_path: Path | None) -> BindingCatalog`.
- Каталог содержит `schema_version=1`, `catalog_id`, `knowledge_sha256`,
  `release_scope`, `predicates`, `catalog_sha256` и проверенные trust metadata.
- Config хранит optional `binding_catalog_path`; отсутствие → `None`,
  повреждение/иная база → конкретная preflight error.

- [ ] Добавить tests `test_unbound_catalog_is_rejected`,
  `test_context_cannot_back_predicate`, `test_deep_relation_must_match_exactly`,
  `test_unreviewed_predicate_is_not_admissible`,
  `test_forged_or_wrong_namespace_signature_is_rejected`,
  `test_missing_catalog_never_enables_legacy_support`.
  Отдельно проверить path traversal, symlink, duplicate JSON keys и неизвестные
  enum/fields по существующим strict-loader conventions.
- [ ] Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_binding_catalog.py tests/test_agent_config.py`.
  Expected RED: новый loader/config отсутствуют.
- [ ] Реализовать точную schema и атомарное чтение manifest/files по digest;
  legacy provenance не усиливается подписью по умолчанию, deep требует подпись
  утверждённого signer в namespace `reqmap-obligation-binding`. Проверить
  owner/capability/action/effect/target/source/version refs через адаптеры.
  Synthetic catalog не копировать в поставляемую KB.
- [ ] Повторить команду. Expected GREEN; validators старых KB не изменяют
  claims и не классифицируют свободный текст автоматически.
- [ ] Commit: `feat: load version-bound reviewed evidence predicates`.

### Task 3: Общий validator покрытия и статусы

**Files:** Create `src/reqmap/binding_engine.py`, `tests/test_binding_engine.py`,
`tests/test_binding_mapping.py`; modify `src/reqmap/mapping.py`,
`src/reqmap/deep_mapping.py`, `src/reqmap/prompts.py` и synthetic mapping fixtures.

**Interfaces:**
- Consumes `BoundObligation`, `BindingCatalog`, прежнее решение validator.
- Produces `check_binding(obligation: BoundObligation,
  predicate_ids: tuple[str, ...], catalog: BindingCatalog | None,
  prior_status: SupportStatus, cited_evidence_ids: tuple[str, ...]) -> BindingDecision`.
- `accept_mapping` и `accept_deep_mapping` получают keyword-only
  `binding_context: BindingContext | None = None`; отсутствие контекста
  всегда блокирует доказательный статус, в том числе прямые Python вызовы.
- Положительное решение возможно только для predicate refs, чьи evidence
  входят в выбранное и ранее проверенное доказательство. Поля результата
  агент не создаёт; backend сохраняет decision и строит supported aspects.

- [ ] Добавить контрастные RED-тесты из probe для обоих acceptors:
  `test_specific_create_does_not_prove_other_obligation` — действие, объект,
  отрицание, latency, GUI, failure, GPU; statuses не `supported`/`partial`.
  Положительные формы `создавать`, `обеспечивать создание`, `ВМ` дают supported
  только с явным synthetic catalog и целым source binding.
- [ ] Добавить `test_no_union_of_create_api_and_delete_gui`,
  `test_conditional_fact_cannot_prove_unconditional_claim`,
  `test_foreign_version_is_not_negative_proof`,
  `test_context_diagnostic_is_independent_of_proposed_status`,
  `test_supported_aspects_are_computed_from_obligations`,
  `test_claimed_not_applicable_cannot_hide_obligation`.
- [ ] Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_binding_engine.py tests/test_binding_mapping.py tests/test_mapping.py tests/test_deep_mapping.py tests/test_evidence_scope.py`.
  Expected RED: исходные specific обходы получают supported.
- [ ] Реализовать exact predicate comparison и diagnostics из спецификации.
  Один predicate покрывает всё обязательство; разные predicates не объединяют
  условия. Прежний validator не может быть обойдён или повышен новым слоем.
  Гaps дают insufficient; применимое отрицательное доказательство проверяется
  для полного предиката. Context-only диагностика не зависит от proposal status.
- [ ] Обновить прежние положительные synthetic fixtures явными binding inputs;
  не менять реальные общие claims и не ослаблять ожидаемые статусы production KB.
  Повторить команду. Expected GREEN.
- [ ] Commit: `fix: require complete source-to-evidence binding in both profiles`.

### Task 4: CLI/MCP, подписи контекста и старые сессии

**Files:** Modify `src/reqmap/pipeline.py`, `src/reqmap/deep_pipeline.py`,
`src/reqmap/agent_context.py`, `src/reqmap/agent_types.py`,
`src/reqmap/agent_replay.py`, `src/reqmap/agent_store.py`,
`src/reqmap/agent_finalize.py`, `src/reqmap/prompts.py`;
create `tests/test_binding_sessions.py`.

**Interfaces:**
- Consumes `BindingContext` Task 1, loader Task 2, validator Task 3.
- Produces `proposal_schema_version=2`, binding-aware context IDs/checkpoints
  и явный `SESSION_CONTRACT_MISMATCH` для несовместимого активного состояния.
- Frozen session settings включают binding engine/grammar/catalog versions и
  hashes. Старый finalized artifact читается по прежней schema read-only;
  он не проходит через новый replay и не получает новую semantic validation.

- [ ] Добавить tests `test_cli_and_mcp_preserve_uncovered_condition`,
  `test_restart_cannot_restore_pre_binding_support`,
  `test_rule_or_catalog_change_invalidates_context`,
  `test_old_active_session_requires_new_analysis`,
  `test_old_finalized_artifacts_remain_historical`,
  `test_repeated_source_id_does_not_share_binding`.
  Для исторического чтения проверить прежние hashes и запрет записи нового
  результата поверх старого; для активного — конкретный код, не SESSION_CORRUPT.
- [ ] Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_binding_sessions.py tests/test_agent_store.py tests/test_agent_finalize.py`.
  Expected RED: context signature не учитывает новый контракт.
- [ ] Провести общий backend через обе pipeline и все submit/replay/finalize
  paths. Подписи включают source/KB/catalog/grammar digests, engine/proposal/result
  versions. Для отсутствующих старых fields нельзя подставлять новую версию.
  Обновить prompts и схемы payload без переименования девяти MCP tools.
- [ ] Повторить команду и `tests/test_pipeline.py`, `tests/test_deep_pipeline.py`.
  Expected GREEN; ни одна отдельная точка входа не сохраняет старый обход.
- [ ] Commit: `feat: version binding-aware CLI and MCP sessions`.

### Task 5: Единые отчёты, миграция и завершение

**Files:** Modify `src/reqmap/export_json.py`, `export_deep_json.py`,
`export_xlsx.py`, `export_deep_xlsx.py`, `export_markdown.py`,
`export_deep_markdown.py`, `crosscheck.py`, `crosscheck_deep.py` (всё в
`src/reqmap/`), `OUTPUT_SCHEMA.md`, `KNOWLEDGE_BASE.md`, `RUNBOOK.md`, `README.md`;
create `tests/test_binding_exports.py`, `docs/acceptance/2026-10-05-obligation-binding-runtime.md`.

**Interfaces:**
- Consumes canonical `SourceBinding`/`BindingDecision` и settings Task 4.
- Produces result schemas `1.1`/`2.1`, пять файлов, прежние пять/семь листов XLSX,
  readable source gaps и диагностические коды с refs, hashes в manifest.

- [ ] Добавить `test_all_exports_preserve_exact_gap_and_predicate_refs`
  для обоих профилей и обеих стадий: после submit и после restart/finalize.
  JSON/XLSX/Markdown должны содержать одинаковые source spans, reasons/statuses;
  journal/manifest — те же версии и hashes. Изменение одного gap в артефакте
  обязано приводить к crosscheck failure.
- [ ] Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_binding_exports.py tests/test_crosscheck.py tests/test_crosscheck_deep.py`.
  Expected RED: schema не содержит binding decisions.
- [ ] Реализовать schema/export изменения; ни один exporter не запускает модель
  или новый mapping. Документировать миграцию, рост insufficient для старых KB,
  необходимость нового анализа и границу исторических результатов.
- [ ] Повторить targeted command, затем `PYTHONPATH=src .venv/bin/python -m pytest -q`.
  Expected GREEN без ослабления старых integrity/security/export tests.
- [ ] Выполнить offline install, installed `knowledge validate`, `pip check`
  и installed MCP submit/restart/finalize/get_result с проверкой пяти hashes.
  Отдельно показать положительный synthetic catalog control и контрастные
  случаи; не выдавать synthetic catalog за поставляемую предметную базу.
- [ ] Выполнить одно независимое ревью всей ветки по executing-plans. Исправить
  блокирующие замечания через RED/GREEN; не объявлять этапы corpus/IDE выполненными.
- [ ] Commit: `feat: export and verify complete obligation binding`.

## Порядок включения

После согласования контракта выполнить задачи последовательно в maintenance
ветке. Все runtime entry points и exports должны перейти вместе: промежуточные
коммиты не публикуются как готовая новая версия. Новая версия без binding
catalog законно возвращает insufficient; готовность окружения не означает
предметную полноту базы. Перед публикацией зафиксировать code/catalog/KB hashes
и ограничения. Установить проверенный пакет, создать новую сессию, проверить
remote SHA. Первую реальную поставку predicates проводить отдельным пакетом
этапа 4 после проверки официальных источников.
