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
