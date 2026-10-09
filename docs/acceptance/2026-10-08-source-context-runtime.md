# Проверенный контекст исходных требований — 08.10.2026

## Краткий вывод и граница готовности

В рабочей ветке реализован общий reviewed source context для legacy/deep,
CLI/MCP и пяти артефактов. Без reviewed-решения строка не получает доказательного
статуса. GUI-ссылка блокирует подтверждение одним API evidence; independent/API
контроли сохраняют положительный результат. Исходные bytes, порядок, координаты,
fields и hints проверяются и сохраняются; внешние refs отделены от локальных spans.

Ветка не слита и не опубликована. Полная автоматическая приёмка завершена:
1840 passed, единственный Important независимого ревью исправлен и проверен.
Живой IDE-eval нового контракта выполнен 09.10.2026:
[15 строк / 16 атомов, 8 контрольных сценариев](2026-10-09-source-context-live-eval.md)
прошли сверку через настоящий Codex MCP. Ручная проверка XLSX/Markdown пропущена
по решению пользователя 08.10.2026 и больше не блокирует приёмку.
Приёмка прежнего Keystone-каталога не
подменяет проверку нового механизма. Реальные книги и trust store не изменялись.

## Контракты

| Область | Версия |
| --- | --- |
| Result legacy / deep | 1.2 / 2.2 |
| Binding engine / SourceBinding | 2.0 / 2.0 |
| MCP tool / workflow | 3.0 / 3.0 |
| Source context map / resolver | 1.0 / 1.0 |
| Prompts decomposition / legacy / deep | 2.1 / 1.4 / 2.2 |
| Seed | 2 |
| Proposal / SQLite STATE_VERSION / grammar | 2 / 1 / 1.2, без изменения |

Frozen payload включает input/map/signature bytes, normalized map с draft,
решения, кодовую идентичность resolver и отдельные grammar_version/grammar_sha256.
Идентичность грамматики входит также в context_id и ключи pure-cache.
Deep повторно проверяет текущий
Ed25519 signer в namespace `reqmap-source-context`; pure-cache проверки
целостности не кеширует доверие. Исторические 1.0/1.1/2.0/2.1 проверяются по
сохранённым hashes без нового resolver, старые active-сессии несовместимы.

## Доказательства разработки

Базовый код до реализации: `4e987fe271ba5862120e013903754cd3365f5d9e`.
Отдельная baseline-проба воспроизвела дефект: пункт без reviewed-контекста
получал supported. Затем наблюдались RED/GREEN по snapshot, strict map/trust,
resolver, frozen codec, общему runtime gate и экспорту.

| Проверка | Наблюдавшийся результат |
| --- | --- |
| Исходный suite | 1655 passed |
| Snapshot + import | 66 passed |
| Map/config/trust | 138 passed |
| Resolver + source binding | 40 passed |
| Все тесты нового snapshot/codec/map/resolver/trust | 98 passed |
| Runtime migration suite без scale | 1814 passed, 3 прежних fork warnings |
| Scale, отдельно в том же runtime | 1 passed; 1950 строк и 1950 атомов сохранены |
| Пять артефактов, export/crosscheck | 134 passed |
| CLI/MCP matrix + offline install + distribution | 15 passed, 52.10 s |
| Русская документация и локальные ссылки | 23 passed |
| Regression по замечанию ревью: смена только грамматики | RED 5 failed; GREEN 5 passed |
| Профильный набор после исправления ревью | 108 passed, 6.98 s |
| Итоговый полный suite после runtime fix | 1840 passed, 3 прежних fork warnings, 543.55 s |
| Документация и frozen live input после правок | 24 passed; все 49 локальных ссылок существуют |

Итоговая команда `PYTHONPATH=src .venv/bin/python -m pytest -q --tb=short`
завершилась с exit 0; `git diff --check` также прошёл. Предупреждения Python3.14
относятся к трём прежним FIFO/fork тестам и присутствовали в baseline.
Прерванный до review fix полный прогон не учитывается как завершённая проверка.

Scale: 397.889 s весь сценарий, 23.418 s финализация, peak RSS 473.92 MiB.
FAILED в этом синтетическом отчёте — ожидаемый operational status намеренно
незавершённых mappings, не результат pytest. Все страницы `get_result` проверены.

Основные команды воспроизведения из корня рабочей ветки:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_runtime.py tests/test_source_context_sessions.py
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_exports.py
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_source_context_acceptance.py tests/test_distribution.py
PYTHONPATH=src .venv/bin/python -m pytest -q
git diff --check
```

В installed acceptance `install.sh` создаёт отдельный tmp venv при
`PIP_NO_INDEX=1`, без PyPI. `PYTHONPATH` удалён у установленного executable;
отдельно проверяется путь модуля внутри нового venv. Проверяются версия CLI,
настоящие девять stdio MCP tools, версии tool/workflow, schemas1.2/2.2, restart
после удаления карты, CLI checkpoint без повторного model POST и пять файлов.

Deep-контрасты с обязательным `insufficient_evidence` сохраняют PARTIAL и отказ
`reqmap_finalize(allow_partial=false)`. Их partial-export проверяется отдельно.
Строгий finalize проверяется на полном положительном deep-контроле с готовым
синтетическим procedure. Это сохраняет прежний operational gate.

## Независимое ревью

Один reviewer проверил всю реализацию `4e987fe..9faa537` и самостоятельно получил
172 passed в context-наборе. Найден один Important, Critical/Minor нет;
отказов от оценки нет. При смене только binding_source-грамматики прежний
resolver digest не менялся, а frozen record мог переразбираться до проверки
контракта: finalized ошибочно становился SESSION_CORRUPT, CLI не мог начать
новый run с доступной картой.

Исправление сохраняет grammar identity отдельно от согласованных шести файлов
resolver, проверяет полный MCP contract до replay, изолирует CLI runs и кеши.
Пять новых тестов сначала воспроизвели сбой, затем подтвердили historical read
с неизменными artifact bytes, active mismatch, CLI с доступной/удалённой картой
и инвалидирование уже прогретого кеша. Отложенных Minor нет.

## Замороженный набор и хеши

[`cases.json`](../../tests/fixtures/source_context/cases.json) — 39 независимо
заданных случаев до resolver. [`live_eval.json`](../../tests/fixtures/source_context/live_eval.json)
— выбранные 8 сценариев / 15 строк для последующего живого клиента. Никаких
реальных книг, приватных ключей или рабочего trust store в fixtures нет.
Ожидания статусов/кодов/числа атомов не вычисляются resolver-ом. Контроли Nova
используют `P-NOVA-VM-CREATE`/`E-NOVA-VM-CREATE` для legacy и синтетические
`P-NOVA-CREATE`/`EV-NOVA-CREATE` для deep. Многоатомный случай дополнительно
использует Neutron port evidence; deep fixture целиком синтетический.

| Объект | SHA-256 |
| --- | --- |
| cases.json | cfdfb338bd60b5285e08cd81f248b93734dd5057b2476fef4b04d5b536924531 |
| live_eval.json | 139335c3ea7f0b66e2f1d0ef70cb6cd7ebc9877b94fef25ed0721b73038df2c5 |
| Input bytes | d327bf223564dfe5f869c9df487881cccebae9b5b78700aab7c767658f79eea5 |
| CLI map bytes | 7c503e488cc6f986e9669f8566e72d594fd6d7362038d15408b6ad4e6baafacd |
| MCP map bytes | c1d12d94ec8298093b4acaf5dae7575948608e8ad6302b566d1037173d00a469 |
| Resolver manifest | 9df1599f759375bdcfb4ea8936b3c8c8e034c69c9ac14d52295d468e719e1e23 |
| Own grammar | 932e7ec629d5b0c5859de7bc13cce129de646c452c997814310754c26b14a990 |

CLI и MCP имеют разные source_name; поэтому карты различаются, хотя input bytes
совпадают. После изменения resolver нужен новый запуск, а не повтор старого
active-контракта. Хеши каждого опубликованного файла сверяет acceptance test с
manifest; manifest и журнал дополнительно содержат source/map/resolver identity.

## Решения при исполнении и цена ошибки

| Решение | Основание и риск при ошибке |
| --- | --- |
| Переиспользовать Ed25519 envelope, identity/namespace проверять OpenSSH | Snapshot-helper жёстко задаёт другую identity; риск — отказ допустимому signer или доверие чужому |
| Исправить два синтетических multi-atom inputs под существующий separator | Грамматика остаётся 1.2, expected statuses прежние; риск — не проверить контекст каждого атома |
| Передать текущий внешний trust в BindingContext/SessionView | Frozen metadata не доказывает актуальное доверие; риск — пропустить отзыв signer |
| Обновить analysis_origin.py/deep_models.py вне исходного списка файлов | Все потребители должны принять версии 3.0/2.2 одновременно; риск — сломать finalize |
| Проверить Task5 suite обычной и scale-частями | Все тесты сохранены; риск взаимодействия частей закрывается итоговым цельным suite |
| Получить исторический schema2.0 fixture старым кодом из archive434c76e | Нужны исходные bytes, а не переименование версии; риск — ложная проверка совместимости |
| Дополнить manifest.py/publication.py | Нужна provenance всех пяти артефактов; риск — потерять context digests в журнале/manifest |
| Сохранить deep PARTIAL/allow_partial gate | Контрастный insufficient не может пройти strict finalize; риск — ослабить operational proof |
| Хранить grammar identity отдельно от шести файлов resolver | Смена грамматики тоже меняет семантику; риск — переинтерпретация истории или старый кеш |

Отложенных Minor нет. Ни одно замечание ревью не оставлено без исправления.

## Пропущенная ручная проверка и живой eval

Проверено программно: полный display payload, точные quotes/refs, безопасные
длинные ячейки и formula-like строки, все пять файлов и crosscheck при подменах.
Удалось открыть Markdown source в VS Code. CUA неоднократно вернул
`ScreenCaptureKit -3812`, invalid AX IDs и `noWindowsAvailable`; Excel дошёл до
выбора result.xlsx, но открытие книги не было подтверждено. Quick Look создал
PNG с одним увеличенным углом листа; этого недостаточно для ручного layout QA.
Ручная проверка XLSX/Markdown rendering **не выполнена**. После этих попыток
пользователь явно решил её пропустить (08.10.2026: «пропускаем ручную проверку»).
Она исключена из обязательной приёмки; визуальное качество не объявляется
проверенным. Это решение не отменяет отдельный живой IDE/model eval.

На 08.10.2026 отдельный новый модельный запуск оставался незавершённым.
09.10.2026 он [выполнен и проверен](2026-10-09-source-context-live-eval.md).
Согласованный frozen prompt для этого сценария:

> Через MCP reqmap обработай массив texts из tests/fixtures/source_context/live_input.json
> с заранее настроенной сопровождающим source_context_path. Сохрани все строки и canonical
> atoms; используй только актуальный get_atom_context. Не меняй карту, код,
> настройки или evidence; не запускай CLI analyze. reported_client=codex.
> Заверши legacy-сессию allow_partial=false, вызови get_result и проверь пять
> artifact hashes. Зафиксируй session/request IDs и фактические statuses/codes/refs.

Перед eval оператор извлекает `maps.mcp.mapping` из frozen live_eval.json точными canonical bytes в файл
и задаёт `source_context_path` отдельной agent-конфигурации. Модель не видит
ожидаемые результаты как основание для вывода. Версии клиента/модели и actual
results записывают после реального запуска. Scripted HTTP/stdio не считается
таким запуском. Этот gate этапа 7 закрыт отчётом от 09.10.2026; полный корпус
и другие клиентские маршруты остаются за пределами данной приёмки.

[Инструкция подготовки и review карты](../../RUNBOOK.md#проверенный-контекст-исходных-строк).
Synthetic suite не доказывает покрытие исходных 1949 строк: для них нужна карта
конкретной согласованной выборки и отдельные метрики grammar/context/evidence.
