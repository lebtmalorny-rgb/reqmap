# Глубокий отчёт reqmap

<!-- reqmap-counts:{"atoms":0,"diagnostics":2,"evidence":0,"procedure_steps":0,"requirements":1,"responsibilities":0} -->

## Сводка

Статус запуска: `FAILED`

| Показатель | Количество |
|---|---:|
| Исходные требования | 1 |
| Атомарные утверждения | 0 |
| Записи ответственности | 0 |
| Шаги процедур | 0 |
| Доказательства | 0 |
| Диагностические записи | 2 |

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
| requirement | REQ-0001 | ["REQ-0001"] | [] | skipped |  | [] | [] | [] | [] | ["AGENT_UNPROCESSED: агент не завершил анализ."] | Учебное требование: создать сервер |

## Атомарные утверждения

| Atom ID | Requirement ID | Ordinal | Text | Source quote | Mandatory | Analysis state | Support status | Responsibility IDs | Supported aspects | Unconfirmed aspects | Diagnostics |
|---|---|---|---|---|---|---|---|---|---|---|---|

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
| 1 | run | run-3f71d4dc-f1e5-4605-a9e3-8f5a46e640ea |  |  |  |  | AGENT_UNPROCESSED: агент не завершил анализ. |
| 2 | requirement | REQ-0001 | REQ-0001 | Учебное требование: создать сервер |  |  | AGENT_UNPROCESSED: агент не завершил анализ. |

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
| requirement | REQ-0001 | Учебное требование: создать сервер |  |  |  | ["AGENT_UNPROCESSED: агент не завершил анализ."] |

## Ошибки обработки

| Scope | Entity ID | Requirement ID | Полный текст требования | Atom ID | Исходная цитата | Причина |
|---|---|---|---|---|---|---|
| run | run-3f71d4dc-f1e5-4605-a9e3-8f5a46e640ea |  |  |  |  | AGENT_UNPROCESSED: агент не завершил анализ. |
| requirement | REQ-0001 | REQ-0001 | Учебное требование: создать сервер |  |  | AGENT_UNPROCESSED: агент не завершил анализ. |
| requirement_state | REQ-0001 | REQ-0001 | Учебное требование: создать сервер |  |  | skipped |

Происхождение анализа (заявлено клиентом): `{"identity_verified":false,"mode":"external_agent","proposal_journal_sha256":"37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570","reported_client":"unknown","revision":1,"session_id":"3f71d4dc-f1e5-4605-a9e3-8f5a46e640ea","tool_contract_version":"1.0","workflow_version":"1.0"}`
