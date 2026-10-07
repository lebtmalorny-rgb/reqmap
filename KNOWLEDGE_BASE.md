# Локальная база знаний reqmap

Runtime-анализ читает неизменяемый snapshot `knowledge/epoxy-2025.1`. Он ограничивает допустимые компоненты и связывает каждый подтверждённый mapping с локальным evidence. Штатный pipeline не обновляет snapshot и не открывает внешние URL.

## Состав snapshot

- `metadata.json` — `openstack_release` и агрегированный `snapshot_sha256`;
- `components.json` — allowlist компонентов, отображаемые имена и aliases;
- `capabilities.jsonl` — атомарные возможности компонента и связанные evidence IDs;
- `evidence.jsonl` — нормализованные доказательства, версия, polarity, strength и локальный locator;
- `synonyms.json` — русские и технические термины для детерминированного retrieval;
- `source-manifest.json` — provenance, официальный URL, дата получения, локальный путь и SHA-256;
- `sources/*.md` — короткие локальные excerpts, достаточные для аудита утверждения.

Команда проверки пересчитывает hashes, валидирует ссылки между сущностями и останавливается при неизвестном component/capability/evidence:

```bash
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
```

## Evidence model

`polarity=positive` прямо или косвенно подтверждает возможность. `polarity=negative` фиксирует прямое ограничение, несовместимость версии или доказанный конфликт. Отсутствие найденной записи не является negative evidence и не разрешает `not_supported`.

`strength=direct` означает прямое применимое утверждение официального источника. `strength=indirect` допускается только как ограниченная связь, которая сама по себе не должна расширять scope компонента. `strength=none` не подтверждает положительный mapping.

В legacy каждое evidence также имеет `claim_scope`: `context` или `specific`.
`context` описывает назначение сервиса, общий контекст или проектное правило;
такая запись доступна поиску и агенту, но не подтверждает реализацию либо её
отсутствие. `specific` обозначает проверенное ограниченное утверждение о
возможности или ограничении. Polarity, strength, версия и принадлежность
компоненту проверяются независимо от scope. Маркер `specific` сам по себе
не является доказательством всех условий произвольного требования.

В старых записях без этого поля используется `context`. Некорректные значения
отклоняются с `EVIDENCE_CLAIM_SCOPE`. Когда proposal пытается подтвердить
реализацию только справочными records, итог — `insufficient_evidence` с
диагностикой `EVIDENCE_CONTEXT_ONLY`. Справочный текст также не участвует в
grounding при совместном цитировании с конкретным evidence.

Каждая запись evidence содержит стабильный `evidence_id`, `component_id`, `capability_id`, `claim_ru`, `source_id`, `locator`, `version_constraint`, `local_path`, `source_sha256`, `retrieved_at` и `provenance`. `source_url` хранится для сопровождения, но runtime его не запрашивает.

Source hints из пользовательского XLSX не включаются в evidence model. Они сохраняются рядом с исходной строкой и могут использоваться как контекст retrieval, но не как доказательство поддержки.

## Правила изменения

Изменение snapshot выполняется только в maintenance-ветке и начинается с конкретного проверяемого утверждения. Нельзя добавлять общую страницу компонента как подтверждение всех функций. Для каждой новой возможности нужны ограниченный claim, точный locator, версия 2025.1 и локальный excerpt.

Перед установкой нового snapshot обновите пакет reqmap: прежний строгий loader
не понимает поле `claim_scope`. После изменения hash базы начинайте новую
сессию анализа. Старую неразмеченную базу новый loader прочитает как context-only;
повышать scope до `specific` можно только после предметной проверки claim,
а не ради получения положительного статуса.

Сетевой инструмент `tools/kb/verify_urls.py` предназначен только для online maintenance. Он разрешает HTTPS к allowlist официальных OpenStack hosts, ограничивает redirects и не вызывается installer или CLI-анализом.

```bash
PYTHONPATH=src .venv/bin/python tools/kb/verify_urls.py \
  knowledge/epoxy-2025.1 10
```

После подготовленного изменения пересоберите source hashes и aggregate hash:

```bash
PYTHONPATH=src .venv/bin/python tools/kb/build_snapshot.py \
  knowledge/epoxy-2025.1
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
```

`tools/kb/build_snapshot.py` атомарно обновляет `sha256` локальных excerpts и `metadata.json.snapshot_sha256`. Коммит должен содержать одновременно изменённые schema records, excerpts, manifest и metadata.

## Чек-лист ревью

1. Все `component_id`, `capability_id`, `evidence_id` уникальны и существуют в соответствующих allowlists.
2. Claim атомарен, не шире цитируемого locator и применим к OpenStack Epoxy 2025.1.
3. Positive/negative polarity и direct/indirect strength обоснованы содержанием excerpt.
4. `local_path` относительный, не содержит `..`, файл существует, а SHA-256 совпадает.
5. `source_url`, `retrieved_at`, `provenance` и version заполнены; сетевой URL проверен только в maintenance-среде.
6. Новый mapping не использует Horizon как доказательство любой GUI-функции, Octavia как балансировщик VM workload, Ceilometer как billing engine или PCI passthrough как достаточное доказательство vGPU.
7. `not_supported` имеет прямой negative evidence или доказанную несовместимость; неизвестный случай остаётся `insufficient_evidence`.
8. `build_snapshot.py`, `knowledge validate`, unit tests и негативные tests проходят на чистом checkout.

Структура выходных ссылок на evidence описана в [OUTPUT_SCHEMA.md](OUTPUT_SCHEMA.md). Ошибки integrity и hash mismatch разобраны в [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Schema v2: сопровождение deep snapshot

Legacy v1 хранится отдельно. Deep schema v2 добавляет `actors.json`,
`targets.jsonl`, `actions.jsonl`, `effects.jsonl`, `procedures.jsonl`, локальный
`corpus/`, `snapshot-manifest.json` и detached `snapshot-manifest.sig`.
Индекс, если поставляется, также покрывается manifest. Корпус даёт кандидатов;
финальную поддержку устанавливают нормализованные evidence. По умолчанию
corpus discovery отключён, production semantic index не поставляется.

`metadata.json` фиксирует `knowledge_schema_version=2`, `snapshot_status`,
`snapshot_id`, `key_id`, OpenStack/Kolla 2025.1, upgrade target 2026.1 и
`host_profile=rocky_linux_9`. Runtime принимает только `snapshot_status=approved`.
Нельзя просто переименовать `draft` в `approved`: сначала проверяются claims,
locators, evidence, действия, эффекты и процедуры. Миграция v1 сама не создаёт
новых действий или доказательств и выдаёт только unsigned draft с review gaps.
Maintenance-проверка такого черновика допускает неполное подтверждение
capabilities и сохранённые policy/indirect evidence из v1, если все evidence
имеют `review_state=needs_review`, а actions, effects и procedures пусты.
Ссылки, hashes и версии проверяются и в этом режиме. Для `approved` эти
послабления не действуют; runtime черновик не принимает.

После предметного review в maintenance-среде собирают manifest и подписывают
его отдельным ключом Ed25519. В примере пути уже подготовлены оператором;
закрытый ключ располагается вне репозитория и snapshot:

```bash
.venv/bin/python tools/kb/build_snapshot_v2.py \
  --path /srv/reqmap-knowledge/epoxy-2025.1-deep
.venv/bin/python tools/kb/sign_snapshot_v2.py \
  --path /srv/reqmap-knowledge/epoxy-2025.1-deep \
  --private-key /srv/reqmap-maintenance-keys/signing_key
.venv/bin/reqmap knowledge validate \
  --path /srv/reqmap-knowledge/epoxy-2025.1-deep \
  --allowed-signers /etc/reqmap/trust/allowed_signers
```

Доверенный `allowed_signers` находится снаружи snapshot. Формат строки:
`reqmap-snapshot ssh-ed25519 <PUBLIC_KEY_BASE64>`; namespace подписи —
`reqmap-snapshot`. Публичный ключ сверяют независимо от передачи snapshot.
Private key не передают runtime и не коммитят. `key_id` — метка manifest;
криптографическое доверие устанавливается проверкой подписи по allowed_signers.

Каждая запись ответственности содержит один contour. Если исполнитель
Kolla-Ansible изменяет host OS, нужны две связанные записи: автоматизация и
изменение подсистемы Rocky Linux 9. `executor_ref` и `target_ref` не объединяются.
Evidence 2025.1 не доказывает поведение 2026.1; target 2026.1 разрешён только
для upgrade action и его upgrade-шагов. Остальные стадии процедуры используют
отдельные actions с подходящими version scopes.

Текущий этап поставляет архитектуру и synthetic fixtures, а не production
корпус. `tests/fixtures/deep_gold.json` фиксирует ожидаемые результаты;
`tests/deep_acceptance_support.py` отдельно задаёт искусственные источники и
ответы fake LLM. Условный исполнитель миграций в fixtures не является
предметным сопоставлением миграций Nova/Cinder/Neutron/Glance/БД.


## Каталог связи с исходными обязательствами

`binding_catalog_path` — optional путь в CLI/agent config, относительный к
каталогу config или абсолютный. Это отдельная ручная проверенная разметка,
а не новая KB schema. Без неё поиск работает, но `supported` / `not_supported`
не выводятся из одной тематической близости. Повреждённый настроенный каталог
прерывает preflight с конкретным `BINDING_*` кодом.

Каталог содержит `binding-manifest.json` и `predicates.jsonl`. Manifest —
строгий JSON object с полями `binding_schema_version=1`, `catalog_id`,
`knowledge_sha256`, `release_scope` и `files`. `files` описывает только
`predicates.jsonl` с `path`, `size`, `sha256`. Digest KB —
`metadata.snapshot_sha256` для legacy или digest проверенного signed snapshot
manifest для deep; `release_scope` должен совпадать с релизом KB.

Каждая строка predicates — object со всеми полями `EvidencePredicate`:

- `predicate_id`; непустые параллельные массивы `evidence_ids`, `source_ids`,
  `locators`, `source_sha256s` в одном порядке;
- `component_ref`, `capability_ref`, nullable `action_ref`, `effect_ref`,
  `target_ref` (три последних обязательны для deep и равны null для legacy);
- `actor`, `action`, `object`, `direction` (`capability` / `prohibition`),
  `interface` (`api`, `gui`, `cli`, `config`), `contour`, `lifecycle_phase`,
  `release_scope`, `polarity`;
- `assumptions`, `constraints`: массивы `{name, operator, value, unit}`;
  operator `eq` / `within`, value — строка, unit nullable;
- `review_state=reviewed`, `reviewed_by`, `reviewed_at` (ISO date),
  `annotation_version`.

Рецензент сопоставляет действие, объект, отрицание и каждое условие с конкретным
официальным источником. Поля нельзя выводить из похожего имени capability или
автоматически назначать всем claims проекта. Loader проверяет refs, version,
polarity, hash/locator источника и точную deep action/effect/target relation.
Требуется `direct` evidence; legacy — `claim_scope=specific`, deep — reviewed
claim из официального источника, не project policy. `context` и `indirect`
остаются полезны при поиске, но не подтверждают predicate этого этапа.

Один predicate покрывает всё обязательство. Гарантируемые constraints
сравниваются точно: отдельные claims «create через API» и «delete через GUI»
не дают «create через GUI». Неподтверждённое время или отказ узла не превращается
в отрицательное доказательство. Непустые assumptions пока блокируют доказательный статус: они не являются
гарантиями, а отдельного правила доказательства предусловий ещё нет.
Новые формы/условия требуют отдельной версии
правил и тестов, а не расширения модели по свободному тексту.

Для legacy действует прежняя maintenance trust boundary. Для deep дополнительно
нужен `binding-manifest.sig`, Ed25519 и внешний `allowed_signers` из
`knowledge_trust`; SSH namespace **`reqmap-obligation-binding`**, signer identity
`reqmap-snapshot`. Подпись KB в namespace `reqmap-snapshot` не заменяет эту
подпись. Ключи и доверие из tests не устанавливаются в production. Symlink,
path traversal, duplicate JSON keys, лишние поля и изменившиеся файлы отвергаются.

В поставке есть legacy-каталог `knowledge/bindings/epoxy-2025.1-api`: 16
predicates для создания, чтения сведений, переименования (`name`) и удаления
ВМ Nova, сетей/портов Neutron и томов Cinder. Исторический набор
`deep_gold.json` сохраняет ожидания нижнего evidence/procedure validator;
сквозной runtime дополнительно проверяет полную исходную строку и закономерно
возвращает недостаточность для фраз вне конечной грамматики. Эти две проверки
не являются измерением качества живой LLM или покрытием реальных требований.

Конфликт полных reviewed predicates в выбранной relation проверяет backend,
включая не выбранные агентом противоположные refs. Удаление negative predicate
или его evidence из proposal не снимает конфликт. Ссылки на найденные
противоречащие evidence сохраняются в binding diagnostics.
Положительный API-пример может доказать наличие возможности при unspecified
интерфейсе; отрицательное API-доказательство не доказывает отсутствие всех
интерфейсов. Общее отрицание или запрет без интерфейса пока остаётся insufficient.


### Сопровождение первого API-пакета

Источники закреплены на коммитах Epoxy: Nova 31.0.0, neutron-lib 3.18.2,
Cinder 26.0.0. Четыре локальных excerpt-файла `SRC-API-*-2025.1.md` содержат
проверенный пересказ, ссылку на неизменяемый исходник, upstream SHA-256,
разделы/строки, endpoints и ограничения. `source-manifest.sha256` и
`source_sha256s` в predicates относятся к локальному excerpt, а не к полному
upstream-файлу. Точная таблица источников и проверок — в
[приёмке пакета](docs/acceptance/2026-10-07-core-api-evidence.md).

Аннотации подготовлены и проверены агентом (`reviewed_by=codex-maintenance`);
это не подпись OpenStack и не аттестация работоспособности стенда. Predicate
с `direction=capability` утверждает наличие API-возможности. Его пустые
`assumptions`/`constraints` не означают безусловного успеха запроса: условия
исполнения описаны в excerpt, а требование с дополнительными условиями должно
иметь отдельное применимое доказательство. Пакет не доказывает GUI, HA, сроки,
resize, произвольные изменения полей или работу без прав и квот.

После предметного review правок KB и аннотаций выполняйте из корня репозитория:

```bash
PYTHONPATH=src .venv/bin/python tools/kb/build_snapshot.py knowledge/epoxy-2025.1
PYTHONPATH=src .venv/bin/python tools/kb/build_binding_catalog.py \
  knowledge/bindings/epoxy-2025.1-api knowledge/epoxy-2025.1
PYTHONPATH=src .venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_evidence_package.py -q
```

Сборщик каталога меняет только digest KB, размер и hash predicates в manifest.
Он проверяет временную копию общим loader до замены manifest и не назначает
`reviewed`, не переписывает refs и source hashes. Изменившийся excerpt требует
повторной проверки claims и осознанного обновления аннотаций. KB и каталог
фиксируются одним коммитом. Для deep действует отдельная процедура подписания.
После обновления KB/каталога/грамматики создайте новую MCP-сессию.

### Пакет объектов Keystone Identity API

Каталог `epoxy-2025.1-api` дополнен 12 операциями создания, чтения сведений,
переименования (`name`) и удаления пользователей, проектов и ролей. Источники —
Keystone 27.0.0, commit `bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a`,
`api-ref/source/v3/{users,projects,roles}.inc`. Каждый из трёх excerpt-файлов
содержит upstream SHA-256, разделы и строки. Действует прежняя процедура
пересборки snapshot и каталога; полный каталог содержит 28 predicates.

Грамматика 1.2 требует явного имени Keystone для этих объектов: общее слово
«пользователь» может относиться к ОС, гипервизору или продуктовому порталу.
Наличие CRUD API не подтверждает назначение ролей, effective policy, запись
в AD/LDAP, GUI или работу при любом backend. Изменение пользователя может
вернуть HTTP 501, если backend-драйвер его не поддерживает.

Новые проверки: `tests/test_keystone_api_evidence.py`; подробные границы и
результаты — в [приёмке пакета](docs/acceptance/2026-10-07-keystone-api-evidence.md).
Историческая живая приёмка первого API-пакета не является приёмкой Keystone.
