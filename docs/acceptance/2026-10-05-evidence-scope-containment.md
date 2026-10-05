# Первый этап исправления доказательности — 05.10.2026

Закрыт воспроизведённый путь ложного подтверждения через общие описания сервисов.
Все 22 специально составленных неверных proposals из
[аудита](2026-10-05-requirements-coverage-audit.md) теперь получают
`insufficient_evidence`. Контекст остаётся доступен поиску и агенту.

Это защитное исправление первого этапа. Оно не доказывает полное соответствие
любого specific evidence всем условиям произвольного требования. Дизайн такой
проверки, пополнение базы, импорт второго формата XLSX и новая предметная
IDE-приёмка остаются этапами 2–5
[плана](../superpowers/plans/2026-10-05-reqmap-coverage-remediation.md).

## Изменение поведения

- В Evidence добавлен `claim_scope=context|specific`. Отсутствующее поле
  означает `context`, неверное значение отклоняется.
- Общая справка не может подтверждать `supported`, `partial`, `not_supported`
  или version conflict. Причина понижения — `EVIDENCE_CONTEXT_ONLY`.
- При совместном цитировании context и specific records слова общей справки
  не участвуют в доказательстве операции, роли и шага.
- Конкретные официальные evidence продолжают проходить существующие проверки
  версии, принадлежности, polarity, strength и grounding.
- Классификацией управляет snapshot, proposal агента её не переопределяет.

В поставляемой базе сохранены те же 37 claims и sources: 30 записей context,
7 specific. Общие записи 22 сервисов и факт наличия sysctl role являются
context, как и 7 project-policy records. Остальные официальные записи остаются
specific в пределах ранее проверенных утверждений. Новых возможностей база
на этом этапе не получила.

Новый snapshot SHA-256:
`2d4d1440c32e827bcd1cc1b192d5ab88c1eee1350eab93431135d2fde406b92a`.

## Проверки

Исходный baseline: **1051 passed** на macOS, Python 3.14.0.
Полный suite после изменения: **1091 passed**, 74.32 s, без skips.
Сначала 35 новых тестов дали ожидаемые отказы; после первого изменения прошли.
Отдельный тест смешивания context и specific evidence воспроизвёл ещё один
ложный `supported` и прошёл после исключения справочного корпуса.

Новый набор `tests/test_evidence_scope.py` включает 40 проверок: 22 сервиса,
недоказанное время операции, explicit/default scope, неверные значения,
отрицательное evidence, прямой validator, положительный specific-контроль,
смешивание записей и полный цикл AgentService.

Последний сценарий проверяет submit, восстановление после перезапуска,
finalize, get_result и пять артефактов. В JSON и XLSX проверяется
`insufficient_evidence`, в Markdown — конкретная диагностика.
Legacy `run_status=SUCCESS` по-прежнему обозначает завершение обработки,
а не подтверждение требования.

Команды проверки:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python tools/kb/build_snapshot.py knowledge/epoxy-2025.1
./install.sh
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
.venv/bin/python -m pip check
```

Три предупреждения Python 3.14 о fork в многопоточном процессе наблюдались
и на исходном baseline; они относятся к тестам подмены FIFO.
Scripted MCP/CLI-проверки не считаются новой приёмкой настоящего агента.

После offline-переустановки выполнен отдельный MCP stdio-запуск установленного
`.venv/bin/reqmap` без `PYTHONPATH`. Вызваны все девять tools, включая чтение
evidence с `claim_scope=context`. Неверное Nova proposal получило
`insufficient_evidence` и `EVIDENCE_CONTEXT_ONLY`, затем созданы пять файлов.
Сессия: `61dcf9f5-5264-4c88-a2a9-1fdd43005bcc`.
Все хеши пересчитаны, четыре артефакта сверены с manifest.
`knowledge validate` и `pip check` прошли.

| Файл | SHA-256 установленного MCP-запуска |
| --- | --- |
| `result.json` | `8a3d0c7df9a69e6f3b48767fca33d892aceb7dfd1b46cbdc4eda5817440fd52b` |
| `result.xlsx` | `af0afbd97890f80bc3f1828db556289270ca492ad2983bc8b8c05f26d3e50950` |
| `report.md` | `0c568eed43e8d78184da7831fe8dbfd756fadbac45b4a68a4b9542ff927e308a` |
| `run.jsonl` | `1ff9c9232a8d51750c6d82c9a5b25fcd614fe519752fa93353dc3643808fd95e` |
| `manifest.json` | `d82f7d1ef444e737067f1a316650802dbec6112305181ad4913850165b847d8c` |

## Совместимость и контрольные сценарии

Обновление требует установки нового пакета **до использования новой базы**.
Прежний строгий loader не понимает поле `claim_scope`. Старые snapshots новый
loader читает, но неразмеченные записи становятся context-only. Повышение
scope требует предметного review, а не массовой замены значения.

Legacy mapping prompt имеет версию `1.1`: изменяются подписи CLI resume и
MCP context IDs. Изменение snapshot hash требует новой сессии. Старые
финализированные результаты не являются результатами повторной проверки.
Поле `claim_scope` добавлено в Evidence JSON schema `1.0` и MCP payload;
строгим потребителям нужно разрешить это поле. Deep schema не менялась.

`MULTI` и `INJECTION` в frozen eval теперь явно обозначают baseline общей
базы и ожидают `insufficient_evidence`. Исторический результат живого запуска
05.10.2026 не переписан и не объявляется прошедшей новой приёмкой.
Положительный operation-backed сценарий требует отдельно проверенного
детального evidence в рамках следующего этапа наполнения KB.

Точный контракт изменения — в
[спецификации](../superpowers/specs/2026-10-05-evidence-scope-containment.md).

## Итог независимого ревью

Проверен диапазон `9fa4191..c98b02e`: Critical и Important замечаний нет,
первый этап готов к слиянию. Reviewer отдельно выполнил 70 focused tests и
проверил неизменность 37 claims, классификацию 30/7, фильтрацию смешанных
evidence, смену context IDs и проверки snapshot при восстановлении сессии.

Отложено одно Minor замечание: спецификация формулирует диагностику
context-only шире реализации. `EVIDENCE_CONTEXT_ONLY` появляется при понижении
заявленного `supported`, `partial` или `not_supported`; если proposal сразу
содержит `insufficient_evidence`, отдельной диагностики может не быть.
Ложного подтверждения это не допускает. Унификацию диагностики следует
включить в контракт второго этапа.

Reviewer также воспроизвёл существующее ограничение этапа 2 на synthetic
specific evidence: штатная операция `POST /servers` может подтвердить атом
с иным действием, объектом, отрицанием, временем или требованием GUI, поскольку
старый grounding ещё не связывает все исходные обязательства с claim.
Это не закрыто защитой от context-only evidence и остаётся отдельным дефектом.

Ревью не переоценивало внешние источники семи specific claims и не выполняло
новую живую IDE-приёмку. Полный suite и установленный MCP проверены автором
изменения, как описано выше; reviewer эти два запуска не повторял.
