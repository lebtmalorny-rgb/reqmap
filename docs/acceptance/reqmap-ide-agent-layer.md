# Приёмка агентного слоя reqmap

Дата проверки: 05.10.2026. Реализация выполнена в `feature/ide-agent-layer`
от `b2d2e82`; MCP/backend — коммиты до `273da30`, руководство — `c544533`.
Точный состав последующих исправлений определяется историей этой ветки.

## Подтверждённый результат

Reqmap предоставляет девять MCP tools без вызова LLM, сохраняет сессии,
проверяет предложения и экспортирует JSON/XLSX/Markdown/JSONL/manifest.
Существующий endpoint-based `reqmap analyze` сохранён. Runtime dependencies
остались `openpyxl` и `et-xmlfile`, MCP использует standard library.

| Проверка | Результат |
| --- | --- |
| macOS 26.6.2, arm64, Python 3.14.0: полный suite | 1040 passed, 3 существующих предупреждения fork, 69,54 s |
| Добавленная затем проверка ссылок frozen eval | 1 passed; исправлены ошибочные IDs в тестовом наборе |
| Offline-копия macOS с пробелами в пути | install.sh, --version, knowledge validate, agent serve --help, EOF, pip check — PASS |
| Установленный пакет без dev PYTHONPATH | Полный scripted MCP flow из отдельной offline-копии — PASS |
| Текст/TXT/XLSX × legacy/deep | 6 subprocess flows — PASS; запрет constructor модели и socket connect, restart, rejected mapping, повтор finalize |
| Три примера MCP-конфигурации | Реальный stdio-процесс, девять tools, пути с пробелами — PASS |
| Skill: baseline и применение | Старый skill не обеспечивал MCP workflow; новый проходит dry-run сценарии последовательности, двух отказов, partial и injection |

Полный suite на macOS запускался как:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Три предупреждения относятся к существующим FIFO/fork-тестам на Python 3.14;
ошибок тестов нет. Subprocess guard проверяет отсутствие обращений backend к
модели/сети. Он не ограничивает и не проверяет сеть выбранного клиента IDE.

## Масштаб

Отдельный `tests/test_agent_scale.py` импортирует 1950 требований, принимает
по одному атому на каждую строку, заново открывает сервис, финализирует и читает
все страницы результата без потерь/повторов. Итог FAILED ожидаем: mapping ещё
не выполнен, все строки и 1950 атомов сохранены с null support_status.

macOS: 46,740 s весь сценарий, 11,438 s финализация, peak RSS процесса теста
142,67 MiB. Это стоимость backend для данного сценария, не скорость/качество
модели и не benchmark 1950 завершённых сопоставлений. Timeout примеров Codex
и OpenCode — 300 s с запасом к измеренной финализации.

## Linux offline

Используется локальный образ `reqmap-acceptance:rocky9-py311`,
SHA-256 `d2ad80b3dd07538fe40abc4f3d75ad82da087740338905c25e8a62af2c6ded3c`.
Контейнер запускается с `--network none`, source-копия монтируется read-only,
распаковывается в отдельный каталог; `PYTHONPATH` удалён.

```bash
PYTHON_BIN=python3.11 ./install.sh
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tools tests
```

Python 3.11.13, Linux 6.12.54-linuxkit, aarch64: **1041 passed за 65,29 s**.
`pip check` и `compileall` завершились с кодом 0. Installed flow, process locks,
масштабный тест и universal-wheel audit входят в этот suite; внешняя сеть отключена.

## Реальные IDE и качество выбранной модели

| Маршрут | Состояние | Основание |
| --- | --- | --- |
| macOS VS Code + Codex | **not run** | Расширение openai.chatgpt 26.930.21537 обнаружено; попытка UI-доступа закончилась noWindowsAvailable, полного диалога нет |
| PyCharm + Codex | **not run** | PyCharm не обнаружен в доступной установке |
| PyCharm + OpenCode ACP | **not run** | PyCharm и OpenCode не обнаружены |
| Linux native IDE chat | **not run** | Проверялся Linux backend в контейнере, GUI/авторизация клиента отсутствуют |

Codex CLI 0.160.0 обнаружен; наличие CLI не заменяет приёмку встроенного чата.
Новые API-ключи не создавались, пользовательские настройки клиентов не менялись.
Готовность кода и установки не объявляется готовностью всех школьных IDE-маршрутов.

Frozen набор: [agent_eval.json](../../tests/fixtures/agent_eval.json) — несколько
обязательств, неоднозначность, negative evidence, deep conflict и prompt injection.
Его IDs сверены с shipped legacy KB и подписанной synthetic deep gold KB.
**Настоящий клиент не прогнан: zero false-supported и полнота выделения
обязательств выбранной моделью пока не подтверждены.** Scripted proposer и
проверка skill по текстовому сценарию не являются такой оценкой.

Для завершения каждой строки IDE-приёмки нужно записать ОС/IDE/client versions,
provider/model label, видимость skill и tools, точный prompt, session_id,
artifact hashes, пропущенные обязательства и false-supported. Для ambiguity
проверяется уточняющий вопрос; для conflict/negative — сохранение отрицательных
доказательств; для injection — отсутствие выполнения вложенных инструкций.
Deep fixtures не являются production OpenStack corpus.
