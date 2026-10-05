# Приёмка агентного слоя reqmap

Дата проверки: 05.10.2026. Реализация выполнена от `b2d2e82` в
`feature/ide-agent-layer`, затем слита в `main` и опубликована.
Итоговый коммит кода и исправлений ревью:
[`cb2e21b66a4e4fcf3b14deaf80339bb0146f0f6e`](https://github.com/lebtmalorny-rgb/reqmap/commit/cb2e21b66a4e4fcf3b14deaf80339bb0146f0f6e).
MCP/backend — коммиты до `273da30`, руководство — `c544533`.

После восстановления связи 05.10.2026 проверены `git status --short --branch`
и `git ls-remote --exit-code origin refs/heads/main`: рабочая копия была чистой,
локальный и удалённый `main` совпадали с `cb2e21b`. Результаты полного suite
ниже относятся к приёмке этого кода; проверка публикации не является повторным
прогоном тестов или живого диалога в IDE.

## Подтверждённый результат

Reqmap предоставляет девять MCP tools без вызова LLM, сохраняет сессии,
проверяет предложения и экспортирует JSON/XLSX/Markdown/JSONL/manifest.
Существующий endpoint-based `reqmap analyze` сохранён. Runtime dependencies
остались `openpyxl` и `et-xmlfile`, MCP использует standard library.

| Проверка | Результат |
| --- | --- |
| macOS 26.6.2, arm64, Python 3.14.0: полный suite | 1051 passed, 3 существующих предупреждения fork, 74,99 s (после исправлений ревью) |
| Ссылки frozen eval | PASS; входят в финальный suite |
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

Python 3.11.13, Linux 6.12.54-linuxkit, aarch64: **1051 passed за 68,41 s** после исправлений ревью.
`pip check` и `compileall` завершились с кодом 0. Installed flow, process locks,
масштабный тест и universal-wheel audit входят в этот suite; внешняя сеть отключена.

## Реальные IDE и качество выбранной модели

| Маршрут | Состояние | Основание |
| --- | --- | --- |
| macOS VS Code + Codex | **not run** | VS Code 1.135.0, openai.chatgpt 26.930.21537. Повторная попытка 05.10 дошла до нового окна IDE, затем UI-доступ прервался с timeoutReached; запрос в чат не отправлен |
| PyCharm + Codex | **not run** | PyCharm не обнаружен в доступной установке |
| PyCharm + OpenCode ACP | **not run** | PyCharm и OpenCode не обнаружены |
| Linux native IDE chat | **not run** | Проверялся Linux backend в контейнере, GUI/авторизация клиента отсутствуют |

Codex CLI 0.160.0 обнаружен при исходной приёмке; наличие CLI не заменяет
приёмку встроенного чата. При исходной проверке настройки клиентов не менялись.
Для повторной попытки создан локальный `.codex/config.toml` проекта с сервером
reqmap и `config.agent.yaml` из примера. Глобальные настройки и авторизация
клиента не менялись, новые API-ключи не создавались. Эти локальные файлы
с абсолютными путями не входят в публикацию.
Готовность кода и установки не объявляется готовностью всех школьных IDE-маршрутов.

Повторная подготовка 05.10.2026 выявила старый установленный пакет в `.venv`:
он не распознавал команду `agent`, хотя исходники уже были обновлены.
Повторный `./install.sh` завершился успешно; `agent serve --help` и `pip check`
прошли. Процесс, запущенный по созданной MCP-конфигурации без `PYTHONPATH`,
ответил на `initialize` и `tools/list`: протокол `2025-11-25`, девять tools,
пустой stderr, код завершения 0. Это проверка установленного backend, а не
диалога с моделью.

При обновлении документации выполнены
`PYTHONPATH=src .venv/bin/python -m pytest tests/test_docs_language.py tests/test_agent_client_configs.py -q`:
**27 passed**. Дополнительно проверены 42 локальные ссылки и якоря в шести
изменённых документах. Полный suite повторно не запускался: код не менялся.

UI-инструмент сначала прочитал окно и открыл отдельное окно VS Code. Затем
чтение состояния, скриншот и повторное подключение после сброса UI-сессии
вернули `Computer Use server error -10005: timeoutReached`. Доступность skill
и tools внутри чата, модель, session_id анализа и хеши отчёта настоящего агента
не получены. Для продолжения требуется доступное окно проекта с панелью Codex.

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

## Независимое ревью и исправления

Свежий reviewer проверил `b2d2e82..3e43012`, самостоятельно запустил 71 focused
тест и нашёл два Important дефекта. Critical и Minor замечаний нет.
Оба исправлены с наблюдаемым RED → GREEN:

- При реальном завершении процесса временные файлы экспортёров оставались
  в staging и мешали повтору finalize. Восстановление теперь удаляет только
  обычные файлы с известными именами экспортёров; symlink, специальные и
  неизвестные файлы не допускаются. Проверены шесть точек os._exit: JSON,
  legacy/deep XLSX raw/verify, manifest.
- После finalize новые отклонённые submit-запросы меняли опубликованный хеш
  журнала. Теперь отказ сохраняется отдельным receipt без изменения событий
  закрытой сессии. Проверены atoms/mapping с текущей и устаревшей revision,
  повтор после restart и reuse request_id с другими аргументами.

Финальные полные suite: macOS **1051 passed**, Linux installed offline
**1051 passed**; повторное ревью не подменяет эти регрессионные проверки.

Принятые границы проверки и их последствия:

1. Реальные IDE остаются not run: школьные подключения требуют живой приёмки.
2. Качество выбранной модели не подтверждено: полноту атомов и false-supported
   ещё нужно измерить на frozen eval через настоящий клиент.
3. Тестируется смерть процесса, а не отключение питания/отказ диска:
   эмпирическая гарантия восстановления после аппаратного сбоя не заявляется.
4. Масштаб измерен с atoms и незавершённым mapping: задержка и память полного
   анализа 1950 строк пока не измерены.
