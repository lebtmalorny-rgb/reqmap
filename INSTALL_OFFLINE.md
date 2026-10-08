# Автономная установка reqmap

Документ описывает перенос полного, заранее проверенного Git-репозитория в зону без доступа к Интернету. Штатная установка использует только системный Python и universal wheels из `vendor/wheels`; PyPI, контейнерный registry и online-документация не нужны.

## 1. Подготовка в online staging

На машине с Git и разрешённым сетевым доступом получите утверждённую ревизию. Не собирайте production-пакет из непроверенного рабочего каталога.

```bash
git clone <APPROVED_REPOSITORY_URL> reqmap
cd reqmap
git fetch --tags --prune
git checkout <APPROVED_TAG_OR_COMMIT>
git status --short
git rev-parse HEAD
```

Пустой вывод `git status --short` подтверждает отсутствие локальных добавок. Затем проверьте lock и bundled wheels локальным suite:

```bash
python3 -m venv .staging-venv
.staging-venv/bin/python -m pip install \
  --no-index --find-links vendor/wheels \
  pytest==8.3.5 hypothesis==6.131.9 openpyxl==3.1.5 et-xmlfile==2.0.0
.staging-venv/bin/python -m pytest tests/test_distribution.py -q
```

Создайте переносимый Git bundle, содержащий утверждённые refs:

```bash
git bundle create ../reqmap.bundle --all
git bundle verify ../reqmap.bundle
cd ..
sha256sum reqmap.bundle > reqmap.bundle.sha256
```

Если staging работает на macOS, эквивалентная команда — `shasum -a 256 reqmap.bundle > reqmap.bundle.sha256`. Передавайте bundle и checksum раздельно либо через контролируемый носитель. SHA-256 должен быть зарегистрирован в журнале передачи.

## 2. Проверка после переноса

В изолированной зоне сначала проверьте checksum, не открывая bundle как доверенный репозиторий:

```bash
sha256sum -c reqmap.bundle.sha256
git bundle verify reqmap.bundle
git clone reqmap.bundle reqmap
cd reqmap
git checkout <APPROVED_TAG_OR_COMMIT>
git rev-parse HEAD
git status --short
```

Сравните commit ID и SHA-256 со значениями из журнала передачи. Если хотя бы одно значение отличается, остановитесь: не запускайте installer и не заменяйте wheel вручную.

Альтернатива для локального mirror — передача bare-репозитория, проверенного `git fsck --full`, с последующим `git clone /media/reqmap.git reqmap`. Требование остаётся тем же: checkout должен быть чистым и совпадать с утверждённым commit.

## 3. Установка без сети

`install.sh` сначала проверяет Python 3.11+, затем точное соответствие `requirements-vendor.lock` и universal `none-any` wheels. Virtualenv создаётся только после этих проверок.

```bash
chmod +x install.sh
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  ./install.sh .venv
```

Для явного интерпретатора задайте `PYTHON_BIN`:

```bash
PYTHON_BIN=/usr/bin/python3.11 ./install.sh /opt/reqmap/venv
```

Относительный target создаётся внутри репозитория; компонент `..` запрещён. Абсолютный target используется буквально, но также не должен содержать `..`. Любая уже существующая символическая ссылка в компонентах пути target запрещена. Installer применяет `pip --no-index --find-links`, поэтому случайный выход на package index исключён настройками команды.

## 4. Smoke test

Installer сам выполняет две проверки. Их можно повторить вручную:

```bash
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
.venv/bin/reqmap analyze --help
```

После этого создайте `config.yaml` из локального примера, укажите разрешённый OpenAI-compatible endpoint и выполните синтетический тест:

```bash
printf '%s\n' 'Проверить синтетическую функцию управления ресурсом' | \
  .venv/bin/reqmap analyze --stdin \
  --config config.yaml \
  --output results/offline-smoke
```

Доступ к локальному LLM endpoint является эксплуатационной зависимостью, но не доступом в Интернет. URL из `source-manifest.json` во время анализа не открываются.

## 5. Обновление поставки

Обновление versions или wheels выполняется только в online staging отдельным изменением `requirements-vendor.lock`. В комплект допускаются лишь файлы с Python-tag `py3` или `py2.py3`, ABI-tag `none` и platform-tag `any`. После обновления повторите полный suite, создайте новый bundle, новый checksum и новую запись в журнале передачи.

Не копируйте отдельные файлы поверх работающего checkout. Разверните новую ревизию рядом, выполните `./install.sh`, smoke test и только затем переключите операторский путь. Старую ревизию сохраните до завершения проверки результатов.

Дальнейший запуск описан в [RUNBOOK.md](RUNBOOK.md), а причины установки без нужного wheel — в [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## 6. Предварительные условия deep

Профиль `legacy` остаётся значением по умолчанию. Для `deep` дополнительно
нужен системный OpenSSH с `ssh-keygen -Y sign` и `ssh-keygen -Y verify`.
В Rocky Linux 9 пакет называется `openssh-clients`; Python 3.11 и этот пакет
готовятся в online staging либо из доверенного локального RPM-репозитория
до автономной установки reqmap. Installer их не скачивает.

В изолированную зону передаются раздельно:

- проверенный source bundle и `vendor/wheels`;
- approved snapshot schema v2 с `snapshot-manifest.json` и `snapshot-manifest.sig`;
- доверенный `allowed_signers`, полученный по независимому доверенному каналу.

Публичный ключ внутри самого snapshot не устанавливает доверие. Private signing
key остаётся в maintenance-среде, API key — в окружении оператора. Production
snapshot deep в архитектурную поставку не входит. Тестовые ключи создаются
заново в временных каталогах и не передаются как доверенные рабочие ключи.

После переноса проверьте snapshot:

```bash
.venv/bin/reqmap knowledge validate \
  --path /opt/reqmap-knowledge/epoxy-2025.1-deep \
  --allowed-signers /etc/reqmap/trust/allowed_signers
```

Тестовая Linux-лаборатория может использовать контейнер с `--network none`:
loopback остаётся доступным fake-серверу модели, внешней сети нет. Docker нужен
только для такой лаборатории и не является зависимостью пользователя reqmap.

## Агентный режим без второго endpoint

Установка `./install.sh` включает `reqmap agent serve`; новых runtime-зависимостей
для MCP нет. Используются те же offline wheels. После установки скопируйте
`config.agent.example.yaml` в новый `config.agent.yaml`, затем подключите
console entry point к клиенту по [CLIENTS_CODEX_OPENCODE.md](CLIENTS_CODEX_OPENCODE.md).
Проверка локальной установки без модели:

```bash
.venv/bin/reqmap agent serve --help
.venv/bin/reqmap agent serve --config config.agent.yaml < /dev/null
```

Вторая команда завершается с кодом 0 без stdout при корректном config.
Проверка KB остаётся отдельной командой `knowledge validate`.
Сам reqmap работает локально; установка клиента, вход и доступ к его provider/model
готовятся отдельно. Offline Python bundle не содержит IDE, Codex/OpenCode или
веса модели и не обеспечивает автономность облачного клиента.


## Проверка контракта контекста после установки

Новые CLI/MCP запуски используют result schema 1.2/2.2 и tool/workflow3.0.
Новых wheel-зависимостей нет. `source_context_path` указывает на отдельно
проверенную карту; deep требует локального OpenSSH Ed25519 и внешнего trust store
с namespace `reqmap-source-context`. Installer не утверждает карту и не расширяет
доверие. Версия пакета `reqmap --version` остаётся 0.1.0; версии контрактов смотрите
в metadata результата. До повторного предметного анализа старые active sessions
не возобновляются; historical finalized сохраняются.

Приёмка `tests/test_source_context_acceptance.py::test_installed_context_contract`
выполняет `install.sh` в tmp venv при `PIP_NO_INDEX=1`, затем настоящий stdio и CLI
с локальным scripted transport. Это воспроизводимая backend-проверка, отдельная
от [живого IDE-eval](docs/acceptance/2026-10-08-source-context-runtime.md).
