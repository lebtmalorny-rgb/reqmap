# reqmap

`reqmap` — автономный CLI-агент для проверяемого сопоставления русскоязычных требований с компонентами OpenStack Epoxy 2025.1. Он сохраняет исходные строки, разбивает составные формулировки на атомарные утверждения, строит many-to-many mappings только по локальному evidence и выпускает согласованные JSON, XLSX, Markdown, журнал и manifest.

## Агент в чате IDE

Codex/OpenCode — клиенты агента; анализирует выбранная в клиенте модель.
Reqmap предоставляет девять MCP-инструментов и проверяет её предложения без
второго endpoint/API-ключа. Диалог доступен через VS Code + Codex,
PyCharm + Codex и PyCharm + OpenCode ACP.

Начните с [инструкции для школьников](BEGINNER_GUIDE.md), затем
[подключите чат](CLIENTS_CODEX_OPENCODE.md). После установки:

```bash
cp config.agent.example.yaml config.agent.yaml
.venv/bin/reqmap agent serve --config config.agent.yaml
```

Последняя команда запускает stdio-сервер; обычно её запускает сам клиент IDE.
Вручную он ждёт JSON-RPC на stdin, а не печатает приглашение для разговора.
Сессии сохраняются локально; финализация публикует проверенные пять файлов.
Готовность конкретных IDE проверяется отдельно от автоматических тестов сервера.

## Границы v1

Предметный pipeline использует локальный snapshot базы знаний и настроенный OpenAI-compatible LLM endpoint. Штатный анализ не требует PyPI, web UI, Docker или Podman. `reqmap` не выполняет OpenStack API, команды Ansible, Kolla-Ansible или MCP: runtime и design-time шаги описываются как результат анализа, но не применяются к инфраструктуре.

Поддерживается только релиз OpenStack Epoxy 2025.1. Отсутствие evidence не превращается автоматически в `not_supported`; такой случай получает `insufficient_evidence`. Ручные пометки из XLSX сохраняются как `source_hints`, но доказательствами не считаются.

## Прежний CLI: быстрый старт

Впервые работаете с программой? Начните с [пошаговой инструкции для начинающих](BEGINNER_GUIDE.md):
Linux и macOS, открытие проекта в VS Code или PyCharm, установка, настройка модели и первый отчёт.

Для Linux и macOS требуются Python 3.11 или новее и Bash. После получения полного репозитория установка выполняется без доступа к Интернету:

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

- [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md) — установка и запуск в VS Code или PyCharm на Linux и macOS простыми словами;
- [INSTALL_OFFLINE.md](INSTALL_OFFLINE.md) — подготовка bundle, перенос и установка в изолированной зоне;
- [RUNBOOK.md](RUNBOOK.md) — запуск, preflight, resume, backup и диагностика;
- [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) — устройство и сопровождение evidence snapshot;
- [OUTPUT_SCHEMA.md](OUTPUT_SCHEMA.md) — JSON, XLSX, enums и cross-artifact правила;
- [CLIENTS_CODEX_OPENCODE.md](CLIENTS_CODEX_OPENCODE.md) — работа через Codex, OpenCode, другие agent CLI и прямой shell;
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — причины отказов и проверяемые действия.

Архитектурные решения и детальный implementation plan находятся в `docs/superpowers/`. Для обычной установки и анализа эти материалы не требуются.

## Расширенный профиль deep

Старые конфигурации сохраняют `analysis_profile=legacy` и output schema `1.0`.
Явный `analysis_profile=deep` включает schema `2.0`: отдельные записи
`openstack_runtime`, `kolla_ansible`, `host_os`, ссылки между исполнителем и
объектом изменения, графы процедур и диагностику пробелов.

Для deep нужны Python 3.11+, системный `ssh-keygen` с поддержкой `-Y`,
knowledge schema v2 со статусом `approved`, подписанный manifest и доверенный
файл `allowed_signers` вне snapshot. Пример конфигурации —
`config.deep.example.yaml`; указанная там production-база **не поставляется**
этим архитектурным этапом. Подписанные тестовые snapshots создаются только
временными fixtures. Нельзя выдавать synthetic gold set за подтверждение
возможностей реального OpenStack или качества выбранной модели.

Базовый профиль — OpenStack/Kolla-Ansible 2025.1 и Rocky Linux 9.
Цель `2026.1` допустима только для upgrade. Анализ не открывает URL источников;
единственная сетевая зависимость — настроенный endpoint модели.

В обоих профилях сохраняются пять файлов результата. Deep XLSX содержит семь
листов; `supported` описывает подтверждённую функцию и не отменяет
`procedure_gap` или `rollback_unverified`. Такие пробелы делают запуск `PARTIAL`.
Недостаток evidence для обязательного атома также даёт `PARTIAL`, даже если
обработка всех требований завершена.
Команды и интерпретация результата приведены в [RUNBOOK.md](RUNBOOK.md),
обслуживание подписанной базы — в [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md).

## Приёмка архитектуры deep — 04.10.2026

Чистый source archive установлен в Rocky Linux 9.8 (`aarch64`) с Python
3.11.13 и OpenSSH 9.9p1. При установке и проверках использовался контейнер
`--network none`; подготовка системных пакетов образа выполнялась заранее.
Installer использовал 12 wheels из `vendor/wheels`. Результат полного suite:
**933 passed**, без skips. `compileall`, `pip check`, legacy KB validation,
миграция полной v1 базы в unsigned draft и проверка подписанного v2 snapshot
завершились успешно.

SHA-256 manifest минимального synthetic snapshot:
`3c49180ab934f8dbb447a8a5f240bbcfc2a891327e2af8241ec23df5c52a080b`.
Это fingerprint тестовой базы, не production snapshot. Приватный ключ
генерировался внутри временного контейнера и в поставку не включён.

Frozen gold содержит 19 сценариев: исходные 16, прямой negative evidence,
запрещённый runtime scope 2026.1 и непроверенный rollback. Проверены буквальные
цитаты атомов, contours, executor/target, версии, gaps, отсутствие ложного
`supported`, согласованность артефактов, resume и отказ до model preflight при
изменении или удалении каждого файла из подписанного manifest. Fake HTTP LLM
разрешены только `GET /v1/models` и `POST /v1/chat/completions` на loopback.

На macOS arm64 с Python 3.14.0 тот же suite дал 933 passed и три предупреждения
stdlib о `fork()` в тестах FIFO. Эти результаты подтверждают программные
контракты. Проверка реальной LLM и предметное наполнение OpenStack/Kolla/Rocky
остаются отдельными этапами; HA, migration и upgrade runbooks не поставлены.
