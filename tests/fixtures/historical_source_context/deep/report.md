# Глубокий отчёт reqmap

<!-- reqmap-counts:{"atoms":1,"diagnostics":3,"evidence":0,"procedure_steps":0,"requirements":1,"responsibilities":0} -->

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

Статус запуска: `PARTIAL`

| Показатель | Количество |
|---|---:|
| Исходные требования | 1 |
| Атомарные утверждения | 1 |
| Записи ответственности | 0 |
| Шаги процедур | 0 |
| Доказательства | 0 |
| Диагностические записи | 3 |

### Проверенные метаданные запуска

| Параметр | Значение |
|---|---|
| Профиль | deep |
| Модель | external-agent |
| Snapshot ID | epoxy-2025.1-deep-001 |
| Key ID | reqmap-maintenance-2026 |
| Signer identity | reqmap-snapshot |
| Manifest SHA-256 | 3c49180ab934f8dbb447a8a5f240bbcfc2a891327e2af8241ec23df5c52a080b |

## Требования и группы

| Type | Entity ID | Source requirement IDs | Group IDs | Analysis state | Support status | Atom IDs | Responsibility IDs | Procedure graph IDs | Component refs | Diagnostics | Full requirement text |
|---|---|---|---|---|---|---|---|---|---|---|---|
| requirement | REQ-0001 | ["REQ-0001"] | [] | completed | insufficient_evidence | ["REQ-0001-A001"] | [] | [] | [] | ["BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен."] | Nova должна создавать ВМ через API за одну миллисекунду |

## Атомарные утверждения

| Atom ID | Requirement ID | Ordinal | Text | Source quote | Mandatory | Analysis state | Support status | Responsibility IDs | Supported aspects | Unconfirmed aspects | Diagnostics |
|---|---|---|---|---|---|---|---|---|---|---|---|
| REQ-0001-A001 | REQ-0001 | 1 | Nova должна создавать ВМ через API за одну миллисекунду | Nova должна создавать ВМ через API за одну миллисекунду | True | completed | insufficient_evidence | [] | [] | ["Nova должна создавать ВМ через API за одну миллисекунду"] | ["BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен."] |

## Контуры ответственности

| Record ID | Requirement ID | Atom ID | Contour | Component | Executor | Target contour | Target | Action | Effect | Phase | Version scope | Evidence IDs | Support status | Related records | Procedure step IDs | Diagnostics |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

### Количество записей по контурам

| Контур | Количество записей |
|---|---:|
| openstack_runtime | 0 |
| kolla_ansible | 0 |
| host_os | 0 |

## Executor → target

| Record ID | Связь | Target contour | Action | Effect | Related records |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

## Версии и область применимости

| Record ID | Phase | Source release | Target release | Kolla-Ansible | Host profile | Constraint |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |

## Процедуры

| Graph ID | Requirement ID | Template ID | Graph diagnostics | Step ID | Phase | Contour | Executor | Target | Action | Preconditions | Success criteria | Evidence IDs | Evidence versions | Depends on | Rollback step ID |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

## Каталог evidence

| Evidence ID | Claim | Claim kind | Polarity | Strength | Source ID | Locator | Version constraint | Applicable contours | Supports entity refs | Local excerpt | Review state |
|---|---|---|---|---|---|---|---|---|---|---|---|

## Нормализованная диагностика

| Order | Scope | Entity ID | Requirement ID | Full requirement text | Atom ID | Source quote | Diagnostic |
|---|---|---|---|---|---|---|---|
| 1 | run | run-ad7a292d-d48f-4638-8d05-54ec1c3dcb7b |  |  |  |  | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |
| 2 | requirement | REQ-0001 | REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду |  |  | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |
| 3 | atom | REQ-0001-A001 | REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду | REQ-0001-A001 | Nova должна создавать ВМ через API за одну миллисекунду | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |

## Конфликты evidence

| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Диагностика |
|---|---|---|---|---|---|---|

| Evidence ID | Polarity | Strength | Source ID | Locator | Version constraint | Claim |
|---|---|---|---|---|---|---|

## Предупреждения procedures и rollback

Диагностики `procedure_gap` и `rollback_unverified` не заменяются придуманными шагами.

| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Диагностика |
|---|---|---|---|---|---|---|

## Проблемные требования и атомы

| Scope | Entity ID | Полный текст требования | Atom ID | Исходная цитата | Статус | Диагностика |
|---|---|---|---|---|---|---|
| requirement | REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду |  |  | insufficient_evidence | ["BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен."] |
| atom | REQ-0001-A001 | Nova должна создавать ВМ через API за одну миллисекунду | REQ-0001-A001 | Nova должна создавать ВМ через API за одну миллисекунду | insufficient_evidence | ["BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен."] |

## Ошибки обработки

| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Причина |
|---|---|---|---|---|---|---|
| run | run-ad7a292d-d48f-4638-8d05-54ec1c3dcb7b |  |  |  |  | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |
| requirement | REQ-0001 | REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду |  |  | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |
| atom | REQ-0001-A001 | REQ-0001 | Nova должна создавать ВМ через API за одну миллисекунду | REQ-0001-A001 | Nova должна создавать ВМ через API за одну миллисекунду | BINDING_CATALOG_MISSING: Каталог проверенных предикатов не настроен. |

Происхождение анализа (заявлено клиентом): `{"identity_verified":false,"mode":"external_agent","proposal_journal_sha256":"191e00806c99b0de756495e114fb7d90990898cd777d4d03d914dd78bd8b6982","reported_client":"unknown","revision":3,"session_id":"ad7a292d-d48f-4638-8d05-54ec1c3dcb7b","tool_contract_version":"2.0","workflow_version":"2.0"}`
