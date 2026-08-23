# Использование reqmap через Codex и OpenCode

Codex, OpenCode и обычный shell используют один CLI и один предметный pipeline. Repo-scoped skill находится в `.agents/skills/reqmap/SKILL.md`; в нём нет таблиц соответствий компонентов, отдельных prompts анализа или инфраструктурных команд.

## Обязанности клиента

Клиент распознаёт запрос на анализ требований, загружает skill, безопасно передаёт исходный текст и запускает `reqmap analyze`. Он не выполняет собственное сопоставление компонентов и не дополняет canonical результат догадками.

После завершения клиент сообщает `run_status`, число исходных требований, exit code и абсолютные пути к `result.json`, `result.xlsx`, `report.md`, `run.jsonl`, `manifest.json`. При `PARTIAL` дополнительно перечисляются IDs незавершённых требований из canonical JSON. Ненулевой код нельзя маскировать успешным текстом.

Клиент не запускает OpenStack API/CLI, Ansible, Kolla-Ansible или команды хостовой ОС. Implementation steps в результате являются описанием возможной реализации, а не разрешением на изменение инфраструктуры.

## Codex

Откройте репозиторий как workspace и убедитесь, что `.agents/skills/reqmap/SKILL.md` доступен в checkout. Запрос должен явно содержать текст либо абсолютный путь к XLSX, существующий config и отдельный output.

Для вставленного текста предпочтительны stdin или аргументы subprocess без shell interpolation:

```bash
.venv/bin/reqmap analyze \
  --requirement 'Синтетическое требование к ресурсу' \
  --config /absolute/path/to/config.yaml \
  --output /absolute/path/to/results/run-001
```

Codex должен показать пользователю фактически созданные файлы и не повторять предметный mapping своими словами как независимый вывод.

## OpenCode

OpenCode открывает тот же корень репозитория и обнаруживает общий skill в `.agents/skills`. Команда и контракт результата не отличаются от Codex. Если клиент не обнаружил skill автоматически, укажите путь к файлу в workspace и выполните прямую CLI-команду; не создавайте второй агент с отдельной логикой.

Пример XLSX:

```bash
.venv/bin/reqmap analyze /absolute/path/to/requirements.xlsx \
  --config /absolute/path/to/config.yaml \
  --output /absolute/path/to/results/run-002
```

## Прямой shell

Использование клиента не обязательно. Для проверки интерфейса выполните:

```bash
.venv/bin/reqmap --version
.venv/bin/reqmap analyze --help
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
```

Прямой запуск, Codex и OpenCode должны давать один и тот же schema contract при одинаковых input/config/model responses.

## MCP

MCP не требуется для v1 и не является скрытым исполнителем анализа. MCP server, web UI и инфраструктурные tools исключены из текущей поставки. Если позднее появится тонкий MCP adapter, он обязан вызывать публичный reqmap API/CLI и не владеть отдельной предметной логикой.

При проблемах с exit codes и артефактами используйте [RUNBOOK.md](RUNBOOK.md) и [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Формат полей описан в [OUTPUT_SCHEMA.md](OUTPUT_SCHEMA.md).
