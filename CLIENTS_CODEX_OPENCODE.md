# Reqmap во встроенном чате IDE

Codex и OpenCode — **клиенты агента**, а provider и модель выбираются в клиенте.
В режиме `agent` reqmap не вызывает модель и не хранит её ключ: он предоставляет
локальные инструменты чтения, поиска, проверки предложений и экспорта.
Рабочий сценарий: чат → предложение модели → проверка reqmap → отчёт.
Пошаговая установка для Linux/macOS: [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md).

На 05.10.2026 код агентного слоя опубликован в `main`. Примеры подключения
проверены через stdio. В macOS VS Code + Codex подтверждены вызовы всех девяти
tools и полный цикл одного анализа; предметный статус контрольного примера
не совпал с ожиданием. [Живой прогон](docs/acceptance/2026-10-05-vscode-codex-live.md)
фиксирует запрос и результат. PyCharm и Linux IDE ещё не проверены;
общая матрица находится в [отчёте приёмки](docs/acceptance/reqmap-ide-agent-layer.md).

## Подготовка проекта

Установите reqmap через `./install.sh`, затем из корня проекта:

```bash
cp config.agent.example.yaml config.agent.yaml
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
pwd
```

Копируйте пример только при отсутствии своего `config.agent.yaml`.
Для существующей установки сначала выполните
[обновление пакета](BEGINNER_GUIDE.md#обновить-установленную-копию).

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
CLI и расширение используют общую [настройку MCP](https://learn.chatgpt.com/docs/extend/mcp).
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
> через reqmap_finalize, проверь reqmap_get_result и покажи session_id, статус
> и ссылки на пять файлов.

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
`reqmap_get_session`. У сессии со статусом `finalized` получите `reqmap_get_result`;
новые предложения она не принимает. Изменённый вход/профиль/уточнения — новая сессия.

## Контракт предложений после обновления

Имена девяти tools сохранены; предложения теперь имеют
`proposal_schema_version=2`. Для atoms используйте
`decomposition_context.canonical_proposal`, включая позиции цитат и обязательность.
Неизвестный текст сохраняется одним полным атомом. Mapping выбирает
`obligation_id` и `predicate_ids` из текущего контекста. Отсутствие predicates
означает пробел в доказательстве, а не проблему подключения MCP.

Для legacy `role_ru` описывает доказанную возможность, `action_ru` — доказанный
шаг. Правила mapping 1.3 рекомендуют подходящий `claim_ru` выбранного specific
evidence: каждое значимое слово описания проверяется по evidence/capability.
Не копируйте нормативную формулировку требования в роль. Полный исходный текст,
условия и отрицания сохраняются в `source_quote`; исправление роли не изменяет
обязательство и не заменяет проверку предиката. В proposal `supported_aspects`
совпадают с выбранными ролями, а в итоговом отчёте их вычисляет backend из цитат.
При `MAPPING_ROLE_UNGROUNDED` / `MAPPING_STEP_UNGROUNDED` проверьте `field` и
слова в `message_ru`, затем при необходимости отправьте исправленное описание
с новым `request_id` и текущей revision. Сервер сам текст не переписывает.

Обновление binding engine с 1.0 на 1.1 требует новой active-сессии; прежние
готовые отчёты остаются историческими. После установки обновления перезапустите
локальный MCP-процесс клиента, чтобы он загрузил новые правила.

Если старая active-сессия отвечает `SESSION_CONTRACT_MISMATCH`, начните новый
анализ исходного текста. Старые finalized-отчёты доступны через get_result
как исторические. При обычном обрыве связи продолжайте с прежним session_id;
не повторяйте уже принятые операции с новыми request_id.

## Отдельный прежний CLI-режим

`reqmap analyze` остаётся самостоятельным режимом с endpoint и моделью
из `config.yaml`; он описан в [RUNBOOK.md](RUNBOOK.md). Настройка модели клиента
не заполняет этот config. Не переключайтесь в CLI как скрытый fallback.

## Граница проверки

Примеры проверяются запуском stdio-процесса, включая пути с пробелами. Такая
проверка не подтверждает авторизацию клиента или работу конкретной версии IDE.
Для живой проверки запишите ОС, версии IDE и клиента, выбранные provider/model,
видимость skill и девяти tools, точный запрос, session_id, итоговый статус и
контрольные суммы артефактов из manifest. Чтение через `reqmap_get_result`
должно подтвердить целостность файлов. Текстовый ответ без вызовов MCP этого
не подтверждает.

Один учебный запрос проверяет подключение. Для оценки качества модели нужен
весь [контрольный набор](tests/fixtures/agent_eval.json): несколько обязательств,
неоднозначность, отрицательное evidence, конфликт и инструкция внутри входа.
Порядок фиксации результатов указан в [отчёте приёмки](docs/acceptance/reqmap-ide-agent-layer.md).
Для `analysis_profile=deep`
преподаватель предоставляет approved signed KBv2 и внешний trust config с `allowed_signers`;
синтетические fixtures из tests не являются production corpus.


## Reviewed source context: контракт 3.0

Клиент продолжает использовать девять прежних инструментов. Карту выбирает
сопровождающий через `source_context_path` в agent-конфигурации; поля карты в
`reqmap_start_session` и proposal не принимаются. Передаваемые parent/hints и
clarifications не утверждают независимость строки. Используйте полные canonical
atoms и актуальный `context_id`; учитывайте `source_binding.context_decision`
и effective obligations в `reqmap_get_atom_context`.

Без reviewed-карты backend вернёт `SOURCE_CONTEXT_UNREVIEWED`. Заголовок GUI не
доказывается API evidence, а source refs не являются evidence implementation.
При перезапуске продолжайте ту же frozen session; обновлённая карта требует нового
start. Старые active contracts несовместимы, historical finalized остаются читаемыми.
[Инструкция оператора](RUNBOOK.md#проверенный-контекст-исходных-строк) и
[статус новой приёмки](docs/acceptance/2026-10-08-source-context-runtime.md).
Прежний живой Keystone-eval проверял предыдущий контракт; он не доказывает новый3.0.
