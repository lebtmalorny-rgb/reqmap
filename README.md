# reqmap

`reqmap` — автономный CLI-агент для проверяемого сопоставления русскоязычных требований с компонентами OpenStack Epoxy 2025.1. Он сохраняет исходные строки, разбивает составные формулировки на атомарные утверждения, строит many-to-many mappings только по локальному evidence и выпускает согласованные JSON, XLSX, Markdown, журнал и manifest.

## Границы v1

Предметный pipeline использует локальный snapshot базы знаний и настроенный OpenAI-compatible LLM endpoint. Штатный анализ не требует PyPI, web UI, Docker или Podman. `reqmap` не выполняет OpenStack API, команды Ansible, Kolla-Ansible или MCP: runtime и design-time шаги описываются как результат анализа, но не применяются к инфраструктуре.

Поддерживается только релиз OpenStack Epoxy 2025.1. Отсутствие evidence не превращается автоматически в `not_supported`; такой случай получает `insufficient_evidence`. Ручные пометки из XLSX сохраняются как `source_hints`, но доказательствами не считаются.

## Быстрый старт

Требуется Linux и Python 3.11 или новее. После получения полного репозитория установка выполняется без доступа к Интернету:

```bash
chmod +x install.sh
./install.sh
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
```

Скопируйте `config.example.yaml` в отдельный рабочий файл и задайте параметры локальной модели. Если endpoint требует ключ, экспортируйте переменную, названную в `api_key_env`; значение ключа в конфигурационный файл не записывается.

Один текст через stdin:

```bash
printf '%s\n' 'Предоставить синтетическую функцию управления ресурсом' | \
  .venv/bin/reqmap analyze --stdin \
  --config config.yaml \
  --output results/text-run
```

Несколько требований без shell interpolation передавайте отдельными аргументами:

```bash
.venv/bin/reqmap analyze \
  --requirement 'Создать тестовый ресурс через API' \
  --requirement 'Подключить тестовую сеть к ресурсу' \
  --config config.yaml \
  --output results/list-run
```

XLSX открывается только для чтения, а SHA-256 считается по тому же byte snapshot, который разбирает importer:

```bash
.venv/bin/reqmap analyze requirements.xlsx \
  --config config.yaml \
  --output results/xlsx-run
```

## Результаты

После успешного или частичного анализа каталог содержит пять опубликованных артефактов:

- `result.json` — канонический `RunResult`;
- `result.xlsx` — пять русских листов для инженерной работы;
- `report.md` — русское резюме и перечни проблемных требований;
- `run.jsonl` — безопасный журнал текущего запуска;
- `manifest.json` — параметры воспроизводимости и SHA-256 артефактов.

CLI печатает абсолютный путь и SHA-256 каждого созданного файла. При непройденном preflight публикуются только `run.jsonl` и `manifest.json`; если output нельзя безопасно очистить, старые файлы не объявляются результатами нового запуска. Значения exit codes и порядок действий оператора описаны в [RUNBOOK.md](RUNBOOK.md).

## Проверенная поставка v1

На 23.08.2026 подтверждена версия `reqmap 0.1.0` на Debian GNU/Linux 13 (trixie), `arm64`, Python 3.11.15. Чистый снимок репозитория был установлен при отключённой внешней сети; installer использовал 12 файлов только из `vendor/wheels`. Затем выполнены команды:

```bash
./install.sh .linux-venv
.linux-venv/bin/python -m pytest -q
.linux-venv/bin/python -m compileall -q src tools tests
```

Результат Linux acceptance: `421 passed`, `compileall` завершён с кодом 0. Проверенный snapshot базы знаний содержит 30 компонентов, 30 capabilities и 37 evidence records; SHA-256: `55a37d7e2dd99f81cf4f6821848396bc055eac859f8aa29415ebe78b004d7994`.

Для воспроизводимой проверки Linux использовалась изолированная контейнерная лаборатория с отключённой сетью; Docker не является зависимостью установки или запуска `reqmap`. Suite подтверждает локальные контракты, fail-closed правила, HTTP-протокол fake LLM и согласованность артефактов. Он не доказывает качество выбранной локальной LLM на реальных требованиях, доступность конкретного пользовательского endpoint, полноту KB для конкретного продуктового профиля или выполнение описанных действий в реальном OpenStack/Kolla окружении.

## Документация

- [INSTALL_OFFLINE.md](INSTALL_OFFLINE.md) — подготовка bundle, перенос и установка в изолированной зоне;
- [RUNBOOK.md](RUNBOOK.md) — запуск, preflight, resume, backup и диагностика;
- [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) — устройство и сопровождение evidence snapshot;
- [OUTPUT_SCHEMA.md](OUTPUT_SCHEMA.md) — JSON, XLSX, enums и cross-artifact правила;
- [CLIENTS_CODEX_OPENCODE.md](CLIENTS_CODEX_OPENCODE.md) — работа через Codex, OpenCode, другие agent CLI и прямой shell;
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — причины отказов и проверяемые действия.

Архитектурные решения и детальный implementation plan находятся в `docs/superpowers/`. Для обычной установки и анализа эти материалы не требуются.
