# Пакет объектов Keystone Identity API — 07.10.2026

Вторая часть этапа 4: 12 конкретных операций Keystone для legacy Epoxy 2025.1.
Пользователи, проекты и роли — отдельные объекты с отдельными evidence и
predicates. Пакет доказывает документированную API-возможность; успешный вызов
на стенде и соответствие составному продуктовому требованию проверяются отдельно.

| Объект | Создание | Чтение | Переименование (`name`) | Удаление |
| --- | --- | --- | --- | --- |
| Пользователь | `POST /v3/users` | `GET /v3/users/{user_id}` | `PATCH /v3/users/{user_id}` | `DELETE /v3/users/{user_id}` |
| Проект | `POST /v3/projects` | `GET /v3/projects/{project_id}` | `PATCH /v3/projects/{project_id}` | `DELETE /v3/projects/{project_id}` |
| Роль | `POST /v3/roles` | `GET /v3/roles/{role_id}` | `PATCH /v3/roles/{role_id}` | `DELETE /v3/roles/{role_id}` |

## Выбор и границы

Локальный аудит исходных книг содержит управление пользователями и ролями,
создание проекта и продуктовый портал. Эти строки смешивают разные контуры;
пакет выбран как повторно используемая часть identity/access, а не по числу
поисковых совпадений. Исходные тексты в fixtures не перенесены. Покрытие
пользовательских строк этим пакетом не оценивалось.

Грамматика 1.2 сохраняет прежние четыре действия и добавляет явный actor
Keystone и объекты user/project/role. Пример:
`Keystone должна создавать пользователя через API`.
Для общих слов «пользователь», «проект», «роль» сервис не угадывается:
без явного actor строка остаётся unresolved. Полный текст, повторные
обязательства и source spans сохраняются.

Создание объекта роли не доказывает назначение роли, scope токена или
effective policy сервисов. Переименование меняет только `name`; остальные
поля требуют своих claims. Не подтверждаются LDAP/AD, учётные записи ОС или
гипервизора, GUI, сроки, HA, иные релизы, обход прав и работа на любом backend.
Неизвестный хвост требования сохраняется целиком и препятствует `supported`.

## Источники

Релиз Keystone 27.0.0 и commit `bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a`
сверены с [официальным перечнем Epoxy](https://releases.openstack.org/epoxy/#keystone).
Три API-файла OpenDev по commit побайтово совпали с официальным GitHub-зеркалом
по tag 27.0.0. Сохранены локальные пересказы, точные locators и upstream hashes:

| Файл | Upstream SHA-256 |
| --- | --- |
| [users.inc](https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/users.inc) | `a6db31166611abbb864284352fdd04b3576011adcba815e489f1ea7fc2d13a5a` |
| [projects.inc](https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/projects.inc) | `7e19c70746bcdd7bb51915438fd9bde49881d8617880ff460f24425c2afc31c0` |
| [roles.inc](https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/roles.inc) | `e6ad252afa4d7d1c3241c793c7a77ca9ebea946d7080cbc6992e0eed9d6f1121` |

В `users.inc` прямо указано: backend-драйвер может не поддерживать update,
возвращая 501. Наличие endpoint не является гарантией записи в любой каталог.
Предусловия API изложены в excerpt; пустые constraints у узкого predicate
не подтверждают дополнительные условия исходной строки. Review-аннотации
подготовлены `codex-maintenance`; это не подпись проекта OpenStack.

Snapshot: 30 компонентов, 58 capabilities, 65 evidence; каталог — 28 predicates.
KB SHA-256: `533252fe09dc73c4fd1a9bd24b1ff60a1067c163547643ed1a0a1af311acc406`.
Catalog SHA-256: `f00f8103e3c0f7e94df58f0b3b63bf262932769c963d2bc5bf041b0f506fc662`.
Прежние записи и evidence сохранены; manifest связывает каталог с новой KB.
Для установки нужны согласованные код/KB/каталог и новая MCP-сессия;
прежние активные контракты несовместимы, finalized-отчёты исторические.
Deep требует отдельного reviewed corpus и подписания.

## Проверки

До изменения — 198 целевых тестов первого API-пакета и source binding.
Новые grammar/operation проверки дали 24 ожидаемых отказа на прежнем коде/KB.
После реализации — 173 теста Keystone: 12 grammar, 12 retrieval/support,
120 контрастов, 12 проверок обязательного predicate, четыре перефразировки,
восемь неоднозначных контуров, четыре подмены actor/object и MCP-экспорт.
Последний сохраняет два одинаковых обязательства с разными spans.

Счётчики shipped snapshot в тесте миграции обновлены с 53/46 на 65/58
после воспроизведения ожидаемого отказа. Требование draft-only сохранено.
В новых тестах устранены две ошибки: ссылка на `atoms` вместо `atom_results`
и попытка пройти retrieval с нерелевантным evidence. Проверка неопределённого
контура теперь намеренно передаёт candidate напрямую, проверяя source gate.

Полный suite, stdio restart/replay и независимое ревью фиксируются ниже после
завершения. Живая приёмка Keystone в IDE не выполнялась. Успех автоматических
proposals не оценивает выбор настоящей модели. Этапы 4 и 5 целиком открыты.
