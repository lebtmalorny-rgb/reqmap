# Схема результатов reqmap

Каноническим результатом является `result.json`. XLSX и Markdown строятся из того же immutable `RunResult` и проходят crosscheck; они не являются независимыми источниками предметной истины.

## JSON верхнего уровня

- `run_id` — детерминированный идентификатор запуска;
- `schema_version` — версия схемы результата;
- `run_status` — операционный `SUCCESS`, `PARTIAL` или `FAILED`;
- `requirements` — исходные строки и результаты их обработки в исходном порядке;
- `groups` — агрегаты, которые сохраняют `source_requirement_ids` и не заменяют построчные записи;
- `evidence` — только evidence, фактически процитированный mappings;
- `metadata` — input/knowledge hashes, модель, безопасный endpoint origin, prompts, seed, `top_k` и resume signature;
- `diagnostics` — дедуплицированные диагностики запуска.

Внутри requirement сохраняются `requirement_id`, исходный `source_id`, полный `text`, `ordinal`, coordinate файла/листа/строки, `parent_id`, `group_ids`, исходные поля и `source_hints`. Результат добавляет `analysis_state`, nullable `support_status`, `atom_results`, агрегированные `mappings` и diagnostics.

Atomic claim содержит `atom_id`, `requirement_id`, буквальную `source_quote`, флаг `mandatory` и ordinal. Mapping содержит `mapping_id`, `atom_id`, `component_id`, русскую роль, relation, phase, implementation source, mechanism, ordered steps, `evidence_ids`, status и обоснование.

## Машинные enums

`analysis_state`: `completed`, `model_failed`, `validation_failed`, `skipped`.

`support_status`: `supported`, `partial`, `not_supported`, `insufficient_evidence`, `not_applicable`. У незавершённой обработки support status равен `null`.

`phase`: `runtime`, `designtime`. Runtime описывает действие через OpenStack API. Design-time содержит отдельный шаг с механизмом `kolla-ansible reconfigure`.

`relation`: `implements`, `configures`, `prerequisite`, `integrates`, `host_os_change`.

`implementation_source`: `upstream`, `kolla_ansible`, `product_extension`, `external_component`.

Evidence использует polarity `positive` или `negative` и strength `direct`, `indirect` или `none`. Технические коды не переводятся; XLSX рядом показывает русское значение.

Legacy Evidence дополнен полем `claim_scope`: `context` или `specific`.
Оно передаётся в canonical JSON schema `1.2` и evidence payload MCP.
Строгие потребители JSON должны разрешить это новое поле. В старой записи
KB без scope применяется `context`; такие записи не подтверждают поддержку.
Причина `EVIDENCE_CONTEXT_ONLY` присутствует в diagnostics и обосновании
пониженного mapping, включая Markdown и лист «Сопоставления» XLSX.
Deep schema `2.2` использует собственную модель evidence.

## Агрегация support status

Приоритет обязателен и воспроизводится из атомов:

1. `not_supported`, если хотя бы один обязательный атом доказанно не поддерживается;
2. отсутствие итогового status, если обязательный атом не `completed` и нет подтверждённого `not_supported`;
3. `insufficient_evidence`, если evidence недостаточно и более сильных причин нет;
4. `partial`, если подтверждена только часть обязательного содержания;
5. `not_applicable`, если все атомы неприменимы;
6. `supported`, если все обязательные применимые атомы поддерживаются, а остальные неприменимы.

Группа применяет тот же приоритет, объединяет только подтверждённые components/mapping IDs и перечисляет все исходные requirement IDs. Дубли требований остаются отдельными строками.

## XLSX

`result.xlsx` содержит ровно пять листов:

1. `Требования` — исходные строки, координаты, parent/group links, состояния, status и компоненты;
2. `Атомарные утверждения` — атомы, source quotes, обязательность и подтверждённые/неподтверждённые аспекты;
3. `Сопоставления` — many-to-many связи, фаза, источник реализации, steps, evidence и reason;
4. `Доказательства` — evidence metadata, URL, локальный путь, locator, версия и SHA-256;
5. `Запуск` — параметры и статистика текущего `RunResult`.

Строковые значения, начинающиеся с `=`, `+`, `-` или `@`, записываются как literal, а не как Excel formula. Exporter проверяет ZIP/OOXML, точные headers/rows/counts, отсутствие formulas и независимое чтение OpenPyXL. ZIP metadata и core modified timestamp нормализованы, поэтому одинаковый `RunResult` даёт одинаковый SHA-256.

## Markdown, журнал и manifest

`report.md` содержит статус, counts, распределение компонентов и фаз, host OS/Kolla-Ansible случаи, неподтверждённые требования и processing failures. Скрытый machine marker counts используется crosscheck и не заменяет читаемый отчёт.

`run.jsonl` — журнал одного запуска с полями `event`, `level`, `message_ru` и allowlisted context. API key redacted рекурсивно. `manifest.json` не хеширует сам себя; он содержит schema/run identifiers, versions, input/knowledge hashes, model, endpoint origin без path/query, seed, prompt/retry metadata и hashes четырёх остальных опубликованных артефактов.

## Cross-artifact правила

Crosscheck повторно строит expected JSON/Markdown/XLSX из canonical объекта и сравнивает bytes, counts, IDs, statuses и `run_status`. Любая формула, symlink, повреждённый ZIP, неизвестный sheet, изменённая строка или несовпадающий hash даёт exit code 5. Такой набор нельзя частично публиковать как успешный.

Проверяемые действия оператора приведены в [RUNBOOK.md](RUNBOOK.md), а предметные правила evidence — в [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md).

## Deep output schema 2.2

Описание пяти листов, mappings и runtime/designtime выше относится к legacy
schema `1.2`. Deep сохраняет те же пять файлов, но сериализует
`DeepRunResult` с явной `schema_version="2.2"`.

Верхний уровень добавляет `responsibility_records` и `procedure_graphs`.
У требования вместо `mappings` находятся `responsibility_ids` и
`procedure_graph_ids`; у атома — `responsibility_ids`. Каждая запись
ответственности содержит `record_id`, `requirement_id`, `atomic_claim_id`,
`contour`, component/executor/target/action/effect refs, `lifecycle_phase`,
`version_scope`, `evidence_ids`, `support_status`, `related_record_ids`,
`procedure_step_ids` и `diagnostics`.

Контуры: `openstack_runtime`, `kolla_ansible`, `host_os`. Фазы: `preflight`,
`deploy`, `runtime`, `reconfigure`, `upgrade`, `migrate`, `recover`, `verify`,
`rollback`. Scope хранит source/target release, Kolla release, host profile и
version constraint. Actions/effects в результате представлены ссылками на
нормализованные записи подписанной базы, а не отдельными top-level массивами.

Граф содержит `graph_id`, `requirement_id`, `template_id`, `steps` и diagnostics.
Шаг содержит phase/contour/executor/target/action, preconditions,
success criteria, evidence IDs, `depends_on` и `rollback_step_id`.
Циклы, неизвестные ссылки и шаги без доказательств не допускаются;
неподтверждённый откат выражается `rollback_unverified`.

Deep XLSX содержит ровно семь листов:
`Требования`, `Атомарные утверждения`, `Ответственность`, `Процедуры`,
`Доказательства`, `Диагностика`, `Запуск`. `crosscheck_deep` независимо проверяет
структуру и содержимое JSON/XLSX/Markdown, включая IDs, contours, version scopes,
связи, diagnostics и counts. Отчёты строятся из того же канонического объекта,
который записан в JSON; повторный model mapping при экспорте не выполняется.

Deep manifest содержит `analysis_profile`, `snapshot_id`, `manifest_sha256`,
`key_id`, `signer_identity`, `release_profile`, модель, input hash, prompts,
seed, `top_k`, retry counts и hashes четырёх артефактов. Полные пути к snapshot,
trust, URL и секреты в deep metadata не публикуются. `run.jsonl` проверяется
как тот же файл с неизменённым уже записанным содержимым перед вычислением hash.

`SUCCESS` означает завершённый анализ без операционных gaps, а не поддержку
всех требований. `PARTIAL` также возможен при всех `analysis_state=completed`,
если обязательный атом имеет `insufficient_evidence`, есть конфликт evidence,
неоднозначность ответственности, пробел процедуры
или неподтверждённый откат. Запись `insufficient_evidence` без evidence может
сохранять кандидатные action/effect refs только с явным
`procedure_gap:responsibility_evidence` и без ссылок на процедурные шаги.
При конфликте применимых прямых evidence сохраняются обе позиции, даже если
модель процитировала только одну из них.

## Происхождение анализа в режиме агента

Агентный режим добавляет optional `metadata.analysis_origin` в текущие схемы 1.2/2.2;
для отчёта агентного режима это поле обязательно. `analyze` не добавляет `analysis_origin`, но использует те же новые binding-поля. Строгим сторонним потребителям необходимо
разрешить новое поле перед чтением таких отчётов.

`analysis_origin` содержит `mode=external_agent`, UUID `session_id`, целую
`revision`, `tool_contract_version=3.0`, `workflow_version=3.0`, SHA-256
`proposal_journal_sha256`, `reported_client` (`codex`, `opencode`, `unknown`) и
`identity_verified=false`. `reported_model` optional: это безопасная строка,
заявленная клиентом, а не проверенное backend имя модели. Она присутствует в
общих метаданных только при одном явно указанном значении во всех принятых
предложениях; отдельные заявления сохраняются в журнале.

В этом режиме `model=external-agent`, `seed=null`, endpoint отсутствует. Ни
Codex, ни OpenCode не являются именем языковой модели. Backend не управляет её
генерацией. Hash журнала покрывает предложения атомов и сопоставлений, включая
отклонённые; чтение сессии и финализация его не меняют. Полная история чата и
скрытые рассуждения не запрашиваются.

Те же сведения присутствуют в manifest, metadata-строке XLSX и блоке
происхождения Markdown. Crosscheck проверяет их согласованность. Изменение
поля в одном из отчётов делает комплект несогласованным.


## Source binding: результат 1.2 / 2.2

Новые запуски всегда сохраняют `source_binding` в результате каждой строки,
включая незавершённые строки. JSON 1.0/1.1/2.0/2.1 из старых finalized-сессий доступен
только как исторический комплект, без пересчёта или перезаписи.

| Объект | Поля и смысл |
| --- | --- |
| `SourceSpan` | `start`, `end`, `quote`: полуинтервал `[start,end)` в Unicode code points, не байты и не UTF-16; `text[start:end] == quote` |
| `SourceBinding` | `requirement_id`, `coordinate`, полный `source_text`, SHA-256 UTF-8 `source_sha256`, упорядоченные `fragments`, `obligations`, `unresolved_fragments`, `grammar_version`, `grammar_sha256`, `contract_version=2.0`, `context_decision` |
| Fragment | `span`, `kind` (`obligation`, `syntax`, `unresolved`), nullable `rule_id`; последовательность восстанавливает всю строку |
| Obligation | `obligation_id`, `requirement_id`, `source_spans`, точная `source_quote`, `parse_state` (`bound`, `unresolved`, `ambiguous`), nullable `rule_id`, `mandatory`, `actor`, `action`, `object`, `direction`, `interface`, `contour`, `lifecycle_phase`, `release_scope`, `constraints` |
| Constraint | `name`, `operator`, `value`, nullable `unit`, `source_spans`; исходное условие сохраняется даже при отсутствии доказательства |
| Atomic claim | К прежним полям добавлены `obligation_id`, `source_spans`, `source_sha256`; backend определяет текст, порядок и `mandatory=true` |
| `BindingDecision` | В `atom_results[].binding_decision`: `obligation_id`, `source_sha256`, `support_status`, `predicate_ids`, `evidence_ids`, `diagnostics`, `uncovered`, nullable `catalog_sha256`, `engine_version` |
| Binding diagnostic | `code`, `message_ru`, `requirement_id`, `obligation_id`, `field`, `source_spans`, `evidence_ids` |

`binding_decision` обязателен для завершённого атома, а у незавершённого равен
`null`. `uncovered` сохраняет полную цитату непокрытого обязательства. При
доказанном положительном или отрицательном результате он пуст. Backend строит
`supported_aspects` из исходных обязательств; свободный текст модели не
создаёт подтверждённые аспекты. Прежний evidence validator остаётся верхней
границей статуса. Один предикат должен покрыть всё обязательство; объединение
частичных предикатов в новую гарантию запрещено. `not_applicable` не принимается
как доказанный результат без отдельного правила применимости.

Примеры кодов: `SOURCE_COVERAGE_GAP`, `SOURCE_MANDATORY`, `SOURCE_UNPARSED`,
`SOURCE_AMBIGUOUS`, `BINDING_CATALOG_MISSING`, `EVIDENCE_CONTEXT_ONLY`.
Точные причины несовпадений и source spans находятся в `BindingDecision`.

Начиная с `binding_engine_version=1.1`, для legacy доступны
`MAPPING_ROLE_UNGROUNDED` и `MAPPING_STEP_UNGROUNDED`. Они означают, что текст
`role_ru` или `steps[i].action_ru` не прошёл существующую лексическую проверку
по выбранному official specific evidence/capability. `field` содержит путь
`mappings[i].role_ru` или `mappings[i].steps[j].action_ru`; `message_ru` перечисляет
слова вне корпуса. Это диагностика описания предложения, а не отрицательное
доказательство функции. Она заменяет общий `EVIDENCE_MISSING` по `prior_status`,
когда выявлен такой дефект текста; остальные ошибки исходного обязательства
сохраняются. JSON, XLSX и Markdown содержат эту диагностику. Схемы результатов
теперь 1.2/2.2; журнал содержит события и версии контракта.

`metadata.binding_contract`, manifest и событие `analysis_finished` в журнале
содержат одинаковые `binding_engine_version`, `grammar_version`,
`grammar_sha256`, `binding_catalog_sha256`, `binding_catalog_schema_version`,
`proposal_schema_version=2`, `result_schema_version`. Два catalog-поля равны
`null`, если каталог не настроен. Hash грамматики покрывает исполняемый файл
правил, catalog hash — байты проверенного `binding-manifest.json`.

Состав XLSX сохраняется: пять листов legacy, семь deep. В конце листа
`Требования` добавлен `Source binding`; в конце `Атомарные утверждения` —
`Obligation ID`, `Source spans`, `Source SHA-256`, `Binding decision`.
В `Сопоставления` / `Ответственность` добавлены `Obligation ID`,
`Predicate IDs (atom)`, `Uncovered spans (atom)`: последние два поля относятся
ко всему атому. Структуры записаны canonical JSON, без обрезания. Лист `Запуск` включает
`binding_contract`. Значение, превышающее 32 767 UTF-16 code units, заменяется
в ячейке JSON-ссылкой `{xlsx_text_key, parts, sha256}`. Его точный текст разделён
на фрагменты до 16 000 Unicode code points на листе `Запуск`; ключи строк —
`xlsx-text:<номер листа>:<номер строки>:<номер столбца>:<номер фрагмента>`.
Нумерация начинается с 1, строка учитывает заголовок. Конкатенация фрагментов
по порядку восстанавливает исходную ячейку; `sha256` вычислен от полного UTF-8.
Crosscheck сравнивает canonical ссылку, hash, все фрагменты и число строк.
Длинные JSON Source binding/Binding decision/Source spans и обычный текст
используют один механизм; число листов и строк предметных сущностей неизменно.
Лимит ответа MCP остаётся отдельным явным ограничением и не обрезает текст.

Markdown содержит полный `SourceBinding` и решения атомов в JSON-блоках.
Crosscheck сравнивает эти данные с canonical result; изменение одного gap
делает комплект несогласованным, даже если числа строк и статусы прежние.
Экспорт не обращается к модели и не выбирает новые доказательства.


## Контекст источника в результатах 1.2 / 2.2

`metadata.source_context` хранит полный frozen snapshot: exact input/map/signature
bytes в base64, полный нормализованный профиль, строки, normalized map и decisions.
`SourceBinding.context_decision` содержит state, target, map/entry/resolver hashes,
context_id, applied_links и effective_obligations. `obligations` остаются собственным
разбором исходной строки. `BindingDecision.context_id`, `uncovered_context_refs` и
`BindingDiagnostic.source_refs` связывают результат с точными внешними цитатами.
Supported aspect сохраняет локальную буквальную цитату; применённые условия явно
представлены ссылками в context_decision и display payload атома.

Версии: binding engine/SourceBinding 2.0; MCP tool/workflow 3.0; seed_version=2;
source context map/resolver 1.0; prompts 2.1/1.4/2.2. Proposal schema остаётся 2,
SQLite STATE_VERSION остаётся 1, грамматика остаётся 1.2. Resolver SHA-256 строится
по отсортированному manifest шести файлов: source_context.py, source_context_models.py,
source_context_codec.py, binding_models.py, binding_engine.py, binding_runtime.py.

В XLSX сохранены 5/7 листов. Колонка `Source context` у атома показывает state,
context_id, own_quote, effective_interface/constraints и все origin refs.
Длинные значения разбиваются на проверяемые части листа «Запуск», без усечения;
formula-like строки записываются как текст. Тот же display payload присутствует
в Markdown. Manifest и JSONL содержат source/map/resolver digests,
`trust_at_capture` и diagnostic_codes. Crosscheck сравнивает полные значения,
включая контекст и части длинных ячеек, а не только итоговые статусы.

Проверка экспорта подтверждает целостность frozen record; актуальное доверие к
signer проверяется отдельно в активном CLI/MCP перед acceptance/finalize.
