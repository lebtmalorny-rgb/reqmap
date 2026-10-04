# Reqmap во встроенном чате IDE

Codex и OpenCode — **клиенты агента**, а provider и модель выбираются в клиенте.
В режиме `agent` reqmap не вызывает модель и не хранит её ключ: он предоставляет
локальные инструменты чтения, поиска, проверки предложений и экспорта.
Рабочий сценарий: чат → предложение модели → проверка reqmap → отчёт.
Пошаговая установка для Linux/macOS: [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md).

## Подготовка проекта

Установите reqmap через `./install.sh`, затем из корня проекта:

```bash
cp config.agent.example.yaml config.agent.yaml
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
pwd
```

В примерах [examples/ide](examples/ide) замените `/ABSOLUTE/PATH/TO/reqmap`
на вывод `pwd`. Пути — отдельные аргументы; `~`, `$HOME` и shell expressions
в конфигурации не подставляются. Для пути с пробелами сохраняйте кавычки JSON/TOML.
Примеры нужно объединять с существующими настройками, а не заменять ими весь файл.

`config.agent.yaml` задаёт разрешённый input_root, базу знаний и отдельные корни
сессий/отчётов. Относительные пути считаются от каталога config, не от cwd IDE.
Пример использует `legacy`; дополнительных model, base_url или api_key в нём нет.

## VS Code + Codex

Установите официальное расширение [Codex](https://learn.chatgpt.com/docs/codex/ide),
откройте панель Codex, войдите предложенным способом и выберите доступную модель.
Добавьте блок из [codex.config.toml](examples/ide/codex.config.toml)
в `~/.codex/config.toml` либо доверенную `.codex/config.toml` проекта.
CLI и расширение используют общую [настройку MCP](https://developers.openai.com/codex/mcp).
После изменения откройте новый чат. `.vscode/mcp.json` для этой инструкции не нужен.

Если Codex CLI уже установлен, аналогичный блок можно зарегистрировать командой
(выполняется в корне проекта):

```bash
codex mcp add reqmap -- "$PWD/.venv/bin/reqmap" agent serve --config "$PWD/config.agent.yaml"
codex mcp list
```

Команда и ручное добавление блока — альтернативы. Не создавайте две регистрации.

## PyCharm + Codex

В актуальном PyCharm с AI Assistant откройте AI Chat, установите/выберите Codex
в списке агентов и выполните вход. В Settings → Tools → AI Assistant →
Model Context Protocol (MCP) добавьте JSON из
[jetbrains-mcp.json](examples/ide/jetbrains-mcp.json).
Затем в AI Assistant → Agents включите **Pass custom MCP servers**.
Это [маршрут JetBrains для Codex](https://www.jetbrains.com/help/ai-assistant/codex-agent.html).
Для этого маршрута регистрируйте reqmap только в IDE, без второго блока Codex.
Интегрированный сервер **Tools → MCP Server** для reqmap включать не требуется.

## PyCharm + OpenCode через ACP

Установите OpenCode по [официальной инструкции](https://opencode.ai/docs/),
один раз запустите `opencode` в терминале, выберите provider, войдите и выберите
модель. Сам анализ будет идти в AI Chat. Узнайте путь командой `command -v opencode`.

Объедините [opencode.json](examples/ide/opencode.json) с `opencode.json` в корне
проекта. Он регистрирует reqmap непосредственно в OpenCode; формат описан в
[OpenCode MCP](https://opencode.ai/docs/mcp-servers/).
В AI Chat → Add Custom Agent откройте `~/.jetbrains/acp.json`, используйте
[jetbrains-acp.json](examples/ide/jetbrains-acp.json) и подставьте путь к OpenCode.
Пример предназначен для отдельной конфигурации: если там уже есть агенты,
согласуйте общие `default_mcp_settings`, чтобы не изменить их настройки случайно.

Здесь `use_custom_mcp=false`: reqmap приходит из OpenCode config, поэтому его
не нужно повторно добавлять в JetBrains MCP. После выбора OpenCode в AI Chat
общение идёт через [ACP](https://opencode.ai/docs/acp/).
Подключение custom agent описано в [JetBrains ACP](https://www.jetbrains.com/help/ai-assistant/acp.html).

Официальное расширение OpenCode для VS Code открывает терминальный TUI.
Это отдельный сценарий, он не считается встроенным чатом данной инструкции.
См. [OpenCode IDE](https://opencode.ai/docs/ide/).

## Проверка подключения и первый запрос

Откройте именно папку проекта. Попросите чат загрузить
`.agents/skills/reqmap/SKILL.md` и перечислить доступные reqmap-инструменты.
В Codex можно вызвать `$reqmap`; OpenCode поддерживает
[проектные skills из `.agents/skills`](https://opencode.ai/docs/skills/).
Должны быть доступны ровно девять инструментов сервера:

- `reqmap_start_session`, `reqmap_get_session`, `reqmap_submit_atoms`;
- `reqmap_get_atom_context`, `reqmap_search_knowledge`, `reqmap_get_evidence`;
- `reqmap_submit_mapping`, `reqmap_finalize`, `reqmap_get_result`.

Учебный запрос:

> Прочитай .agents/skills/reqmap/SKILL.md и используй режим агента через MCP.
> Проверь требование: «Создание виртуальной машины через Nova REST API».
> Сначала покажи атомы. Используй evidence из контекста reqmap. Заверши анализ
> через reqmap_finalize и покажи session_id, статус и ссылки на пять файлов.

После ответа проверьте `report.md` и таблицу `result.xlsx`. По умолчанию файлы
будут в `results/agent-reports/<UUID>/`. `result.json` содержит канонический
результат, `run.jsonl` — события проверки, `manifest.json` — контрольные суммы.
`analysis_origin.identity_verified=false`: имя клиента/модели заявлено клиентом,
а не аттестовано сервером. Воспроизводима проверка сохранённых предложений;
повторный разговор с моделью может дать иные предложения.

Если агент сообщает PARTIAL/FAILED, изучите незавершённые строки и diagnostics.
`allow_partial=true` разрешается только осознанно. При сбое не редактируйте файлы:
повторите исходный finalize с теми же request_id и аргументами.
Чтобы продолжить анализ в новом чате, передайте session_id и попросите
`reqmap_get_session`. Изменённый вход/профиль/уточнения — новая сессия.

## Отдельный прежний CLI-режим

`reqmap analyze` остаётся самостоятельным режимом с endpoint и моделью
из `config.yaml`; он описан в [RUNBOOK.md](RUNBOOK.md). Настройка модели клиента
не заполняет этот config. Не переключайтесь в CLI как скрытый fallback.

## Граница проверки

Примеры проверяются запуском stdio-процесса, включая пути с пробелами. Такая
проверка не подтверждает авторизацию клиента или работу конкретной версии IDE.
Версии и фактические результаты приёмки фиксируются отдельно. Для `analysis_profile=deep`
преподаватель предоставляет approved signed KBv2 и внешний trust config с `allowed_signers`;
синтетические fixtures из tests не являются production corpus.
