# Первый пакет API-доказательств Epoxy 2025.1

Дата проверки: 07.10.2026. Это первая часть этапа 4 плана покрытия.
Пакет содержит 16 конкретных capabilities, evidence и reviewed predicates:
создание, чтение сведений, переименование и удаление четырёх типов ресурсов.

| Ресурс | Create | Read | Rename (`name`) | Delete |
| --- | --- | --- | --- | --- |
| ВМ Nova | `POST /servers` | `GET /servers/{server_id}` | `PUT /servers/{server_id}` | `DELETE /servers/{server_id}` |
| Сеть Neutron | `POST /v2.0/networks` | `GET /v2.0/networks/{network_id}` | `PUT /v2.0/networks/{network_id}` | `DELETE /v2.0/networks/{network_id}` |
| Порт Neutron | `POST /v2.0/ports` | `GET /v2.0/ports/{port_id}` | `PUT /v2.0/ports/{port_id}` | `DELETE /v2.0/ports/{port_id}` |
| Том Cinder | `POST /v3/{project_id}/volumes` | `GET /v3/{project_id}/volumes/{volume_id}` | `PUT /v3/{project_id}/volumes/{volume_id}` | `DELETE /v3/{project_id}/volumes/{volume_id}` |

## Источники и смысл доказательства

Использованы официальные API reference из закреплённых исходников релиза:

- [Nova 31.0.0, servers.inc](https://opendev.org/openstack/nova/raw/commit/6042300453411133652dd8d3b789f5057a084075/api-ref/source/servers.inc), commit `6042300453411133652dd8d3b789f5057a084075`.
- [neutron-lib 3.18.2, networks.inc](https://opendev.org/openstack/neutron-lib/raw/commit/bf21a6dcd48bdd15c28086f256319ac035b7fef0/api-ref/source/v2/networks.inc) и [ports.inc](https://opendev.org/openstack/neutron-lib/raw/commit/bf21a6dcd48bdd15c28086f256319ac035b7fef0/api-ref/source/v2/ports.inc), commit `bf21a6dcd48bdd15c28086f256319ac035b7fef0`.
- [Cinder 26.0.0, volumes-v3-volumes.inc](https://opendev.org/openstack/cinder/raw/commit/06e8c0b5049cd978018740f4794cb162c7043540/api-ref/source/v3/volumes-v3-volumes.inc), commit `06e8c0b5049cd978018740f4794cb162c7043540`.

Версии сверены с официальными release manifests Epoxy. Все четыре файла,
полученные из OpenDev по commit, побайтово совпали с файлами официального
GitHub-зеркала по release tag. Upstream hashes, номера строк, endpoints и
ограничения записаны в четырёх `sources/SRC-API-*-2025.1.md`. Их содержимое —
проверенный пересказ, не дословная цитата. Source hashes в KB и predicates
проверяют локальные excerpt-файлы; полный upstream hash указан внутри excerpt.

`Supported` доказывает наличие документированной API-возможности в upstream
Epoxy. Это не гарантия успешного выполнения запроса на произвольном стенде.
Аутентификация, policy, квоты, параметры и состояние ресурсов остаются
предусловиями вызова. Nova/Cinder создают ресурсы асинхронно; удаление может
зависеть от состояния, attachments, snapshots и политики восстановления.
Код HTTP сам по себе не доказывает время завершения операции.

Пустые `assumptions`/`constraints` относятся к узкому утверждению о наличии
возможности. Дополнительные условия исходного требования сохраняются и
проверяются отдельно; этот пакет не подтверждает их. Reviewed-аннотации
подготовлены агентом `codex-maintenance`, а не подписаны проектом OpenStack.

## Контракт и совместимость

Каталог: `knowledge/bindings/epoxy-2025.1-api`, только legacy KB.
Snapshot содержит 30 компонентов, 46 capabilities и 53 evidence.
KB SHA-256: `cf944089dfe7dad6603b7549f42c2a8d33a11a646750bc34e991f9240ab0c7c6`.
Catalog SHA-256: `888dea3a703fc02b1d95cdc45d8bba21c13f7909cc093a524180b5febb0a13c5`.

Грамматика 1.1 распознаёт конечные глагольные и именные формы четырёх операций.
Известный объект позволяет выбрать сервис при отсутствии его имени.
Явно указанное неверное имя сервиса не заменяется. Полная исходная строка,
позиции цитат и обязательность частей сохраняются. Иные формулировки остаются
непокрытыми; разбор произвольного русского текста не заявлен.

Переименование — только `name`; произвольный update и resize не входят в пакет.
API не подтверждает GUI, внешнюю продуктовую оболочку, HA, гарантии времени,
обход прав/квот или другую версию OpenStack. Старые общие scope claims остаются
контекстом поиска. Deep production corpus требует отдельных reviewed relations
и подписи; миграция legacy в draft сама их не создаёт.

Примеры CLI/MCP-конфигураций выбирают каталог явно. В существующую legacy
конфигурацию добавляется `binding_catalog_path`; затем перезапускается MCP и
создаётся новая сессия. Активные сессии с прежней KB/грамматикой/каталогом
возвращают `SESSION_CONTRACT_MISMATCH`. Старые finalized-отчёты исторические.
Порядок сопровождения — в [KNOWLEDGE_BASE.md](../../KNOWLEDGE_BASE.md#сопровождение-первого-api-пакета).

## Выполненные проверки

- 173 предметных теста: 16 проверок грамматики, 16 положительных сопоставлений,
  112 контрастов (время, отказ, GUI, отрицание, дополнительное действие,
  другая версия, обход квот/прав), 16 случаев без выбранного predicate,
  5 перефразировок, 5 посторонних/подменённых требований, retrieval, MCP и сборщик.
- Для каждого положительного случая проверены конкретный retrieval candidate,
  evidence, predicate и итог общего `accept_mapping`; ожидания заданы отдельно
  от production-аннотаций. AD/LDAPS и LACP не получают новые API-доказательства.
- Сборщик каталога воспроизводим, не назначает review автоматически и сохраняет
  старый manifest, если staged-проверка не прошла.
- `reqmap knowledge validate`: успешно, 30/46/53.
- Scripted stdio MCP: 9 инструментов, 32 строки, 16 `supported` и
  16 `insufficient_evidence`, пять артефактов; перезапуск после первого разбиения
  и повторная финализация после перезапуска успешны. Session ID:
  `3cfae70d-0392-453d-ab3c-07e9372fbe1f`.

Полный прогон: **1446 passed**, три прежних предупреждения Python 3.14 о
`fork()` в многопоточном процессе. Команда:
`PYTHONPATH=src .venv/bin/python -m pytest -q`.
При первом прогоне выявлены устаревший счётчик evidence теста миграции и
ссылка на ещё не созданный отчёт; исправлены, повторный полный прогон успешен.
Независимое read-only ревью коммита `1097f10` завершено: Critical, Important
и Minor не обнаружены, решение **Ready to merge: Yes**. Рецензент отдельно
сверил версии, source hashes, endpoints и поле `name`; адресный прогон дал
**291 passed**. Повторная предметная оценка приватных XLSX и наследования
контекста между строками не выполнялась; эти свойства не заявляются данным
пакетом. Успех на стенде, live LLM/IDE, deep и остальные семейства остаются
за границами этой приёмки.
Scripted proposals проверяют backend/transport, а не качество выбора LLM.
Живой диалог VS Code, реальные OpenStack-вызовы и анализ пользовательских XLSX
этим пакетным прогоном не выполнялись. Последующий отдельный
[живой API-eval](2026-10-07-api-live-eval.md) завершил 25 строк / 26 атомов,
но выявил 17 необоснованных воздержаний из-за текста `role_ru`; это ограничение
предметной приёмки не отменяет приведённые выше автоматические проверки.

## Принятые границы и продолжение

Первый пакет ограничен API и полем `name` для изменений. Следующие пакеты:
остальные изменения ресурсов, identity/access, host/deployment, HA/migration,
monitoring и GUI с собственными источниками и контрастными проверками.
Этап 4 целиком и этап 5 живой предметной приёмки остаются незавершёнными.
Помощник плана не распознал русские заголовки этапов: чек-лист выполнен вручную
и сверяется с этим отчётом, чтобы не потерять обязательные проверки.
