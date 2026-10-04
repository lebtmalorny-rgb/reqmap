# Использование reqmap через Codex, OpenCode и другие CLI

Codex, OpenCode, другой agent CLI и обычный shell используют один исполняемый файл `reqmap` и один предметный pipeline. Клиент только распознаёт запрос, читает repo-scoped skill и безопасно запускает команду. Он не выполняет собственное сопоставление компонентов и не дополняет canonical результат догадками.

Repo-scoped skill находится в `.agents/skills/reqmap/SKILL.md`. В нём нет отдельной таблицы соответствий, скрытых prompts или инфраструктурных команд.

## 1. Получение и установка

Клонируйте опубликованный репозиторий и установите CLI из включённых в поставку universal wheels:

```bash
git clone https://github.com/lebtmalorny-rgb/reqmap.git
cd reqmap
chmod +x install.sh
./install.sh
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
```

Для утверждённой автономной поставки проверенной платформой остаётся Linux с Python 3.11 или новее. Скрипт использует переносимые `none-any` wheels; текущий checkout также прошёл полный suite на macOS arm64 с Python 3.14, однако эта проверка не заменяет Linux acceptance, описанный в [README.md](README.md).

## 2. Обязательная модель

Agent CLI, через который пользователь общается с `reqmap`, не является моделью предметного pipeline. До запуска анализа нужен отдельный OpenAI-compatible endpoint, доступный с машины, где выполняется команда.

Создайте рабочую конфигурацию из примера:

```bash
cp config.example.yaml config.yaml
```

В `config.yaml` задайте:

- `model.base_url` — URL OpenAI-compatible API;
- `model.model` — точный ID модели, присутствующий в `GET /models`;
- `model.api_key_env` — имя переменной окружения с API-ключом, если он требуется.

Ключ передаётся только через окружение. Не записывайте его в YAML, prompt, аргументы команды или Git:

```bash
export REQMAP_API_KEY='<значение задаётся локально>'
```

Клиент должен поддерживать `GET /models` и `POST /chat/completions`. Если endpoint не настроен, `reqmap` завершит preflight с exit code `3` и создаст только диагностические `run.jsonl` и `manifest.json`. Это штатный fail-closed результат, а не разрешение выполнить сопоставление средствами agent CLI.

## 3. Прямой запуск в shell

Использование agent CLI необязательно. Для одного требования:

```bash
.venv/bin/reqmap analyze \
  --requirement 'ПВ должна поддерживать IP Multicast для IPv4 и IPv6' \
  --config "$PWD/config.yaml" \
  --output "$PWD/results/run-001"
```

Для XLSX передайте абсолютный путь к книге:

```bash
.venv/bin/reqmap analyze /absolute/path/to/requirements.xlsx \
  --config "$PWD/config.yaml" \
  --output "$PWD/results/run-002"
```

Для нескольких требований используйте повторяемый `--requirement` либо stdin. Не собирайте исходный текст через `eval`, command substitution или shell interpolation.

## 4. Codex CLI

Откройте корень checkout как workspace. Codex должен обнаружить `.agents/skills/reqmap/SKILL.md`; если skill не загрузился автоматически, явно укажите путь в запросе.

Готовый запрос для текста:

```text
Используй .agents/skills/reqmap/SKILL.md. Проанализируй через reqmap требование:
«ПВ должна поддерживать IP Multicast для IPv4 и IPv6».
Config: /absolute/path/to/reqmap/config.yaml
Output: /absolute/path/to/reqmap/results/dtn-05
Не выполняй собственное сопоставление и покажи status, exit code и только реально созданные артефакты.
```

Готовый запрос для книги:

```text
Используй .agents/skills/reqmap/SKILL.md и reqmap analyze.
Источник: /absolute/path/to/requirements.xlsx
Config: /absolute/path/to/reqmap/config.yaml
Output: /absolute/path/to/reqmap/results/xlsx-run
Не изменяй исходную книгу. После запуска покажи число требований, status, exit code и абсолютные пути артефактов.
```

Codex не должен пересказывать собственный mapping как второй независимый результат.

## 5. OpenCode

Откройте тот же корень репозитория. OpenCode использует общий skill из `.agents/skills`; команда и контракт результата не отличаются от Codex.

Если автоматическое обнаружение skill отсутствует, передайте клиенту такой запрос:

```text
Прочитай файл .agents/skills/reqmap/SKILL.md и строго следуй ему.
Запусти reqmap analyze для указанного требования или XLSX с явными --config и --output.
Не выполняй mapping самостоятельно и не запускай OpenStack, Ansible, Kolla-Ansible или MCP.
```

Затем добавьте к запросу исходный текст либо абсолютный путь к XLSX, config и отдельный output.

## 6. Любой другой agent CLI

Клиент подходит, если он умеет работать в checkout и запускать локальные subprocess. Автоматическая поддержка формата `.agents/skills` необязательна: достаточно явно поручить клиенту прочитать skill и вызвать публичный CLI.

Универсальный шаблон запроса:

```text
Работай из корня репозитория reqmap.
1. Прочитай .agents/skills/reqmap/SKILL.md.
2. Проверь .venv/bin/reqmap --version и knowledge validate.
3. Запусти только reqmap analyze с переданными input, --config и --output.
4. Не выполняй предметное сопоставление самостоятельно.
5. Сообщи exit code, run_status, число исходных требований, незавершённые IDs и абсолютные пути только реально созданных файлов.
6. Не запускай OpenStack API/CLI, Ansible, Kolla-Ansible, MCP или команды изменения инфраструктуры.
```

Если agent CLI не может запускать subprocess, используйте прямые shell-команды из раздела 3. Не создавайте второй агент или prompt с отдельной логикой сопоставления.

## 7. Что клиент обязан вернуть пользователю

После запуска клиент сообщает exit code, `run_status`, число исходных требований и абсолютные пути реально созданных файлов:

- `result.json`;
- `result.xlsx`;
- `report.md`;
- `run.jsonl`;
- `manifest.json`.

Основные exit codes:

| Код | Результат | Что сообщить |
| --- | --- | --- |
| `0` | `SUCCESS` | Анализ завершён; перечислить пять артефактов. |
| `2` | Ошибка input, CLI или config | Передать диагностику; анализ не считать начатым. |
| `3` | Preflight `FAILED` | Перечислить только фактически созданные diagnostic artifacts. |
| `4` | `PARTIAL` | Перечислить незавершённые requirement IDs из canonical результата. |
| `5` | Export/crosscheck `FAILED` | Не распространять отчёт как согласованный результат. |
| `6` | Analysis `FAILED` | Передать состояния требований и диагностику без успешного резюме. |

Ненулевой код нельзя маскировать успешным текстом. Старые файлы из повторно выбранного output нельзя объявлять результатом нового запуска.

## 8. Границы автоматизации

Клиент не запускает OpenStack API/CLI, Ansible, Kolla-Ansible, MCP или команды хостовой ОС. Implementation steps в результате являются описанием возможной реализации, а не разрешением на изменение инфраструктуры.

MCP не требуется для v1 и не является скрытым исполнителем анализа. Если позднее появится тонкий MCP adapter, он должен вызывать публичный API/CLI `reqmap` и не владеть отдельной предметной логикой.

При проблемах используйте [RUNBOOK.md](RUNBOOK.md) и [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Формат полей и cross-artifact contract описаны в [OUTPUT_SCHEMA.md](OUTPUT_SCHEMA.md).

## 8. Выбор deep-профиля

Профиль определяется рабочим config: отсутствие `analysis_profile` означает
`legacy`, явное `analysis_profile=deep` требует approved signed snapshot v2,
внешний `allowed_signers` и системный `ssh-keygen -Y`. Установленный repo-scoped
skill передаёт этот config в тот же CLI, не подменяя профиль при отказе preflight.
Production deep corpus в архитектурную поставку не входит.

Для deep передайте тот же запрос с путём к рабочему deep config. При exit code 4
агент сообщает не только незавершённые IDs, но и `procedure_gap`,
`evidence_conflict`, `responsibility_ambiguous`, `rollback_unverified`:
результат может быть частичным даже при завершённой обработке всех строк.
При коде 3 показываются только реально созданные диагностические файлы.
