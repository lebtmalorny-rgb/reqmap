# Отчёт reqmap

<!-- reqmap-counts:{"atoms":1,"evidence":0,"mappings":0,"requirements":1} -->

## Связь исходного обязательства с доказательством

Смещения `[start,end)` измеряются в Unicode code points. Непокрытая цитата сохраняется полностью.

### REQ-0001

```json
{"contract_version":"1.0","coordinate":{"row":1,"sheet":null,"source_name":"agent-texts"},"fragments":[{"kind":"obligation","rule_id":"api.resource.verbal.1","span":{"end":55,"quote":"Nova должна создавать ВМ через API за одну миллисекунду","start":0}}],"grammar_sha256":"932e7ec629d5b0c5859de7bc13cce129de646c452c997814310754c26b14a990","grammar_version":"1.2","obligations":[{"action":"create","actor":"nova","constraints":[{"name":"duration","operator":"within","source_spans":[{"end":55,"quote":"за одну миллисекунду","start":35}],"unit":"ms","value":"1"}],"contour":"openstack_runtime","direction":"capability","interface":"api","lifecycle_phase":"runtime","mandatory":true,"object":"vm","obligation_id":"REQ-0001-O001","parse_state":"bound","release_scope":"2025.1","requirement_id":"REQ-0001","rule_id":"api.resource.verbal.1","source_quote":"Nova должна создавать ВМ через API за одну миллисекунду","source_spans":[{"end":55,"quote":"Nova должна создавать ВМ через API за одну миллисекунду","start":0}]}],"requirement_id":"REQ-0001","source_sha256":"1fc5ebc72b3d7d183d8b088531e5409f16d302788c0c6700fe3b90d2a00aef2d","source_text":"Nova должна создавать ВМ через API за одну миллисекунду","unresolved_fragments":[]}
```

```json
{"catalog_sha256":null,"diagnostics":[{"code":"BINDING_CATALOG_MISSING","evidence_ids":[],"field":"catalog","message_ru":"Каталог проверенных предикатов не настроен.","obligation_id":"REQ-0001-O001","requirement_id":"REQ-0001","source_spans":[{"end":55,"quote":"Nova должна создавать ВМ через API за одну миллисекунду","start":0}]}],"engine_version":"1.1","evidence_ids":[],"obligation_id":"REQ-0001-O001","predicate_ids":[],"source_sha256":"1fc5ebc72b3d7d183d8b088531e5409f16d302788c0c6700fe3b90d2a00aef2d","support_status":"insufficient_evidence","uncovered":[{"end":55,"quote":"Nova должна создавать ВМ через API за одну миллисекунду","start":0}]}
```

## Сводка

Статус запуска: `SUCCESS`

| Показатель | Количество |
|---|---:|
| Исходные требования | 1 |
| Атомарные утверждения | 1 |
| Сопоставления | 0 |
| Доказательства | 0 |

### Состояния обработки

| Код | Русское название | Количество требований |
|---|---|---:|
| completed | обработка завершена | 1 |
| model_failed | ошибка модели | 0 |
| validation_failed | ошибка валидации | 0 |
| skipped | пропущено | 0 |

### Статусы поддержки

| Код | Русское название | Количество требований |
|---|---|---:|
| supported | поддерживается | 0 |
| partial | поддерживается частично | 0 |
| not_supported | не поддерживается | 0 |
| insufficient_evidence | недостаточно доказательств | 1 |
| not_applicable | неприменимо | 0 |
| не определён | не определён | 0 |

## Компоненты

Сопоставления компонентов отсутствуют.

## Runtime и design-time

Сопоставления фаз отсутствуют.

## Изменения хостовой ОС

Изменения хостовой ОС отсутствуют.

## Неподтверждённые требования

| Requirement ID | Полная формулировка | Статус | Причина |
|---|---|---|---|
| REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду | insufficient_evidence | Nova должна создавать ВМ через API за одну миллисекунду; BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |

## Ошибки обработки

| Requirement ID | Полная формулировка | Состояние | Причина |
|---|---|---|---|
| REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду | completed | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |

Происхождение анализа (заявлено клиентом): `{"identity_verified":false,"mode":"external_agent","proposal_journal_sha256":"3a19b7302d25bcee7df2ad0a835c5e196624bab9f237e6cf8474dc80c8f70822","reported_client":"unknown","revision":3,"session_id":"db6fcf73-f3d9-4a6d-8424-3a49811d5a11","tool_contract_version":"2.0","workflow_version":"2.0"}`
