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
Оно передаётся в canonical JSON schema `1.0` и evidence payload MCP.
Строгие потребители JSON должны разрешить это новое поле. В старой записи
KB без scope применяется `context`; такие записи не подтверждают поддержку.
Причина `EVIDENCE_CONTEXT_ONLY` присутствует в diagnostics и обосновании
пониженного mapping, включая Markdown и лист «Сопоставления» XLSX.
Deep schema `2.0` использует собственную модель evidence.

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

## Deep output schema 2.0

Описание пяти листов, mappings и runtime/designtime выше относится к legacy
schema `1.0`. Deep сохраняет те же пять файлов, но сериализует
`DeepRunResult` с явной `schema_version="2.0"`.

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

Агентный режим добавляет optional `metadata.analysis_origin` в схемы 1.0/2.0;
для отчёта агентного режима это поле обязательно. Существующий `analyze` без
этого поля сохраняет прежний формат. Строгим сторонним потребителям необходимо
разрешить новое поле перед чтением таких отчётов.

`analysis_origin` содержит `mode=external_agent`, UUID `session_id`, целую
`revision`, `tool_contract_version=1.0`, `workflow_version=1.0`, SHA-256
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
