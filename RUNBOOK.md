# Руководство оператора reqmap

Этот runbook описывает штатную проверку, запуск, возобновление и разбор результатов. Команды выполняются из корня проверенного checkout. Установка и перенос рассматриваются отдельно в [INSTALL_OFFLINE.md](INSTALL_OFFLINE.md).

## 1. Предсменная проверка

Убедитесь, что CLI и локальная база знаний доступны:

```bash
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
```

Проверка snapshot не обращается к URL из provenance manifest. Затем проверьте `config.yaml`: `base_url` должен указывать на разрешённый локальный OpenAI-compatible endpoint, `model` — на фактически загруженную модель, `knowledge_path` — на snapshot OpenStack Epoxy 2025.1. API key передавайте только через переменную из `api_key_env`.

Команда `analyze` сама выполняет preflight до предметных вызовов модели. Preflight проверяет конфигурацию, output path, integrity базы знаний и доступность модели. При отказе предметный анализ не начинается.

## 2. Выбор input mode

Ровно один источник обязателен для каждого запуска.

XLSX:

```bash
.venv/bin/reqmap analyze requirements.xlsx \
  --config config.yaml \
  --output results/run-001
```

Несколько строк stdin, одна непустая строка — одно требование:

```bash
.venv/bin/reqmap analyze --stdin --text-mode lines \
  --config config.yaml \
  --output results/run-002 < requirements.txt
```

Один многострочный текст stdin:

```bash
.venv/bin/reqmap analyze --stdin --text-mode single \
  --config config.yaml \
  --output results/run-003 < requirement.txt
```

Повторяемый аргумент подходит для заранее сформированного массива строк. Не собирайте requirement text через `eval`, command substitution или конкатенацию shell-кода.

```bash
.venv/bin/reqmap analyze \
  --requirement 'Синтетическое требование к API ресурса' \
  --requirement 'Синтетическое требование к подключению сети' \
  --config config.yaml \
  --output results/run-004
```

## 3. Exit codes

| Код | Значение | Действие оператора |
| --- | --- | --- |
| `0` | `SUCCESS` | Проверить hashes, сохранить пять артефактов и передать результат потребителю. |
| `2` | Ошибка usage или config | Исправить источник, параметры CLI или `config.yaml`; анализ не считать начатым. |
| `3` | Preflight `FAILED` | Читать `run.jsonl` и `manifest.json`, восстановить KB/model/output и повторить запуск. |
| `4` | `PARTIAL` | Использовать только завершённые записи, перечислить незавершённые requirement IDs и планировать resume. |
| `5` | Export или crosscheck `FAILED` | Не распространять отчёт; сохранить диагностику и повторить экспорт после исправления причины. |
| `6` | Analysis `FAILED` после успешного preflight | Проверить состояния требований и внутреннюю диагностику; результат не объявлять полным. |

Traceback скрыт по умолчанию. Флаг `--debug` разрешён для локальной диагностики оператором, но вывод перед передачей необходимо проверить на чувствительные данные.

## 4. Артефакты и первичная проверка

При кодах 0, 4, 5 или 6 после начавшегося анализа ожидаются `result.json`, `result.xlsx`, `report.md`, `run.jsonl` и `manifest.json`. CLI печатает абсолютные пути и SHA-256 реально созданных файлов. При коде 3 публикуются только диагностические файлы, если output был безопасно подготовлен.

Сначала сопоставьте напечатанные hashes с `manifest.json`, затем проверьте `run_status` в `result.json`, лист `Запуск` в XLSX и строку статуса в `report.md`. Расхождение означает, что набор нельзя использовать как единый результат.

При повторном использовании output пять опубликованных имён очищаются до model preflight, а рабочий `.work` сохраняется. Если старый артефакт нельзя удалить или вместо файла обнаружен каталог, новые диагностические файлы не публикуются: выберите исправный output и не принимайте старые файлы за текущий запуск.

## 5. Resume

Для каждого запуска вычисляется resume signature. В неё входят SHA-256 input, SHA-256 knowledge snapshot, имя модели, версии decomposition/mapping prompts, `top_k` и `seed`. Checkpoints хранятся в `.work/<resume signature>/REQ-NNNN.json` с ограниченными правами.

Повторите исходную команду с тем же input, config и output. Завершённые валидные checkpoints будут прочитаны после нового preflight; требования со сбоями обрабатываются повторно. Изменение любого значимого параметра создаёт другую signature и не переиспользует несовместимый checkpoint.

Не редактируйте checkpoint вручную. Forged, повреждённая или противоречащая текущей KB запись отклоняется и пересчитывается. Не копируйте `.work` между разными входными книгами.

## 6. Backup и хранение

После проверки скопируйте весь каталог результата, включая скрытый `.work`, в контролируемое хранилище. Пример локального backup:

```bash
cp -a results/run-001 results/archive-run-001
sha256sum results/archive-run-001/{result.json,result.xlsx,report.md,run.jsonl,manifest.json}
```

Для аудита сохраните commit ID reqmap, checksum repository bundle, config без секрета, input SHA-256 и `manifest.json`. Исходный XLSX храните по правилам владельца данных; reqmap его не изменяет и не включает автоматически в output.

## 7. Чтение failures

`run.jsonl` содержит машинные events и русские сообщения. `manifest.json` содержит безопасный origin endpoint без path/query, параметры воспроизводимости и hashes. `result.json.diagnostics` и diagnostics отдельных требований показывают предметные и модельные отказы.

При `PARTIAL` найдите записи, где `analysis_state` равен `model_failed`, `validation_failed` или `skipped`. Поле `support_status` для незавершённой записи должно быть `null`; трактовать его как `not_supported` запрещено.

Если причина не очевидна, выполните ту же команду с `--debug` в изолированной консоли и следуйте [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Не заменяйте локальный evidence выводом оператора и не запускайте OpenStack/Kolla-Ansible действия из описанных implementation steps.
