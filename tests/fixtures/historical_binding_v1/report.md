# Отчёт reqmap

<!-- reqmap-counts:{"atoms":1,"evidence":1,"mappings":1,"requirements":1} -->

## Сводка

Статус запуска: `SUCCESS`

| Показатель | Количество |
|---|---:|
| Исходные требования | 1 |
| Атомарные утверждения | 1 |
| Сопоставления | 1 |
| Доказательства | 1 |

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

| Component ID | Сопоставлений | Требования | Фазы |
|---|---:|---|---|
| nova | 1 | REQ-0001 | runtime |

## Runtime и design-time

| Фаза | Mapping ID | Atom ID | Component ID | Механизм | Шаги |
|---|---|---|---|---|---|
| runtime | REQ-0001-A001-M001 | REQ-0001-A001 | nova | openstack_api | 1. Compute instances через REST API; mechanism=openstack_api; api_operation=Compute instances через REST API |

## Изменения хостовой ОС

Изменения хостовой ОС отсутствуют.

## Неподтверждённые требования

| Requirement ID | Полная формулировка | Статус | Причина |
|---|---|---|---|
| REQ-0001 | создание виртуальной машины через Nova REST API | insufficient_evidence | EVIDENCE_CONTEXT_ONLY: общая справка не доказывает конкретную реализацию или ограничение.; Compute instances через REST API; Статус понижен: отсутствует достаточное официальное evidence. |

## Ошибки обработки

| Requirement ID | Полная формулировка | Состояние | Причина |
|---|---|---|---|
| REQ-0001 | создание виртуальной машины через Nova REST API | completed | Статус понижен: отсутствует достаточное официальное evidence.; EVIDENCE_CONTEXT_ONLY: общая справка не доказывает конкретную реализацию или ограничение. |

Происхождение анализа (заявлено клиентом): `{"identity_verified":false,"mode":"external_agent","proposal_journal_sha256":"27c185eab974a2caa3ae36070a4abd5ea0a813d79e9efcc964848d75933ab93e","reported_client":"unknown","revision":3,"session_id":"476c5479-21be-4d84-ae86-190d3e2e533f","tool_contract_version":"1.0","workflow_version":"1.0"}`
