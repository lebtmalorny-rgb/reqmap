# Глубокая evidence-модель `reqmap` и разделение контуров ответственности

Дата: 2026-08-25

Статус: архитектурные разделы утверждены пользователем; письменная спецификация ожидает итогового ревью

Язык пользовательской и эксплуатационной документации: русский

## 1. Назначение

Эта спецификация определяет первый этап развития `reqmap`: архитектурный фундамент для глубокого анализа требований с явным разделением трёх контуров ответственности:

- `openstack_runtime` — операции через OpenStack API/CLI над ресурсами работающего облака;
- `kolla_ansible` — deploy, configuration, `reconfigure` и `upgrade`;
- `host_os` — изменения Rocky Linux 9, включая kernel, networking, firewall, storage, packages, users, services и container runtime.

Главный результат этапа — не увеличение числа документов само по себе, а доказуемое различение:

1. кто отвечает за реализацию требования;
2. кто исполняет действие;
3. какой объект или контур изменяется;
4. какой version-pinned источник подтверждает вывод;
5. какие зависимые шаги нужны для выполнения, проверки и отката.

Одно исходное требование может затрагивать несколько контуров. Оно должно быть разложено на связанные атомарные записи без потери исходного текста и координат.

## 2. Связь с `reqmap` v1

Спецификация расширяет дизайн `docs/superpowers/specs/2026-08-20-reqmap-agent-design.md` и сохраняет его основные инварианты:

- CLI-first архитектуру;
- канонический JSON и производные XLSX/Markdown;
- many-to-many mapping на уровне атомарных утверждений;
- локальный evidence snapshot;
- отсутствие сетевого доступа во время штатного анализа;
- fail-closed обработку;
- разделение support status и analysis state;
- сохранение исходных требований без изменений.

Для knowledge schema v2 эта спецификация заменяет следующие ограничения v1:

- плоские runtime/design-time mappings заменяются нормализованными записями ответственности;
- неявная связь Kolla-Ansible и host OS заменяется явным разделением `executor` и `target`;
- создаётся frozen gold set для проверки классификации контуров и процедур;
- snapshot v2 получает проверяемую Ed25519-подпись, а не только агрегированный SHA-256;
- полнотекстовый корпус допускается как источник кандидатов, но не как непосредственное основание финального статуса.

Текущая knowledge schema v1 и существующие команды продолжают работать в legacy-режиме.

## 3. Утверждённый предметный охват

### 3.1. Базовый профиль

- upstream OpenStack Epoxy 2025.1;
- соответствующая ветка и документация Kolla-Ansible 2025.1;
- Rocky Linux 9 как единственный целевой host OS profile первого цикла наполнения;
- штатный runtime-анализ без доступа к Интернету.

### 3.2. Upgrade profile

Upgrade evidence может описывать только переход:

```text
OpenStack 2025.1 → upstream OpenStack 2026.1
```

Evidence 2025.1 нельзя автоматически применять к 2026.1. Запись upgrade обязана иметь раздельные `source_release` и `target_release`.

### 3.3. Последующие корпуса

Архитектура должна поддержать отдельное наполнение для:

- глубокого корпуса OpenStack 2025.1;
- Kolla-Ansible и Rocky Linux 9;
- HA-сценариев;
- миграций VM, volumes, networks, images, control-plane services и databases между узлами или кластерами;
- upgrade OpenStack 2025.1 → 2026.1.

Наполнение этих корпусов не входит в первый этап и выполняется отдельными спецификациями и планами.

## 4. Архитектурный подход

Используется нормализованная evidence-модель. Полнотекстовый или семантический поиск является вспомогательным механизмом discovery и не заменяет нормализованные доказательства.

```text
Исходное требование
    |
    +--> AtomicClaim
            |
            +--> ResponsibilityRecord [openstack_runtime]
            +--> ResponsibilityRecord [kolla_ansible]
            +--> ResponsibilityRecord [host_os]
                         |
                         +--> Action
                         +--> Effect
                         +--> Evidence
                         +--> ProcedureStep graph
```

У одного `AtomicClaim` может быть от нуля до нескольких `ResponsibilityRecord`. Отсутствие подтверждённой записи не удаляет атом: он получает явный gap или `insufficient_evidence`.

## 5. Каноническая модель результата

### 5.1. `AtomicClaim`

Существующая атомарная декомпозиция сохраняется. Каждый атом обязан содержать:

- стабильный `atomic_claim_id`;
- `requirement_id` и неизменённый `source_quote`;
- атомарный текст обязательства;
- признак обязательности;
- состояние обработки;
- ссылки на все связанные `responsibility_record_ids`.

LLM не вправе добавлять обязательство, отсутствующее в `source_quote`.

### 5.2. `ResponsibilityRecord`

Запись ответственности содержит:

```text
record_id
requirement_id
atomic_claim_id
contour
component_ref
executor_ref
target_contour
target_ref
action_ref
effect_ref
lifecycle_phase
version_scope
evidence_ids
support_status
related_record_ids
procedure_step_ids
diagnostics
```

Поле `contour` принимает ровно одно значение:

- `openstack_runtime`;
- `kolla_ansible`;
- `host_os`.

Значения наподобие `mixed`, `kolla+os` или `openstack_host` запрещены. Смешанное требование представляется несколькими связанными записями.

### 5.3. Исполнитель и объект изменения

`executor_ref` определяет, кто выполняет действие. `target_contour` и `target_ref` определяют, что изменяется. Эти оси нельзя объединять.

Если Kolla-Ansible изменяет `sysctl`, создаются минимум две записи:

```text
record A:
  contour = kolla_ansible
  executor_ref = kolla_ansible
  target_contour = host_os
  target_ref = rocky_linux_9.kernel_sysctl
  action_ref = apply_host_configuration

record B:
  contour = host_os
  executor_ref = kolla_ansible
  target_contour = host_os
  target_ref = rocky_linux_9.kernel_sysctl
  action_ref = change_kernel_parameter
```

Записи связываются через `related_record_ids`. Запись A показывает механизм автоматизации, запись B — фактический изменяемый контур.

### 5.4. `Action` и `Effect`

`Action` описывает проверяемую операцию, а `Effect` — ожидаемое изменение состояния. Они являются раздельными справочными сущностями knowledge schema v2.

`Action` содержит:

- стабильный ID и русское отображаемое имя;
- допустимый контур;
- тип интерфейса: OpenStack API, CLI, Ansible action, host command или file/config change;
- конкретную API operation, subcommand, role/task либо ограниченный шаблон действия;
- version constraints;
- обязательные evidence IDs.

`Effect` содержит:

- тип и идентификатор target;
- наблюдаемое состояние до и после действия;
- критерий проверки;
- возможную обратимость;
- evidence IDs.

### 5.5. Lifecycle phase

Допустимые фазы:

- `preflight`;
- `deploy`;
- `runtime`;
- `reconfigure`;
- `upgrade`;
- `migrate`;
- `recover`;
- `verify`;
- `rollback`.

Фаза не заменяет contour. Например, `upgrade` может содержать отдельные действия Kolla-Ansible и host OS.

### 5.6. Version scope

Каждая запись содержит:

```text
source_release
target_release
kolla_ansible_release
host_profile
version_constraint
```

Для обычного анализа `source_release` и `target_release` равны `2025.1`. Значение `2026.1` разрешено как `target_release` только для `lifecycle_phase=upgrade`.

## 6. Knowledge schema v2

### 6.1. Логические сущности

Snapshot v2 содержит:

- `ComponentRecord`;
- `ActorRecord`;
- `TargetRecord`;
- `CapabilityRecord`;
- `ActionRecord`;
- `EffectRecord`;
- `EvidenceRecord`;
- `ProcedureTemplateRecord`;
- `SourceArtifactRecord`;
- нормализованные synonyms и retrieval metadata.

`ResponsibilityRecord` и конкретные `ProcedureStep` создаются в результате анализа и ссылаются только на известные сущности snapshot.

### 6.2. Размещение

Legacy snapshot остаётся в:

```text
knowledge/epoxy-2025.1/
```

Первый v2 snapshot создаётся отдельно:

```text
knowledge/epoxy-2025.1-deep/
```

Минимальная структура v2:

```text
metadata.json
components.json
actors.json
targets.jsonl
capabilities.jsonl
actions.jsonl
effects.jsonl
evidence.jsonl
procedures.jsonl
synonyms.json
source-manifest.json
snapshot-manifest.json
snapshot-manifest.sig
corpus/
indexes/
```

Индексы считаются производными данными, но входят в manifest, если поставляются runtime.

### 6.3. Metadata

`metadata.json` содержит минимум:

- `knowledge_schema_version = 2`;
- `snapshot_id`;
- `snapshot_status`;
- базовый OpenStack release;
- допустимый upgrade target;
- Kolla-Ansible release;
- host OS profile;
- timestamp сборки;
- ID ключа подписи;
- версии schema всех JSON/JSONL records.

Runtime deep-profile принимает только `snapshot_status=approved`.

## 7. Источники и evidence

### 7.1. Два слоя данных

Snapshot разделяется на:

- `corpus/` — локальные копии официальной документации и version-pinned исходников;
- нормализованные records — ограниченные атомарные утверждения, допустимые для финального вывода.

Найденный corpus chunk является только кандидатом. Он не становится доказательством, пока не создан и не проверен `EvidenceRecord`.

### 7.2. Допустимые первичные источники

- официальные OpenStack API Reference, configuration, admin и install guides;
- официальные Kolla-Ansible documentation и release notes;
- исходники OpenStack и Kolla-Ansible на точном tag/commit;
- Kolla-Ansible roles, defaults, handlers и templates;
- policy/config schemas и migration scripts;
- официальная документация Rocky Linux 9;
- применимые исходники и документация upstream-компонентов Rocky Linux 9.

Тесты и примеры могут усиливать доказательство, но сами по себе не подтверждают production-возможность.

### 7.3. `SourceArtifactRecord`

Для каждого source artifact фиксируются:

```text
source_id
source_type
project
release
git_tag
git_commit
source_url
retrieved_at
local_path
content_sha256
provenance
```

Для документа обязательны точный URL и локатор раздела. Для исходного кода обязательны repository URL, commit SHA, local path и устойчивый locator. Номер строки может выводиться для удобства, но доказательство привязывается к hash содержимого, поскольку строки могут смещаться.

### 7.4. `EvidenceRecord`

Evidence содержит:

```text
evidence_id
claim
claim_kind
polarity
strength
source_id
locator
version_constraint
applicable_contours
supports_entity_refs
local_excerpt
review_state
```

Допустимые значения `polarity`:

- `positive`;
- `negative`.

Допустимые значения `strength`:

- `direct`;
- `indirect`;
- `none`.

`supported` требует применимого direct positive evidence. `not_supported` требует direct negative evidence либо доказанной version incompatibility. Отсутствие записи означает `insufficient_evidence`.

Internal workflow policy хранится отдельно от upstream facts и не может подтверждать возможность OpenStack, Kolla-Ansible или Rocky Linux.

### 7.5. Evidence для процедур

Каждый исполняемый, проверочный и rollback-шаг имеет собственные evidence IDs. Evidence общего описания компонента не подтверждает конкретную команду, API operation или порядок действий.

Если rollback не подтверждён, результат содержит `rollback_unverified`, а не автоматически сгенерированный шаг.

## 8. Доверие и offline-проверка snapshot

Online maintenance pipeline:

1. получает источники только из разрешённых upstream locations;
2. фиксирует release/tag/commit;
3. сохраняет локальные artifacts;
4. нормализует и проверяет records;
5. строит retrieval indexes;
6. создаёт канонический `snapshot-manifest.json`;
7. подписывает manifest отдельным Ed25519-ключом.

`snapshot-manifest.json` содержит SHA-256 и размер каждого авторитетного и поставляемого runtime-файла. Сам manifest и detached signature не включаются рекурсивно в список файлов.

Offline runtime получает путь к доверенному публичному ключу отдельно от snapshot. Публичный ключ не может становиться доверенным только потому, что он находится внутри проверяемого snapshot.

До чтения требований runtime проверяет:

- формат и schema version manifest;
- ID разрешённого ключа;
- Ed25519-подпись manifest;
- SHA-256 и размеры всех файлов;
- отсутствие незаявленных авторитетных records;
- внутренние ссылки и version constraints.

Любой отказ завершает preflight. Runtime не загружает отсутствующие файлы и не открывает `source_url`.

## 9. Pipeline анализа

```text
Input
→ parse и сохранение source coordinates
→ atomic decomposition
→ contour candidate classification
→ normalized retrieval
→ corpus discovery retrieval
→ evidence gate
→ ResponsibilityRecord creation
→ procedure graph composition
→ validation и cross-check
→ JSON/XLSX/Markdown/run.jsonl/manifest.json
```

### 9.1. Retrieval

Normalized retrieval использует детерминированный lexical/BM25-поиск, synonyms и metadata filters по release, contour, component и host profile.

Corpus retrieval может использовать локальный semantic index. Внешний embedding или vector service во время runtime запрещён. Все необходимые индексы и embeddings создаются online maintenance pipeline и входят в подписанный snapshot.

Corpus retrieval возвращает только source candidates. Ни corpus chunk, ни LLM output не могут напрямую выставить `supported`.

### 9.2. Evidence gate

Evidence gate проверяет:

- существование всех entity и evidence IDs;
- применимость release и host profile;
- polarity и strength;
- совпадение contour/action/target;
- достаточность evidence для support status;
- отсутствие запрещённого расширения claim относительно source locator.

### 9.3. Procedure graph

Для operational-сценария строится ориентированный ациклический граф:

```text
preflight → action → verify
                 ↘ rollback
```

Каждый `ProcedureStep` содержит:

- `procedure_step_id`;
- phase;
- contour;
- executor и target;
- command/API operation/action reference;
- preconditions;
- success criteria;
- evidence IDs;
- зависимости от других шагов;
- rollback trigger и rollback step reference.

Граф не обязан быть линейным, но циклы и ссылки на отсутствующие шаги запрещены.

### 9.4. Обязательные cross-contour инварианты

Валидатор запрещает:

- записывать host OS operation как OpenStack API operation;
- считать изменение Kolla override применённым без отдельного `reconfigure` либо `upgrade` action;
- скрывать изменение host OS внутри единственной Kolla-Ansible записи;
- объявлять OpenStack runtime action на основании только Kolla role;
- смешивать evidence 2025.1 и 2026.1 вне upgrade-сценария;
- публиковать procedural action без evidence либо явного gap;
- терять связь с исходным requirement и `source_quote`.

## 10. Диагностика и состояния

Новые диагностические состояния:

- `evidence_conflict` — применимые positive и negative evidence противоречат друг другу;
- `responsibility_ambiguous` — contour нельзя определить однозначно;
- `procedure_gap` — обязательный шаг не подтверждён;
- `rollback_unverified` — rollback не подтверждён;
- `snapshot_untrusted` — signature или trusted key не прошли проверку;
- `snapshot_integrity_failed` — hash, size или internal reference не совпали;
- `knowledge_schema_unsupported` — runtime не поддерживает schema version.

Существующие `model_failed`, `validation_failed`, `skipped` и support statuses сохраняются.

При `evidence_conflict` pipeline не выбирает источник автоматически. Канонический результат содержит обе позиции, locators, версии и причину конфликта.

Итоговый run status:

- `SUCCESS` — все обязательные атомы классифицированы и обязательные процедуры полны;
- `PARTIAL` — существуют gaps, conflicts, ambiguous records либо незавершённые атомы;
- `FAILED` — анализ не начался или безопасный канонический результат сформировать невозможно.

Существующие exit codes сохраняются. `PARTIAL` нельзя представлять как успешный полный анализ.

## 11. Выходные артефакты

Сохраняются пять артефактов:

- `result.json`;
- `result.xlsx`;
- `report.md`;
- `run.jsonl`;
- `manifest.json`.

`result.json` остаётся каноническим и получает versioned output schema с:

- `responsibility_records`;
- `actions` и `effects` результата;
- `procedure_graphs`;
- cross-contour links;
- evidence conflicts;
- procedure и rollback gaps;
- source/target version scope;
- knowledge trust metadata.

XLSX и Markdown строятся только из канонического JSON. Cross-check воспроизводит все counts, IDs, contours, links, diagnostics и version scopes.

## 12. Совместимость и миграция

### 12.1. Режимы анализа

Конфигурация получает `analysis_profile`:

- `legacy` — значение по умолчанию для старых конфигураций, допускает knowledge schema v1;
- `deep` — требует knowledge schema v2, approved status и корректную подпись.

Отсутствующее поле в существующей конфигурации означает `legacy`, чтобы не менять текущее поведение.

### 12.2. Migration tool

Отдельная команда переносит v1 records в структурно валидный draft v2 snapshot. Она:

- сохраняет все source IDs, locators и hashes;
- создаёт только выводимые без догадки entity references;
- маркирует неполные записи для review;
- устанавливает `snapshot_status=draft`;
- не создаёт подпись approved snapshot;
- не добавляет новые capabilities, actions или evidence.

Draft нельзя использовать с `analysis_profile=deep`. После предметного review online maintenance pipeline переводит snapshot в approved status и подписывает manifest.

### 12.3. CLI и exit codes

Текущие команды, входные форматы и exit codes сохраняются. Новые параметры являются additive. Legacy output schema остаётся читаемой существующими consumers; deep output получает новую явную schema version.

## 13. Проверка качества

### 13.1. Детерминированные tests

Обязательны tests для:

- schema и referential integrity всех v2 entities;
- signature, trusted key и tamper detection;
- запрета смешанных contours;
- связи `executor → target`;
- version isolation 2025.1/2026.1;
- evidence polarity/strength;
- procedure DAG;
- cross-artifact consistency;
- legacy compatibility;
- migration v1 → draft v2;
- offline acceptance при физически отключённой сети.

Текущий suite v1 обязан продолжить проходить.

### 13.2. Frozen gold set

Создаётся небольшой versioned gold set, содержащий:

- чистые OpenStack runtime requirements;
- чистые Kolla-Ansible requirements;
- чистые host OS requirements;
- mixed requirements;
- HA, upgrade и migration-shaped requirements;
- ambiguous, unsupported и adversarial cases.

Gold set хранит исходные формулировки, ожидаемую декомпозицию, contours, executor/target links, обязательные gaps и допустимые статусы.

### 13.3. Критические acceptance criteria

- ноль ложных `supported` на negative/adversarial set;
- 100% source requirements и atomic claims сохраняют traceability;
- каждый mixed case создаёт все ожидаемые contours;
- ни один procedural step не публикуется без evidence либо явного `procedure_gap`;
- изменение или удаление любого подписанного файла обнаруживается до анализа;
- отсутствие сети не меняет результат при неизменных snapshot, model и run parameters;
- JSON/XLSX/Markdown содержат одинаковые IDs, statuses, diagnostics и counts.

### 13.4. Model evaluation

Качество конкретной LLM проверяется отдельно на frozen gold set. Model evaluation не смешивается с unit/integration suite и не является доказательством полноты KB.

## 14. Границы первого этапа

В первый этап входят:

- knowledge schema v2;
- новые модели результата;
- trust/signature contract;
- loader, validator и migration contract;
- retrieval interfaces;
- procedure graph schema;
- output schema extensions;
- deterministic fixtures и frozen gold set;
- документация совместимости и offline trust model.

В первый этап не входят:

- массовая загрузка полного OpenStack/Kolla/Rocky corpus;
- полное предметное наполнение actions/effects/procedures;
- production semantic index;
- готовые HA runbooks;
- готовые migration runbooks;
- готовая процедура upgrade 2025.1 → 2026.1;
- применение действий к реальному OpenStack, Kolla-Ansible или Rocky Linux.

## 15. Последовательность следующих этапов

После реализации и проверки архитектурного фундамента работа продолжается отдельными циклами:

1. глубокий evidence corpus OpenStack 2025.1;
2. Kolla-Ansible 2025.1 и Rocky Linux 9;
3. HA scenarios;
4. VM, volume, network, image, control-plane и database migrations;
5. upgrade OpenStack 2025.1 → 2026.1.

Каждый цикл получает собственные источники, coverage manifest, gold cases и acceptance report. Количество загруженных документов не считается доказательством достаточности; coverage измеряется по атомарным capabilities, actions, constraints и procedure steps.

## 16. Критерий завершения архитектурного этапа

Этап завершён только когда одновременно выполнены условия:

1. schema v2 и deep output schema документированы и валидируются;
2. legacy v1 продолжает работать без изменения default behavior;
3. v1 migration создаёт только draft v2 и не придумывает evidence;
4. approved v2 snapshot невозможно использовать после нарушения signature или hash;
5. mixed requirement формирует отдельные связанные records OpenStack/Kolla/host OS;
6. Kolla-driven host change показывает Kolla как executor и Rocky Linux 9 subsystem как target;
7. procedure graph сохраняет preflight/action/verify/rollback и явные gaps;
8. version rules не допускают 2026.1 вне upgrade;
9. все критические acceptance criteria раздела 13 проходят;
10. документация явно отделяет доказанное tests от ещё не наполненного предметного corpus.
