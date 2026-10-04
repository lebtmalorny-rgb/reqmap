# Диагностика reqmap

Начинайте с exit code, русской строки ошибки и реально напечатанных путей. Не используйте старые файлы из повторно выбранного output как результат нового запуска. Traceback включайте флагом `--debug` только в защищённой операторской сессии.

## Модель недоступна на preflight

Признаки: exit code 3, `MODEL_*` в diagnostics, предметных model calls нет. Проверьте локальный маршрут до `base_url`, имя загруженной модели, timeout и endpoint `/chat/completions`. Затем повторите исходную команду с тем же output для resume.

HTTP `401` означает неверный или отсутствующий credential. Проверьте имя `api_key_env` в config и наличие переменной окружения, не печатая её значение. Ключ нельзя записывать в YAML, журнал или командную строку.

HTTP `429` означает ограничение локального endpoint. Уменьшите параллельную нагрузку, дождитесь освобождения capacity и учитывайте настроенные retries. Не перенаправляйте запрос на внешний сервис без отдельного разрешения.

HTTP `5xx` означает ошибку model server или reverse proxy. Проверьте server logs, доступную память, health модели и совместимость OpenAI Chat Completions. Reqmap не должен скрывать повторный отказ как `not_supported`.

## Invalid JSON модели

На invalid JSON выполняется одна корректирующая попытка со строгой schema. Повторный невалидный ответ выставляет `analysis_state=model_failed`, оставляет `support_status=null` и продолжает соседние требования. Итог обычно `PARTIAL` с exit code 4, если хотя бы одно требование завершилось.

Проверьте поддержку JSON response format, лимит ответа и отсутствие пояснительного текста вокруг JSON. При необходимости временно примените `--debug`, но не исправляйте canonical результат вручную.

## Неоднозначный XLSX

Ошибка `XLSX_PROFILE_AMBIGUOUS` или текст со словом «неоднозначна» означает, что importer нашёл несколько подходящих sheets/headers либо не смог однозначно выбрать ID/text columns. Задайте `input_profile` в config: включаемые листы, header row, ID column и text column.

Формула в ячейке требования отклоняется как `XLSX_TEXT_FORMULA`. Замените её подтверждаемым исходным текстом в копии входной книги. Не меняйте оригинал средствами reqmap.

Если source path совпадает с `result.json`, `result.xlsx`, `report.md`, `run.jsonl` или `manifest.json`, выберите другой `--output`. Это fail-closed защита исходного файла.

## Ошибка базы знаний

`KNOWLEDGE_*`, hash mismatch или несовпадение `metadata.json.snapshot_sha256` означает повреждённый либо неполный snapshot. Выполните:

```bash
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
git status --short knowledge/epoxy-2025.1
```

Не пересчитывайте hash только ради прохождения проверки. Восстановите весь snapshot из утверждённого commit. `tools/kb/build_snapshot.py` используется лишь при осознанном maintenance-изменении с review evidence.

## PARTIAL

При `PARTIAL` CLI возвращает 4 и печатает IDs незавершённых требований. В `result.json` найдите `model_failed`, `validation_failed` или `skipped`; завершённые записи можно анализировать, но весь run нельзя называть полным.

Повторите запуск с идентичными input/config/model parameters и output. Resume signature выберет совместимые checkpoints из `.work`. Если изменились KB hash, prompts, model, seed или `top_k`, создастся новая signature.

## Export или crosscheck FAILED

Exit code 5 означает несогласованный JSON/XLSX/Markdown либо невозможность безопасно записать артефакт. Проверьте свободное место, права owner, отсутствие symlink в output path и отсутствие каталогов с зарезервированными именами.

`CROSSCHECK_XLSX_*` требует отклонить всю книгу: не копируйте отдельный лист из старого результата. `CROSSCHECK_JSON_*` и `CROSSCHECK_MARKDOWN_*` также требуют нового полного экспорта из canonical `RunResult`.

## Недостающий wheel

Installer завершится до создания virtualenv, если package из `requirements-vendor.lock` не имеет точного universal wheel либо найден лишний wheel. Проверьте имя, version, Python tag `py3`/`py2.py3`, ABI `none` и platform `any`.

Не разрешайте pip загрузить замену из PyPI. Верните поставку в online staging, добавьте проверенный wheel, синхронно обновите lock, прогоните tests и создайте новый repository bundle с SHA-256.

## Безопасный debug

```bash
.venv/bin/reqmap analyze --stdin \
  --config config.yaml \
  --output results/debug-run \
  --debug < synthetic-requirement.txt
```

По умолчанию CLI скрывает Python traceback и выводит русскую диагностику. В debug output проверьте отсутствие API key, Authorization header, query token и пользовательских секретов до передачи третьей стороне. Endpoint в manifest должен содержать только scheme, host и port.

Дополнительные операционные шаги находятся в [RUNBOOK.md](RUNBOOK.md), правила восстановления поставки — в [INSTALL_OFFLINE.md](INSTALL_OFFLINE.md), структура evidence — в [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md).

## Deep: trust, schema и предметные пробелы

`KNOWLEDGE_SCHEMA_UNSUPPORTED` означает несовместимую схему: deep требует v2,
legacy использует v1. Не переключайте профиль автоматически, чтобы скрыть
ошибку. `SNAPSHOT_*` указывает на проверку доверия или целостности: сверяйте
approved status, Ed25519-подпись, внешний allowed_signers, hashes и состав
файлов. Проверьте системный `ssh-keygen` с поддержкой `-Y`; runtime не может
исправлять или скачивать snapshot. Подменённый либо отсутствующий файл должен
останавливать анализ до обращения к модели.

`evidence_conflict` сохраняет противоречащие доказательства; не выбирайте
положительное вручную. `procedure_gap` означает отсутствие подтверждённого
обязательного шага, `rollback_unverified` — отсутствие проверенного отката.
`responsibility_ambiguous` также является причиной PARTIAL; текущий mapping
без выбранных записей возвращает insufficient_evidence без этой диагностики.
Эти случаи требуют review базы и/или формулировки, а не выполнения действий
на инфраструктуре. Все атомы и исходные строки должны оставаться прослеживаемыми.

Отсутствие `knowledge/epoxy-2025.1-deep` после установки ожидаемо: production
корпус не поставляется архитектурным этапом. Миграция v1 создаёт draft,
который нельзя использовать как approved. Наличие тестов с fake LLM не
подтверждает качество конкретного локального model endpoint.
