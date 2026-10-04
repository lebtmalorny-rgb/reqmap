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

Каждая запись evidence содержит стабильный `evidence_id`, `component_id`, `capability_id`, `claim_ru`, `source_id`, `locator`, `version_constraint`, `local_path`, `source_sha256`, `retrieved_at` и `provenance`. `source_url` хранится для сопровождения, но runtime его не запрашивает.

Source hints из пользовательского XLSX не включаются в evidence model. Они сохраняются рядом с исходной строкой и могут использоваться как контекст retrieval, но не как доказательство поддержки.

## Правила изменения

Изменение snapshot выполняется только в maintenance-ветке и начинается с конкретного проверяемого утверждения. Нельзя добавлять общую страницу компонента как подтверждение всех функций. Для каждой новой возможности нужны ограниченный claim, точный locator, версия 2025.1 и локальный excerpt.

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
